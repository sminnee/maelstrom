"""Task documents: the reference grammar, and the rules over the store.

See ``docs/specs/tasks/CONTEXT.md``, "Task document", "Document link" and "Fragment", and
``docs/adr/0001-task-documents.md``. The store is
:mod:`mael_domain.task_attachments`.
"""

import asyncio
import hashlib
import logging
import re
import weakref
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from . import sarif
from .attachments import markdown_ref, save_media
from .document_tags import KINDS as TAG_KINDS
from .document_tags import (
    MediaRef,
    media_refs,
    not_shown,
    read_worktree_file,
    replace_media,
    stays_within,
)
from .task_attachments import (
    Link,
    TaskAttachment,
    TaskAttachmentTable,
    Version,
    attachment_id,
    link_id,
    task_key,
    version_id,
)

log = logging.getLogger(__name__)

KINDS = ("markdown", "sarif")
#: The Document role: the kind of the Document that stored it. See ``CONTEXT.md``.
ROLES = ("plan", *TAG_KINDS)
#: The name a task's plan is stored under. Only the server writes it.
PLAN_NAME = "plan"
#: A version's review status: the world document's ``status``.
REVIEW_STATUSES = (
    "draft",
    "awaiting-review",
    "approved",
    "changes-requested",
    "superseded",
    "stale",
)


class TaskDocumentError(ValueError):
    """Anything the Task document rules refuse."""


class DocumentNotFound(TaskDocumentError):
    """No document, or no such version of it."""


class InvalidName(TaskDocumentError):
    """A name or a reference the grammar refuses."""


class InvalidDocument(TaskDocumentError):
    """A kind that is not known, or a body its kind cannot read."""


class UnknownFragment(TaskDocumentError):
    """A fragment the document's latest version does not have."""


class NotOwner(TaskDocumentError):
    """A task that does not own the document asked to change it."""


class KindMismatch(TaskDocumentError):
    """A version of another kind than the document's."""


@dataclass(frozen=True)
class ReviewGroup:
    """The review group a version was presented in: one verdict covers it."""

    id: str
    title: str
    position: int = 0


@dataclass(frozen=True)
class DocumentRef:
    """``<project>:<task-id>:<name>[@v<n>][#<fragment>]``, parsed."""

    project: str
    task_id: str
    name: str
    version: int | None = None
    fragment: str | None = None

    @property
    def task_key(self) -> str:
        return task_key(self.project, self.task_id)

    def __str__(self) -> str:
        text = f"{self.project}:{self.task_id}:{self.name}"
        if self.version is not None:
            text += f"@v{self.version}"
        if self.fragment is not None:
            text += f"#{self.fragment}"
        return text


def validate_name(name: str) -> None:
    """Refuse a name a reference cannot carry. ``/`` is allowed.

    Raises:
        InvalidName: Empty, holds ``:`` ``@`` ``#`` or a control character, or
            starts or ends with a space.
    """
    if not name:
        raise InvalidName("A document name cannot be empty")
    if any(c in name for c in ":@#"):
        raise InvalidName(f"A document name cannot contain ':', '@' or '#': {name!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise InvalidName(
            f"A document name cannot contain a control character: {name!r}"
        )
    if name != name.strip():
        raise InvalidName(f"A document name cannot start or end with a space: {name!r}")


def parse_ref(
    text: str,
    *,
    project: str | Callable[[], str] | None,
    task_id: str | None,
) -> DocumentRef:
    """Parse ``[<project>:][<task-id>:]<name>[@v<n>][#<fragment>]``.

    A missing project is ``project``; a missing task id is ``task_id``.
    ``project`` may be a callable, which is called only when the text names
    no project: the cwd's project is not looked up for a full reference.

    Raises:
        InvalidName: The text does not parse, or names no project or task.
    """
    rest, hash_, fragment = text.partition("#")
    if hash_ and not fragment:
        raise InvalidName(f"An empty fragment in {text!r}")
    version: int | None = None
    found = re.fullmatch(r"(.*)@v(\d+)", rest, re.DOTALL)
    if found:
        rest, version = found[1], int(found[2])
    parts = rest.split(":")
    if len(parts) > 3:
        raise InvalidName(f"Too many ':' in {text!r}")
    name = parts[-1]
    task_id = parts[-2] if len(parts) >= 2 else task_id
    found_project = (
        parts[-3] if len(parts) == 3 else project() if callable(project) else project
    )
    if not found_project or not task_id:
        missing = "task" if found_project else "project"
        raise InvalidName(f"{text!r} names no {missing}")
    validate_name(name)
    return DocumentRef(
        found_project, task_id, name, version, fragment if hash_ else None
    )


