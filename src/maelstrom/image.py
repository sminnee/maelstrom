"""What counts as an image, sniffed from its bytes.

A leaf: the task repo's attachments and the agent daemon's image messages both
accept the same formats under the same size cap.
"""

from pathlib import Path

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
