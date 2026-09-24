"""The canonical Agent store."""

import asyncio

from mael_domain.agent_store import (
    InMemoryMilestoneStore,
    SqliteAgentStore,
    SqliteMilestoneStore,
)
from mael_domain.state_db.migrate import open_state_db


def test_agent_records_survive_a_state_db_reopen(tmp_path) -> None:
    async def scenario() -> dict:
        path = tmp_path / "state.db"
        first = open_state_db(path)
        await first.migrate()
        await SqliteAgentStore(first).save(
            {
                "id": "thread-1",
                "harness": "codex",
                "task_session_id": "task-session-1",
                "task_id": "2026-09-16.4.3",
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
            "task_session_id": "task-session-1",
            "task_id": "2026-09-16.4.3",
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