def ref_of(attachment: TaskAttachment, version: int | None = None) -> DocumentRef:
    """The reference that names ``attachment``, at ``version`` when given."""
    project, _, task_id = attachment.task_key.partition("/")
    return DocumentRef(project, task_id, attachment.name, version)


#: One lock per store, so a read of the latest version and the write of the
#: next are one step. Two writes to one document would otherwise both take
#: the same number, and the second would replace the first's text.
_writing: "weakref.WeakKeyDictionary[TaskAttachmentTable, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


async def add(
    table: TaskAttachmentTable,
    *,
    project: str,
    task_id: str,
    name: str,
    kind: str,
    title: str,
    body: str,
    now: str,
    digest: str | None = None,
    review_status: str = "draft",
    role: str | None = None,
    agent_id: str = "",
    group: ReviewGroup | None = None,
) -> tuple[TaskAttachment, Version]:
    """Add ``body`` as the next version of the task's document ``name``.

    The first add makes the document. A body and title the latest version
    already has adds no version, and returns that version: its review status
    is not written here, but its agent and group are. ``digest`` stands in for
    the body's own when the caller hashes more than the text, as
    :func:`prepare` does with media. An archived document still takes versions.

    ``role`` replaces the document's role when given. A new document without
    one is ``other``.

    Raises:
        InvalidName, InvalidDocument, KindMismatch.
    """
    validate_name(name)
    if kind not in KINDS:
        raise InvalidDocument(f"Unknown kind {kind!r}: one of {', '.join(KINDS)}")
    if role is not None and role not in ROLES:
        raise InvalidDocument(f"Unknown role {role!r}: one of {', '.join(ROLES)}")
    if kind == "sarif":
        try:
            sarif.parse(body)
        except ValueError as exc:
            raise InvalidDocument(f"Not a SARIF report: {exc}") from None
    lock = _writing.setdefault(table, asyncio.Lock())
    async with lock:
        return await _add(
            table,
            key=task_key(project, task_id),
            name=name,
            kind=kind,
            title=title,
            body=body,
            now=now,
            digest=digest or hashlib.sha256(body.encode()).hexdigest(),
            review_status=review_status,
            role=role,
            agent_id=agent_id,
            group=group,
        )


async def _add(
    table: TaskAttachmentTable,
    *,
    key: str,
    name: str,
    kind: str,
    title: str,
    body: str,
    now: str,
    digest: str,
    review_status: str,
    role: str | None,
    agent_id: str,
    group: ReviewGroup | None,
) -> tuple[TaskAttachment, Version]:
    document = await table.read(key, name)
    versions = await table.versions(document.id) if document else []
    if document is None:
        document = TaskAttachment(
            id=attachment_id(key, name),
            task_key=key,
            kind=kind,
            name=name,
            archived=False,
            attached_at=now,
            role=role or "other",
        )
        await table.put(document)
    elif document.kind != kind:
        raise KindMismatch(f"{name} is {document.kind}, not {kind}")
    elif role is not None and document.role != role:
        document = replace(document, role=role)
        await table.put(document)
    latest = versions[-1] if versions else None
    if latest is not None and (latest.digest, latest.title) == (digest, title):
        return document, await _presented_by(table, latest, agent_id, group)
    number = (latest.version if latest else 0) + 1
    version = Version(
        id=version_id(document.id, number),
        attachment_id=document.id,
        version=number,
        title=title,
        body=body,
        digest=digest,
        created_at=now,
        review_status=review_status,
        **_group_fields(agent_id, group),
    )
    await table.add_version(version)
    return document, version


def _group_fields(agent_id: str, group: ReviewGroup | None) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "group_id": group.id if group else "",
        "group_title": group.title if group else "",
        "group_position": group.position if group else 0,
    }


