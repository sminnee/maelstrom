"""Storage layer for the task metadata index.

Whole-notebook read operations (``task list``/``task next``, actionability) walk
the entire task tree and re-parse every ``.md`` file. This module is a rebuildable
*cache* of just the metadata needed to serve those reads fast: one indexed row per
task keyed by ``(project, id)``, so a lookup is a single-row query instead of a
full filesystem scan + YAML parse.

The ``.md`` files under ``~/.maelstrom/tasks/`` remain the single source of truth;
the index is derived from them and can be rebuilt at any time (``task reindex``).
A stored HEAD stamp lets the model detect when the index is stale relative to the
task store's git HEAD and fall back to a file scan.

The index is a *separate collaborator* that sits beside the store, never behind
``store.write``. There is one backend, :class:`SqliteTaskIndex` — a stdlib
``sqlite3`` implementation (no new dependency); indexed single-row upserts and
``WHERE``-filtered queries scale as the notebook grows, where a JSON snapshot
would rewrite the whole file per mutation. It accepts a ``:memory:`` path, which
the tests use for a real-but-ephemeral index (production wires an on-disk one from
the CLI, and the model falls back to a shared in-memory default when none is
passed). The :class:`TaskIndex` ``Protocol`` documents the contract every consumer
relies on.

Dependency arrow stays model → index-store: this module knows only its own
:class:`TaskMeta` row type and never imports :mod:`maelstrom.task`; the
``Task`` ↔ ``TaskMeta`` mapping lives in the model.
"""

import json
import sqlite3
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True)
class TaskMeta:
    """The indexed metadata of a single task — a projection of a ``Task``.

    Metadata only: never the body (``content``/``steps``/``log``). A consumer that
    needs full fidelity must go back to the ``.md`` file. Keyed by
    ``(project, id)``; ``status`` is the folder the task lives in.
    """

    project: str
    id: str
    status: str
    title: str = ""
    priority: str = ""
    branch: str = ""
    parent: str = ""
    follows: tuple[str, ...] = ()
    command: str = ""
    mode: str = ""
    schedule: str = ""
    last_run: str = ""
    created: str = ""
    updated: str = ""


class TaskIndex(Protocol):
    """A metadata index over the task notebook, keyed by ``(project, id)``.

    Write ops (:meth:`upsert`/:meth:`remove`) keep the cache current beside each
    store mutation; read ops (:meth:`find`/:meth:`list`) serve the fast paths. The
    HEAD stamp (:meth:`head`/:meth:`set_head`) records the store git HEAD the index
    was last consistent with, so the model can detect staleness. :meth:`transaction`
    buffers a batch of ops and applies them atomically on a clean block exit,
    discarding them on an exception — nested *inside* the store transaction so an
    error discards the index buffer before the store rolls back the filesystem.
    """

    def upsert(self, meta: "TaskMeta") -> None:
        """Insert or replace the row for ``(meta.project, meta.id)``."""
        ...

    def remove(self, project: str, id: str) -> None:
        """Remove the row for ``(project, id)``. A no-op if absent."""
        ...

    def find(self, project: str, id: str) -> "TaskMeta | None":
        """Return the row for ``(project, id)``, or ``None`` if absent."""
        ...

    def list(
        self, project: str, *, status: str | None = None, parent: str | None = None
    ) -> list["TaskMeta"]:
        """Return ``project``'s rows, optionally filtered by status and/or parent."""
        ...

    def head(self) -> str | None:
        """Return the stored HEAD stamp, or ``None`` if never set."""
        ...

    def set_head(self, sha: str | None) -> None:
        """Record ``sha`` as the HEAD the index is now consistent with."""
        ...

    def clear(self) -> None:
        """Drop every row (but keep the schema). Used before a full rebuild."""
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """Buffer ops in the block, applying them atomically on a clean exit.

        On an exception the buffer is discarded (no partial apply). Backends with
        no batching treat this as a plain no-op context manager.
        """
        ...


# Columns persisted per row, in a fixed order (used for both DDL and INSERT).
_COLUMNS = (
    "project",
    "id",
    "status",
    "title",
    "priority",
    "branch",
    "parent",
    "follows",  # JSON-encoded list text
    "command",
    "mode",
    "schedule",
    "last_run",
    "created",
    "updated",
)


