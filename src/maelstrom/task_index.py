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


_STALE_INDEX_MESSAGE = (
    "Task index is from an older schema; run 'mael task reindex' to rebuild it."
)


class StaleTaskIndexError(Exception):
    """An on-disk index.db predating a column this build needs.

    ``CREATE TABLE IF NOT EXISTS`` leaves an existing database on its old schema,
    so a build that adds a column finds it missing. Raised in place of the bare
    ``sqlite3.OperationalError`` so the CLI can tell the user the one-line fix
    (``mael task reindex``, which drops and recreates the table). The index is a
    rebuildable cache of the ``.md`` tree, so no data is at risk.
    """


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
    model: str = ""
    schedule: str = ""
    last_run: str = ""
    created: str = ""
    updated: str = ""
    session_id: str = ""


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

    def find_by_session_id(self, session_id: str) -> "TaskMeta | None":
        """Return the row whose ``session_id`` matches, or ``None`` if none does.

        The deterministic ``session_id_for`` link, indexed for reverse lookup
        (session-id → task). A falsy ``session_id`` never resolves (the default
        for never-launched rows is ``""``), so a blank returns ``None``.
        """
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
    "model",
    "schedule",
    "last_run",
    "created",
    "updated",
    "session_id",
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
            self._create_tasks_table(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    @staticmethod
    def _create_tasks_table(conn: sqlite3.Connection) -> None:
        """Create the ``tasks`` table and its secondary indexes if absent.

        Split out from :meth:`_connect` so :meth:`clear` can drop+recreate the
        table on a rebuild — the upgrade path for an on-disk db written under an
        older column set (``task reindex`` recreates from this current schema).

        The ``session_id`` secondary index is created best-effort: on a *legacy*
        on-disk table (written before ``session_id`` existed) the ``CREATE
        INDEX`` references a missing column and raises. That is swallowed here so
        ``_connect`` still succeeds and ``task reindex`` — which drops+recreates
        this table under the current schema, then rebuilds the index properly —
        can run. A plain read/write against an un-reindexed old table still errors
        on any column added since (``session_id``, ``model``); the accepted upgrade
        path is ``task reindex``.
        """
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            "project TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, "
            "title TEXT, priority TEXT, branch TEXT, parent TEXT, "
            "follows TEXT, command TEXT, mode TEXT, model TEXT, schedule TEXT, "
            "last_run TEXT, created TEXT, updated TEXT, session_id TEXT, "
            "PRIMARY KEY (project, id))"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS tasks_project_status "
            "ON tasks (project, status)"
        )
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS tasks_session_id "
                "ON tasks (session_id)"
            )
        except sqlite3.OperationalError:
            # Legacy table without session_id; reindex will recreate it.
            pass

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
            meta.model,
            meta.schedule,
            meta.last_run,
            meta.created,
            meta.updated,
            meta.session_id,
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
            model=row["model"] or "",
            schedule=row["schedule"] or "",
            last_run=row["last_run"] or "",
            created=row["created"] or "",
            updated=row["updated"] or "",
            session_id=row["session_id"] or "",
        )

    # --- buffered write plumbing ---

    def _apply(self, conn: sqlite3.Connection, op: str, args: tuple) -> None:
        if op == "upsert":
            (meta,) = args
            placeholders = ", ".join("?" for _ in _COLUMNS)
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO tasks ({', '.join(_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    self._to_row(meta),
                )
            except sqlite3.OperationalError as e:
                # An on-disk table written under an older column set: every
                # column in _COLUMNS must exist for this INSERT. Name the remedy
                # rather than surfacing a bare "no column named <x>" — this is
                # the first thing a user hits after pulling a schema change, and
                # ``clear``/``reindex`` drops and recreates from the current DDL.
                raise StaleTaskIndexError(_STALE_INDEX_MESSAGE) from e
        elif op == "remove":
            project, id = args
            conn.execute(
                "DELETE FROM tasks WHERE project = ? AND id = ?", (project, id)
            )
        elif op == "clear":
            # Drop+recreate rather than DELETE so a rebuild starts from the
            # current schema: an on-disk db written under an older column set is
            # upgraded here (``task reindex`` is the upgrade path). The ``meta``
            # table (HEAD stamp) is untouched, so ``clear`` still preserves it.
            conn.execute("DROP TABLE IF EXISTS tasks")
            self._create_tasks_table(conn)

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
    #
    # ``SELECT *`` succeeds on a legacy table; it is ``_from_row`` that then
    # fails looking up a column the row doesn't carry. Both read paths route
    # their row mapping through ``_rows`` so the remedy is named once.

    def _row(self, row: "sqlite3.Row | None") -> "TaskMeta | None":
        """Map one row, translating a legacy-schema miss into a named error."""
        if row is None:
            return None
        try:
            return self._from_row(row)
        except (IndexError, KeyError) as e:
            raise StaleTaskIndexError(_STALE_INDEX_MESSAGE) from e

    def find(self, project: str, id: str) -> "TaskMeta | None":
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM tasks WHERE project = ? AND id = ?", (project, id)
        ).fetchone()
        return self._row(row)

    def find_by_session_id(self, session_id: str) -> "TaskMeta | None":
        # A blank never resolves: "" is the default for never-launched rows, so
        # a falsy query must not match one of them.
        if not session_id:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT * FROM tasks WHERE session_id = ? LIMIT 1", (session_id,)
        ).fetchone()
        return self._row(row)

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
        # _row never returns None for a non-None row; the cast keeps the
        # list-comprehension type honest.
        return [meta for r in rows if (meta := self._row(r)) is not None]

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
