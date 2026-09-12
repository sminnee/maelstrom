"""Tests for maelstrom.state_db — the state database and its shared machinery."""

import asyncio
import sqlite3
import threading

import pytest

from maelstrom.state_db.db import StateDb
from maelstrom.state_db.migrate import open_state_db
from maelstrom.state_db.types import (
    Migration,
    PythonMigration,
    SchemaTooNewError,
    SchemaTooOldError,
    TableSpec,
    TransactionOpenError,
    UnknownColumnError,
    UnknownTableError,
    Write,
    WrongThreadError,
)


@pytest.fixture
async def db():
    """A migrated in-memory database, closed at the end of the test."""
    state_db = open_state_db(":memory:")
    await state_db.migrate()
    yield state_db
    state_db.close()


class TestOpenAndMigrate:
    """Slice 1: the spine exists, and migrating twice changes nothing."""

    async def test_a_migrated_db_starts_at_revision_zero(self, db):
        assert await db.revision() == 0

    async def test_migrating_twice_is_a_no_op(self, db):
        await db.migrate()
        assert await db.revision() == 0

    async def test_check_passes_on_a_migrated_db(self, db):
        await db.check()

    async def test_a_file_backed_db_journals_in_wal(self, tmp_path):
        state_db = open_state_db(tmp_path / "state.db")
        await state_db.migrate()
        assert await state_db.pragma("journal_mode") == "wal"
        state_db.close()

    async def test_an_in_memory_db_does_not_journal_in_wal(self, db):
        assert await db.pragma("journal_mode") != "wal"


class TestSchemaVersion:
    """Slice 2: a version mismatch refuses rather than upgrading."""

    async def test_an_unmigrated_db_is_too_old(self, tmp_path):
        state_db = open_state_db(tmp_path / "state.db")
        with pytest.raises(SchemaTooOldError) as exc:
            await state_db.check()
        assert "mael admin migrate" in str(exc.value)
        state_db.close()

    async def test_a_subsystem_behind_this_build_names_the_command(self, db):
        db.ladders["fake"] = (_noop_migration(), _noop_migration())
        await db.migrate()
        db.ladders["fake"] = (*db.ladders["fake"], _noop_migration())
        with pytest.raises(SchemaTooOldError) as exc:
            await db.check()
        assert "fake" in str(exc.value)
        assert "2" in str(exc.value) and "3" in str(exc.value)
        assert "mael admin migrate" in str(exc.value)

    async def test_a_subsystem_ahead_of_this_build_refuses(self, db):
        db.ladders["fake"] = (_noop_migration(), _noop_migration())
        await db.migrate()
        db.ladders["fake"] = (_noop_migration(),)
        with pytest.raises(SchemaTooNewError) as exc:
            await db.check()
        assert "fake" in str(exc.value)
        assert "2" in str(exc.value) and "1" in str(exc.value)

    async def test_check_never_upgrades(self, db):
        db.ladders["fake"] = (_noop_migration(),)
        with pytest.raises(SchemaTooOldError):
            await db.check()
        with pytest.raises(SchemaTooOldError):
            await db.check()

    async def test_migrate_brings_a_behind_subsystem_up(self, db):
        db.ladders["fake"] = (Migration(("CREATE TABLE fake (id TEXT PRIMARY KEY)",)),)
        await db.migrate()
        await db.check()


class TestLadderInjection:
    """A StateDb is given its ladders, so the engine stays below them.

    `open_state_db` is what supplies this build's. A StateDb built directly
    knows no schema, which is what keeps `db.py` from importing `migrate.py`.
    """

    async def test_a_bare_state_db_carries_no_ladders(self):
        bare = StateDb(":memory:")
        try:
            assert bare.ladders == {}
            assert bare.tables == {}
            await bare.migrate()
            # The spine is a ladder too, so an injected-nothing database has
            # not even that: `check` cannot pass and no table exists.
            assert not await bare.has_table("desk")
        finally:
            bare.close()

    async def test_open_state_db_migrates_the_desk(self, tmp_path):
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await db.has_table("desk")
            await db.check()
        finally:
            db.close()


