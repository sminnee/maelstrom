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
from typing import Any, AsyncGenerator

from .state_db.db import StateDb, Txn
from .task import Task, session_id_for

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
        columns[name] = getattr(task, name)
    return columns


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
        self._depth = 0

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
        self._rows[row_id(task.project, task.id)] = columns_for(task)

    async def delete(self, project: str, id: str) -> None:
        self._rows.pop(row_id(project, id), None)

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
        self._depth = 1
        try:
            yield
        except BaseException:
            assert self._saved is not None
            self._rows = self._saved
            raise
        finally:
            self._depth = 0
            self._saved = None


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

    async def save(self, task: Task) -> None:
        await self._db.upsert(TABLE, row_id(task.project, task.id), **columns_for(task))

    async def delete(self, project: str, id: str) -> None:
        await self._db.delete(TABLE, row_id(project, id))

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
