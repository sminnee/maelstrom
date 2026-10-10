"""An **Operation**: one slow change to the world, run on the server as steps.

The model layer. Each function takes an operation and returns the next one; none
mutates its argument, so a caller may hold an earlier operation by reference.
:mod:`mael_domain.operation_store` keeps them, and the orchestrator server runs
them. See ``CONTEXT.md``, "Operation", and ``docs/dev/orchestrator-server.md``,
"Operations".
"""

from typing import TYPE_CHECKING

from .protocol import Operation, OperationStep

if TYPE_CHECKING:
    from .operation_store import OperationStore

#: How many operations the store keeps. About 50 run a day, so this is about
#: ten days of history.
OPERATION_CAP = 500

RUNNING = "running"
DONE = "done"
REFUSED = "refused"
FAILED = "failed"
PENDING = "pending"

#: What an operation the server held as running when it stopped reads.
INTERRUPTED_WORDS = "Server stopped during the operation"


def new_operation(
    id: str,
    kind: str,
    worktree_id: str,
    words: str,
    now: str,
    *,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> Operation:
    """A running operation with no steps yet."""
    return {
        "id": id,
        "kind": kind,
        "worktreeId": worktree_id,
        "taskId": task_id,
        "agentId": agent_id,
        "words": words,
        "state": RUNNING,
        "startedAt": now,
        "endedAt": None,
        "seen": False,
        "steps": [],
    }


def _step(name: str) -> OperationStep:
    return {
        "name": name,
        "words": "",
        "state": PENDING,
        "startedAt": None,
        "endedAt": None,
    }


def plan(op: Operation, names: list[str]) -> Operation:
    """``op`` with its steps named, in order.

    A step the operation already holds keeps its state, so a retry's plan
    leaves the steps a past run did as done.
    """
    held = {s["name"]: s for s in op["steps"]}
    return {**op, "steps": [held.get(name) or _step(name) for name in names]}


def _with_step(op: Operation, name: str, **fields: object) -> Operation:
    steps = list(op["steps"])
    for i, step in enumerate(steps):
        if step["name"] == name:
            steps[i] = {**step, **fields}  # type: ignore[typeddict-item]
            break
    else:
        # A step outside the plan still runs; it is recorded where it ran.
        steps.append({**_step(name), **fields})  # type: ignore[typeddict-item]
    return {**op, "steps": steps}


def start_step(op: Operation, name: str, now: str) -> Operation:
    """``op`` with the step ``name`` running."""
    return _with_step(op, name, state=RUNNING, words="", startedAt=now, endedAt=None)


def end_step(
    op: Operation, name: str, state: str, words: str | None, now: str
) -> Operation:
    """``op`` with the step ``name`` ended ``done``, ``refused`` or ``failed``."""
    return _with_step(op, name, state=state, words=words or "", endedAt=now)


def finish(op: Operation, state: str, words: str, now: str) -> Operation:
    """``op`` ended. A ``done`` operation has nothing to see, so it is seen."""
    return {**op, "state": state, "words": words, "endedAt": now, "seen": state == DONE}


def restart(op: Operation, words: str) -> Operation:
    """``op`` running again from its first step that did not finish.

    The steps that are done stay done; the others go back to pending.
    """
    steps: list[OperationStep] = [
        s if s["state"] == DONE else _step(s["name"]) for s in op["steps"]
    ]
    return {
        **op,
        "state": RUNNING,
        "words": words,
        "endedAt": None,
        "seen": False,
        "steps": steps,
    }


def interrupted(op: Operation, now: str) -> Operation:
    """``op`` failed because the server stopped while it ran."""
    steps: list[OperationStep] = [
        {**s, "state": FAILED, "endedAt": now} if s["state"] == RUNNING else s
        for s in op["steps"]
    ]
    return finish({**op, "steps": steps}, FAILED, INTERRUPTED_WORDS, now)


async def fail_interrupted(store: "OperationStore", now: str) -> list[Operation]:
    """Fail every operation the store holds as running, and return them.

    Called once, at server start: nothing runs before the server does, so a
    running row is one the last server life never finished.
    """
    failed: list[Operation] = []
    for op in await store.list():
        record = await store.read(op["id"])
        if record is None or op["state"] != RUNNING:
            continue
        record.operation = interrupted(op, now)
        await store.save(record)
        failed.append(record.operation)
    return failed
