"""The canonical Agent store."""

import asyncio

from maelstrom.agent_store import SqliteAgentStore
from maelstrom.state_db.migrate import open_state_db


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


def test_remove_drops_the_row_from_a_later_list(tmp_path) -> None:
    async def scenario() -> list:
        db = open_state_db(tmp_path / "state.db")
        await db.migrate()
        store = SqliteAgentStore(db)
        await store.save({"id": "thread-1", "harness": "codex"})
        await store.remove("thread-1")
        result = await store.list()
        db.close()
        return result

    assert asyncio.run(scenario()) == []


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
