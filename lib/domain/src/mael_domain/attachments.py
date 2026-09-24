"""Image attachments in the task repo.

One mechanism for every image that reaches an agent, whatever brought it in.
An image downloaded from a Linear brief and a screenshot pasted into the
orchestrator UI are the same thing once the bytes are in hand: a file in the
git-backed task repo, and a portable token in the task's content. Only the
source differs, so only the source lives with its caller — the naming,
de-duping, directory layout and token minting are here.

The token is :data:`~mael_domain.task.MAEL_TASK_DIR_TOKEN`, which
``task.build_prompt`` expands to an absolute path at launch. That is what lets
the agent ``Read`` the file while the stored ``.md`` stays machine-independent.

Files are written untracked; the caller's next notebook commit sweeps them in
via ``git add -A``.
"""

from pathlib import Path

from mael_common.image import MAX_BYTES, image_extension, is_image

from . import task_store


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
    """A markdown image ref, so the callers that write one cannot drift.

    ``alt`` is escaped. It comes from an agent's tag or an uploaded filename,
    so a ``]`` in it would close the ref early and let the rest of the text
    name any URL the browser would then fetch.
    """
    return f"![{_escape_alt(alt)}]({target})"


def _escape_alt(alt: str) -> str:
    """``alt`` with the characters that would end it or the ref made literal."""
    for char in ("\\", "[", "]", "(", ")"):
        alt = alt.replace(char, f"\\{char}")
    # A ref is one line. A newline would leave the tail of the alt behind it as
    # markdown of its own.
    return " ".join(alt.split())


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
