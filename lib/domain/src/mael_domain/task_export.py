"""The markdown export: what the notebook still owes the files.

The state database is the notebook's source of truth. The markdown tree under
``~/.maelstrom/tasks`` survives as an export — for reading a task in an editor,
for ``git log`` over the notebook's history, and for the attachments that live
beside it — but nothing reads it back.

Writing a file is not part of a task write. A CLI process that wrote one would
have to hold the notebook's git lock, and a write that failed there would leave
the row and the file disagreeing with no way to tell which was right. So a task
write **enqueues**, and the orchestrator drains.

The queue is a table in the same database as the tasks, which is the whole
point: an enqueue joins the transaction that caused it. A task write that rolls
back queues nothing, and a task write that commits cannot fail to queue, because
they are one cut rather than two operations hoping to agree.

One row per task, not one per write. The export carries the notebook's current
state rather than its history, so ten writes to a task between two drains export
once: an enqueue is an upsert on the task's own row id.

A write re-renders from the live row at drain time rather than storing the
markdown, so a task that moved again before the drain exports what it says now.
A delete cannot: there is no row left to render. Its path is recorded at enqueue
time instead, which is the only thing the queue stores that the tasks table
could not answer for itself.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import Executor
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Protocol

from .state_db.db import StateDb, Txn
from .task import Task, task_key
from .task_store import TaskStore

log = logging.getLogger(__name__)

#: The table this store writes, as declared in
#: :data:`mael_domain.state_db.migrate.TABLES`.
TABLE = "task_export"


@dataclass(frozen=True)
class Queued:
    """One task the export still owes the files.

    ``path`` is the export key as it stood when the row was queued. A write
    ignores it and re-derives from the live task; a delete has nothing else to
    go on.
    """

    id: str
    path: str
    deleted: bool
    queued_at: str


def split_row_id(id: str) -> tuple[str, str]:
    """A queue row's id back into project and task id.

    The inverse of :func:`~mael_domain.task_table.row_id`. Split once from the
    left, because a task id may not contain a slash but a project name is
    likewise the first segment — ``is_safe_id`` rejects a slash in an id, so the
    first separator is always the right one.
    """
    project, _, task_id = id.partition("/")
    return project, task_id


class TaskReader(Protocol):
    """The one thing the drain asks of the notebook: load a task.

    Structural rather than :class:`~mael_domain.task_table.TaskTable`, and
    deliberately so. The task table enqueues into this module, so importing the
    class back would close a cycle. Naming only the method the drain calls
    keeps the edge pointing one way.
    """

    async def load(self, project: str, id: str) -> Task | None: ...


class ExportQueue(ABC):
    """What the markdown export still owes, as rows.

    A backend subclasses this rather than matching it by shape, so which classes
    claim the contract is readable from the class statement.
    """

    @abstractmethod
    async def pending(self) -> list[Queued]:
        """Everything queued, oldest first. ``[]`` when the export is caught up."""

    @abstractmethod
    async def clear(self, id: str) -> None:
        """Drop one queued task, once it has been exported."""

    @abstractmethod
    async def depth(self) -> int:
        """How many tasks the export owes."""

    @abstractmethod
    async def oldest(self) -> str | None:
        """When the longest-waiting entry was queued, or ``None`` when none is.

        A depth alone cannot tell a busy notebook from a stalled drain; this is
        the number that does.
        """

    @abstractmethod
    async def queue_all(self, entries: list[Queued]) -> None:
        """Queue several tasks at once, outside any task write.

        The rebuild path, and the in-memory backend's enqueue. It is separate
        from :func:`enqueue` because that one joins a caller's transaction by
        taking its :class:`~mael_domain.state_db.db.Txn`; this one has no write to
        join, so it opens its own.
        """


class InMemoryExportQueue(ExportQueue):
    """An :class:`ExportQueue` with no database, for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, Queued] = {}

    async def queue_all(self, entries: list[Queued]) -> None:
        for entry in entries:
            self._rows[entry.id] = entry

    async def pending(self) -> list[Queued]:
        return sorted(self._rows.values(), key=lambda row: (row.queued_at, row.id))

    async def clear(self, id: str) -> None:
        self._rows.pop(id, None)

    async def depth(self) -> int:
        return len(self._rows)

    async def oldest(self) -> str | None:
        owed = await self.pending()
        return owed[0].queued_at if owed else None


