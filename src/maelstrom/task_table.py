"""Storage layer for the task notebook.

A task is one row, its prose included. The row carries ``content``, ``steps``
and ``log`` beside the frontmatter fields, so a write is one transaction over
the whole task and a rollback leaves nothing behind — splitting a task across
two stores is what made a rollback partial.

Two backends are provided:

- :class:`InMemoryTaskTable` — a table with no filesystem, for tests.
- :class:`SqliteTaskTable` — the state database. Tasks are canonical, so this is
  where they belong.

Each one subclasses :class:`TaskTable`, so a reader sees which classes claim the
contract rather than having to trust duck typing.

Async throughout, for the reason ``state_db/`` is: reversibility, not I/O. See
``docs/dev/architecture-patterns.md``, convention 7.

A notebook written before the state database is brought in by the tasks ladder's
second rung, not by a backend — see
:mod:`maelstrom.state_db.migrations.notebook_md`.
"""

import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from . import task_export as export
from .state_db.db import StateDb, Txn
from .task import Task, session_id_for, task_key
from .util import now_iso

#: The table this store writes, as declared in
#: :data:`maelstrom.state_db.migrate.TABLES`.
TABLE = "tasks"

#: Every scalar column a row carries, and the :class:`~maelstrom.task.Task`
#: attribute it holds. ``follows`` is JSON text and handled apart; ``project``,
#: ``status`` and ``session_id`` are derived on the way in.
_SCALARS = (
    "title",
    "command",
    "mode",
    "branch",
    "parent",
    "pre_action",
    "post_action",
    "created",
    "updated",
    "schedule",
    "last_run",
    "priority",
    "model",
    "base",
    "content",
    "steps",
    "log",
)


def row_id(project: str, id: str) -> str:
    """The row key for a task. ``StateDb`` keys every table by one ``id``."""
    return f"{project}/{id}"


@dataclass(frozen=True)
class TaskChanges:
    """What moved since some revision, and the revision to ask from next.

    ``removed`` is reported in its own right rather than inferred from what is
    missing. A reading that held only the changed rows could not tell "this
    task was deleted" from "this task did not move", and a caller diffing it
    against a whole world would delete every task the read left out.

    ``revision`` is what the caller stores and hands back next time, so no
    change is read twice and none is skipped.
    """

    tasks: list[Task] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    revision: int = 0


#: The body columns, stored stripped. The markdown round trip used to strip
#: them on every write — ``_split_sections`` trims each section — so a caller
#: handing over a file's trailing newline got it back without one. Keeping that
#: here means a reader comparing ``content`` need not know where it came from.
_STRIPPED = ("content", "steps", "log")


def columns_for(task: Task) -> dict[str, Any]:
    """``task`` as the columns a row carries.

    ``session_id`` is derived rather than stored on the task: it is a pure
    function of the project and the id, and a column free to disagree with
    :func:`~maelstrom.task.session_id_for` would break the reverse lookup.
    """
    columns: dict[str, Any] = {
        "project": task.project,
        "task_id": task.id,
        "status": task.status,
        "session_id": session_id_for(task.project, task.id),
        "follows": json.dumps(list(task.follows)),
    }
    for name in _SCALARS:
        value = getattr(task, name)
        columns[name] = value.strip() if name in _STRIPPED else value
    return columns


def _without_revision(row: dict[str, Any]) -> dict[str, Any]:
    """``row`` less its revision stamp, for comparing one write against the last.

    The in-memory backend keeps the stamp in the row, as the database keeps it
    in a column. Comparing stamped rows would make every save look like a
    change, and a no-op save has to stay silent.
    """
    return {key: value for key, value in row.items() if key != "revision"}


def task_from_row(row: sqlite3.Row) -> Task:
    """One row as a full-fidelity :class:`~maelstrom.task.Task`.

    Every field round-trips, the prose included: a reader that wants a task's
    content queries the table rather than going back to a file.
    """
    task = Task(id=row["task_id"], title=row["title"], project=row["project"])
    task.status = row["status"]
    task.follows = list(json.loads(row["follows"] or "[]"))
    for name in _SCALARS:
        setattr(task, name, row[name])
    return task


