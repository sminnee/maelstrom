"""Storage layer for the desk.

The desk is what the canvas draws: tasks, and agents with no task. It is one
table keyed by desk id, so unlike :mod:`maelstrom.env_store` there is no key
space: :meth:`load` and :meth:`save` move the whole table.

Three backends are provided:

- :class:`InMemoryDeskStore` — a store with no filesystem, for tests.
- :class:`JsonDeskStore` — one file, written atomically through
  :func:`maelstrom.util.atomic_write_json`, so a crash mid-write can never
  leave a truncated desk.
- :class:`SqliteDeskStore` — the state database. The desk is canonical, so this
  is where it belongs; the other two remain, one as the import path and both as
  backends the contract tests cover.

:class:`DeskStore` is typed to take either a table or an awaitable one, so the
two sync backends and the async one all satisfy it — as ``WorktreeSource.read``
already allows.
"""

import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Protocol

from .context import get_maelstrom_dir
from .state_db import StateDb
from .util import atomic_write_json

log = logging.getLogger(__name__)

#: The stored desk: desk id to that entry. The entry's own shape is the wire's,
#: which this layer neither reads nor names.
DeskTable = dict[str, Any]

#: The kinds a desk id can name, duplicated to keep this layer below the model.
_KIND_PREFIXES = ("task:", "agent:")


def get_desk_path() -> Path:
    """Where the desk is kept."""
    return get_maelstrom_dir() / "desk.json"


class DeskStore(Protocol):
    """The desk table, loaded and saved whole.

    Either sync or async. A backend reaching a database returns an awaitable;
    one holding a dict returns the table. The server's ``_run`` already takes
    either, which is the seam that let the desk move without changing a caller.
    """

    def load(self) -> "DeskTable | Awaitable[DeskTable]":
        """The stored table. ``{}`` when there is none, or it cannot be read."""
        ...

    def save(self, table: DeskTable) -> "None | Awaitable[None]":
        """Store ``table``, replacing whatever was there."""
        ...


class InMemoryDeskStore:
    """A :class:`DeskStore` with no filesystem.

    The table is copied on the way in and out through a JSON round trip, so a
    caller cannot change stored state through a shared reference — the same
    load-fresh semantics the persistent backend has.
    """

    def __init__(self) -> None:
        self._text = "{}"

    def load(self) -> DeskTable:
        return json.loads(self._text)

    def save(self, table: DeskTable) -> None:
        self._text = json.dumps(table, sort_keys=True)


class JsonDeskStore:
    """A :class:`DeskStore` backed by one JSON file.

    The path defaults to :func:`get_desk_path` and is resolved lazily, so a
    test that redirects ``get_maelstrom_dir`` is honoured. A file that cannot
    be read loads as an empty desk, logged: a desk is a convenience, and
    refusing to start over a corrupt one would help nobody. The log is what
    tells an unreadable desk apart from no desk at all, because the next save
    writes over whatever could not be read.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else get_desk_path()

    def load(self) -> DeskTable:
        try:
            with open(self.path) as f:
                table = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.warning("desk at %s could not be read", self.path, exc_info=True)
            return {}
        if not isinstance(table, dict):
            return {}
        # The file is state a user can edit, so an entry the wire would refuse
        # is dropped here rather than published to every client.
        return {
            k: v for k, v in (_migrated(k, v) for k, v in table.items()) if _is_entry(v)
        }

    def save(self, table: DeskTable) -> None:
        atomic_write_json(self.path, table)


def _migrated(key: str, value: Any) -> tuple[str, Any]:
    """``key`` and its entry, with a desk written before ids carried a kind fixed.

    A desk from that time held bare task ids. Left alone they would match no
    task and the user would lose their canvas, so the key and the entry's own
    id both gain the ``task:`` prefix. An id that already carries a kind is
    left as it is, so the two shapes need no version field to tell apart.
    """
    if not isinstance(key, str) or key.startswith(_KIND_PREFIXES):
        return key, value
    migrated = f"task:{key}"
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return migrated, {**value, "id": migrated}
    return migrated, value


def _is_entry(value: Any) -> bool:
    """Whether ``value`` is a desk entry the wire can carry."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("addedAt"), str)
    )


#: Marks the one-time ``desk.json`` import as done, so a desk a user then
#: emptied does not spring back from the file on the next open.
IMPORTED = "desk_imported"


class SqliteDeskStore:
    """A :class:`DeskStore` on the state database.

    The desk is canonical: maelstrom authors it, the write is the authoritative
    act, and losing a row loses the user's canvas. So it is a table, versioned
    and migrated, with no ``fetched_at`` — nobody else authors a desk.

    One entry's body is JSON text. This layer neither reads nor names the
    entry's shape, which is the wire's, so a column per field would be this
    layer learning the model's business.
    """

    def __init__(self, db: StateDb, json_path: Path | None = None) -> None:
        self._db = db
        #: The file a first open imports from. Resolved lazily, so a test that
        #: redirects ``get_maelstrom_dir`` is honoured.
        self._json_path = json_path

    async def load(self) -> DeskTable:
        """The stored table, importing ``desk.json`` the first time.

        A user with a desk from before the state database keeps their canvas.
        The file is left on disk as a fallback rather than deleted, and the
        import runs once: a desk the user then emptied must stay empty.
        """
        await self._import_once()
        table: DeskTable = {}
        for row in await self._db.read_all("desk"):
            try:
                entry = json.loads(row["body"])
            except json.JSONDecodeError:
                # Same contract as JsonDeskStore: a desk is a convenience, and
                # refusing to start over one bad row would help nobody. The log
                # is what tells a dropped entry from one never stored.
                log.warning("desk entry %s could not be read", row["id"])
                continue
            if _is_entry(entry):
                table[row["id"]] = entry
        return table

    async def save(self, table: DeskTable) -> None:
        """Store ``table`` as one cut.

        Which ids to delete is derived from what is stored, so the read and
        the writes share one transaction: between a read outside it and the
        batch, another writer could add a row this save would then neither
        delete nor notice. This is the case :meth:`StateDb.transact` exists
        for — a later write depending on an earlier read.

        An entry that did not move raises no notice and consumes no revision,
        so an idle save is silent, which is what a reader polling
        ``changed_since`` relies on.
        """
        async with self._db.transact() as txn:
            stored = {row["id"] for row in await self._db.read_all("desk")}
            for id, entry in table.items():
                txn.upsert("desk", id, body=json.dumps(entry, sort_keys=True))
            for id in sorted(stored - set(table)):
                txn.delete("desk", id)

    async def _import_once(self) -> None:
        """Bring an existing ``desk.json`` in, once and only once."""
        if await self._db.meta(IMPORTED):
            return
        path = self._json_path if self._json_path is not None else get_desk_path()
        # Through JsonDeskStore, so its bare-id fix and malformed-entry drop apply.
        table = JsonDeskStore(path=path).load()
        async with self._db.transact() as txn:
            for id, entry in table.items():
                txn.upsert("desk", id, body=json.dumps(entry, sort_keys=True))
            # In the same transaction as the rows. Set afterwards, a crash
            # between the two would re-import on the next open — and a desk
            # the user had since emptied would come back, which is the one
            # thing the marker exists to stop.
            txn.set_meta(IMPORTED, "1")
