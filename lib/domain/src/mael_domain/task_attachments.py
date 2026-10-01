"""Attached documents: the table, and ``attach``, which copies a document into it.

See ``CONTEXT.md``, "Attached document", and ``docs/dev/orchestrator-server.md``,
"An attached document".
"""

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .attachments import markdown_ref, save_media
from .document_tags import (
    MediaRef,
    media_refs,
    not_shown,
    read_worktree_file,
    replace_media,
    stays_within,
)
from .state_db.db import StateDb

log = logging.getLogger(__name__)

TABLE = "task_attachments"


@dataclass(frozen=True)
class TaskAttachment:
    """One attached document. ``(task_key, path)`` is its identity."""

    id: str
    task_key: str
    kind: str
    path: str
    title: str
    version: int
    body: str
    digest: str
    attached_at: str


def task_key(project: str, task_id: str) -> str:
    """The key a task's attached documents are stored under."""
    return f"{project}/{task_id}"


def _id_of(key: str, path: str) -> str:
    """A stable id for one ``(task_key, path)``, safe in a URL."""
    return hashlib.sha256(f"{key}\0{path}".encode()).hexdigest()[:16]


class TaskAttachmentTable(ABC):
    """Where attached documents are stored."""

    @abstractmethod
    async def upsert(
        self,
        *,
        task_key: str,
        kind: str,
        path: str,
        title: str,
        body: str,
        digest: str,
        attached_at: str,
    ) -> TaskAttachment:
        """Store a document under its ``(task_key, path)`` and return the row.

        A first upsert is version 1. Each later one for the same pair replaces
        the row and adds one.
        """

    @abstractmethod
    async def read(self, key: str, path: str) -> TaskAttachment | None:
        """The document attached for ``path`` under the task ``key``, or ``None``."""

    @abstractmethod
    async def list(self) -> list[TaskAttachment]:
        """Every attached document."""

    async def _next(self, **fields: Any) -> TaskAttachment:
        """The row an upsert of ``fields`` stores: its id and its next version."""
        previous = await self.read(fields["task_key"], fields["path"])
        return TaskAttachment(
            id=_id_of(fields["task_key"], fields["path"]),
            version=(previous.version if previous else 0) + 1,
            **fields,
        )


class InMemoryTaskAttachmentTable(TaskAttachmentTable):
    """Attached documents for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, TaskAttachment] = {}

    async def upsert(self, **fields: Any) -> TaskAttachment:
        stored = await self._next(**fields)
        self._rows[stored.id] = stored
        return stored

    async def read(self, key: str, path: str) -> TaskAttachment | None:
        return self._rows.get(_id_of(key, path))

    async def list(self) -> list[TaskAttachment]:
        return list(self._rows.values())


class SqliteTaskAttachmentTable(TaskAttachmentTable):
    """Attached documents in the state database."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def upsert(self, **fields: Any) -> TaskAttachment:
        stored = await self._next(**fields)
        await self._db.upsert(TABLE, stored.id, version=stored.version, **fields)
        return stored

    async def read(self, key: str, path: str) -> TaskAttachment | None:
        row = await self._db.read(TABLE, _id_of(key, path))
        return _from_row(row) if row is not None else None

    async def list(self) -> list[TaskAttachment]:
        return [_from_row(row) for row in await self._db.read_all(TABLE)]


def _from_row(row: Any) -> TaskAttachment:
    return TaskAttachment(
        id=row["id"],
        task_key=row["task_key"],
        kind=row["kind"],
        path=row["path"],
        title=row["title"],
        version=row["version"],
        body=row["body"],
        digest=row["digest"],
        attached_at=row["attached_at"],
    )


async def attach(
    table: TaskAttachmentTable,
    *,
    project: str,
    task_id: str,
    cwd: str,
    path: str,
    kind: str,
    title: str,
    now: str,
    body: str | None = None,
) -> TaskAttachment | None:
    """Attach the document at ``path`` for one task, and return its row.

    ``None`` means the file cannot be read, and nothing is attached. A document
    already attached as it stands returns the row it has, and writes nothing: an
    attach replays the backlog, so the same document reaches here again.

    "As it stands" covers the media too. The digest takes the bytes of every
    file the body names, so a recording made again under the same name is a
    new version.

    ``body`` is the markdown when the caller already holds it. A plan's file is
    outside the worktree, so its text comes from the document and ``path`` is
    only its identity. Media refs resolve against ``cwd`` in both cases.
    """
    source = body if body is not None else read_worktree_file(cwd, path)
    if source is None:
        return None
    # Off the loop: a video is tens of megabytes to hash and to copy.
    digest = await asyncio.to_thread(_digest, source, cwd)
    key = task_key(project, task_id)
    previous = await table.read(key, path)
    if previous is not None and (previous.digest, previous.kind, previous.title) == (
        digest,
        kind,
        title,
    ):
        return previous
    attached_body = await asyncio.to_thread(
        _with_attached_media, source, project, task_id, cwd
    )
    return await table.upsert(
        task_key=key,
        kind=kind,
        path=path,
        title=title,
        body=attached_body,
        digest=digest,
        attached_at=now,
    )


def _digest(markdown: str, cwd: str) -> str:
    """A digest of ``markdown`` and of the bytes of each media file it names.

    A file that cannot be read adds a marker in place of its bytes, so a file
    that appears later changes the digest.
    """
    digest = hashlib.sha256(markdown.encode())
    for ref in media_refs(markdown):
        digest.update(b"\0")
        try:
            if not stays_within(cwd, ref.target):
                raise OSError("outside the worktree")
            with (Path(cwd) / ref.target).open("rb") as media:
                digest.update(hashlib.file_digest(media, "sha256").digest())
        except OSError:
            digest.update(b"unreadable")
    return digest.hexdigest()


def _with_attached_media(markdown: str, project: str, bucket: str, cwd: str) -> str:
    """``markdown`` with each media ref copied into the bucket and re-pointed.

    A ref that cannot be attached becomes the prose a refused image leaves, so the
    reader is told which file is missing. The log holds the reason.
    """

    def attached(ref: MediaRef) -> str:
        if not stays_within(cwd, ref.target):
            log.warning("media %s not attached: outside the worktree", ref.target)
            return not_shown(ref.target)
        try:
            token = save_media(project, bucket, Path(cwd) / ref.target)
        except (ValueError, OSError) as refused:
            log.warning("media %s not attached: %s", ref.target, refused)
            return not_shown(ref.target)
        return markdown_ref(ref.alt, token)

    return replace_media(markdown, attached)
