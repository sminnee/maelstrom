"""What the orchestrator server keeps about an agent: the ``orchestrator_data`` column.

The ``agents`` row's ``body`` is the agent's lifecycle record. The daemon
bridge holds it in memory and saves it whole, so a field another writer put
there is lost on the next save. ``orchestrator_data`` is a companion column on
the same row that only the server writes. A write updates that column alone.

A new field here needs no migration: add it to :class:`OrchestratorData` with a
default. The dataclass validates the JSON on each read and write. An older
build ignores a field it does not know, and drops it at its next write. See
``docs/dev/data-architecture.md``, "Orchestrator data".
"""

from abc import ABC, abstractmethod
from dataclasses import fields, replace
from typing import Any, Literal

from pydantic import ConfigDict, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from .state_db.db import StateDb


# `extra="ignore"`: a field a newer build wrote does not break an older read.
@dataclass(frozen=True, config=ConfigDict(extra="ignore"))
class OrchestratorData:
    #: The External event the agent's **Conditional end** waits on, if any.
    end_on: Literal["pr.merged"] | None = None


EMPTY = OrchestratorData()
_ADAPTER = TypeAdapter(OrchestratorData)


class OrchestratorDataStore(ABC):
    @abstractmethod
    async def read(self, agent_id: str) -> OrchestratorData:
        """``agent_id``'s data. :data:`EMPTY` when nothing is stored."""

    @abstractmethod
    async def list(self) -> dict[str, OrchestratorData]:
        """Each agent whose data is not :data:`EMPTY`."""

    @abstractmethod
    async def _write(self, agent_id: str, data: OrchestratorData) -> None: ...

    async def update(self, agent_id: str, **changes: Any) -> OrchestratorData:
        """Set ``changes`` on ``agent_id``'s data, and return the data now.

        ``replace`` runs the dataclass's validation, so a bad value raises and
        stores nothing. An unknown name raises too: ``extra="ignore"`` would
        drop it, and a misspelt clear would store nothing.

        The read and the write are not one transaction. With one field the
        last writer wins, which is enough; a second field written from two
        tasks at once needs a column-level JSON write.
        """
        unknown = changes.keys() - {f.name for f in fields(OrchestratorData)}
        if unknown:
            raise TypeError(f"not an OrchestratorData field: {sorted(unknown)}")
        before = await self.read(agent_id)
        data = replace(before, **changes)
        if data != before:
            # Skipped when equal: a user message clears on every send.
            await self._write(agent_id, data)
        return data


class InMemoryOrchestratorDataStore(OrchestratorDataStore):
    """Orchestrator data for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, OrchestratorData] = {}

    async def read(self, agent_id: str) -> OrchestratorData:
        return self._rows.get(agent_id, EMPTY)

    async def list(self) -> dict[str, OrchestratorData]:
        return {k: v for k, v in self._rows.items() if v != EMPTY}

    async def _write(self, agent_id: str, data: OrchestratorData) -> None:
        self._rows[agent_id] = data


class SqliteOrchestratorDataStore(OrchestratorDataStore):
    """Orchestrator data in the ``agents`` table's ``orchestrator_data`` column."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, agent_id: str) -> OrchestratorData:
        row = await self._db.read("agents", agent_id)
        return EMPTY if row is None else _decoded(agent_id, row["orchestrator_data"])

    async def list(self) -> dict[str, OrchestratorData]:
        rows = await self._db.read_all("agents")
        found = {
            row["id"]: _decoded(row["id"], row["orchestrator_data"]) for row in rows
        }
        return {k: v for k, v in found.items() if v != EMPTY}

    async def _write(self, agent_id: str, data: OrchestratorData) -> None:
        # Only this column: `body` belongs to the daemon bridge.
        await self._db.upsert(
            "agents", agent_id, orchestrator_data=_ADAPTER.dump_json(data).decode()
        )


def _decoded(agent_id: str, raw: str) -> OrchestratorData:
    """The stored JSON as data. A row that does not validate reads as empty.

    A bad row must not stop the server: losing a Conditional end leaves the
    session open, which is the safe side.
    """
    try:
        return _ADAPTER.validate_json(raw or "{}")
    except ValidationError:
        return EMPTY
