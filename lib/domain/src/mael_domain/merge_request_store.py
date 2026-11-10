"""Where Merge requests and their Builds are kept.

:mod:`mael_domain.merge_request` decides what each record is; this module only
stores them. The tables stay small, one row per submit, so a query reads every
row and filters in Python.
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, fields
from typing import Any, TypeVar

from .merge_request import UNFINISHED, Build, MergeRequest, record_number
from .state_db.db import StateDb

MERGE_REQUESTS = "merge_requests"
BUILDS = "builds"


_Record = TypeVar("_Record", MergeRequest, Build)


def _newest_first(rows: list[_Record]) -> list[_Record]:
    return sorted(rows, key=lambda r: record_number(r.id), reverse=True)


def _next_id(prefix: str, ids: list[str]) -> str:
    return f"{prefix}{max((record_number(i) for i in ids), default=0) + 1}"


class MergeRequestStore(ABC):
    @abstractmethod
    async def read(self, id: str) -> MergeRequest | None:
        """The request ``id``, or ``None``."""

    @abstractmethod
    async def for_task(self, task: str) -> list[MergeRequest]:
        """Every request of ``task`` (``<project>/<task id>``), newest first."""

    @abstractmethod
    async def all(self) -> list[MergeRequest]:
        """Every request, newest first."""

    @abstractmethod
    async def save(self, mr: MergeRequest) -> None:
        """Store ``mr``, replacing the row with its id."""

    async def current(self, task: str) -> MergeRequest | None:
        """The newest request of ``task``, whatever its state, or ``None``."""
        found = await self.for_task(task)
        return found[0] if found else None

    async def open(self) -> list[MergeRequest]:
        """Every ``open`` request, newest first."""
        return [mr for mr in await self.all() if mr.state == "open"]

    async def next_id(self) -> str:
        """An id no request holds: one past the highest."""
        return _next_id("mr", [mr.id for mr in await self.all()])


class BuildStore(ABC):
    @abstractmethod
    async def read(self, id: str) -> Build | None:
        """The Build ``id``, or ``None``."""

    @abstractmethod
    async def for_request(self, mr_id: str) -> list[Build]:
        """Every Build of the request ``mr_id``, newest first."""

    @abstractmethod
    async def all(self) -> list[Build]:
        """Every Build, newest first."""

    @abstractmethod
    async def save(self, build: Build) -> None:
        """Store ``build``, replacing the row with its id."""

    async def unfinished(self) -> list[Build]:
        """Every ``queued`` and ``running`` Build, newest first."""
        return [b for b in await self.all() if b.state in UNFINISHED]

    async def next_id(self) -> str:
        """An id no Build holds: one past the highest."""
        return _next_id("b", [b.id for b in await self.all()])


class InMemoryMergeRequestStore(MergeRequestStore):
    """Requests for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, MergeRequest] = {}

    async def read(self, id: str) -> MergeRequest | None:
        return self._rows.get(id)

    async def for_task(self, task: str) -> list[MergeRequest]:
        return [mr for mr in await self.all() if mr.task == task]

    async def all(self) -> list[MergeRequest]:
        return _newest_first(list(self._rows.values()))

    async def save(self, mr: MergeRequest) -> None:
        self._rows[mr.id] = mr


class InMemoryBuildStore(BuildStore):
    """Builds for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, Build] = {}

    async def read(self, id: str) -> Build | None:
        return self._rows.get(id)

    async def for_request(self, mr_id: str) -> list[Build]:
        return [b for b in await self.all() if b.merge_request == mr_id]

    async def all(self) -> list[Build]:
        return _newest_first(list(self._rows.values()))

    async def save(self, build: Build) -> None:
        self._rows[build.id] = build


def _columns(record: MergeRequest | Build) -> dict[str, Any]:
    cols = asdict(record)
    del cols["id"]
    return cols


def _request(row: Any) -> MergeRequest:
    return MergeRequest(**{f.name: row[f.name] for f in fields(MergeRequest)})


def _build(row: Any) -> Build:
    return Build(**{f.name: row[f.name] for f in fields(Build)})


class SqliteMergeRequestStore(MergeRequestStore):
    """Requests in the state database's ``merge_requests`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, id: str) -> MergeRequest | None:
        row = await self._db.read(MERGE_REQUESTS, id)
        return _request(row) if row is not None else None

    async def for_task(self, task: str) -> list[MergeRequest]:
        rows = await self._db.read_where(MERGE_REQUESTS, "task", task)
        return _newest_first([_request(r) for r in rows])

    async def all(self) -> list[MergeRequest]:
        return _newest_first(
            [_request(r) for r in await self._db.read_all(MERGE_REQUESTS)]
        )

    async def save(self, mr: MergeRequest) -> None:
        await self._db.upsert(MERGE_REQUESTS, mr.id, **_columns(mr))


class SqliteBuildStore(BuildStore):
    """Builds in the state database's ``builds`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, id: str) -> Build | None:
        row = await self._db.read(BUILDS, id)
        return _build(row) if row is not None else None

    async def for_request(self, mr_id: str) -> list[Build]:
        rows = await self._db.read_where(BUILDS, "merge_request", mr_id)
        return _newest_first([_build(r) for r in rows])

    async def all(self) -> list[Build]:
        return _newest_first([_build(r) for r in await self._db.read_all(BUILDS)])

    async def save(self, build: Build) -> None:
        await self._db.upsert(BUILDS, build.id, **_columns(build))
