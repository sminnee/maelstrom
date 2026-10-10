"""Running operations in the background, and telling the world each step.

The server's half of an **Operation** (``CONTEXT.md``). A route starts one and
answers at once with its id; the work runs here as an asyncio task, and every
step it starts or ends is an ``operation`` upsert on the change stream. The
record is saved as it moves, so a reload loses nothing and a server restart
finds what was running. See ``docs/dev/orchestrator-server.md``, "Operations".
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mael_domain import operations as model
from mael_domain.operation_store import (
    OperationRecord,
    OperationStore,
    operation_number,
)
from mael_domain.protocol import Operation, ServerEvent
from mael_domain.worktree_steps import StepEnd, StepHook

from .sources import CloseBlocked

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperationPlan:
    """What one operation runs, and the words it is read by.

    ``work`` runs the steps, telling the hook it is given. It raises
    :class:`CloseBlocked` for a refusal and anything else for a fault.
    ``refresh`` re-reads what the work changed. It runs at the end however the
    work ended, because a teardown that stops partway has still changed the
    world.
    """

    kind: str
    worktree_id: str
    #: What the operation reads while it runs: ``Closing alpha``.
    doing: str
    #: What it reads once done: ``Closed alpha``.
    done: str
    #: The start of what a fault reads: ``Could not close the worktree``.
    failing: str
    work: Callable[[StepHook], Awaitable[None]]
    refresh: Callable[[], Awaitable[None]]


class Busy(Exception):
    """Another operation runs on the worktree. ``operation_id`` names it."""

    def __init__(self, operation_id: str) -> None:
        super().__init__(f"Operation {operation_id} is already running here")
        self.operation_id = operation_id


class _Hook(StepHook):
    """Turns each step the work reports into an upsert, and skips what is done."""

    def __init__(self, runner: "OperationRunner", id: str, skip: set[str]) -> None:
        self._runner = runner
        self._id = id
        self._skip = skip

    def planned(self, names: list[str]) -> None:
        self._runner._change(self._id, lambda op: model.plan(op, names))

    def skips(self, name: str) -> bool:
        return name in self._skip

    def started(self, name: str) -> None:
        now = self._runner.clock()
        self._runner._change(self._id, lambda op: model.start_step(op, name, now))

    def ended(self, end: StepEnd) -> None:
        now = self._runner.clock()
        self._runner._change(
            self._id,
            lambda op: model.end_step(op, end.name, end.state, end.words, now),
            lines=(end.name, list(end.lines)),
        )


class OperationRunner:
    """The operations of one server life: the running ones, and the store.

    ``apply`` is the server's own: it puts events in the world and tells the
    notice hub. The runner owns no world of its own, so what a client reads is
    always what ``apply`` last put there.
    """

    def __init__(
        self,
        store: OperationStore,
        apply: Callable[[list[ServerEvent]], None],
        clock: Callable[[], str],
    ) -> None:
        self.store = store
        self._apply = apply
        self.clock = clock
        #: The running operations, with their log and command, by id.
        self._records: dict[str, OperationRecord] = {}
        #: Which operation runs on each worktree.
        self._busy: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: Per operation, the last save, so saves land in the order made.
        self._saves: dict[str, asyncio.Task[None]] = {}
        #: The number of the last id minted. Minted here, not read from the
        #: store, because a save lands after ``begin`` has returned.
        self._last = 0

    async def load(self) -> None:
        """Fail what the last server life left running, then put all in the world."""
        await model.fail_interrupted(self.store, self.clock())
        kept = await self.store.list()
        self._last = max((operation_number(op["id"]) for op in kept), default=0)
        self._apply(
            [
                {"type": "upsert", "kind": "operation", "entity": op}
                for op in reversed(kept)
            ]
        )

    async def begin(
        self,
        command: dict[str, Any],
        plan: OperationPlan,
        *,
        again: OperationRecord | None = None,
    ) -> str:
        """Start ``plan`` in the background and return the operation's id.

        ``again`` is a refused or failed record to run once more, from its
        first step that did not finish.

        Raises:
            Busy: If an operation already runs on the worktree.
        """
        # No await before the claim: two requests at once must not both pass.
        running = self._busy.get(plan.worktree_id)
        if running is not None:
            raise Busy(running)
        if again is None:
            self._last += 1
            id = f"op{self._last}"
            op = model.new_operation(
                id, plan.kind, plan.worktree_id, plan.doing, self.clock()
            )
            record = OperationRecord(op, {}, command)
        else:
            op = model.restart(again.operation, plan.doing)
            record = OperationRecord(op, again.log, again.command)
            id = op["id"]
        done = {s["name"] for s in op["steps"] if s["state"] == model.DONE}
        self._busy[plan.worktree_id] = id
        self._records[id] = record
        self._change(id, lambda _op: op)
        self._tasks[id] = asyncio.create_task(
            self._run(id, plan, _Hook(self, id, done))
        )
        return id

    async def _run(self, id: str, plan: OperationPlan, hook: _Hook) -> None:
        try:
            try:
                await plan.work(hook)
            except CloseBlocked as exc:
                state, words = model.REFUSED, str(exc)
            except Exception as exc:  # noqa: BLE001 — the operation says why
                log.exception("operation %s failed", id)
                state, words = model.FAILED, f"{plan.failing}: {exc}"
            else:
                state, words = model.DONE, plan.done
            try:
                await plan.refresh()
            except Exception:  # noqa: BLE001 — the operation's end must land
                log.exception("the refresh after operation %s failed", id)
            # Ended after the refresh, so a client that sees it end reads a
            # world that already holds what it changed.
            self._change(id, lambda op: model.finish(op, state, words, self.clock()))
            await self._saves[id]
        finally:
            self._busy.pop(plan.worktree_id, None)
            self._records.pop(id, None)
            self._tasks.pop(id, None)
            self._saves.pop(id, None)

    def _change(
        self,
        id: str,
        change: Callable[[Operation], Operation],
        *,
        lines: tuple[str, list[str]] | None = None,
    ) -> None:
        """Move one running operation on, in the world and then in the store.

        ``lines`` is a step's name and log lines, for a step that ended.
        """
        record = self._records[id]
        record.operation = change(record.operation)
        if lines is not None:
            record.log[lines[0]] = lines[1]
        self._apply(
            [{"type": "upsert", "kind": "operation", "entity": record.operation}]
        )
        snapshot = OperationRecord(
            record.operation,
            {k: list(v) for k, v in record.log.items()},
            record.command,
        )
        self._saves[id] = asyncio.create_task(
            self._save_after(self._saves.get(id), snapshot)
        )

    async def _save_after(
        self, before: asyncio.Task[None] | None, record: OperationRecord
    ) -> None:
        if before is not None:
            await before
        try:
            await self.store.save(record)
        except Exception:  # noqa: BLE001 — the world still holds it
            log.exception("could not save operation %s", record.operation["id"])

    async def read(self, id: str) -> OperationRecord | None:
        """The operation with its log and command; a running one as it stands."""
        running = self._records.get(id)
        if running is not None:
            return OperationRecord(
                running.operation,
                {k: list(v) for k, v in running.log.items()},
                running.command,
            )
        return await self.store.read(id)

    async def mark_seen(self, op: Operation) -> None:
        """Mark ``op`` seen, in the world and the store."""
        seen: Operation = {**op, "seen": True}
        self._apply([{"type": "upsert", "kind": "operation", "entity": seen}])
        record = await self.store.read(op["id"])
        if record is not None:
            record.operation = seen
            await self.store.save(record)

    async def stop(self) -> None:
        """Cancel what runs. Its rows stay ``running``; the next start fails them."""
        tasks = list(self._tasks.values())
        # Taken first: a cancelled run drops its own entry on the way out.
        saves = list(self._saves.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*saves, return_exceptions=True)