class TaskTable(ABC):
    """The task notebook, as rows keyed by project and id.

    A backend subclasses this rather than matching it by shape, so which classes
    claim the contract is readable from the class statement.
    """

    @abstractmethod
    async def load(self, project: str, id: str) -> Task | None:
        """One task, or ``None`` when there is none."""

    @abstractmethod
    async def list(
        self, project: str, *, status: str | None = None, parent: str | None = None
    ) -> list[Task]:
        """``project``'s tasks, id-sorted, optionally filtered."""

    @abstractmethod
    async def save(self, task: Task) -> None:
        """Write ``task``, replacing whatever row was there."""

    @abstractmethod
    async def delete(self, project: str, id: str) -> None:
        """Remove one task. Removing what is absent changes nothing."""

    @abstractmethod
    async def find_by_session_id(self, session_id: str) -> Task | None:
        """The task a session runs, or ``None`` when none does.

        A blank never resolves: ``""`` is the default for a never-launched row,
        so a falsy query must not match one of them.
        """

    @abstractmethod
    def transact(self) -> Any:
        """Open a write transaction: every write inside it lands as one cut.

        An exception inside the block rolls every write in it back, leaving no
        partial row.
        """

    @abstractmethod
    async def changed_since(self, since: int) -> TaskChanges:
        """Every task written after ``since``, and every id removed after it.

        What a poller reads instead of the whole table. The revision says *that*
        something moved; this says *which rows*, so a poll costs the rows that
        changed rather than every task in every project.

        Every project is read: scoping is the caller's, because one query that
        answers for the whole notebook beats one per project.
        """

    @abstractmethod
    async def revision(self) -> int:
        """A number that moves when a task moves.

        What a poller compares between reads. On the database this is the state
        database's own revision counter, so it also moves for a write to another
        table — which costs an occasional no-op re-read and saves keeping a
        second counter honest.
        """