async def _presented_by(
    table: TaskAttachmentTable,
    version: Version,
    agent_id: str,
    group: ReviewGroup | None,
) -> Version:
    """``version``, presented again by ``agent_id`` in ``group``.

    Its text stays. The agent and group move, so a verdict reaches the agent
    that presented it last, and settles the group it is in now. A caller that
    names neither, such as a hand add, keeps them.
    """
    if not agent_id and group is None:
        return version
    moved = replace(version, **_group_fields(agent_id, group))
    if moved != version:
        await table.add_version(moved)
    return moved


async def set_status(
    table: TaskAttachmentTable, document: TaskAttachment, status: str
) -> Version | None:
    """Write ``status`` on the document's latest version. ``None``: no version."""
    versions = await table.versions(document.id)
    if not versions:
        return None
    latest = versions[-1]
    if latest.review_status != status:
        await table.set_review_status(latest.id, status)
        latest = replace(latest, review_status=status)
    return latest


async def resolve(
    table: TaskAttachmentTable, ref: DocumentRef
) -> tuple[TaskAttachment, Version]:
    """The document ``ref`` names, at its version, or the latest.

    Raises:
        DocumentNotFound: No such document or version.
    """
    document = await table.read(ref.task_key, ref.name)
    versions = await table.versions(document.id) if document else []
    if document is None or not versions:
        raise DocumentNotFound(f"No document {ref}")
    if ref.version is None:
        return document, versions[-1]
    found = next((v for v in versions if v.version == ref.version), None)
    if found is None:
        raise DocumentNotFound(f"No version {ref.version} of {ref_of(document)}")
    return document, found


