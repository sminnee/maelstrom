"""Storage layer for the desk.

The desk is what the canvas draws: tasks, and agents with no task. It is one
table keyed by desk id, so unlike :mod:`maelstrom.env_store` there is no key
space: :meth:`DeskStore.load` and :meth:`DeskStore.save` move the whole table.

Two backends are provided:

- :class:`InMemoryDeskStore` — a store with no filesystem, for tests.
- :class:`SqliteDeskStore` — the state database. The desk is canonical, so this
  is where it belongs.

Each one subclasses :class:`DeskStore`, so a reader sees which classes claim the
contract rather than having to trust duck typing.

A desk written before the state database is brought in by the desk ladder's
second rung, not by a backend — see
:mod:`maelstrom.state_db.migrations.desk_json`.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from .state_db.db import StateDb
from .state_db.migrations.desk_json import is_entry

log = logging.getLogger(__name__)

#: The stored desk: desk id to that entry. The entry's own shape is the wire's,
#: which this layer neither reads nor names.
DeskTable = dict[str, Any]


class DeskStore(ABC):
    """The desk table, loaded and saved whole, or changed one entry at a time.

    Async throughout, because the backend the server runs is a database. A
    backend subclasses this rather than matching it by shape, so which classes
    claim the contract is readable from the class statement.
    """

    @abstractmethod
    async def load(self) -> DeskTable:
        """The stored table. ``{}`` when there is none, or it cannot be read."""

    @abstractmethod
    async def save(self, table: DeskTable) -> None:
        """Store ``table``, replacing whatever was there."""

    @abstractmethod
    async def add(self, id: str, entry: Any) -> None:
        """Put one entry on the desk, leaving every other entry alone."""

    @abstractmethod
    async def remove(self, id: str) -> None:
        """Take one entry off the desk. Removing what is absent changes nothing."""


class InMemoryDeskStore(DeskStore):
    """A :class:`DeskStore` with no filesystem.

    The table is copied on the way in and out through a JSON round trip, so a
    caller cannot change stored state through a shared reference — the same
    load-fresh semantics the persistent backend has.
    """

    def __init__(self) -> None:
        self._text = "{}"

    async def load(self) -> DeskTable:
        return json.loads(self._text)

    async def save(self, table: DeskTable) -> None:
        self._text = json.dumps(table, sort_keys=True)

    async def add(self, id: str, entry: Any) -> None:
        table = json.loads(self._text)
        table[id] = entry
        self._text = json.dumps(table, sort_keys=True)

    async def remove(self, id: str) -> None:
        table = json.loads(self._text)
        table.pop(id, None)
        self._text = json.dumps(table, sort_keys=True)


class SqliteDeskStore(DeskStore):
    """A :class:`DeskStore` on the state database.

    The desk is canonical: maelstrom authors it, the write is the authoritative
    act, and losing a row loses the user's canvas. So it is a table, versioned
    and migrated, with no ``fetched_at`` — nobody else authors a desk.

    One entry's body is JSON text. This layer neither reads nor names the
    entry's shape, which is the wire's, so a column per field would be this
    layer learning the model's business.
    """

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def load(self) -> DeskTable:
        """The stored table. A pure read: it imports nothing and writes nothing."""
        table: DeskTable = {}
        for row in await self._db.read_all("desk"):
            try:
                entry = json.loads(row["body"])
            except json.JSONDecodeError:
                # A desk is a convenience, and refusing to start over one bad
                # row would help nobody. The log is what tells a dropped entry
                # from one never stored.
                log.warning("desk entry %s could not be read", row["id"])
                continue
            if is_entry(entry):
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

    async def add(self, id: str, entry: Any) -> None:
        """Write one entry as its own revision, leaving the rest of the table alone."""
        await self._db.upsert("desk", id, body=json.dumps(entry, sort_keys=True))

    async def remove(self, id: str) -> None:
        """Delete one entry as its own revision, raising one removal notice."""
        await self._db.delete("desk", id)
