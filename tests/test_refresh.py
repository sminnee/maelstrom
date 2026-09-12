"""Tests for maelstrom.refresh — the refresher contract, over a real table."""

import asyncio

import pytest

from maelstrom.refresh import (
    Fetched,
    Health,
    RefreshRefused,
    Scope,
    due,
    health_entity,
    health_from_row,
    health_row,
    refused,
    succeeded,
)
from maelstrom.state_db.db import StateDb
from maelstrom.state_db.migrate import open_state_db
from maelstrom.state_db.types import Migration, TableSpec, Write

#: Wall time a test stamps a fact with, so a `since` is readable rather than now.
AT = "2026-09-12T10:00:00Z"
LATER = "2026-09-12T10:05:00Z"

#: The monotonic readings beside them. Two clocks, so a test that moves one and
#: not the other proves which fact each one drives.
NOW = 1000.0
NOW_LATER = 1300.0


class FakeRefresher:
    """A refresher a test drives: what it answers, and when it blocks.

    ``blocked_on`` is the pattern :class:`InMemoryWorktreeSource` already uses
    (``sources.py``): the real read shells out and takes seconds, this one
    returns at once, so a test that cares about a read in flight has no window
    without it.
    """

    name = "fake"
    cadence = 60.0
    stand_off_secs = 300.0

    def __init__(self, rows: dict[str, dict] | None = None) -> None:
        self.rows = rows or {}
        self.want = True
        self.narrowed: Scope = None
        self.complete = True
        self.refuse: RefreshRefused | None = None
        self.asked: list[Scope] = []
        self.blocked_on: asyncio.Event | None = None

    def wanted(self) -> bool:
        return self.want

    def scope(self) -> Scope:
        return self.narrowed

    async def fetch(self, scope: Scope) -> Fetched:
        self.asked.append(scope)
        if self.blocked_on is not None:
            await self.blocked_on.wait()
        if self.refuse is not None:
            raise self.refuse
        return Fetched(rows=dict(self.rows), complete=self.complete)


@pytest.fixture
async def db():
    """A database with one cached table, for a refresher to keep current."""
    state_db = open_state_db(":memory:")
    state_db.ladders["thing"] = (
        Migration(
            (
                "CREATE TABLE thing (id TEXT PRIMARY KEY, "
                "revision INTEGER NOT NULL, fetched_at TEXT, "
                "body TEXT NOT NULL DEFAULT '')",
                "CREATE INDEX thing_revision ON thing (revision)",
            )
        ),
    )
    state_db.tables["thing"] = TableSpec("thing", cached=True)
    await state_db.migrate()
    yield state_db
    state_db.close()


def unknown(name: str = "fake") -> Health:
    """A refresher nothing is yet known about."""
    return Health(name=name, reachable=True, since="")


async def refresh(
    db: StateDb, refresher, health: Health, at: str, now: float = NOW
) -> Health:
    """One refresh: fetch outside the transaction, then write what came back.

    The shape every refresher uses. ``fetch`` is awaited first and only its
    ``Fetched`` enters a write, so no network call ever happens with the write
    lock held.
    """
    try:
        fetched = await refresher.fetch(refresher.scope())
    except RefreshRefused as exc:
        return refused(health, exc, refresher, now, at)
    writes = [
        Write("thing", id, columns=row, fetched_at=at)
        for id, row in fetched.rows.items()
    ]
    if fetched.complete:
        # Only a complete fetch may delete: a narrowed one never asked about
        # the rows it left out, so treating it as complete would delete them.
        known = {row["id"] for row in await db.read_all("thing")}
        writes += [Write("thing", id) for id in sorted(known - set(fetched.rows))]
    await db.write_all(writes)
    return succeeded(health, now, at)