class SqliteExportQueue(ExportQueue):
    """An :class:`ExportQueue` on the state database, beside the tasks it exports.

    Same database, deliberately. A queue in a file or a separate database could
    be written when the task write rolled back, or missed when it committed.
    """

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def pending(self) -> list[Queued]:
        rows = await self._db.read_all(TABLE)
        entries = [
            Queued(
                id=row["id"],
                path=row["path"],
                deleted=bool(row["deleted"]),
                queued_at=row["queued_at"],
            )
            for row in rows
        ]
        entries.sort(key=lambda row: (row.queued_at, row.id))
        return entries

    async def clear(self, id: str) -> None:
        await self._db.delete(TABLE, id)

    async def depth(self) -> int:
        return len(await self._db.read_all(TABLE))

    async def oldest(self) -> str | None:
        owed = await self.pending()
        return owed[0].queued_at if owed else None

    async def queue_all(self, entries: list[Queued]) -> None:
        """Queue several entries as one cut, joining any open transaction."""
        async with self._db.transact() as txn:
            for entry in entries:
                enqueue(
                    txn,
                    entry.id,
                    path=entry.path,
                    deleted=entry.deleted,
                    queued_at=entry.queued_at,
                )


def enqueue(txn: Txn, id: str, *, path: str, deleted: bool, queued_at: str) -> None:
    """Queue one task for export, inside ``txn``.

    Sync, and takes a :class:`~mael_domain.state_db.db.Txn` rather than a store,
    because it runs inside the transaction that wrote the task. That is the
    guarantee this whole module rests on, so the enqueue cannot be reached any
    other way.
    """
    txn.upsert(TABLE, id, path=path, deleted=1 if deleted else 0, queued_at=queued_at)


class TaskExporter:
    """Drains the queue: renders each owed task and writes it to the export tree.

    The server owns one. A CLI process never drains — it enqueues and exits,
    and the orchestrator picks the work up.
    """

    def __init__(
        self,
        queue: ExportQueue,
        tasks: TaskReader,
        store: TaskStore,
        *,
        executor: Executor | None = None,
    ) -> None:
        self.queue = queue
        self.tasks = tasks
        self.store = store
        #: Where the store's blocking work runs. ``GitFileStore`` takes a
        #: cross-process lock and shells out to git, so on the server's loop
        #: those calls would stall every socket it serves. ``None`` runs them
        #: inline, which is what a test and a CLI want.
        self.executor = executor

    async def _off_loop(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run one blocking store call away from the event loop.

        The server's own ``_run`` cannot serve here: it passes a coroutine
        function straight through, and :meth:`drain` is one. The blocking work
        is the sync store calls inside it, so the offload belongs here.
        """
        if self.executor is None:
            return fn(*args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, fn, *args)

    async def drain(self) -> int:
        """Export everything owed, and return how many tasks were written.

        Each entry is cleared only after its file is written, so a crash
        mid-drain leaves the rest of the queue for the next one. Re-exporting a
        task that was already written is harmless: the render is a pure function
        of the row, so the second write produces the same bytes.
        """
        owed = await self.queue.pending()
        if not owed:
            return 0
        done = 0
        for entry in owed:
            try:
                await self._export(entry)
                # Inside the guard with the export: a clear that fails on a
                # locked database would otherwise abandon every entry after
                # this one, with nothing logged to say why.
                await self.queue.clear(entry.id)
            except Exception:  # noqa: BLE001 — one bad task must not stall the queue
                log.exception("could not export %s", entry.id)
                continue
            done += 1
        return done

    async def _export(self, entry: Queued) -> None:
        """Write or unlink one task's file.

        A queued write whose row has since been deleted exports nothing: the
        delete queued its own entry, and that entry is what removes the file.
        """
        if entry.deleted:
            if entry.path:
                await self._off_loop(
                    partial(self.store.delete, message=f"task: remove {entry.id}"),
                    entry.path,
                )
            return
        project, task_id = split_row_id(entry.id)
        task = await self.tasks.load(project, task_id)
        if task is None:
            # A delete queues its own entry, so this is a row that went without
            # one — a failed migration, or a hand-edited database. Logged
            # because the file it left behind is now orphaned in the tree.
            log.warning("no task row for queued export %s; skipping", entry.id)
            return
        path = task_key(task.project, task.status, task.id)
        await self._off_loop(
            partial(self.store.write, message=f"task: update {entry.id}"),
            path,
            task.to_markdown(),
        )
        if path != entry.path and entry.path:
            # The task moved status between the enqueue and the drain, so the
            # file it used to be is still sitting at the old path.
            await self._off_loop(
                partial(self.store.delete, message=f"task: move {entry.id}"),
                entry.path,
            )
