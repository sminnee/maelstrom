"""Task documents: the reference grammar, and the rules over the store.

See ``CONTEXT.md``, "Task document", "Document link" and "Fragment", and
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
from pathlib import Path

from . import sarif
from .attachments import markdown_ref, save_media
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
#: The name an approved plan is stored under. Only the server writes it.
PLAN_NAME = "plan"


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
) -> tuple[TaskAttachment, Version]:
    """Add ``body`` as the next version of the task's document ``name``.

    The first add makes the document. A body and title the latest version
    already has adds nothing, and returns that version. ``digest`` stands in
    for the body's own when the caller hashes more than the text, as
    :func:`attach` does with media. An archived document still takes versions.

    Raises:
        InvalidName, InvalidDocument, KindMismatch.
    """
    validate_name(name)
    if kind not in KINDS:
        raise InvalidDocument(f"Unknown kind {kind!r}: one of {', '.join(KINDS)}")
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
        )
        await table.put(document)
    elif document.kind != kind:
        raise KindMismatch(f"{name} is {document.kind}, not {kind}")
    latest = versions[-1] if versions else None
    if latest is not None and (latest.digest, latest.title) == (digest, title):
        return document, latest
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
    )
    await table.add_version(version)
    return document, version


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
    """A heading's anchor, as GitHub makes it."""
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


async def attach(
    table: TaskAttachmentTable,
    *,
    project: str,
    task_id: str,
    cwd: str,
    path: str,
    title: str,
    now: str,
    name: str | None = None,
    body: str | None = None,
    review_status: str = "draft",
) -> tuple[TaskAttachment, Version] | None:
    """Add the markdown at ``path`` as the next version of the task's document.

    ``name`` defaults to ``path``. ``None`` means the file cannot be read, and
    nothing is added. A document already stored as it stands returns the
    version it has, and writes nothing: an attach replays the backlog, so the
    same document reaches here again.

    "As it stands" covers the media too. The digest takes the bytes of every
    file the body names, so a recording made again under the same name is a
    new version.

    ``body`` is the markdown when the caller already holds it. A plan's file is
    outside the worktree, so its text comes from the document and ``path`` is
    only where it was. Media refs resolve against ``cwd`` in both cases.
    """
    source = body if body is not None else read_worktree_file(cwd, path)
    if source is None:
        return None
    name = name or path
    # Off the loop: a video is tens of megabytes to hash and to copy.
    digest = await asyncio.to_thread(_digest, source, cwd)
    document = await table.read(task_key(project, task_id), name)
    if document is not None:
        versions = await table.versions(document.id)
        if versions and (versions[-1].digest, versions[-1].title) == (digest, title):
            return document, versions[-1]
    attached_body = await asyncio.to_thread(
        _with_attached_media, source, project, task_id, cwd
    )
    return await add(
        table,
        project=project,
        task_id=task_id,
        name=name,
        kind="markdown",
        title=title,
        body=attached_body,
        now=now,
        digest=digest,
        review_status=review_status,
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