class TestFailedMigration:
    """Slice 3: a migration that raises leaves the schema where it was."""

    async def test_a_failed_migration_leaves_the_version_unmoved(self, db):
        db.ladders["fake"] = (
            Migration(("CREATE TABLE fake_one (id TEXT PRIMARY KEY)",)),
        )
        await db.migrate()
        db.ladders["fake"] = (
            *db.ladders["fake"],
            Migration(
                (
                    "CREATE TABLE fake_two (id TEXT PRIMARY KEY)",
                    "THIS IS NOT SQL",
                )
            ),
        )
        with pytest.raises(Exception):
            await db.migrate()
        assert await db.schema_version("fake") == 1
        assert not await db.has_table("fake_two")


class TestPythonRung:
    """A rung may run Python, for a step SQL cannot take.

    One use ships: the desk's `desk.json` import. These tests pin the two
    guarantees that use depends on.
    """

    async def test_a_rung_runs_inside_the_migration_transaction(self, db):
        """It shares the run's transaction, so a later failure unwrites its rows."""
        db.ladders["fake"] = (
            Migration(("CREATE TABLE fake (id TEXT PRIMARY KEY)",)),
            PythonMigration(
                run=lambda conn: conn.execute("INSERT INTO fake (id) VALUES ('a')")
            ),
        )
        await db.migrate()
        assert await db.schema_version("fake") == 2

    async def test_a_rung_that_raises_leaves_the_version_unmoved(self, db):
        """The transactional-DDL guarantee, extended to Python."""

        def boom(conn):
            conn.execute("INSERT INTO fake (id) VALUES ('a')")
            raise RuntimeError("no")

        db.ladders["fake"] = (
            Migration(
                (
                    "CREATE TABLE fake (id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL DEFAULT 0)",
                )
            ),
        )
        db.tables["fake"] = TableSpec("fake")
        await db.migrate()
        db.ladders["fake"] = (*db.ladders["fake"], PythonMigration(run=boom))
        with pytest.raises(RuntimeError):
            await db.migrate()
        assert await db.schema_version("fake") == 1
        # The row the rung wrote before it raised went back with the version.
        assert await db.read_all("fake") == []

    async def test_a_rung_s_rows_carry_revision_zero(self, db):
        """A migration must not bump the counter: its rows are the starting state."""
        db.ladders["fake"] = (
            Migration(
                (
                    "CREATE TABLE fake (id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL)",
                    "CREATE INDEX fake_revision ON fake (revision)",
                )
            ),
            PythonMigration(
                run=lambda conn: conn.execute(
                    "INSERT INTO fake (id, revision) VALUES ('a', 0)"
                )
            ),
        )
        db.tables["fake"] = TableSpec("fake")
        await db.migrate()
        assert await db.revision() == 0
        assert [row["id"] for row in await db.read_all("fake")] == ["a"]
        assert await db.changed_since("fake", 0) == []


def _noop_migration() -> "Migration":
    """A migration with no statements, for testing version arithmetic alone."""
    return Migration(())


class TestWriteAllAndRevision:
    """Slice 4: a batch is one cut, and an empty batch consumes no revision."""

    async def test_a_batch_shares_one_revision(self, db):
        revision = await db.write_all(
            [
                Write("desk", "task:a", columns={"body": "one"}),
                Write("desk", "task:b", columns={"body": "two"}),
            ]
        )
        assert revision == 1
        rows = await db.read_all("desk")
        assert {row["revision"] for row in rows} == {1}

    async def test_two_batches_give_two_revisions(self, db):
        assert await db.write_all([Write("desk", "a", columns={"body": "one"})]) == 1
        assert await db.write_all([Write("desk", "b", columns={"body": "two"})]) == 2

    async def test_an_empty_batch_consumes_no_revision(self, db):
        await db.write_all([Write("desk", "a", columns={"body": "one"})])
        assert await db.write_all([]) == 1
        assert await db.revision() == 1

    async def test_a_write_with_no_columns_deletes(self, db):
        await db.write_all([Write("desk", "a", columns={"body": "one"})])
        await db.write_all([Write("desk", "a")])
        assert await db.read("desk", "a") is None

    async def test_an_unknown_table_is_refused(self, db):
        with pytest.raises(UnknownTableError):
            await db.write_all([Write("no_such_table", "a", columns={})])

    async def test_a_caller_cannot_stamp_the_revision(self, db):
        with pytest.raises(ValueError):
            await db.upsert("desk", "a", revision=9)