class SqliteTaskIndex:
    """A :class:`TaskIndex` backed by a stdlib ``sqlite3`` database.

    The schema is created lazily on first use. A single ``tasks`` table carries one
    row per task with a UNIQUE ``(project, id)`` key and a secondary
    ``(project, status)`` index for the common status-filtered ``list``; ``follows``
    is stored as JSON text. A tiny ``meta(key, value)`` table holds the HEAD stamp.

    ``path`` may be ``":memory:"`` so the test fixture gets a real SQLite backend
    with no file. WAL journalling is enabled on a file-backed database so a reader
    never blocks the short-lived writer. :meth:`transaction` buffers ops and applies
    them in one ``sqlite3`` transaction on a clean exit, discarding on an exception.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: sqlite3.Connection | None = None
        self._buffer: list[tuple[str, tuple]] | None = None

    # --- connection / schema ---

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._path)
            conn.row_factory = sqlite3.Row
            # WAL keeps a reader from blocking the writer; harmless (and skipped)
            # for an in-memory db, which journals in memory anyway.
            if self._path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "project TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, "
                "title TEXT, priority TEXT, branch TEXT, parent TEXT, "
                "follows TEXT, command TEXT, mode TEXT, schedule TEXT, "
                "last_run TEXT, created TEXT, updated TEXT, "
                "PRIMARY KEY (project, id))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_project_status "
                "ON tasks (project, status)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    # --- row (de)serialization ---

    @staticmethod
    def _to_row(meta: "TaskMeta") -> tuple:
        return (
            meta.project,
            meta.id,
            meta.status,
            meta.title,
            meta.priority,
            meta.branch,
            meta.parent,
            json.dumps(list(meta.follows)),
            meta.command,
            meta.mode,
            meta.schedule,
            meta.last_run,
            meta.created,
            meta.updated,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> "TaskMeta":
        follows = tuple(json.loads(row["follows"]) if row["follows"] else [])
        return TaskMeta(
            project=row["project"],
            id=row["id"],
            status=row["status"],
            title=row["title"] or "",
            priority=row["priority"] or "",
            branch=row["branch"] or "",
            parent=row["parent"] or "",
            follows=follows,
            command=row["command"] or "",
            mode=row["mode"] or "",
            schedule=row["schedule"] or "",
            last_run=row["last_run"] or "",
            created=row["created"] or "",
            updated=row["updated"] or "",
        )

    # --- buffered write plumbing ---

    def _apply(self, conn: sqlite3.Connection, op: str, args: tuple) -> None:
        if op == "upsert":
            (meta,) = args
            placeholders = ", ".join("?" for _ in _COLUMNS)
            conn.execute(
                f"INSERT OR REPLACE INTO tasks ({', '.join(_COLUMNS)}) "
                f"VALUES ({placeholders})",
                self._to_row(meta),
            )
        elif op == "remove":
            project, id = args
            conn.execute(
                "DELETE FROM tasks WHERE project = ? AND id = ?", (project, id)
            )
        elif op == "clear":
            conn.execute("DELETE FROM tasks")

    def _write(self, op: str, args: tuple) -> None:
        """Apply a write now, or buffer it if a transaction is open."""
        if self._buffer is not None:
            self._buffer.append((op, args))
            return
        conn = self._connect()
        self._apply(conn, op, args)
        conn.commit()

    def upsert(self, meta: "TaskMeta") -> None:
        self._write("upsert", (meta,))

    def remove(self, project: str, id: str) -> None:
        self._write("remove", (project, id))

    def clear(self) -> None:
        self._write("clear", ())

    # --- reads ---

    def find(self, project: str, id: str) -> "TaskMeta | None":
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM tasks WHERE project = ? AND id = ?", (project, id)
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self, project: str, *, status: str | None = None, parent: str | None = None
    ) -> list["TaskMeta"]:
        conn = self._connect()
        clauses = ["project = ?"]
        params: list[str] = [project]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if parent is not None:
            clauses.append("parent = ?")
            params.append(parent)
        rows = conn.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(clauses)} ORDER BY id",
            params,
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def head(self) -> str | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'head'"
        ).fetchone()
        return row["value"] if row is not None else None

    def set_head(self, sha: str | None) -> None:
        conn = self._connect()
        if sha is None:
            conn.execute("DELETE FROM meta WHERE key = 'head'")
        else:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('head', ?)",
                (sha,),
            )
        conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._buffer is not None:  # already buffering — re-enter
            yield
            return
        self._buffer = []
        try:
            yield
        except BaseException:
            self._buffer = None  # discard the buffer; nothing was written
            raise
        buffered, self._buffer = self._buffer, None
        conn = self._connect()
        try:
            for op, args in buffered:
                self._apply(conn, op, args)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