class InMemoryTaskTable(TaskTable):
    """A :class:`TaskTable` with no filesystem.

    Rows are held as the same column mapping the SQLite backend writes and are
    copied on the way out, so a caller cannot change stored state through a
    shared reference — the load-fresh semantics the persistent backend has.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        #: The rows to restore if the open transaction fails. ``None`` when no
        #: transaction is open.
        self._saved: dict[str, dict[str, Any]] | None = None
        #: The removals to restore alongside them, for the same reason.
        self._saved_removals: dict[str, int] | None = None
        #: The revision to restore alongside both. A rollback that left the
        #: counter advanced would report a change that never landed.
        self._saved_revision: int | None = None
        self._depth = 0
        #: Bumped by every write, so a poller sees the same shape it would on
        #: the database.
        self._revision = 0
        #: The revision each removed id vanished at, mirroring the database's
        #: ``removals`` table. Kept because absence cannot be read as deletion:
        #: a partial reading has to name what went.
        self._removals: dict[str, int] = {}

    async def load(self, project: str, id: str) -> Task | None:
        row = self._rows.get(row_id(project, id))
        return task_from_row(row) if row is not None else None  # type: ignore[arg-type]

    async def list(
        self, project: str, *, status: str | None = None, parent: str | None = None
    ) -> list[Task]:
        found = [
            task_from_row(row)  # type: ignore[arg-type]
            for row in self._rows.values()
            if row["project"] == project
            and (status is None or row["status"] == status)
            and (parent is None or row["parent"] == parent)
        ]
        found.sort(key=lambda t: t.id)
        return found

    async def save(self, task: Task) -> None:
        id = row_id(task.project, task.id)
        columns = columns_for(task)
        stored = self._rows.get(id)
        if stored is not None and _without_revision(stored) == columns:
            return
        self._revision += 1
        self._rows[id] = {**columns, "revision": self._revision}
        # A saved id is present again, so an earlier removal of it is stale.
        self._removals.pop(id, None)

    async def delete(self, project: str, id: str) -> None:
        key = row_id(project, id)
        if self._rows.pop(key, None) is not None:
            self._revision += 1
            self._removals[key] = self._revision

    async def revision(self) -> int:
        return self._revision

    async def changed_since(self, since: int) -> TaskChanges:
        tasks = [
            task_from_row(row)  # type: ignore[arg-type]
            for row in self._rows.values()
            if row["revision"] > since
        ]
        tasks.sort(key=lambda t: (t.project, t.id))
        removed = sorted(
            key for key, revision in self._removals.items() if revision > since
        )
        return TaskChanges(tasks=tasks, removed=removed, revision=self._revision)

    async def find_by_session_id(self, session_id: str) -> Task | None:
        if not session_id:
            return None
        for row in self._rows.values():
            if row["session_id"] == session_id:
                return task_from_row(row)  # type: ignore[arg-type]
        return None

    @asynccontextmanager
    async def transact(self) -> AsyncGenerator[None]:
        """Copy the rows aside, and put them back if the block raises.

        Nesting joins the outer block, as :meth:`StateDb.transact` does, so an
        inner failure rolls the whole outer cut back rather than half of it.
        """
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self._saved = {id: dict(row) for id, row in self._rows.items()}
        self._saved_removals = dict(self._removals)
        self._saved_revision = self._revision
        self._depth = 1
        try:
            yield
        except BaseException:
            assert self._saved is not None
            assert self._saved_removals is not None
            assert self._saved_revision is not None
            self._rows = self._saved
            self._removals = self._saved_removals
            # The counter goes back too. Left advanced, it would report a
            # change for a write that never landed, and the database backend
            # does not behave that way.
            self._revision = self._saved_revision
            raise
        finally:
            self._depth = 0
            self._saved = None
            self._saved_removals = None
            self._saved_revision = None


class SqliteTaskTable(TaskTable):
    """A :class:`TaskTable` on the state database.

    Tasks are canonical: maelstrom authors them, the write is the authoritative
    act, and losing a row loses the user's work. So this is a table, versioned
    and migrated, with no ``fetched_at`` — nobody else authors a task.

    Status is a column here, not a folder, so moving a task is a single-column
    update rather than a write-new and delete-old pair.
    """

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def load(self, project: str, id: str) -> Task | None:
        row = await self._db.read(TABLE, row_id(project, id))
        return task_from_row(row) if row is not None else None

    async def list(
        self, project: str, *, status: str | None = None, parent: str | None = None
    ) -> list[Task]:
        """``project``'s tasks, served by the ``(project, status)`` index.

        Filtered in SQL rather than in Python: the whole point of the move is
        that a status-filtered list is a query, not a scan of every task.
        """
        clauses = ["project = ?"]
        params: list[str] = [project]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if parent is not None:
            clauses.append("parent = ?")
            params.append(parent)
        where = " AND ".join(clauses)
        rows = await self._db._call(
            lambda conn: conn.execute(
                f"SELECT * FROM {TABLE} WHERE {where} ORDER BY task_id",  # noqa: S608 — the table name is this module's own and the clauses are bound
                params,
            ).fetchall()
        )
        return [task_from_row(row) for row in rows]

    async def changed_since(self, since: int) -> TaskChanges:
        """The database's own answer: one range scan on the revision index.

        Two queries rather than one, because a removal leaves no row to select.
        :meth:`~maelstrom.state_db.db.StateDb.removed_since` reads the spine's
        ``removals`` table, which is the only record that a task ever existed.
        """
        rows = await self._db.changed_since(TABLE, since)
        removed = await self._db.removed_since(TABLE, since)
        return TaskChanges(
            tasks=[task_from_row(row) for row in rows],
            removed=list(removed),
            revision=await self._db.revision(),
        )

    async def save(self, task: Task) -> None:
        """Write the row, and queue its markdown export in the same cut.

        The enqueue is here rather than in :mod:`maelstrom.task`, because this
        is the only place a task row is written: a caller cannot save a task
        and miss the export. :meth:`StateDb.transact` nests, so a save inside a
        caller's own transaction queues inside it too, and a rollback takes
        both.
        """
        id = row_id(task.project, task.id)
        columns = columns_for(task)
        async with self._db.transact() as txn:
            # The status the row had before this write, which is the path the
            # export tree still holds a file at. A status move is one save, so
            # without this the queue would name the new path for both and the
            # file at the old status would never be unlinked.
            stored = await self._db.read(TABLE, id)
            # ``id`` and ``revision`` are the database's own columns: the key
            # this row was read by, and the cut it was written in. Neither is
            # in what ``columns_for`` builds, so both come off before the
            # comparison or every save looks like a change.
            unchanged = (
                stored is not None
                and {
                    key: value
                    for key, value in dict(stored).items()
                    if key not in ("id", "revision")
                }
                == columns
            )
            if unchanged:
                # Nothing moved, so nothing is queued. The upsert below would
                # be a no-op anyway, but the enqueue carries a fresh timestamp
                # and so would always look like a change — bumping the counter
                # for a write that changed nothing, which is what an idle poll
                # relies on never happening.
                return
            was = stored["status"] if stored is not None else task.status
            txn.upsert(TABLE, id, **columns)
            export.enqueue(
                txn,
                id,
                path=task_key(task.project, was, task.id),
                deleted=False,
                queued_at=now_iso(),
            )

    async def delete(self, project: str, id: str) -> None:
        """Remove the row, and queue the unlink of the file it had.

        The row is read before it goes: a delete leaves nothing to render, so
        the path it occupied is recorded now or the file is orphaned. Reading
        inside the transaction rather than before it means a concurrent write
        cannot move the task between the read and the delete.
        """
        key = row_id(project, id)
        async with self._db.transact() as txn:
            row = await self._db.read(TABLE, key)
            if row is None:
                return
            txn.delete(TABLE, key)
            export.enqueue(
                txn,
                key,
                path=task_key(project, row["status"], id),
                deleted=True,
                queued_at=now_iso(),
            )

    async def find_by_session_id(self, session_id: str) -> Task | None:
        if not session_id:
            return None
        row = await self._db._call(
            lambda conn: conn.execute(
                f"SELECT * FROM {TABLE} WHERE session_id = ? LIMIT 1",  # noqa: S608 — the table name is this module's own
                (session_id,),
            ).fetchone()
        )
        return task_from_row(row) if row is not None else None

    def transact(self) -> Any:
        """The database's own transaction, so every write inside is one cut."""
        return self._db.transact()

    async def revision(self) -> int:
        """The state database's revision counter.

        One counter across every table, so this moves for a desk write too. A
        poller comparing it re-reads occasionally for nothing, which is cheaper
        than a second counter that could disagree with the first.
        """
        return await self._db.revision()


#: Re-exported for a caller that opens a transaction and writes through the
#: :class:`~maelstrom.state_db.db.Txn` directly.
__all__ = [
    "TABLE",
    "InMemoryTaskTable",
    "SqliteTaskTable",
    "TaskTable",
    "Txn",
    "columns_for",
    "row_id",
    "task_from_row",
]