class TestAFailedWrite:
    """Slice 5: a failed write leaves rows and the revision untouched."""

    async def test_a_failed_batch_writes_neither_row(self, db):
        await db.write_all([Write("desk", "a", columns={"body": "seeded"})])
        with pytest.raises(UnknownColumnError):
            await db.write_all(
                [
                    Write("desk", "b", columns={"body": "two"}),
                    Write("desk", "a", columns={"no_such_column": "x"}),
                ]
            )
        assert await db.read("desk", "b") is None
        assert (await db.read("desk", "a"))["body"] == "seeded"
        assert await db.revision() == 1

    async def test_a_transact_block_that_raises_rolls_back(self, db):
        await db.write_all([Write("desk", "a", columns={"body": "seeded"})])
        with pytest.raises(RuntimeError):
            async with db.transact() as txn:
                txn.upsert("desk", "b", body="two")
                raise RuntimeError("boom")
        assert await db.read("desk", "b") is None
        assert await db.revision() == 1

    async def test_a_nested_failure_rolls_back_the_whole(self, db):
        with pytest.raises(RuntimeError):
            async with db.transact() as txn:
                txn.upsert("desk", "a", body="one")
                async with db.transact() as inner:
                    inner.upsert("desk", "b", body="two")
                    raise RuntimeError("boom")
        assert await db.read_all("desk") == []
        assert await db.revision() == 0


class TestDeleteAndRemovals:
    """Slice 6: a vanished row is learnable from a revision cursor."""

    async def test_a_deleted_id_is_removed_not_changed(self, db):
        await db.write_all([Write("desk", "a", columns={"body": "one"})])
        await db.delete("desk", "a")
        assert await db.removed_since("desk", 1) == ["a"]
        assert await db.changed_since("desk", 1) == []

    async def test_re_inserting_clears_the_removal(self, db):
        await db.upsert("desk", "a", body="one")
        await db.delete("desk", "a")
        await db.upsert("desk", "a", body="back")
        assert await db.removed_since("desk", 0) == []
        assert [row["id"] for row in await db.changed_since("desk", 0)] == ["a"]

    async def test_deleting_what_is_not_there_consumes_no_revision(self, db):
        await db.upsert("desk", "a", body="one")
        assert await db.delete("desk", "b") == 1


class TestFreshness:
    """Slice 7: a fetched_at-only write raises no notice."""

    @pytest.fixture
    def cached_db(self, db):
        """``db`` with a cached table, so freshness has somewhere to land."""
        db.ladders["thing"] = (
            Migration(
                (
                    "CREATE TABLE thing (id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL, fetched_at TEXT, "
                    "body TEXT NOT NULL DEFAULT '')",
                    "CREATE INDEX thing_revision ON thing (revision)",
                )
            ),
        )
        db.tables["thing"] = TableSpec("thing", cached=True)
        return db

    async def test_a_new_fetched_at_alone_moves_no_revision(self, cached_db):
        await cached_db.migrate()
        await cached_db.upsert("thing", "a", fetched_at="t1", body="one")
        assert await cached_db.upsert("thing", "a", fetched_at="t2", body="one") == 1
        assert (await cached_db.read("thing", "a"))["fetched_at"] == "t2"
        assert await cached_db.revision() == 1

    async def test_a_changed_column_with_a_new_fetched_at_does(self, cached_db):
        await cached_db.migrate()
        await cached_db.upsert("thing", "a", fetched_at="t1", body="one")
        assert await cached_db.upsert("thing", "a", fetched_at="t2", body="two") == 2

    async def test_a_write_with_no_stamp_leaves_the_stored_one_alone(self, cached_db):
        """A write that is not a fetch says nothing about when the row was fetched."""
        await cached_db.migrate()
        await cached_db.upsert("thing", "a", fetched_at="t1", body="one")
        await cached_db.upsert("thing", "a", body="two")
        row = await cached_db.read("thing", "a")
        assert row["body"] == "two"
        assert row["fetched_at"] == "t1"

    async def test_fetched_at_on_a_canonical_table_is_refused(self, db):
        with pytest.raises(ValueError) as exc:
            await db.upsert("desk", "a", fetched_at="t", body="one")
        assert "canonical" in str(exc.value)


