"""The Task document store: each document, its versions, and the tasks that link it.

The table and the module keep the name they had when a row was one attached file.
See ``CONTEXT.md``, "Task document", and ``docs/adr/0001-task-documents.md``. The
rules that sit on top of the store are in :mod:`mael_domain.task_documents`.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any

from .state_db.db import StateDb

TABLE = "task_attachments"
VERSIONS = "task_attachment_versions"
LINKS = "task_attachment_links"


@dataclass(frozen=True)
class TaskAttachment:
    """One Task document. ``(task_key, name)`` is its identity."""

    id: str
    task_key: str
    kind: str
    name: str
    archived: bool
    attached_at: str


@dataclass(frozen=True)
class Version:
    """One version of a Task document. Versions are never changed or removed."""

    id: str
    attachment_id: str
    version: int
    title: str
    body: str
    digest: str
    created_at: str
    review_status: str


@dataclass(frozen=True)
class Link:
    """One task's link to a Task document, or to one fragment of it."""

    id: str
    attachment_id: str
    project: str
    task_id: str
    fragment: str


def task_key(project: str, task_id: str) -> str:
    """The key a task's documents are stored under."""
    return f"{project}/{task_id}"


def attachment_id(key: str, name: str) -> str:
    """A stable id for one ``(task_key, name)``, safe in a URL."""
    return hashlib.sha256(f"{key}\0{name}".encode()).hexdigest()[:16]


def version_id(attachment: str, version: int) -> str:
    return f"{attachment}@v{version}"


def link_id(attachment: str, project: str, task_id: str, fragment: str) -> str:
    seed = "\0".join((attachment, project, task_id, fragment))
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


class TaskAttachmentTable(ABC):
    """Where Task documents are stored."""

    @abstractmethod
    async def get(self, attachment: str) -> TaskAttachment | None:
        """The document with this id, or ``None``."""

    async def read(self, key: str, name: str) -> TaskAttachment | None:
        """The document ``name`` owned by the task ``key``, or ``None``."""
        return await self.get(attachment_id(key, name))

    @abstractmethod
    async def all_documents(self) -> list[TaskAttachment]:
        """Every document."""

    @abstractmethod
    async def owned(self, key: str) -> list[TaskAttachment]:
        """The documents the task ``key`` owns, by name."""

    @abstractmethod
    async def put(self, attachment: TaskAttachment) -> None:
        """Store the document row, replacing the one with its id."""

    @abstractmethod
    async def versions(self, attachment: str) -> list[Version]:
        """A document's versions, oldest first."""

    @abstractmethod
    async def add_version(self, version: Version) -> None:
        """Store one new version."""

    @abstractmethod
    async def links(self, attachment: str) -> list[Link]:
        """Every link to a document."""

    @abstractmethod
    async def task_links(self, project: str, task_id: str) -> list[Link]:
        """Every link one task holds."""

    @abstractmethod
    async def add_link(self, link: Link) -> None:
        """Store a link. Storing one that is there changes nothing."""

    @abstractmethod
    async def remove_link(self, link: str) -> None:
        """Remove the link with this id, when it is there."""

    async def linked(self, project: str, task_id: str) -> list[TaskAttachment]:
        """The documents one task links, by id."""
        ids = sorted(
            {link.attachment_id for link in await self.task_links(project, task_id)}
        )
        found = [await self.get(i) for i in ids]
        return [a for a in found if a is not None]


class InMemoryTaskAttachmentTable(TaskAttachmentTable):
    """Task documents for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, TaskAttachment] = {}
        self._versions: dict[str, Version] = {}
        self._links: dict[str, Link] = {}

    async def get(self, attachment: str) -> TaskAttachment | None:
        return self._rows.get(attachment)

    async def all_documents(self) -> list[TaskAttachment]:
        return sorted(self._rows.values(), key=lambda a: a.id)

    async def owned(self, key: str) -> list[TaskAttachment]:
        return sorted(
            (a for a in self._rows.values() if a.task_key == key), key=lambda a: a.name
        )

    async def put(self, attachment: TaskAttachment) -> None:
        self._rows[attachment.id] = attachment

    async def versions(self, attachment: str) -> list[Version]:
        return sorted(
            (v for v in self._versions.values() if v.attachment_id == attachment),
            key=lambda v: v.version,
        )

    async def add_version(self, version: Version) -> None:
        self._versions[version.id] = version

    async def links(self, attachment: str) -> list[Link]:
        return [v for v in self._links.values() if v.attachment_id == attachment]

    async def task_links(self, project: str, task_id: str) -> list[Link]:
        return [
            v
            for v in self._links.values()
            if (v.project, v.task_id) == (project, task_id)
        ]

    async def add_link(self, link: Link) -> None:
        self._links[link.id] = link

    async def remove_link(self, link: str) -> None:
        self._links.pop(link, None)


class SqliteTaskAttachmentTable(TaskAttachmentTable):
    """Task documents in the state database."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def get(self, attachment: str) -> TaskAttachment | None:
        row = await self._db.read(TABLE, attachment)
        return _attachment(row) if row is not None else None

    async def all_documents(self) -> list[TaskAttachment]:
        return [_attachment(row) for row in await self._db.read_all(TABLE)]

    async def owned(self, key: str) -> list[TaskAttachment]:
        rows = await self._db.read_where(TABLE, "task_key", key)
        return sorted((_attachment(row) for row in rows), key=lambda a: a.name)

    async def put(self, attachment: TaskAttachment) -> None:
        await self._db.upsert(
            TABLE,
            attachment.id,
            task_key=attachment.task_key,
            kind=attachment.kind,
            name=attachment.name,
            archived=int(attachment.archived),
            attached_at=attachment.attached_at,
        )

    async def versions(self, attachment: str) -> list[Version]:
        rows = await self._db.read_where(VERSIONS, "attachment_id", attachment)
        return sorted(
            (Version(**row) for row in _without_revision(rows)), key=lambda v: v.version
        )

    async def add_version(self, version: Version) -> None:
        fields = asdict(version)
        await self._db.upsert(VERSIONS, fields.pop("id"), **fields)

    async def links(self, attachment: str) -> list[Link]:
        rows = await self._db.read_where(LINKS, "attachment_id", attachment)
        return [Link(**row) for row in _without_revision(rows)]

    async def task_links(self, project: str, task_id: str) -> list[Link]:
        rows = await self._db.read_where(LINKS, "task_id", task_id)
        return [
            Link(**row) for row in _without_revision(rows) if row["project"] == project
        ]

    async def add_link(self, link: Link) -> None:
        fields = asdict(link)
        await self._db.upsert(LINKS, fields.pop("id"), **fields)

    async def remove_link(self, link: str) -> None:
        await self._db.delete(LINKS, link)


def _without_revision(rows: list[Any]) -> list[dict[str, Any]]:
    return [{k: row[k] for k in row.keys() if k != "revision"} for row in rows]


def _attachment(row: Any) -> TaskAttachment:
    return TaskAttachment(
        id=row["id"],
        task_key=row["task_key"],
        kind=row["kind"],
        name=row["name"],
        archived=bool(row["archived"]),
        attached_at=row["attached_at"],
    )
