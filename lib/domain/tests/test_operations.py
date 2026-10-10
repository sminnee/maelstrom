"""Tests for the operation store — see ``CONTEXT.md``, "Operation"."""

import pytest

from mael_domain import operations
from mael_domain.operation_store import (
    InMemoryOperationStore,
    OperationRecord,
    SqliteOperationStore,
    operation_number,
)
from mael_domain.state_db.migrate import open_state_db

NOW = "2026-10-10T09:00:00+00:00"
LATER = "2026-10-10T09:00:05+00:00"


@pytest.fixture(params=["memory", "sqlite"])
async def make_store(request):
    """Build a store with a given cap, against every backend."""
    dbs = []

    async def build(cap: int = operations.OPERATION_CAP):
        if request.param == "memory":
            return InMemoryOperationStore(cap=cap)
        db = open_state_db(":memory:")
        await db.migrate()
        dbs.append(db)
        return SqliteOperationStore(db, cap=cap)

    yield build
    for db in dbs:
        db.close()


async def started(store, kind: str = "close"):
    ids = [operation_number(op["id"]) for op in await store.list()]
    op = operations.new_operation(
        f"op{max(ids, default=0) + 1}", kind, "northwind-alpha", "Closing alpha", NOW
    )
    await store.save(OperationRecord(op))
    return op


async def test_operations_list_newest_first_in_number_order(make_store):
    store = await make_store()
    for _ in range(11):
        await started(store)
    # op10 sorts before op2 as text; the list is numeric.
    assert [op["id"] for op in await store.list()] == [
        f"op{n}" for n in range(11, 0, -1)
    ]


async def test_an_operation_is_stored_whole(make_store):
    store = await make_store()
    op = operations.new_operation(
        "op1",
        "close",
        "northwind-alpha",
        "Closing alpha",
        NOW,
        task_id="northwind/t1",
        agent_id="ag1",
    )
    op = operations.plan(op, ["stop_agents", "git_close"])
    op = operations.start_step(op, "stop_agents", NOW)
    await store.save(OperationRecord(op))
    assert await store.list() == [
        {
            "id": "op1",
            "kind": "close",
            "worktreeId": "northwind-alpha",
            "taskId": "northwind/t1",
            "agentId": "ag1",
            "words": "Closing alpha",
            "state": "running",
            "startedAt": NOW,
            "endedAt": None,
            "seen": False,
            "steps": [
                {
                    "name": "stop_agents",
                    "words": "",
                    "state": "running",
                    "startedAt": NOW,
                    "endedAt": None,
                },
                {
                    "name": "git_close",
                    "words": "",
                    "state": "pending",
                    "startedAt": None,
                    "endedAt": None,
                },
            ],
        }
    ]


async def test_the_log_and_command_read_back_with_the_operation(make_store):
    store = await make_store()
    op = await started(store)
    log = {"stop_agents": ["one", "two"], "git_close": ["three"]}
    command = {
        "type": "worktree.sync",
        "worktreeId": "northwind-alpha",
        "mode": "plain",
    }
    await store.save(OperationRecord(op, log, command))
    assert await store.read(op["id"]) == OperationRecord(op, log, command)


async def test_an_unknown_operation_reads_as_none(make_store):
    store = await make_store()
    assert await store.read("op9") is None


async def test_only_the_newest_operations_up_to_the_cap_are_kept(make_store):
    store = await make_store(cap=3)
    for _ in range(5):
        await started(store)
    assert [op["id"] for op in await store.list()] == ["op5", "op4", "op3"]
    assert await store.read("op1") is None


async def test_a_saved_operation_replaces_its_earlier_row(make_store):
    store = await make_store()
    op = await started(store)
    await store.save(
        OperationRecord(operations.finish(op, "done", "Closed alpha", LATER))
    )
    [only] = await store.list()
    assert (only["state"], only["words"], only["endedAt"]) == (
        "done",
        "Closed alpha",
        LATER,
    )