class TestDue:
    """Slice 14: due refuses for three reasons, and only those."""

    def test_a_refresher_never_run_is_due(self):
        assert due(unknown(), FakeRefresher(), now=NOW)

    def test_standing_off_is_not_due(self):
        health = Health(name="fake", reachable=False, since=AT, stand_off_until="9999")
        assert not due(health, FakeRefresher(), now=NOW)

    def test_a_stand_off_that_has_passed_is_due_again(self):
        health = Health(name="fake", reachable=False, since=AT, stand_off_until="900")
        assert due(health, FakeRefresher(), now=NOW)

    def test_what_nobody_watches_is_not_due(self):
        refresher = FakeRefresher()
        refresher.want = False
        assert not due(unknown(), refresher, now=NOW)

    def test_inside_the_cadence_is_not_due(self):
        health = Health(name="fake", reachable=True, since=AT, last_attempt="970")
        assert not due(health, FakeRefresher(), now=NOW)

    def test_past_the_cadence_is_due(self):
        health = Health(name="fake", reachable=True, since=AT, last_attempt="900")
        assert due(health, FakeRefresher(), now=NOW)


class TestRefusal:
    """Slice 15: a refused refresh leaves the last-known rows standing."""

    async def test_the_rows_and_the_revision_do_not_move(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}})
        health = await refresh(db, refresher, unknown(), AT)
        before = await db.revision()

        refresher.refuse = RefreshRefused("rate limited", stand_off=300.0)
        health = await refresh(db, refresher, health, LATER, NOW_LATER)

        assert [row["body"] for row in await db.read_all("thing")] == ["one"]
        assert await db.revision() == before
        assert not health.reachable
        assert health.stand_off_until
        assert health.detail == "rate limited"

    async def test_since_does_not_move_on_a_second_refusal(self, db):
        """`since` says how long it has been down, so a re-refusal must not reset it."""
        refresher = FakeRefresher()
        refresher.refuse = RefreshRefused("rate limited")
        first = await refresh(db, refresher, unknown(), AT)
        second = await refresh(db, refresher, first, LATER, NOW_LATER)
        assert first.since == AT
        assert second.since == AT

    async def test_recovering_moves_since(self, db):
        refresher = FakeRefresher()
        refresher.refuse = RefreshRefused("rate limited")
        down = await refresh(db, refresher, unknown(), AT)
        refresher.refuse = None
        up = await refresh(db, refresher, down, LATER, NOW_LATER)
        assert up.reachable
        assert up.since == LATER
        assert up.stand_off_until == ""

    async def test_a_success_that_changes_nothing_leaves_since_alone(self, db):
        refresher = FakeRefresher()
        first = await refresh(db, refresher, unknown(), AT)
        second = await refresh(db, refresher, first, LATER, NOW_LATER)
        assert second.since == first.since
        assert second.last_success == LATER


class TestOneNotice:
    """Slice 16: an upsert raises exactly one notice."""

    async def test_only_the_changed_row_is_named(self, db):
        refresher = FakeRefresher(
            {"a": {"body": "one"}, "b": {"body": "two"}, "c": {"body": "three"}}
        )
        await refresh(db, refresher, unknown(), AT)
        cursor = await db.revision()

        refresher.rows["b"] = {"body": "changed"}
        await refresh(db, refresher, unknown(), LATER, NOW_LATER)

        notices, _ = await db.notices_since(cursor)
        assert notices == {"thing": {"b"}}

    async def test_a_fetch_that_changes_nothing_is_silent(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}})
        await refresh(db, refresher, unknown(), AT)
        cursor = await db.revision()
        await refresh(db, refresher, unknown(), LATER, NOW_LATER)
        assert await db.notices_since(cursor) == ({}, cursor)

    async def test_a_silent_fetch_still_stamps_freshness(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}})
        await refresh(db, refresher, unknown(), AT)
        await refresh(db, refresher, unknown(), LATER, NOW_LATER)
        assert (await db.read("thing", "a"))["fetched_at"] == LATER


