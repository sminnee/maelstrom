"""The canonical Agent store."""

import asyncio
import sqlite3
from pathlib import Path

from mael_domain.agent_store import (
    InMemoryAgentStore,
    InMemoryMilestoneStore,
    SqliteAgentStore,
    SqliteMilestoneStore,
    new_agent_record,
)
from mael_domain.state_db.migrate import open_state_db

FIXTURES = Path(__file__).parent / "fixtures"


def test_agent_records_survive_a_state_db_reopen(tmp_path) -> None:
    async def scenario() -> dict:
        path = tmp_path / "state.db"
        first = open_state_db(path)
        await first.migrate()
        await SqliteAgentStore(first).save(
            {
                "id": "thread-1",
                "harness": "codex",
                "session_id": "task-session-1",
                "task": "northwind/2026-09-16.4.3",
                "cwd": "/worktree",
                "model": "codex:sol",
                "mode": "plan",
            },
        )
        first.close()
        second = open_state_db(path)
        await second.check()
        result = await SqliteAgentStore(second).list()
        second.close()
        return result

    assert asyncio.run(scenario()) == [
        {
            "id": "thread-1",
            "harness": "codex",
            "session_id": "task-session-1",
            "task": "northwind/2026-09-16.4.3",
            "cwd": "/worktree",
            "model": "codex:sol",
            "mode": "plan",
        }
    ]


def test_list_skips_a_row_whose_body_is_not_json_or_whose_id_disagrees(
    tmp_path,
) -> None:
    """A store bug should not corrupt the list; it should drop the bad row."""

    async def scenario() -> list:
        db = open_state_db(tmp_path / "state.db")
        await db.migrate()
        await db.upsert("agents", "not-json", body="{not valid json")
        await db.upsert("agents", "mismatched-id", body='{"id": "other-id"}')
        store = SqliteAgentStore(db)
        await store.save({"id": "thread-1", "harness": "codex"})
        result = await store.list()
        db.close()
        return result

    assert asyncio.run(scenario()) == [{"id": "thread-1", "harness": "codex"}]


def test_read_answers_one_record_without_scanning_the_table(tmp_path) -> None:
    """The router asks about one id at adoption, not the whole history.

    The table holds every agent Maelstrom ever started, so reading it whole to
    answer "is there a record for this id" costs the lifetime agent count.
    """

    async def scenario() -> tuple:
        db = open_state_db(tmp_path / "state.db")
        await db.migrate()
        store = SqliteAgentStore(db)
        await store.save({"id": "a1", "harness": "claude", "status": "ended"})
        await store.save({"id": "a2", "harness": "codex"})
        found = await store.read("a1")
        missing = await store.read("nobody")
        db.close()
        return found, missing

    found, missing = asyncio.run(scenario())

    assert found == {"id": "a1", "harness": "claude", "status": "ended"}
    assert missing is None


# --- the relation: a task's sessions -----------------------------------------


def record(agent_id: str, task: str, session_id: str, started_at: str) -> dict:
    """One Agent record, as a launch writes it."""
    return new_agent_record(
        agent_id,
        harness="claude",
        session_id=session_id,
        task=task,
        cwd="/worktree",
        model="opus",
        mode="auto",
        started_at=started_at,
    )


#: Three launches of one task, and one of another. `a2` resumed the session
#: `a1` opened, so one session id spans two records.
#:
#: The stamps sort differently as text than as instants: `a3` is the newest
#: instant and the smallest string, and `a0` has no start at all.
LAUNCHES = (
    record("a1", "northwind/NORT-7", "s1", "2026-09-21T10:00:00+00:00"),
    record("a3", "northwind/NORT-7", "s2", "2026-09-22T09:00:00-10:00"),
    record("a2", "northwind/NORT-7", "s1", "2026-09-22T10:00:00+00:00"),
    # Adoption by hand records no start. It reads as the oldest.
    record("a0", "northwind/NORT-7", "s0", ""),
    record("b1", "northwind/NORT-8", "s9", "2026-09-22T12:00:00+00:00"),
    # An adopted agent: a session, and no task.
    record("c1", "", "s5", "2026-09-22T13:00:00+00:00"),
)


