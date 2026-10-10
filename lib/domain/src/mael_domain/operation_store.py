"""Where operations are kept. See ``CONTEXT.md``, "Operation".

:mod:`mael_domain.operations` decides what an operation is; this module only
stores it, with its log beside it. Only the newest ``cap`` operations are kept.
"""

import copy
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

from .operations import OPERATION_CAP
from .protocol import Operation
from .state_db.db import StateDb

OPERATIONS = "operations"

#: An operation's log lines, by step name, in the order the steps ran.
OperationLog = dict[str, list[str]]


def operation_number(id: str) -> int:
    """The ``n`` of ``op<n>``, so ``op10`` sorts after ``op2``."""
    return int(id[2:])


@dataclass
class OperationRecord:
    """An operation as the store keeps it.

    ``log`` and ``command`` are not part of the entity. The log is a separate
    read; the command is what a retry runs again, so it must outlive the server
    life that started it.
    """

    operation: Operation
    log: OperationLog = field(default_factory=dict)
    command: dict[str, Any] = field(default_factory=dict)


class OperationStore(ABC):
    """Where operations are kept, newest ``cap`` only."""

    @abstractmethod
    async def list(self) -> list[Operation]:
        """Every kept operation, newest first."""

    @abstractmethod
    async def read(self, id: str) -> OperationRecord | None:
        """The operation with its log and command, or ``None`` when not kept."""

    @abstractmethod
    async def save(self, record: OperationRecord) -> None:
        """Store ``record``, replacing the row with its id.

        The oldest operations past the cap are dropped. The caller mints the
        id: the newest row is never the one dropped, so the highest kept
        number is the high mark.
        """


class InMemoryOperationStore(OperationStore):
    """Operations for a test or a server with no state database."""

    def __init__(self, *, cap: int = OPERATION_CAP) -> None:
        self._rows: dict[str, OperationRecord] = {}
        self._cap = cap

    async def list(self) -> list[Operation]:
        ops = [copy.deepcopy(r.operation) for r in self._rows.values()]
        return sorted(ops, key=lambda op: operation_number(op["id"]), reverse=True)

    async def read(self, id: str) -> OperationRecord | None:
        found = self._rows.get(id)
        return copy.deepcopy(found) if found else None

    async def save(self, record: OperationRecord) -> None:
        id = record.operation["id"]
        self._rows[id] = copy.deepcopy(record)
        for old in sorted(self._rows, key=operation_number)[: -self._cap]:
            del self._rows[old]


class SqliteOperationStore(OperationStore):
    """Operations in the state database's ``operations`` table.

    The entity, its log and its command are JSON columns. Nothing queries
    inside any of them, and the entity's shape is the wire's, so a column per
    field would only be a second place to keep it.
    """

    def __init__(self, db: StateDb, *, cap: int = OPERATION_CAP) -> None:
        self._db = db
        self._cap = cap

    async def list(self) -> list[Operation]:
        ops = [_record(r).operation for r in await self._db.read_all(OPERATIONS)]
        return sorted(ops, key=lambda op: operation_number(op["id"]), reverse=True)

    async def read(self, id: str) -> OperationRecord | None:
        row = await self._db.read(OPERATIONS, id)
        return _record(row) if row is not None else None

    async def save(self, record: OperationRecord) -> None:
        id = record.operation["id"]
        # A running operation saves on every step; only a new row can push an
        # old one past the cap, so only a new row pays for the prune.
        new = await self._db.read(OPERATIONS, id) is None
        await self._db.upsert(
            OPERATIONS,
            id,
            body=json.dumps(record.operation),
            log=json.dumps(record.log),
            command=json.dumps(record.command),
        )
        if not new:
            return
        ids = sorted(
            (r["id"] for r in await self._db.read_all(OPERATIONS)), key=operation_number
        )
        for old in ids[: -self._cap]:
            await self._db.delete(OPERATIONS, old)


def _record(row: Any) -> OperationRecord:
    return OperationRecord(
        operation=cast(Operation, json.loads(row["body"])),
        log=dict(json.loads(row["log"] or "{}")),
        command=dict(json.loads(row["command"] or "{}")),
    )