class TestReadDuringRefresh:
    """Slice 17: a read answers while a refresh is in flight."""

    async def test_the_previous_rows_answer_and_the_read_does_not_block(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}})
        await refresh(db, refresher, unknown(), AT)

        refresher.blocked_on = asyncio.Event()
        refresher.rows = {"a": {"body": "two"}}
        refreshing = asyncio.create_task(
            refresh(db, refresher, unknown(), LATER, NOW_LATER)
        )
        await asyncio.sleep(0)

        rows = await asyncio.wait_for(db.read_all("thing"), timeout=1.0)
        assert [row["body"] for row in rows] == ["one"]

        refresher.blocked_on.set()
        await refreshing
        assert [row["body"] for row in await db.read_all("thing")] == ["two"]


class TestNarrowedFetch:
    """Slice 18: a narrowed fetch never deletes."""

    async def test_out_of_scope_rows_stand(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}, "b": {"body": "two"}})
        await refresh(db, refresher, unknown(), AT)

        refresher.rows = {"a": {"body": "changed"}}
        refresher.complete = False
        refresher.narrowed = {"a"}
        await refresh(db, refresher, unknown(), LATER, NOW_LATER)

        rows = {row["id"]: row["body"] for row in await db.read_all("thing")}
        assert rows == {"a": "changed", "b": "two"}
        assert refresher.asked[-1] == {"a"}

    async def test_a_complete_fetch_does_delete(self, db):
        refresher = FakeRefresher({"a": {"body": "one"}, "b": {"body": "two"}})
        await refresh(db, refresher, unknown(), AT)
        refresher.rows = {"a": {"body": "one"}}
        await refresh(db, refresher, unknown(), LATER, NOW_LATER)
        assert [row["id"] for row in await db.read_all("thing")] == ["a"]


class TestHealthEntity:
    """Slice 19: health matches Host where the two overlap, and no further."""

    def test_reachable_and_since_carry_host_s_meanings(self):
        entity = health_entity(
            Health(name="fake", reachable=False, since=AT, detail="rate limited")
        )
        assert entity["id"] == "fake"
        assert entity["reachable"] is False
        assert entity["since"] == AT

    def test_it_carries_no_socket_or_usage(self):
        """Those belong to the agent host alone; a null here would invite a question."""
        entity = health_entity(unknown())
        assert "socket" not in entity
        assert "usage" not in entity


class TestHealthRoundTrip:
    """Health survives a restart, which is what the stand-off needs it to do.

    A refused refresher that forgot its stand-off would ask again on the next
    start, which is exactly what a rate limit is telling it not to do.
    """

    async def test_a_refusal_survives_a_restart(self, db):
        refresher = FakeRefresher()
        refresher.refuse = RefreshRefused("rate limited", stand_off=300.0)
        health = await refresh(db, refresher, unknown(), AT)
        await db.write_health(health.name, **health_row(health))

        read_back = health_from_row("fake", await db.read_health("fake"))
        assert read_back == health
        assert not due(read_back, refresher, now=NOW + 1)
        assert due(read_back, refresher, now=NOW + 301)

    async def test_a_refresher_with_no_row_has_never_run(self, db):
        assert health_from_row("fake", await db.read_health("fake")) == unknown()

    async def test_health_raises_no_notice_and_consumes_no_revision(self, db):
        """A refresher standing off must not make every client refetch."""
        await db.upsert("thing", "a", fetched_at=AT, body="one")
        cursor = await db.revision()
        health = refused(unknown(), RefreshRefused("no"), FakeRefresher(), NOW, AT)
        await db.write_health(health.name, **health_row(health))
        assert await db.notices_since(cursor) == ({}, cursor)


class TestAnUnreadableClockReading:
    """A row a different build wrote must not raise out of `due`.

    The poller catches and logs, so an exception here would log once a tick
    forever. Both readings degrade to "ask now", which is the safe way to be
    wrong.
    """

    def test_an_unreadable_stand_off_does_not_hold(self):
        health = Health(name="fake", reachable=False, since=AT, stand_off_until="soon")
        assert due(health, FakeRefresher(), now=NOW)

    def test_an_unreadable_last_attempt_reads_as_due(self):
        health = Health(name="fake", reachable=True, since=AT, last_attempt="ages ago")
        assert due(health, FakeRefresher(), now=NOW)
