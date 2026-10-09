"""Where comms are kept. See ``CONTEXT.md``, "Comm".

:mod:`mael_domain.comms` decides what to write; this module only stores it.
The tasks a comm links to are not here: each task names its comms.
"""

import copy
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .state_db.db import StateDb

COMMS = "comms"


@dataclass
class Comm:
    """One **Comm**. See ``CONTEXT.md``, "Comm".

    ``content`` is the request as it came in. ``recipients`` are free strings: a
    Slack channel, an email address, or a note. ``closed_at`` is empty while the
    comm is open. ``category`` is free text; ``project`` is the project a task
    made from the comm goes to.
    """

    id: str
    title: str
    content: str = ""
    recipients: list[str] = field(default_factory=list)
    created_at: str = ""
    closed_at: str = ""
    category: str = ""
    project: str = ""


@dataclass(frozen=True)
class CommChanges:
    """The comms written since some revision, and the revision to ask from next.

    Comms are never deleted, so there is no ``removed`` list.
    """

    comms: list[Comm] = field(default_factory=list)
    revision: int = 0


def _number(id: str) -> int:
    """The ``n`` of ``c<n>``, so ``c10`` sorts after ``c2``."""
    return int(id[1:])


def _next(ids: list[str]) -> str:
    return f"c{max((_number(i) for i in ids), default=0) + 1}"


class CommStore(ABC):
    """Where comms are kept."""

    @abstractmethod
    async def read(self, id: str) -> Comm | None:
        """The stored comm, or ``None``."""

    @abstractmethod
    async def list(self) -> list[Comm]:
        """Every comm, open and closed, in id order."""

    @abstractmethod
    async def save(self, comm: Comm) -> None:
        """Store ``comm``, replacing the row with its id."""

    @abstractmethod
    async def next_id(self) -> str:
        """The id the next comm takes. Call it inside :meth:`transact`."""

    @abstractmethod
    def transact(self) -> Any:
        """A write block: ``next_id`` and ``save`` inside it are one cut."""

    @abstractmethod
    async def revision(self) -> int:
        """The revision of the last write."""

    @abstractmethod
    async def changed_since(self, since: int) -> CommChanges:
        """Every comm written after revision ``since``."""


class InMemoryCommStore(CommStore):
    """Comms for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[int, Comm]] = {}
        self._revision = 0

    async def read(self, id: str) -> Comm | None:
        found = self._rows.get(id)
        return copy.deepcopy(found[1]) if found else None

    async def list(self) -> list[Comm]:
        return [
            copy.deepcopy(c)
            for _, c in sorted(self._rows.values(), key=lambda r: _number(r[1].id))
        ]

    async def save(self, comm: Comm) -> None:
        stored = self._rows.get(comm.id)
        if stored is not None and stored[1] == comm:
            return
        self._revision += 1
        self._rows[comm.id] = (self._revision, copy.deepcopy(comm))

    async def next_id(self) -> str:
        return _next(list(self._rows))

    @asynccontextmanager
    async def transact(self) -> AsyncGenerator[None]:
        yield

    async def revision(self) -> int:
        return self._revision

    async def changed_since(self, since: int) -> CommChanges:
        changed = [copy.deepcopy(c) for rev, c in self._rows.values() if rev > since]
        changed.sort(key=lambda c: _number(c.id))
        return CommChanges(comms=changed, revision=self._revision)


class SqliteCommStore(CommStore):
    """Comms in the state database's canonical ``comms`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, id: str) -> Comm | None:
        row = await self._db.read(COMMS, id)
        return _comm_from_row(row) if row is not None else None

    async def list(self) -> list[Comm]:
        comms = [_comm_from_row(r) for r in await self._db.read_all(COMMS)]
        return sorted(comms, key=lambda c: _number(c.id))

    async def save(self, comm: Comm) -> None:
        await self._db.upsert(
            COMMS,
            comm.id,
            title=comm.title,
            content=comm.content,
            recipients=json.dumps(list(comm.recipients)),
            created_at=comm.created_at,
            closed_at=comm.closed_at,
            category=comm.category,
            project=comm.project,
        )

    async def next_id(self) -> str:
        return _next([r["id"] for r in await self._db.read_all(COMMS)])

    def transact(self) -> Any:
        return self._db.transact()

    async def revision(self) -> int:
        return await self._db.revision()

    async def changed_since(self, since: int) -> CommChanges:
        rows = await self._db.changed_since(COMMS, since)
        changed = sorted((_comm_from_row(r) for r in rows), key=lambda c: _number(c.id))
        return CommChanges(comms=changed, revision=await self._db.revision())


def _comm_from_row(row: Any) -> Comm:
    return Comm(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        recipients=list(json.loads(row["recipients"] or "[]")),
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        category=row["category"],
        project=row["project"],
    )
