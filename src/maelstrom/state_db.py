"""Storage layer for the state database.

One SQLite file at ``~/.maelstrom/state.db`` holds every canonical and cached
table. One file gives one transaction, so a canonical write and its derived
rows commit or roll back together, and one revision counter names the cut.
See ``docs/dev/data-architecture.md``.

This is the only module that writes SQL against these tables. A subsystem's
store maps its own rows; the model never learns SQL.

**Every public method is a coroutine, and the engine underneath is sync.**
The surface is async for reversibility, not for I/O, and :meth:`StateDb._call`
is the whole seam. See ``docs/dev/architecture-patterns.md``, convention 7.

Reach for :meth:`StateDb.write_all` for several rows, :meth:`StateDb.upsert` or
:meth:`StateDb.delete` for one, and :meth:`StateDb.transact` only when a later
write depends on an earlier read. See ``docs/dev/data-architecture.md``,
"Writing several rows".
"""

import asyncio
import sqlite3
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence, TypeVar

from .context import get_maelstrom_dir

T = TypeVar("T")

#: What every ordinary open tells a user whose database is behind this build.
_MIGRATE_COMMAND = "mael admin migrate"

#: How long a nested write may wait for the transaction lock before it is
#: called a deadlock. A transaction here is microseconds of SQLite, so a wait
#: this long is not contention: it is a coroutine awaiting back into a database
#: whose lock its own task already holds.
_LOCK_TIMEOUT_SECS = 5.0


class StateDbError(Exception):
    """Anything the state database refuses."""


class SchemaTooNewError(StateDbError):
    """The database was written by a newer build than this one.

    Proceeding would write rows missing every column the newer migration
    added, unrecoverably. Refusing is the only non-destructive answer.
    """


class SchemaTooOldError(StateDbError):
    """The database is behind this build, and names the command that fixes it.

    Upgrading is a command a person runs, never a background rewrite — see
    ``docs/dev/data-architecture.md``, "Schema versions".
    """


class UnknownTableError(StateDbError):
    """A table name no migration declared.

    Table names cannot be bound as parameters, so every one is checked against
    the declared specs before it is interpolated. This check is the injection
    guard, not a nicety — and it catches a typo besides.
    """


class UnknownColumnError(StateDbError):
    """A column the table does not have.

    Named rather than letting the mapping lookup raise ``IndexError`` from deep
    inside the upsert, where nothing says which table or which column.
    """


class TransactionOpenError(StateDbError):
    """A transaction is already open on this task.

    Raised instead of hanging when a coroutine awaits back into the same
    :class:`StateDb` from inside a :meth:`StateDb.transact` block. A hung
    server is the worst failure mode here, so the wait has a timeout.
    """


class WrongThreadError(StateDbError):
    """The connection was reached from a thread other than the one that opened it.

    Every call runs inline on the event loop's thread, so this cannot happen by
    design. It is checked rather than assumed, because the alternative is a
    bare ``sqlite3.ProgrammingError`` at a random call site.
    """


@dataclass(frozen=True)
class Migration:
    """One step up a subsystem's ladder: the statements it runs.

    A ladder is an append-only tuple, so a migration's version is its index
    plus one. The number is derived and never hand-maintained.
    """

    statements: tuple[str, ...]


@dataclass(frozen=True)
class TableSpec:
    """A table the database knows, and whether it carries freshness.

    ``cached`` is the one property that separates a cached table from a
    canonical one — not a separate database, a separate read path, or a
    separate design.
    """

    name: str
    cached: bool = False


@dataclass(frozen=True)
class Write:
    """One row for :meth:`StateDb.write_all`. A delete when ``columns`` is ``None``."""

    table: str
    id: str
    columns: dict[str, Any] | None = None
    fetched_at: str | None = None