def fragments(kind: str, body: str) -> list[str]:
    """The fragments a body has: heading slugs, or SARIF finding keys."""
    if kind == "sarif":
        return [f.key for f in sarif.parse(body).findings]
    slugs: list[str] = []
    seen: dict[str, int] = {}
    fence: str | None = None
    for line in body.splitlines():
        opener = re.match(r"\s{0,3}(`{3,}|~{3,})", line)
        if opener:
            mark = opener[1][0]
            if fence is None:
                fence = mark
            elif fence == mark:
                fence = None
            continue
        heading = None if fence else re.match(r"\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if heading:
            slug = heading_slug(heading[1])
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            slugs.append(f"{slug}-{count}" if count else slug)
    return slugs


def heading_slug(text: str) -> str:
    """A heading's anchor, as GitHub makes it.

    A link keeps only its text. ``_x_`` emphasis keeps its
    underscores, where GitHub drops them: telling it from ``snake_case`` needs
    a markdown parser. ``heading-slugs.json`` holds the cases that agree.
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    slug = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return slug.replace(" ", "-")


async def link(
    table: TaskAttachmentTable, ref: DocumentRef, *, project: str, task_id: str
) -> Link:
    """Link the task to the document, or to the fragment ``ref`` names.

    Linking again changes nothing.

    Raises:
        DocumentNotFound: No such document.
        TaskDocumentError: The task owns the document, so it links it already.
        UnknownFragment: The latest version has no such fragment.
    """
    if ref.version is not None:
        raise InvalidName(
            f"A link names a document, not one version of it: drop @v{ref.version}"
        )
    document, latest = await resolve(table, ref)
    if document.task_key == task_key(project, task_id):
        raise TaskDocumentError(f"{project}/{task_id} owns {ref_of(document)}")
    fragment = ref.fragment or ""
    if fragment and fragment not in fragments(document.kind, latest.body):
        raise UnknownFragment(f"{ref_of(document)} has no fragment #{fragment}")
    made = Link(
        id=link_id(document.id, project, task_id, fragment),
        attachment_id=document.id,
        project=project,
        task_id=task_id,
        fragment=fragment,
    )
    await table.add_link(made)
    return made


async def unlink(
    table: TaskAttachmentTable, ref: DocumentRef, *, project: str, task_id: str
) -> None:
    """Remove the task's link to the document, or to the fragment ``ref`` names.

    Raises:
        DocumentNotFound: No such document.
    """
    document = await table.read(ref.task_key, ref.name)
    if document is None:
        raise DocumentNotFound(f"No document {ref}")
    await table.remove_link(link_id(document.id, project, task_id, ref.fragment or ""))


async def archive(
    table: TaskAttachmentTable,
    ref: DocumentRef,
    archived: bool,
    *,
    by: str | None = None,
) -> TaskAttachment:
    """Set whether the document is archived. ``by`` is the asking task's key.

    Raises:
        DocumentNotFound: No such document.
        NotOwner: ``by`` names a task other than the owner.
    """
    document = await table.read(ref.task_key, ref.name)
    if document is None:
        raise DocumentNotFound(f"No document {ref}")
    if by is not None and by != document.task_key:
        raise NotOwner(f"{by} does not own {ref_of(document)}")
    changed = replace(document, archived=archived)
    if changed != document:
        await table.put(changed)
    return changed


@dataclass(frozen=True)
class Prepared:
    """A presented markdown file as it stands: its text, and its digest.

    The digest takes the bytes of every media file the text names, so a
    recording made again under the same name is a new version.
    """

    source: str
    digest: str


def prepare(cwd: str, path: str, body: str | None = None) -> Prepared | None:
    """Read the markdown at ``path`` and hash it. ``None``: it cannot be read.

    ``body`` is the markdown when the caller already holds it. A plan's file is
    outside the worktree, so its text comes from the document. Media refs
    resolve against ``cwd`` in both cases. Blocking: run it off the loop, as a
    video is tens of megabytes to hash.
    """
    source = body if body is not None else read_worktree_file(cwd, path)
    if source is None:
        return None
    return Prepared(source, _digest(source, cwd))


@dataclass(frozen=True)
class Presented:
    """A Task document an agent presented, as the store step reads it.

    - ``project``, ``task_id``: the task in its own notebook.
    - ``status``, ``world_version``: the world document's. ``world_version``
      is ``None`` when the presenter does not number versions as the store
      does: a **Delegated agent**'s first presentation, or a ``final`` one.
      The store then tells a new version by its ``digest``.
    - ``has_path``: false for a ``stored`` source, which names no file, so it
      adds no version.
    - ``text_in_event``: the text is in the agent's event, as a plan's is, so
      a replay can tell text the store missed by its ``digest``.
    - ``body``: the text with its media refs settled. Sent only when
      :func:`store_presented` asks for it.
    - ``final``: the agent has gone, and this is its file as it left it.
    """

    project: str
    task_id: str
    name: str
    role: str
    title: str
    status: str
    world_version: int | None
    agent_id: str = ""
    group: ReviewGroup | None = None
    has_path: bool = True
    text_in_event: bool = False
    digest: str | None = None
    body: str | None = None
    final: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "taskId": self.task_id,
            "name": self.name,
            "role": self.role,
            "title": self.title,
            "status": self.status,
            "worldVersion": self.world_version,
            "agentId": self.agent_id,
            "group": (
                {
                    "id": self.group.id,
                    "title": self.group.title,
                    "position": self.group.position,
                }
                if self.group
                else None
            ),
            "hasPath": self.has_path,
            "textInEvent": self.text_in_event,
            "digest": self.digest,
            "body": self.body,
            "final": self.final,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Presented":
        """The :meth:`to_json` form, read back.

        Raises:
            InvalidDocument: A field is missing or of the wrong type.
        """
        try:
            group = data.get("group")
            flags = (
                data.get("hasPath", True),
                data.get("textInEvent", False),
                data.get("final", False),
            )
            if data["status"] not in REVIEW_STATUSES or not all(
                isinstance(flag, bool) for flag in flags
            ):
                raise ValueError("a status or flag of the wrong value")
            world_version = data["worldVersion"]
            return cls(
                project=str(data["project"]),
                task_id=str(data["taskId"]),
                name=str(data["name"]),
                role=str(data["role"]),
                title=str(data["title"]),
                status=str(data["status"]),
                world_version=None if world_version is None else int(world_version),
                agent_id=str(data.get("agentId") or ""),
                group=(
                    ReviewGroup(
                        str(group["id"]), str(group["title"]), int(group["position"])
                    )
                    if group
                    else None
                ),
                has_path=flags[0],
                text_in_event=flags[1],
                digest=_optional_str(data.get("digest")),
                body=_optional_str(data.get("body")),
                final=flags[2],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidDocument(f"Not a presented document: {exc}") from None


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


class NeedsBody(Enum):
    """:func:`store_presented` must add a version, and was given no body."""

    NEEDS_BODY = "needs-body"


NEEDS_BODY = NeedsBody.NEEDS_BODY

#: What :func:`store_presented` did: the document and the version it holds
#: now, ``None`` for nothing to store, or :data:`NEEDS_BODY`.
Stored = tuple[TaskAttachment, Version] | None | NeedsBody


async def store_presented(
    table: TaskAttachmentTable,
    presented: Presented,
    *,
    replay: bool,
    verdict: bool,
    now: str,
) -> Stored:
    """Store a presented document: add a version, write the status, or nothing.

    The rules are the table in ``docs/dev/orchestrator-server.md``, "Storing a
    Task document". ``verdict``: the call only settles a review, so it writes a
    status and never adds a version. A replay keeps the stored latest version,
    unless the text travels in the event and no version has its digest.

    A new version needs ``presented.body`` and ``presented.digest``. Without
    them the answer is :data:`NEEDS_BODY`, unless the digest and title match
    the latest version.

    A ``final`` presentation is the file the agent left: see "A delegated
    agent's document on the parent" in the same doc.
    """
    p = presented
    row = await table.read(task_key(p.project, p.task_id), p.name)
    versions = await table.versions(row.id) if row else []
    latest = versions[-1] if versions else None
    if p.final:
        return await _store_final(table, p, row, latest, now=now)
    if verdict and (
        latest is None or p.world_version is None or p.world_version > latest.version
    ):
        # The user settled text the store does not hold, such as the
        # placeholder of a file that could not be read.
        return None
    missed = (
        replay
        and p.text_in_event
        and p.digest is not None
        and all(v.digest != p.digest for v in versions)
    )
    if (
        row is not None
        and latest is not None
        and not missed
        and (
            replay
            or not p.has_path
            or (p.world_version is not None and p.world_version <= latest.version)
        )
    ):
        stored = row, latest
    elif not p.has_path:
        return None
    elif (
        row is not None
        and latest is not None
        and row.role == p.role
        and (latest.digest, latest.title) == (p.digest, p.title)
    ):
        stored = row, await _presented_by(table, latest, p.agent_id, p.group)
    elif p.body is None or p.digest is None:
        return NEEDS_BODY
    else:
        stored = await add(
            table,
            project=p.project,
            task_id=p.task_id,
            name=p.name,
            kind="markdown",
            title=p.title,
            body=p.body,
            now=now,
            digest=p.digest,
            review_status=p.status,
            role=p.role,
            agent_id=p.agent_id,
            group=p.group,
        )
    row, version = stored
    if not replay and version.review_status != p.status:
        version = await set_status(table, row, p.status) or version
    return row, version


async def _store_final(
    table: TaskAttachmentTable,
    p: Presented,
    row: TaskAttachment | None,
    latest: Version | None,
    *,
    now: str,
) -> Stored:
    """The file an agent left, stored as :func:`store_presented` says."""
    if not p.has_path:
        return None
    if (
        row is not None
        and latest is not None
        and row.role == p.role
        and (latest.digest, latest.title) == (p.digest, p.title)
    ):
        return row, latest
    if p.body is None or p.digest is None:
        return NEEDS_BODY
    return await add(
        table,
        project=p.project,
        task_id=p.task_id,
        name=p.name,
        kind="markdown",
        title=p.title,
        body=p.body,
        now=now,
        digest=p.digest,
        review_status="draft",
        role=p.role,
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


def with_attached_media(markdown: str, project: str, bucket: str, cwd: str) -> str:
    """``markdown`` with each media ref copied into the bucket and re-pointed.

    Blocking: it copies files.

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


def without_media(markdown: str) -> str:
    """``markdown`` with each media ref as the prose a refused image leaves.

    A **Delegated agent**'s document goes to its parent as text, without media.
    """
    return replace_media(markdown, lambda ref: not_shown(ref.target))