def agent_stores(tmp_path) -> list:
    """Each backend, so one test holds the two to one contract."""
    db = open_state_db(tmp_path / "state.db")
    asyncio.run(db.migrate())
    return [SqliteAgentStore(db), InMemoryAgentStore()]


def ids(records: list[dict]) -> list[str]:
    return [r["id"] for r in records]


def test_a_task_reads_its_records_newest_first(tmp_path) -> None:
    async def scenario(store) -> dict:
        for launch in LAUNCHES:
            await store.save(launch)
        return {
            "NORT-7": ids(await store.for_task("northwind/NORT-7")),
            "NORT-8": ids(await store.for_task("northwind/NORT-8")),
            "never launched": ids(await store.for_task("northwind/NORT-9")),
            # A blank names no task. It must not return the adopted agents.
            "blank": ids(await store.for_task("")),
        }

    for store in agent_stores(tmp_path):
        assert asyncio.run(scenario(store)) == {
            "NORT-7": ["a3", "a2", "a1", "a0"],
            "NORT-8": ["b1"],
            "never launched": [],
            "blank": [],
        }


def test_a_session_reads_its_records_newest_first(tmp_path) -> None:
    async def scenario(store) -> dict:
        for launch in LAUNCHES:
            await store.save(launch)
        return {
            "s1": ids(await store.for_session("s1")),
            "s5": ids(await store.for_session("s5")),
            "unknown": ids(await store.for_session("s404")),
            # A record with no session must not answer for a bare `claude`.
            "blank": ids(await store.for_session("")),
        }

    for store in agent_stores(tmp_path):
        assert asyncio.run(scenario(store)) == {
            "s1": ["a2", "a1"],
            "s5": ["c1"],
            "unknown": [],
            "blank": [],
        }


def test_a_saved_record_moves_to_the_task_it_now_names(tmp_path) -> None:
    """The columns follow the body: a record saved again is found by its new task."""

    async def scenario(store) -> tuple:
        await store.save(record("a1", "", "s1", "2026-09-21T10:00:00+00:00"))
        await store.save(
            record("a1", "northwind/NORT-7", "s1", "2026-09-21T10:00:00+00:00")
        )
        return ids(await store.for_task("northwind/NORT-7")), await store.read("a1")

    for store in agent_stores(tmp_path):
        found, stored = asyncio.run(scenario(store))
        assert found == ["a1"]
        assert stored == record(
            "a1", "northwind/NORT-7", "s1", "2026-09-21T10:00:00+00:00"
        )


def test_a_save_that_changes_nothing_is_not_a_change(tmp_path) -> None:
    """A poller reads by revision, so a repeated save must not look like news.

    The router saves a record it has just read on every adoption.
    """

    async def scenario(store) -> tuple:
        launch = record("a1", "northwind/NORT-7", "s1", "2026-09-21T10:00:00+00:00")
        await store.save(launch)
        _, before = await store.changed_since(0)
        await store.save(dict(launch))
        moved, after = await store.changed_since(before)
        return moved, after == before

    for store in agent_stores(tmp_path):
        assert asyncio.run(scenario(store)) == ([], True)


def test_a_renamed_task_keeps_its_records(tmp_path) -> None:
    async def scenario(store) -> dict:
        for launch in LAUNCHES:
            await store.save(launch)
        before = (await store.changed_since(0))[1]
        await store.retask("northwind/NORT-7", "southwind/NORT-7")
        moved, _ = await store.changed_since(before)
        return {
            "old": ids(await store.for_task("northwind/NORT-7")),
            "new": ids(await store.for_task("southwind/NORT-7")),
            "other": ids(await store.for_task("northwind/NORT-8")),
            "body": (await store.read("a1"))["task"],
            # A poller reads the move: the records are written, not only indexed.
            "moved": sorted(ids(moved)),
        }

    for store in agent_stores(tmp_path):
        assert asyncio.run(scenario(store)) == {
            "old": [],
            "new": ["a3", "a2", "a1", "a0"],
            "other": ["b1"],
            "body": "southwind/NORT-7",
            "moved": ["a0", "a1", "a2", "a3"],
        }