class TestNoticesSince:
    """Slice 8: a cursor names what moved, and where it now stands."""

    async def test_notices_name_each_cut(self, db):
        await db.upsert("desk", "a", body="one")
        await db.upsert("desk", "b", body="two")
        assert await db.notices_since(0) == ({"desk": {"a", "b"}}, 2)
        assert await db.notices_since(1) == ({"desk": {"b"}}, 2)
        assert await db.notices_since(2) == ({}, 2)

    async def test_a_removal_is_a_notice(self, db):
        await db.upsert("desk", "a", body="one")
        await db.delete("desk", "a")
        assert await db.notices_since(1) == ({"desk": {"a"}}, 2)


class TestConcurrency:
    """Slice 9: two writers serialise rather than interleave."""

    async def test_a_batch_and_a_block_land_whole(self, db):
        async def batch():
            await db.write_all(
                [
                    Write("desk", "b1", columns={"body": "batch"}),
                    Write("desk", "b2", columns={"body": "batch"}),
                ]
            )

        async def block():
            async with db.transact() as txn:
                txn.upsert("desk", "t1", body="block")
                await asyncio.sleep(0)
                txn.upsert("desk", "t2", body="block")

        await asyncio.gather(block(), batch())
        rows = {row["id"]: row["revision"] for row in await db.read_all("desk")}
        assert rows["b1"] == rows["b2"]
        assert rows["t1"] == rows["t2"]
        assert rows["b1"] != rows["t1"]


class TestReentrancy:
    """Slice 10: a nested block joins, and a deadlock is named not hung."""

    async def test_a_nested_block_shares_the_outer_revision(self, db):
        async with db.transact() as outer:
            outer.upsert("desk", "a", body="one")
            async with db.transact() as inner:
                assert inner.revision == outer.revision
                inner.upsert("desk", "b", body="two")
        rows = {row["id"]: row["revision"] for row in await db.read_all("desk")}
        assert rows == {"a": 1, "b": 1}
        assert await db.revision() == 1

    async def test_a_nested_block_commits_once_at_the_outer_exit(self, tmp_path):
        """The inner exit must not commit: a second connection sees nothing yet.

        Asserted through another connection, because a read on the writer's own
        connection sees its uncommitted rows whether or not they are committed.
        """
        path = tmp_path / "state.db"
        writer = open_state_db(path)
        await writer.migrate()
        reader = open_state_db(path)
        await reader.check()
        async with writer.transact() as outer:
            async with writer.transact() as inner:
                inner.upsert("desk", "a", body="one")
            assert await reader.read_all("desk") == []
            outer.upsert("desk", "b", body="two")
        assert [row["id"] for row in await reader.read_all("desk")] == ["a", "b"]
        assert await writer.revision() == 1
        writer.close()
        reader.close()

    async def test_awaiting_back_in_from_another_task_is_named(self, db, monkeypatch):
        """A second task's write while a block is open is refused, not hung."""
        monkeypatch.setattr("maelstrom.state_db.db._LOCK_TIMEOUT_SECS", 0.05)

        async def other():
            await db.write_all([Write("desk", "b", columns={"body": "two"})])

        async with db.transact() as txn:
            txn.upsert("desk", "a", body="one")
            with pytest.raises(TransactionOpenError):
                await asyncio.create_task(other())


class TestSingleRowWrite:
    """Slice 11: the common path needs no transaction."""

    async def test_an_upsert_outside_a_block_commits_on_its_own(self, db):
        assert await db.upsert("desk", "a", body="one") == 1
        assert (await db.read("desk", "a"))["body"] == "one"
        assert await db.revision() == 1

    async def test_an_upsert_inside_a_block_joins_it(self, db):
        async with db.transact() as txn:
            txn.upsert("desk", "a", body="one")
            await db.upsert("desk", "b", body="two")
        rows = {row["id"]: row["revision"] for row in await db.read_all("desk")}
        assert rows == {"a": 1, "b": 1}


class TestContention:
    """Slice 12: a contended write waits rather than failing."""

    async def test_a_second_connection_waits_for_the_first(self, tmp_path):
        path = tmp_path / "state.db"
        first = open_state_db(path)
        await first.migrate()

        async def release():
            await asyncio.sleep(0.05)

        async with first.transact() as txn:
            txn.upsert("desk", "a", body="one")
            writing = asyncio.create_task(asyncio.to_thread(_blocking_write, str(path)))
            await release()
        await writing
        assert (await first.read("desk", "b"))["body"] == "two"
        first.close()


