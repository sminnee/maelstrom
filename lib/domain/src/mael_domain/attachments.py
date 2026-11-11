"""Attachments in the attachments directory: images, videos and other files.

One mechanism for every file that reaches an agent, whatever brought it in.
An image downloaded from a Linear brief, a screenshot pasted into the
orchestrator UI and a JSON dump the user attached are the same thing once the
bytes are in hand: a file in the attachments directory, and a portable token in
the task's content. Only the source differs, so only the source lives with its
caller — the naming, de-duping, directory layout and token minting are here.

The token is :data:`~mael_domain.task.MAEL_TASK_DIR_TOKEN`, which
``task.build_prompt`` expands to an absolute path at launch. That is what lets
the agent ``Read`` the file while the stored content stays machine-independent.
"""

import hashlib
import re
from pathlib import Path
from typing import Literal

from mael_common.image import MAX_BYTES, image_extension, is_image

from .state_db import paths


def is_bare_name(value: object) -> bool:
    """Whether ``value`` is one path segment: no separator, and not ``.`` or ``..``."""
    return (
        isinstance(value, str)
        and value == Path(value).name
        and value not in ("", ".", "..")
    )


def _bare_name(value: str, what: str) -> str:
    """One path segment, or a refusal.

    Every segment of an attachment path arrives from a client: ``project`` and
    ``bucket`` off a multipart body or a URL, ``name`` off a URL. A separator or
    a ``..`` segment in any of them reaches outside the attachments directory, so all three
    are held to one rule. Refused rather than sanitised: a caller that meant a
    path has a bug worth hearing about.
    """
    if not is_bare_name(value):
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
    # points ``attachments_root`` at a temp dir reaches this too.
    return (
        paths.attachments_root()
        / _bare_name(project, "project")
        / "images"
        / _bare_name(bucket, "bucket")
    )


def save_attachment(project: str, bucket: str, data: bytes, *, name: str = "") -> str:
    """Write one image into the attachments directory and return its portable token.

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
    stem = _plain_stem(Path(name).stem, "image")
    directory = bucket_dir(project, bucket)
    filename = _free_name(directory, stem, ext)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(data)
    return f"{MAEL_TASK_DIR_TOKEN}/images/{bucket}/{filename}"


#: The largest image or video a Task document stores. A short screen recording
#: is under this; the cap is here so one capture cannot fill the attachments directory.
MAX_MEDIA_BYTES = 50 * 1024 * 1024


#: The kind of one saved upload: an image is embedded, a file is linked.
AttachmentKind = Literal["image", "file"]

#: An extension that is safe to keep: short and alphanumeric.
_SAFE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,10}")


def save_file(
    project: str, bucket: str, data: bytes, *, name: str
) -> tuple[AttachmentKind, str]:
    """Write one uploaded file into the attachments directory.

    An image goes through :func:`save_attachment`, under its 5 MB cap, because
    the agent daemon sends its bytes to the model. Any other file is capped at
    :data:`MAX_MEDIA_BYTES`: the agent reads it from disk, not from its context.

    The filename keeps ``name``'s stem, made plain by :func:`_plain_stem`, and
    its extension. An extension that is not short and alphanumeric becomes
    ``.bin``. The name is de-duped against the
    bucket on disk, as for an image.

    Returns:
        The kind, and the portable token.

    Raises:
        ValueError: the file is too large, or a path segment is not bare.
    """
    from .task import MAEL_TASK_DIR_TOKEN

    if is_image(data):
        return "image", save_attachment(project, bucket, data, name=name)
    if len(data) > MAX_MEDIA_BYTES:
        raise ValueError(
            f"attachment is too large: {len(data)} bytes, limit {MAX_MEDIA_BYTES}"
        )
    source = Path(Path(name).name) if is_bare_name(Path(name).name) else Path("file")
    suffix = source.suffix
    ext = suffix.lower() if _SAFE_EXTENSION.fullmatch(suffix) else ".bin"
    stem = _plain_stem(source.stem if suffix else source.name, "file")
    directory = bucket_dir(project, bucket)
    filename = _free_name(directory, stem, ext)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(data)
    return "file", f"{MAEL_TASK_DIR_TOKEN}/images/{bucket}/{filename}"


def file_label(name: str, size: int) -> str:
    """The label of a file's link: its name and a human size, as ``File: a.json, 412 KB``."""
    return f"File: {name}, {_human_size(size)}"


