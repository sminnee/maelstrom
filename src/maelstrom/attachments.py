"""Image attachments in the task repo.

One mechanism for every image that reaches an agent, whatever brought it in.
An image downloaded from a Linear brief and a screenshot pasted into the
orchestrator UI are the same thing once the bytes are in hand: a file in the
git-backed task repo, and a portable token in the task's content. Only the
source differs, so only the source lives with its caller — the naming,
de-duping, directory layout and token minting are here.

The token is :data:`~maelstrom.task.MAEL_TASK_DIR_TOKEN`, which
``task.build_prompt`` expands to an absolute path at launch. That is what lets
the agent ``Read`` the file while the stored ``.md`` stays machine-independent.

Files are written untracked; the caller's next notebook commit sweeps them in
via ``git add -A``.
"""

from pathlib import Path

from . import task_store

#: Magic-byte → extension sniffing for the image formats we accept. Upload URLs
#: are often extensionless UUIDs and a pasted blob has no name at all, so the
#: bytes are the only reliable source for an extension.
IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),  # WEBP is RIFF-framed; good enough for a filename.
)

#: The largest attachment we store. A screenshot is well under this; the cap is
#: here so one paste cannot fill the notebook repo.
MAX_BYTES = 5 * 1024 * 1024


def image_extension(name: str, data: bytes) -> str:
    """Pick a filename extension for an image.

    Sniffs magic bytes first, then falls back to the extension in ``name``
    (e.g. ``image.png``), then ``.bin``.
    """
    for magic, ext in IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    suffix = Path(name).suffix.lower()
    if suffix:
        return suffix
    return ".bin"


def is_image(data: bytes) -> bool:
    """Whether the bytes sniff as one of the formats we accept."""
    return any(data.startswith(magic) for magic, _ in IMAGE_MAGIC)


def _bare_name(value: str, what: str) -> str:
    """One path segment, or a refusal.

    Every segment of an attachment path arrives from a client: ``project`` and
    ``bucket`` off a multipart body or a URL, ``name`` off a URL. A separator or
    a ``..`` segment in any of them reaches outside the task repo, so all three
    are held to one rule. Refused rather than sanitised: a caller that meant a
    path has a bug worth hearing about.
    """
    if value != Path(value).name or value in ("", ".", ".."):
        raise ValueError(f"not a {what}: {value!r}")
    return value


def bucket_dir(project: str, bucket: str) -> Path:
    """The directory one bucket's attachments live in.

    A bucket groups the images of one piece of work: a task's notebook id, a
    Linear issue identifier, ``agent-<id>`` for work tied to no task, or
    ``draft-<random>`` for a task that does not exist yet.

    Raises:
        ValueError: ``project`` or ``bucket`` is not a bare path segment.
    """
    # Looked up through the module rather than bound at import, so a test that
    # points ``tasks_root`` at a temp dir reaches this too.
    return (
        task_store.tasks_root()
        / _bare_name(project, "project")
        / "images"
        / _bare_name(bucket, "bucket")
    )


def save_attachment(project: str, bucket: str, data: bytes, *, name: str = "") -> str:
    """Write one image into the task repo and return its portable token.

    ``name`` is a hint only: it seeds the filename stem and backs up the
    extension sniff. Linear passes the URL's last segment (a UUID) so a
    re-localized brief keeps stable filenames; the UI passes the uploaded
    filename.

    The stem is de-duped against what is **already on disk** in the bucket, not
    against a set held for one call. Attachments arrive one request at a time,
    so an in-memory set would let a later upload overwrite an earlier one.

    A refused image leaves no directory behind: both guards run before the
    ``mkdir``.
    """
    from .task import MAEL_TASK_DIR_TOKEN

    if len(data) > MAX_BYTES:
        raise ValueError(
            f"attachment is too large: {len(data)} bytes, limit {MAX_BYTES}"
        )
    if not is_image(data):
        raise ValueError("attachment is not an image")

    ext = image_extension(name, data)
    stem = Path(name).stem or "image"
    directory = bucket_dir(project, bucket)
    filename = _free_name(directory, stem, ext)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(data)
    return f"{MAEL_TASK_DIR_TOKEN}/images/{bucket}/{filename}"


def _free_name(directory: Path, stem: str, ext: str) -> str:
    """A filename in ``directory`` that no file already uses."""
    filename = f"{stem}{ext}"
    counter = 1
    while (directory / filename).exists():
        filename = f"{stem}-{counter}{ext}"
        counter += 1
    return filename


def markdown_ref(alt: str, target: str) -> str:
    """A markdown image ref, so the callers that write one cannot drift."""
    return f"![{alt}]({target})"


def resolve_attachment(project: str, bucket: str, name: str) -> Path:
    """The path of one saved attachment, for serving its bytes back.

    ``name`` is a filename and never a path: it arrives from a URL, so a
    separator or a ``..`` segment would otherwise read any file the server can
    reach. Anything that is not a bare name is refused rather than sanitised,
    because a caller that meant a path has a bug worth hearing about.

    Raises:
        ValueError: the name is not a bare filename.
        KeyError: no such attachment.
    """
    found = bucket_dir(project, bucket) / _bare_name(name, "filename")
    if not found.is_file():
        raise KeyError(f"no attachment {name!r} in {project}/{bucket}")
    return found
