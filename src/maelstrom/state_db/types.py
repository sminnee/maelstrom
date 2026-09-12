"""What the state database refuses, and the shapes a ladder is built from.

The leaf layer: stdlib only, so it can never join an import cycle. Both
:mod:`maelstrom.state_db.db` and every ladder need :class:`Migration`.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable


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
    ``StateDb`` from inside a ``StateDb.transact`` block. A hung server is the
    worst failure mode here, so the wait has a timeout.
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
class PythonMigration:
    """One rung that runs Python rather than SQL, for a step SQL cannot take.

    Reading a file into rows is the case: the desk's second rung imports
    ``desk.json``. There is exactly one use, and this is not a plugin system.

    **``run`` is handed the raw connection, never a** ``Txn``. That is the
    obvious-looking mistake, so the reason is here: a ``Txn`` carries a bumped
    revision by construction, and a migration must not bump the counter. A rung
    names ``revision = 0`` on the rows it writes, so a client polling
    ``changed_since`` treats them as the state it started from rather than as a
    change. The connection is already inside the migration's transaction, so a
    rung that raises rolls the whole run back with it.
    """

    run: Callable[[sqlite3.Connection], None]
    description: str = ""


#: One step up a ladder, in either form. A closed union of two frozen
#: dataclasses, so a type checker narrows an ``isinstance`` dispatch on it.
Rung = Migration | PythonMigration


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
    """One row for ``StateDb.write_all``. A delete when ``columns`` is ``None``."""

    table: str
    id: str
    columns: dict[str, Any] | None = None
    fetched_at: str | None = None