# --- the ladder --------------------------------------------------------------


def released_state_db(tmp_path) -> Path:
    """A copy of a real state database, at the last released ladder versions."""
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript((FIXTURES / "state_db_agents2_tasks4.sql").read_text())
    conn.close()
    return path


def test_the_ladder_links_each_released_record_to_its_task(tmp_path) -> None:
    """The released record held a task session id, and at best a bare task id.

    The task row id comes from the join on ``tasks.session_id``, which is the
    only place the project was recorded. So the agents rung must run before the
    tasks rung that drops the column.
    """

    async def scenario() -> dict:
        db = open_state_db(released_state_db(tmp_path))
        await db.migrate()
        await db.check()
        store = SqliteAgentStore(db)
        result = {
            "records": {r["id"]: r for r in await store.list()},
            "launched": ids(await store.for_task("maelstrom/2026-10-01.1")),
            "adopted": ids(await store.for_task("maelstrom/2026-10-01.2")),
            "by session": ids(
                await store.for_session("fb1c3ad2-fa96-4605-9740-97c4441d4053")
            ),
        }
        db.close()
        return result

    result = asyncio.run(scenario())

    assert result["launched"] == ["7c1e02af"]
    # Adoption wrote no task id. The session id alone finds the task.
    assert result["adopted"] == ["b40d91e6"]
    # The real record: a session that ran on no task.
    assert result["by session"] == ["13a30a55"]
    # The body is rewritten with the columns: a record is read whole from it.
    assert result["records"] == {
        "13a30a55": {
            "cwd": "/private/tmp/claude/resume-probe",
            "ended_at": "2026-09-23T02:48:48.478288+00:00",
            "harness": "claude",
            "id": "13a30a55",
            "mode": "normal",
            "model": "claude-haiku-4-5-20251001",
            "started_at": "2026-09-23T02:47:32.574493+00:00",
            "status": "ended",
            "swept": False,
            "task": "",
            "session_id": "fb1c3ad2-fa96-4605-9740-97c4441d4053",
        },
        "7c1e02af": {
            "cwd": "/Users/sam/Projects/maelstrom/maelstrom-alpha",
            "ended_at": "",
            "harness": "claude",
            "id": "7c1e02af",
            "mode": "plan",
            "model": "opus",
            "started_at": "2026-10-01T05:10:00.000000+00:00",
            "status": "running",
            "task": "maelstrom/2026-10-01.1",
            "session_id": "a29d4e5b-2e6c-5f58-8e30-899b83fb3983",
        },
        "b40d91e6": {
            "cwd": "/Users/sam/Projects/maelstrom/maelstrom-bravo",
            "ended_at": "2026-10-01T05:30:00.000000+00:00",
            "harness": "claude",
            "id": "b40d91e6",
            "mode": "normal",
            "model": "opus",
            "started_at": "2026-10-01T05:20:00.000000+00:00",
            "status": "ended",
            "swept": True,
            "task": "maelstrom/2026-10-01.2",
            "session_id": "732cf960-9c1b-5851-aa6e-000522f02c7a",
        },
    }


def test_the_ladder_drops_the_derived_session_id_from_tasks(tmp_path) -> None:
    async def scenario() -> list[str]:
        db = open_state_db(released_state_db(tmp_path))
        await db.migrate()
        row = await db.read("tasks", "maelstrom/2026-10-01.1")
        db.close()
        assert row is not None
        return list(row.keys())

    assert "session_id" not in asyncio.run(scenario())