def _blocking_write(path: str) -> None:
    """Write through a fresh connection, waiting out the other writer's lock."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'revision'")
    conn.execute("INSERT INTO desk (id, revision, body) VALUES ('b', 99, 'two')")
    conn.execute("COMMIT")
    conn.close()


class TestOneConnection:
    """Slice 13: an in-memory database keeps the connection it opened."""

    async def test_two_reads_see_the_same_data(self, db):
        await db.upsert("desk", "a", body="one")
        assert len(await db.read_all("desk")) == 1
        assert len(await db.read_all("desk")) == 1

    async def test_a_second_thread_is_refused(self, db):
        await db.revision()
        error: list[BaseException] = []

        def touch() -> None:
            try:
                db._connection()
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=touch)
        thread.start()
        thread.join()
        assert isinstance(error[0], WrongThreadError)


class TestAnUnusedRevisionIsGivenBack:
    """The counter must not drift on a write that moves nothing a client draws.

    Both paths take a revision up front and give it back when the transaction
    turns out to be empty. The give-back runs inside the transaction, because a
    stamp-only write commits and would otherwise persist a revision no row
    carries.
    """

    @pytest.fixture
    def cached_db(self, db):
        db.ladders["thing"] = (
            Migration(
                (
                    "CREATE TABLE thing (id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL, fetched_at TEXT, "
                    "body TEXT NOT NULL DEFAULT '')",
                    "CREATE INDEX thing_revision ON thing (revision)",
                )
            ),
        )
        db.tables["thing"] = TableSpec("thing", cached=True)
        return db

    async def test_a_stamp_only_transact_block_commits_without_a_revision(
        self, cached_db
    ):
        await cached_db.migrate()
        await cached_db.upsert("thing", "a", fetched_at="t1", body="one")
        async with cached_db.transact() as txn:
            txn.upsert("thing", "a", fetched_at="t2", body="one")
        assert (await cached_db.read("thing", "a"))["fetched_at"] == "t2"
        assert await cached_db.revision() == 1

    async def test_an_empty_transact_block_leaves_the_revision_alone(self, db):
        await db.upsert("desk", "a", body="one")
        async with db.transact():
            pass
        assert await db.revision() == 1

    async def test_the_next_write_reuses_the_revision_that_was_given_back(self, db):
        await db.upsert("desk", "a", body="one")
        async with db.transact():
            pass
        assert await db.upsert("desk", "b", body="two") == 2


class TestTheLockIsAlwaysGivenBack:
    """A failed open must not leave the write lock held.

    Held, every later write on this StateDb waits the full timeout and then
    reports a deadlock that is not one — a wedged database in a long-lived
    server, with no restart-free repair.
    """

    async def test_a_failed_begin_releases_the_lock(self, db, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(db, "_next_revision", refuse)
        with pytest.raises(sqlite3.OperationalError):
            async with db.transact():
                pass
        monkeypatch.undo()
        # The lock is free, so an ordinary write still lands.
        assert await db.upsert("desk", "a", body="one") == 1


class TestMigrationRefreshesTheColumnCache:
    """A column a migration adds must be writable through the same instance."""

    async def test_a_column_added_by_a_migration_is_writable(self, db):
        db.ladders["thing"] = (
            Migration(
                (
                    "CREATE TABLE thing (id TEXT PRIMARY KEY, "
                    "revision INTEGER NOT NULL, body TEXT NOT NULL DEFAULT '')",
                    "CREATE INDEX thing_revision ON thing (revision)",
                )
            ),
        )
        db.tables["thing"] = TableSpec("thing")
        await db.migrate()
        await db.upsert("thing", "a", body="one")

        db.ladders["thing"] = (
            *db.ladders["thing"],
            Migration(("ALTER TABLE thing ADD COLUMN note TEXT NOT NULL DEFAULT ''",)),
        )
        await db.migrate()
        await db.upsert("thing", "a", body="one", note="added")
        assert (await db.read("thing", "a"))["note"] == "added"


class TestATableDeclaredButNotMigrated:
    """A spec without its ladder names itself rather than raising bare SQL."""

    async def test_it_raises_a_named_error(self, db):
        db.tables["ghost"] = TableSpec("ghost")
        with pytest.raises(UnknownColumnError) as exc:
            await db.upsert("ghost", "a", body="one")
        assert "ghost" in str(exc.value)