def _human_size(size: int) -> str:
    """``size`` bytes in B, KB or MB, base 1024."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


#: Plain text extensions served inline, always as ``text/plain``.
_INLINE_TEXT = frozenset(
    (".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".log", ".yaml", ".yml", ".toml")
)

#: Image and video extensions served inline, with their media types. No SVG:
#: see :func:`inline_media_type`.
_INLINE_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


def inline_media_type(path: Path) -> str | None:
    """The media type to serve ``path`` inline as, or ``None`` to serve a download.

    Only types that run no script are inline. HTML, SVG and any unknown type
    are a download: the route answers on the UI's origin, so a page there
    would run its script with the UI's rights.
    """
    ext = path.suffix.lower()
    if ext in _INLINE_TEXT:
        return "text/plain; charset=utf-8"
    return _INLINE_MEDIA.get(ext)


#: ISO-BMFF major brands that are still images. They share the ``ftyp`` box
#: with MP4 and QuickTime, and a browser's ``<video>`` cannot play them.
_IMAGE_BRANDS = (
    b"heic",
    b"heix",
    b"hevc",
    b"heim",
    b"heis",
    b"mif1",
    b"msf1",
    b"avif",
    b"avis",
)


def _video_extension(name: str, data: bytes) -> str | None:
    """The extension of a video sniffed from its bytes, or ``None`` for no video.

    WebM is EBML-framed. MP4 and QuickTime share the ``ftyp`` box, so the
    source's own extension tells them apart.
    """
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    if data[4:8] == b"ftyp" and data[8:12] not in _IMAGE_BRANDS:
        suffix = Path(name).suffix.lower()
        return suffix if suffix in (".mp4", ".mov") else ".mp4"
    return None


def save_media(project: str, bucket: str, source: Path) -> str:
    """Copy one image or video into the attachments directory and return its portable token.

    :func:`save_attachment` takes the bytes of an upload: an image, up to
    5 MB. This takes a file a Task document names: an image or a video, up
    to :data:`MAX_MEDIA_BYTES`.

    The filename carries a digest of the bytes. A document is attached once per
    version, so an unchanged file is found under its name and stored once,
    and two files that share a name, as every Playwright ``video.webm`` does,
    do not collide.

    Raises:
        ValueError: the file is too large, or is neither an image nor a video.
        OSError: the file cannot be read.
    """
    from .task import MAEL_TASK_DIR_TOKEN

    size = source.stat().st_size
    if size > MAX_MEDIA_BYTES:
        raise ValueError(f"media is too large: {size} bytes, limit {MAX_MEDIA_BYTES}")
    data = source.read_bytes()
    if is_image(data):
        ext = image_extension(source.name, data)
    else:
        video = _video_extension(source.name, data)
        if video is None:
            raise ValueError("media is neither an image nor a video")
        ext = video

    digest = hashlib.sha256(data).hexdigest()[:12]
    filename = f"{source.stem or 'media'}-{digest}{ext}"
    directory = bucket_dir(project, bucket)
    if not (directory / filename).is_file():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(data)
    return f"{MAEL_TASK_DIR_TOKEN}/images/{bucket}/{filename}"


#: The longest stem kept. A longer name would fail the write with
#: ``ENAMETOOLONG``, after the upload had passed every check.
_MAX_STEM = 100


def _plain_stem(stem: str, default: str) -> str:
    """``stem`` with each run of characters outside ``[A-Za-z0-9._-]`` as one ``-``.

    The token sits in a markdown link target and a URL path, where a space or
    a paren would end it. A screenshot's own name, ``Screen Shot 10.00.png``,
    has spaces.
    """
    plain = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)[:_MAX_STEM].strip(".-")
    return plain or default


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


def markdown_link(label: str, target: str) -> str:
    """A markdown link, with ``label`` escaped as :func:`markdown_ref` does."""
    return f"[{_escape_alt(label)}]({target})"


def _escape_alt(alt: str) -> str:
    """``alt`` with the characters that would end it or the ref made literal."""
    for char in ("\\", "[", "]", "(", ")"):
        alt = alt.replace(char, f"\\{char}")
    # A ref is one line. A newline would leave the tail of the alt behind it as
    # markdown of its own.
    return " ".join(alt.split())


#: The route that serves an attachment's bytes, under which each one is
#: ``<project>/<bucket>/<name>``.
ROUTE = "/api/attachments"


def attachment_url(project: str, bucket: str, name: str) -> str:
    """The URL a browser fetches one saved attachment from."""
    return f"{ROUTE}/{project}/{bucket}/{name}"


def attachment_urls(markdown: str, project: str) -> str:
    """``markdown`` with each attachment ref rewritten to the route that serves it.

    The token takes its project from ``project``; an absolute path under
    ``attachments_root()`` names its own. So does one under the old git notebook,
    ``<root>/tasks/``: a prompt sent before the media moved holds that path, and
    the transcript that keeps it is not ours to rewrite. Only the prefix of a
    ref's target changes:
    the route checks the bucket and the name when the browser asks. A path in
    prose or code stays as written, so the reader sees what was sent.
    """
    from .task import MAEL_TASK_DIR_TOKEN

    markdown = markdown.replace(
        f"]({MAEL_TASK_DIR_TOKEN}/images/", f"]({ROUTE}/{project}/"
    )
    roots = "|".join(
        re.escape(str(root))
        for root in (
            paths.attachments_root(),
            paths.notebook_path(paths.notebook_root()),
        )
    )
    return re.sub(rf"\]\((?:{roots})/([^/\s()]+)/images/", rf"]({ROUTE}/\1/", markdown)


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