def test_the_ladder_leaves_what_it_cannot_link(tmp_path) -> None:
    """A row the store already skips must not stop the migration.

    And a bare task id is not a link: it names no project, so a record whose
    session matches no task row ends with no task.
    """
    unlinked = '{"id": "lost", "task_id": "2026-10-01.1", "task_session_id": "no-such-session"}'

    async def scenario() -> dict:
        path = released_state_db(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO agents VALUES ('bad', 20, '{not valid json')")
        conn.execute("INSERT INTO agents VALUES ('list', 21, '[1, 2]')")
        conn.execute("INSERT INTO agents VALUES ('lost', 22, ?)", (unlinked,))
        conn.commit()
        conn.close()
        db = open_state_db(path)
        await db.migrate()
        bodies = {
            row["id"]: row["body"]
            for row in await db.read_all("agents")
            if row["id"] in ("bad", "list")
        }
        lost = await SqliteAgentStore(db).read("lost")
        db.close()
        return {"bodies": bodies, "lost": lost}

    assert asyncio.run(scenario()) == {
        "bodies": {"bad": "{not valid json", "list": "[1, 2]"},
        "lost": {"id": "lost", "task": "", "session_id": "no-such-session"},
    }


# --- the milestone ledger ---------------------------------------------------


def milestone(agent_id: str, name: str, own: int, sub: int, cost: float) -> dict:
    """One snapshot, as the server writes it."""
    return {
        "agent_id": agent_id,
        "name": name,
        "at": "2026-09-21T10:00:00Z",
        "recognised": True,
        "own_tokens": own,
        "subagent_tokens": sub,
        "cost_usd": cost,
    }


def spend(row: dict) -> dict:
    """One stored row projected to what it says was spent.

    Every field but the ids and the clock, so an assertion sees the whole
    figure rather than the first key that moved.
    """
    return {
        "name": row["name"],
        "recognised": row["recognised"],
        "own_total": row["own_total"],
        "sub_total": row["sub_total"],
        "own_delta": row["own_delta"],
        "sub_delta": row["sub_delta"],
        "cost_usd": row["cost_usd"],
        "cost_delta": row["cost_delta"],
    }


def ledger(store, *snapshots: dict) -> list[dict]:
    """``store`` after ``snapshots``, as its rows."""

    async def scenario() -> list:
        for snapshot in snapshots:
            await store.record(snapshot)
        return await store.list()

    return asyncio.run(scenario())


def sqlite_store(tmp_path) -> SqliteMilestoneStore:
    db = open_state_db(tmp_path / "state.db")
    asyncio.run(db.migrate())
    return SqliteMilestoneStore(db)


def both_backends(tmp_path) -> list:
    """Each backend, so one test holds the two to one contract."""
    return [sqlite_store(tmp_path), InMemoryMilestoneStore()]


def test_both_backends_store_a_snapshot_the_same_way(tmp_path) -> None:
    """The in-memory backend is the production default without a database.

    A divergence between the two would ship, and no other test would see it:
    each backend is otherwise exercised alone.
    """
    rows = [
        [
            spend(row)
            for row in ledger(
                store,
                milestone("a1", "planned", own=100, sub=10, cost=0.5),
                milestone("a1", "green", own=350, sub=60, cost=2.0),
            )
        ]
        for store in both_backends(tmp_path)
    ]
    assert rows[0] == rows[1]


def test_a_milestone_records_the_delta_since_the_one_before_it(tmp_path) -> None:
    """A snapshot is cumulative *and* a delta: a stage's own cost is the point."""
    rows = ledger(
        sqlite_store(tmp_path),
        milestone("a1", "planned", own=100, sub=10, cost=0.5),
        milestone("a1", "green", own=350, sub=60, cost=2.0),
    )
    assert [spend(row) for row in rows] == [
        {
            "name": "planned",
            "recognised": True,
            "own_total": 100,
            "sub_total": 10,
            # Nothing came before it, so everything spent so far is its own.
            "own_delta": 100,
            "sub_delta": 10,
            "cost_usd": 0.5,
            "cost_delta": 0.5,
        },
        {
            "name": "green",
            "recognised": True,
            "own_total": 350,
            "sub_total": 60,
            "own_delta": 250,
            "sub_delta": 50,
            "cost_usd": 2.0,
            "cost_delta": 1.5,
        },
    ]


def test_a_figure_lower_than_the_one_before_it_floors_at_zero(tmp_path) -> None:
    """A daemon restart puts a running total back to 0.

    A negative delta would read as a stage that gave tokens back, and the
    report sums the deltas.
    """
    rows = ledger(
        sqlite_store(tmp_path),
        milestone("a1", "built", own=900, sub=80, cost=4.0),
        milestone("a1", "green", own=40, sub=0, cost=0.2),
    )
    assert rows[1]["own_delta"] == 0
    assert rows[1]["sub_delta"] == 0
    assert rows[1]["cost_delta"] == 0.0


def test_one_agents_milestones_do_not_delta_against_anothers(tmp_path) -> None:
    """Two agents spend separately; a delta that crossed them would be nonsense."""
    rows = ledger(
        sqlite_store(tmp_path),
        milestone("a1", "planned", own=100, sub=0, cost=1.0),
        milestone("a2", "planned", own=40, sub=0, cost=0.2),
    )
    second = next(row for row in rows if row["agent_id"] == "a2")
    assert second["own_delta"] == 40
    assert second["cost_delta"] == 0.2


def test_the_same_stage_twice_records_two_snapshots(tmp_path) -> None:
    """A stage reached again is a second snapshot, not an overwrite.

    A re-run of the review stage spends real tokens, and a ledger that
    collapsed the two would hide them.
    """
    rows = ledger(
        sqlite_store(tmp_path),
        milestone("a1", "reviewed", own=100, sub=0, cost=1.0),
        milestone("a1", "reviewed", own=180, sub=0, cost=1.8),
    )
    assert [row["own_delta"] for row in rows] == [100, 80]
    assert rows[0]["id"] != rows[1]["id"]


def test_an_unrecognised_milestone_name_is_stored_and_flagged(tmp_path) -> None:
    """Never dropped: a typo must be visible in the report."""
    [row] = ledger(
        sqlite_store(tmp_path),
        {**milestone("a1", "deployed", 10, 0, 0.1), "recognised": False},
    )
    assert row["name"] == "deployed"
    assert row["recognised"] is False


def test_milestones_survive_a_state_db_reopen(tmp_path) -> None:
    """The point of the table: an agent's spend outlives its daemon."""

    async def scenario() -> list:
        path = tmp_path / "state.db"
        first = open_state_db(path)
        await first.migrate()
        await SqliteMilestoneStore(first).record(
            milestone("a1", "shipped", own=100, sub=10, cost=0.5)
        )
        first.close()
        second = open_state_db(path)
        await second.check()
        result = await SqliteMilestoneStore(second).list("a1")
        second.close()
        return result

    [row] = asyncio.run(scenario())
    assert row["name"] == "shipped"
    assert row["sub_total"] == 10


def test_record_returns_the_row_it_wrote(tmp_path) -> None:
    """The caller needs the delta it did not compute.

    ``_snapshot`` is the one place the delta arithmetic lives. The server
    appends a transcript bar saying what the stage cost, and reading the
    ledger back to learn it would be a second read of a figure the write
    already had in hand.
    """

    async def scenario(store) -> dict:
        await store.record(milestone("a1", "planned", own=100, sub=10, cost=0.5))
        return await store.record(milestone("a1", "green", own=350, sub=60, cost=2.0))

    for store in both_backends(tmp_path):
        assert spend(asyncio.run(scenario(store))) == {
            "name": "green",
            "recognised": True,
            "own_total": 350,
            "sub_total": 60,
            "own_delta": 250,
            "sub_delta": 50,
            "cost_usd": 2.0,
            "cost_delta": 1.5,
        }