@dataclass
class Txn:
    """One write transaction.

    Its methods are sync deliberately. They run inside the lock
    :meth:`StateDb.transact` holds, so making them awaitable would offer a
    yield point exactly where yielding is unsafe. The async surface is on
    :class:`StateDb`, where the reversibility argument applies.
    """

    revision: int
    _db: "StateDb"
    _conn: sqlite3.Connection
    #: The ids this transaction has touched, by table — the notices it raises.
    touched: dict[str, set[str]] = field(default_factory=dict)
    #: Whether a write moved only ``fetched_at``. Such a write raises no notice
    #: and consumes no revision, but its stamp must still be committed: "we
    #: asked and it said the same" is different from "we have not asked".
    stamped: bool = False

    def upsert(
        self, table: str, id: str, *, fetched_at: str | None = None, **cols: Any
    ) -> None:
        """Insert or update ``id`` in ``table``, stamped with this revision."""
        self._db._apply_upsert(self._conn, self, table, id, fetched_at, cols)

    def delete(self, table: str, id: str) -> None:
        """Remove ``id`` from ``table``, leaving a removal at this revision."""
        self._db._apply_delete(self._conn, self, table, id)

    def set_meta(self, key: str, value: str) -> None:
        """Record ``key`` in this transaction, so it lands with the rows.

        A fact about the database rather than about an entity, so it raises no
        notice and consumes no revision. It is offered here because a marker
        recording that a write happened must commit with that write, or a
        crash between the two leaves the two disagreeing.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        # A transaction holding only a marker must still commit.
        self.stamped = True


#: The spine every subsystem depends on. Versioned through ``PRAGMA
#: user_version``, because it is the one table set a global version legitimately
#: describes.
SPINE: tuple[Migration, ...] = (
    Migration(
        (
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "INSERT INTO meta (key, value) VALUES ('revision', '0')",
            "CREATE TABLE schema_version ("
            "name TEXT PRIMARY KEY, version INTEGER NOT NULL)",
            "CREATE TABLE removals ("
            "table_name TEXT NOT NULL, id TEXT NOT NULL, "
            "revision INTEGER NOT NULL, PRIMARY KEY (table_name, id))",
            "CREATE INDEX removals_revision ON removals (revision)",
            "CREATE TABLE refresher_health ("
            "name TEXT PRIMARY KEY, "
            "reachable INTEGER NOT NULL DEFAULT 1, "
            "since TEXT NOT NULL DEFAULT '', "
            "last_attempt TEXT NOT NULL DEFAULT '', "
            "last_success TEXT NOT NULL DEFAULT '', "
            "stand_off_until TEXT NOT NULL DEFAULT '', "
            "detail TEXT NOT NULL DEFAULT '')",
        )
    ),
)

#: The desk, the first subsystem on the database. Canonical, so no
#: ``fetched_at``: nobody else authors a desk.
DESK: tuple[Migration, ...] = (
    Migration(
        (
            # The entry's own fields, `addedAt` among them, live inside
            # `body`. A column beside it would be a second copy free to
            # disagree, and nothing queries the desk by date.
            "CREATE TABLE desk ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX desk_revision ON desk (revision)",
        )
    ),
)

#: Every subsystem's ladder, by name. A subsystem's schema moves without
#: dragging the others.
LADDERS: dict[str, tuple[Migration, ...]] = {"desk": DESK}

#: Every table a subsystem declares. The spine's own tables are not here: they
#: are the machinery, not rows a caller writes.
TABLES: dict[str, TableSpec] = {"desk": TableSpec("desk")}


def get_state_db_path() -> Path:
    """Where the state database is kept."""
    return get_maelstrom_dir() / "state.db"


class StateDb:
    """The state database: one SQLite file, one revision counter, one notice path.

    ``path`` may be ``":memory:"`` so a test gets a real-but-ephemeral
    database, exactly as :class:`maelstrom.task_index.SqliteTaskIndex` does.
    The connection is held for the object's lifetime either way — each
    connection to ``":memory:"`` gets its own private database, so a
    per-call connection would lose every row.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = str(path) if path is not None else None
        self._conn: sqlite3.Connection | None = None
        self._thread: int | None = None
        self._write_lock = asyncio.Lock()
        #: Open transaction depth. Nesting joins the outer transaction rather
        #: than re-acquiring a lock ``asyncio.Lock`` would refuse to give twice.
        self._depth = 0
        self._txn: Txn | None = None
        #: The task holding the open transaction. Re-entrancy is scoped to it:
        #: a *different* task's write must wait for the lock, or it would
        #: commit its rows under someone else's cut.
        self._holder: asyncio.Task[Any] | None = None
        #: Per instance, so a test can add a ladder without reaching into the
        #: module and changing what every other test sees.
        self.ladders: dict[str, tuple[Migration, ...]] = dict(LADDERS)
        self.tables: dict[str, TableSpec] = dict(TABLES)
        #: Each table's column names, read once. The schema only moves through
        #: a migration, and a migration reopens nothing mid-run.
        self._known_columns: dict[str, frozenset[str]] = {}

    @property
    def path(self) -> str:
        """The file this database is kept in, resolved lazily.

        Lazy, so a test that redirects ``get_maelstrom_dir`` is honoured.
        """
        return self._path if self._path is not None else str(get_state_db_path())

    # -- the seam --

    async def _call(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run one unit of database work.

        Sync today: SQLite is microseconds and a thread per connection is not
        yet worth it. The public surface is async regardless, so moving this
        body to ``aiosqlite`` or ``asyncio.to_thread`` later changes this
        function and nothing that calls it.
        """
        return fn(self._connection())

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._connect()
            self._thread = threading.get_ident()
        elif threading.get_ident() != self._thread:
            raise WrongThreadError(
                f"the state database at {self.path} was opened on thread "
                f"{self._thread} and reached from {threading.get_ident()}"
            )
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        path = self.path
        # isolation_level=None is the single most important setting here: with
        # the default the driver picks the transaction boundary, and that is
        # not a detail a canonical table should depend on.
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        if path != ":memory:":
            # WAL keeps a reader from blocking the writer across processes.
            # An in-memory database journals in memory and refuses it.
            conn.execute("PRAGMA journal_mode=WAL")
        # Replaces GitFileStore's hand-rolled flock poll: a contended writer
        # waits rather than raising SQLITE_BUSY.
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        # Chosen, not defaulted: NORMAL is durable under WAL against a process
        # crash, and only risks the last transaction against a power loss.
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def close(self) -> None:
        """Close the connection. Sync: there is no thread to join."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            self._thread = None

    # -- schema --

    async def migrate(self) -> None:
        """Bring every ladder up to this build's version.

        Forward-only, and the whole run is one SQLite transaction: SQLite has
        genuinely transactional DDL, so a migration that fails halfway leaves
        the version rows and the tables where they were.

        There is no ``down()``. A down-migration for a canonical table is a
        data-destroying operation written speculatively and first exercised on
        the day it matters.
        """
        await self._call(self._migrate)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._migrate_spine(conn)
            for name, ladder in self.ladders.items():
                found = self._read_version(conn, name)
                if found > len(ladder):
                    raise SchemaTooNewError(_too_new(name, found, len(ladder)))
                for migration in ladder[found:]:
                    for statement in migration.statements:
                        conn.execute(statement)
                if found != len(ladder):
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version (name, version) "
                        "VALUES (?, ?)",
                        (name, len(ladder)),
                    )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        # A migration can add a column to a table this instance has already
        # read, and a stale cache would then refuse a write to it.
        self._known_columns.clear()

    def _migrate_spine(self, conn: sqlite3.Connection) -> None:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        if found > len(SPINE):
            raise SchemaTooNewError(_too_new("the spine", found, len(SPINE)))
        for migration in SPINE[found:]:
            for statement in migration.statements:
                conn.execute(statement)
        if found != len(SPINE):
            # A pragma takes no parameter binding; the value is our own int.
            conn.execute(f"PRAGMA user_version = {len(SPINE)}")

    async def check(self) -> None:
        """Refuse unless every ladder is exactly at this build's version.

        The normal open. ``check`` never upgrades: that is :meth:`migrate`,
        and a person runs it.
        """
        await self._call(self._check)

    def _check(self, conn: sqlite3.Connection) -> None:
        spine = conn.execute("PRAGMA user_version").fetchone()[0]
        if spine > len(SPINE):
            raise SchemaTooNewError(_too_new("the spine", spine, len(SPINE)))
        if spine < len(SPINE):
            raise SchemaTooOldError(_too_old("the spine", spine, len(SPINE)))
        for name, ladder in self.ladders.items():
            found = self._read_version(conn, name)
            if found > len(ladder):
                raise SchemaTooNewError(_too_new(name, found, len(ladder)))
            if found < len(ladder):
                raise SchemaTooOldError(_too_old(name, found, len(ladder)))

    @staticmethod
    def _read_version(conn: sqlite3.Connection, name: str) -> int:
        """``name``'s stored version, or 0 when it has none.

        Reads through the spine, so a database with no ``schema_version`` table
        yet answers 0 rather than raising: that is exactly the un-migrated case.
        """
        try:
            row = conn.execute(
                "SELECT version FROM schema_version WHERE name = ?", (name,)
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["version"]) if row is not None else 0

    async def schema_version(self, name: str) -> int:
        """``name``'s stored version, or 0 when it has none."""
        return await self._call(lambda conn: self._read_version(conn, name))

    async def has_table(self, name: str) -> bool:
        """Whether ``name`` exists in the database. For tests and diagnostics."""
        return await self._call(
            lambda conn: (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (name,),
                ).fetchone()
                is not None
            )
        )

    async def pragma(self, name: str) -> Any:
        """One connection setting's value, for a test that pins it.

        The settings are chosen rather than defaulted — ``journal_mode`` and
        ``busy_timeout`` are what a contended writer's behaviour rests on — so
        a test asserts them rather than trusting :meth:`_connect` to keep them.
        ``name`` is checked against a fixed set rather than interpolated, for
        the same reason a table name is: a pragma takes no parameter binding.
        """
        if name not in ("journal_mode", "busy_timeout"):
            raise ValueError(f"not a pragma this reads: {name}")
        return await self._call(
            lambda conn: conn.execute(f"PRAGMA {name}").fetchone()[0]
        )

    # -- writing --

    def _spec(self, table: str) -> TableSpec:
        spec = self.tables.get(table)
        if spec is None:
            raise UnknownTableError(f"no such table in the state database: {table}")
        return spec

    def _columns(self, conn: sqlite3.Connection, table: str) -> frozenset[str]:
        """``table``'s column names, cached for the connection's lifetime."""
        known = self._known_columns.get(table)
        if known is None:
            known = frozenset(
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM pragma_table_info(?)", (table,)
                ).fetchall()
            )
            self._known_columns[table] = known
        return known

    def _next_revision(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
        revision = int(row["value"]) + 1
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'revision'", (str(revision),)
        )
        return revision

    def _apply_upsert(
        self,
        conn: sqlite3.Connection,
        txn: Txn,
        table: str,
        id: str,
        fetched_at: str | None,
        cols: dict[str, Any],
    ) -> None:
        spec = self._spec(table)
        if "revision" in cols:
            raise ValueError(
                "the state database stamps `revision`; a caller cannot, or the "
                "row would name a cut it was not part of"
            )
        if fetched_at is not None and not spec.cached:
            raise ValueError(
                f"{table} is canonical, so it carries no fetched_at: nobody "
                "else authors it"
            )
        known = self._columns(conn, spec.name)
        unknown = sorted(set(cols) - known)
        if unknown:
            # Before the read, so a table declared in `tables` but missing its
            # migration names itself rather than raising a bare
            # OperationalError: `_columns` answers an empty set for a table
            # that is not there, so every column reads as unknown.
            raise UnknownColumnError(f"{spec.name} has no column {', '.join(unknown)}")
        existing = conn.execute(
            f"SELECT * FROM {spec.name} WHERE id = ?",
            (id,),  # noqa: S608 — checked
        ).fetchone()
        moved = existing is None or any(
            existing[name] != value for name, value in cols.items()
        )
        if not moved and fetched_at is None:
            return
        if not moved:
            # Moving only `fetched_at` is not a change a client draws.
            conn.execute(
                f"UPDATE {spec.name} SET fetched_at = ? WHERE id = ?",  # noqa: S608
                (fetched_at, id),
            )
            txn.stamped = True
            return
        values: dict[str, Any] = {**cols, "id": id, "revision": txn.revision}
        if spec.cached and fetched_at is not None:
            # Only a caller that gave a stamp sets one. Omitting it leaves the
            # stored stamp alone rather than nulling it: a write that is not a
            # fetch has nothing to say about when the row was last fetched, and
            # erasing the answer would make a fresh row read as never-asked.
            values["fetched_at"] = fetched_at
        names = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        conn.execute(
            f"INSERT INTO {spec.name} ({names}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT (id) DO UPDATE SET "
            + ", ".join(f"{name} = :{name}" for name in values if name != "id"),
            values,
        )
        # Re-inserting an id clears its removal, in the same transaction, so a
        # reader polling "since N" is not told the row is both back and gone.
        conn.execute(
            "DELETE FROM removals WHERE table_name = ? AND id = ?", (spec.name, id)
        )
        txn.touched.setdefault(spec.name, set()).add(id)

    def _apply_delete(
        self, conn: sqlite3.Connection, txn: Txn, table: str, id: str
    ) -> None:
        spec = self._spec(table)
        cursor = conn.execute(
            f"DELETE FROM {spec.name} WHERE id = ?",
            (id,),  # noqa: S608 — checked
        )
        if cursor.rowcount == 0:
            return
        # Deletes are hard, with a removal at the same revision: a reader
        # polling "since N" cannot otherwise learn about a vanished row.
        conn.execute(
            "INSERT OR REPLACE INTO removals (table_name, id, revision) "
            "VALUES (?, ?, ?)",
            (spec.name, id, txn.revision),
        )
        txn.touched.setdefault(spec.name, set()).add(id)

    async def write_all(self, writes: Sequence[Write]) -> int:
        """Apply every write as one transaction, and return the revision they share.

        The batch is handed over whole, so the engine runs it start to finish
        with no suspension point inside: no other coroutine can interleave, and
        nothing can await back into this database mid-transaction. Both hazards
        :meth:`transact` has to guard against are absent by construction here.

        This is the documented default for a multi-row write. An empty batch
        consumes no revision, so a no-op poll leaves the counter where it was.
        """
        if not writes:
            return await self.revision()
        # Table names first, so a typo is refused before anything is written.
        for write in writes:
            self._spec(write.table)
        if self._holds():
            # Inside this task's own open block. Join it: a second
            # ``BEGIN`` on the one connection is what SQLite refuses, and
            # waiting for the lock this task holds is what would deadlock.
            txn = self._txn
            assert txn is not None
            await self._call(lambda conn: self._apply_writes(conn, txn, writes))
            return txn.revision
        await self._acquire()
        try:
            return await self._call(lambda conn: self._write_batch(conn, writes))
        finally:
            self._write_lock.release()

    def _write_batch(self, conn: sqlite3.Connection, writes: Sequence[Write]) -> int:
        conn.execute("BEGIN IMMEDIATE")
        try:
            txn = Txn(revision=self._next_revision(conn), _db=self, _conn=conn)
            self._apply_writes(conn, txn, writes)
            if not txn.touched:
                # Nothing a client draws moved, so nothing consumes a revision
                # — otherwise every no-op poll bumps the counter and the number
                # stops meaning "something changed". A `fetched_at` stamp still
                # commits: it moved no row, but it is what was asked for.
                self._unbump(conn, txn)
                conn.execute("COMMIT" if txn.stamped else "ROLLBACK")
                return txn.revision - 1
            conn.execute("COMMIT")
            return txn.revision
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _unbump(conn: sqlite3.Connection, txn: Txn) -> None:
        """Give back the revision this transaction took but did not use.

        Runs inside the transaction. A stamp-only write commits, so the
        counter has to be put back before the ``COMMIT`` or the commit would
        persist a revision nothing carries.
        """
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'revision'",
            (str(txn.revision - 1),),
        )

    def _apply_writes(
        self, conn: sqlite3.Connection, txn: Txn, writes: Sequence[Write]
    ) -> None:
        for write in writes:
            if write.columns is None:
                self._apply_delete(conn, txn, write.table, write.id)
            else:
                self._apply_upsert(
                    conn, txn, write.table, write.id, write.fetched_at, write.columns
                )

    async def upsert(
        self, table: str, id: str, *, fetched_at: str | None = None, **cols: Any
    ) -> int:
        """Write one row, as its own transaction. Returns the revision now.

        The common path: a desk add, a task status change, one refreshed pull
        request. It needs no :meth:`transact` block.
        """
        return await self.write_all(
            [Write(table, id, columns=cols, fetched_at=fetched_at)]
        )

    async def delete(self, table: str, id: str) -> int:
        """Remove one row, as its own transaction. Returns the revision now."""
        return await self.write_all([Write(table, id)])

    @asynccontextmanager
    async def transact(self) -> AsyncIterator[Txn]:
        """Open a write transaction. One at a time per :class:`StateDb`.

        The lock is held for the whole block, because a transaction spans
        several statements on one shared connection and an interleaved write
        from another coroutine would be committed by whichever ``COMMIT`` ran
        first.

        Three rules follow, each a way to get this wrong:

        - **Do not await anything slow inside.** Every other writer waits.
          Fetch first, then open the transaction and write.
        - **Never await back into the same** :class:`StateDb`. It would wait on
          a lock the current task already holds; :class:`TransactionOpenError`
          is raised rather than hanging.
        - **Nesting joins the outer transaction.** A nested block shares the
          outer revision and commits once, at the outer exit.

        Reach for :meth:`write_all` instead unless a later write depends on an
        earlier read inside the same transaction.
        """
        if self._holds():
            assert self._txn is not None
            self._depth += 1
            try:
                yield self._txn
            finally:
                self._depth -= 1
            return
        await self._acquire()
        # Everything below is inside the try, because opening can fail:
        # another process's writer can hold the file lock past the busy
        # timeout, and a BEGIN outside the try would leak the lock. Every
        # later write on this StateDb would then wait five seconds and report
        # a deadlock that is not one.
        txn: Txn | None = None
        began = False
        try:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            began = True
            txn = Txn(revision=self._next_revision(conn), _db=self, _conn=conn)
            self._txn = txn
            self._depth = 1
            self._holder = asyncio.current_task()
            yield txn
        except BaseException:
            # `began` rather than `txn`, because the revision read between the
            # two can itself fail: the transaction is open from BEGIN onwards
            # and must be closed even when no Txn was ever built.
            if began:
                conn.execute("ROLLBACK")
            raise
        else:
            if not txn.touched:
                # An empty cut consumes no revision, so the bump this block
                # took is given back *inside* the transaction, before it ends.
                # Outside it, the statement would be its own commit and would
                # race the next writer for the counter.
                self._unbump(conn, txn)
            if txn.touched or txn.stamped:
                conn.execute("COMMIT")
            else:
                conn.execute("ROLLBACK")
        finally:
            self._depth = 0
            self._txn = None
            self._holder = None
            self._write_lock.release()

    def _holds(self) -> bool:
        """Whether this task already holds the open transaction.

        Re-entrancy comes from here, not from the lock: ``asyncio.Lock`` is not
        re-entrant, so a nested block must join rather than re-acquire. Scoped
        to the task, because another task's write belongs to its own cut and
        must wait.
        """
        return self._depth > 0 and self._holder is asyncio.current_task()

    async def _acquire(self) -> None:
        """Take the write lock, or name the deadlock rather than hanging."""
        try:
            await asyncio.wait_for(
                self._write_lock.acquire(), timeout=_LOCK_TIMEOUT_SECS
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise TransactionOpenError(
                "a write transaction is already open on the state database; a "
                "transaction must not await back into the database it holds"
            ) from exc

    # -- reading --

    async def read_health(self, name: str) -> sqlite3.Row | None:
        """One refresher's stored health, or ``None`` when it has none.

        Its own pair of methods rather than a table in :data:`TABLES`: the
        health row carries no ``revision`` and raises no notice, because it is
        a fact about the refresher rather than about anything a client draws.
        A refresher standing off must not make every client refetch.
        """
        return await self._call(
            lambda conn: conn.execute(
                "SELECT * FROM refresher_health WHERE name = ?", (name,)
            ).fetchone()
        )

    async def write_health(self, name: str, **cols: Any) -> None:
        """Record ``name``'s health. Consumes no revision and raises no notice.

        Joins an open transaction when one is running on this connection, so a
        health write inside a :meth:`transact` block commits or rolls back with
        it rather than on its own.
        """
        await self._call(lambda conn: self._write_health(conn, name, cols))

    def _write_health(
        self, conn: sqlite3.Connection, name: str, cols: dict[str, Any]
    ) -> None:
        known = self._columns(conn, "refresher_health")
        unknown = sorted(set(cols) - known)
        if unknown:
            raise UnknownColumnError(
                f"refresher_health has no column {', '.join(unknown)}"
            )
        values: dict[str, Any] = {**cols, "name": name}
        names = ", ".join(values)
        placeholders = ", ".join(f":{key}" for key in values)
        conn.execute(
            f"INSERT INTO refresher_health ({names}) "  # noqa: S608 — checked above
            f"VALUES ({placeholders}) ON CONFLICT (name) DO UPDATE SET "
            + ", ".join(f"{key} = :{key}" for key in values if key != "name"),
            values,
        )

    async def meta(self, key: str) -> str | None:
        """One ``meta`` row's value, or ``None`` when it has none."""
        return await self._call(
            lambda conn: (
                row["value"]
                if (
                    row := conn.execute(
                        "SELECT value FROM meta WHERE key = ?", (key,)
                    ).fetchone()
                )
                is not None
                else None
            )
        )

    async def set_meta(self, key: str, value: str) -> None:
        """Record ``key``. Not a row, so it consumes no revision and is no notice.

        For a fact about the database rather than about an entity — a one-time
        import having run, say. ``revision`` itself lives here.
        """
        await self._call(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
            )
        )

    async def revision(self) -> int:
        """The revision the last committed write consumed."""
        return await self._call(
            lambda conn: int(
                conn.execute(
                    "SELECT value FROM meta WHERE key = 'revision'"
                ).fetchone()["value"]
            )
        )

    async def read(self, table: str, id: str) -> sqlite3.Row | None:
        """One row, or ``None`` when there is none."""
        spec = self._spec(table)
        return await self._call(
            lambda conn: conn.execute(
                f"SELECT * FROM {spec.name} WHERE id = ?",
                (id,),  # noqa: S608
            ).fetchone()
        )

    async def read_all(self, table: str) -> list[sqlite3.Row]:
        """Every row in ``table``, whatever its age.

        A reader takes what is there. It never asks whether a row is fresh, and
        it never triggers a fetch.
        """
        spec = self._spec(table)
        return await self._call(
            lambda conn: conn.execute(
                f"SELECT * FROM {spec.name} ORDER BY id"  # noqa: S608
            ).fetchall()
        )

    async def changed_since(self, table: str, since: int) -> list[sqlite3.Row]:
        """``table``'s rows written after revision ``since``.

        One range scan on the ``revision`` index, which is why every table
        carries one.
        """
        spec = self._spec(table)
        return await self._call(
            lambda conn: conn.execute(
                f"SELECT * FROM {spec.name} WHERE revision > ? "  # noqa: S608
                "ORDER BY revision, id",
                (since,),
            ).fetchall()
        )

    async def removed_since(self, table: str, since: int) -> list[str]:
        """The ids removed from ``table`` after revision ``since``."""
        spec = self._spec(table)
        return await self._call(
            lambda conn: [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM removals WHERE table_name = ? AND revision > ? "
                    "ORDER BY revision, id",
                    (spec.name, since),
                ).fetchall()
            ]
        )

    async def notices_since(self, since: int) -> tuple[dict[str, set[str]], int]:
        """What moved after ``since``, by table, and the revision now.

        The shape is ``notices.Notices``'s structurally, without importing it:
        the storage layer must not depend on the orchestrator package. The
        server folds this into the pushed half with ``merge_notices``.

        Keyed by **table name**, which is not the same thing as an entity kind
        even though every table so far shares its kind's name. Mapping one to
        the other is the caller's, because this layer knows no subsystem's wire
        shape.
        """
        return await self._call(lambda conn: self._notices_since(conn, since))

    def _notices_since(
        self, conn: sqlite3.Connection, since: int
    ) -> tuple[dict[str, set[str]], int]:
        out: dict[str, set[str]] = {}
        for spec in self.tables.values():
            name = spec.name
            ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM {name} WHERE revision > ?",
                    (since,),  # noqa: S608
                ).fetchall()
            }
            ids |= {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM removals WHERE table_name = ? AND revision > ?",
                    (name, since),
                ).fetchall()
            }
            if ids:
                out[name] = ids
        revision = int(
            conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()[
                "value"
            ]
        )
        return out, revision


def _too_new(name: str, found: int, build: int) -> str:
    return (
        f"the state database's {name} schema is at version {found}, and this "
        f"build knows version {build}. Run the newer build, or update this one."
    )


def _too_old(name: str, found: int, build: int) -> str:
    return (
        f"the state database's {name} schema is at version {found}, and this "
        f"build needs version {build}. Run `{_MIGRATE_COMMAND}`."
    )
