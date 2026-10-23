"""The key that signs a **Live feedback** URL: one for each notebook, in its root.

Why the URL is signed, and what it signs, is in ``docs/dev/live-feedback.md``.
"""

import hashlib
import hmac
import os
import secrets
import tempfile

from mael_domain.notebook_root import notebook_root

KEY_FILE = "live-feedback.key"
KEY_BYTES = 32


def sign(path: str, service: str) -> str:
    """The hex signature of a live feedback URL's ``path`` and ``service``."""
    message = f"{path}\0{service}".encode()
    return hmac.new(_key(), message, hashlib.sha256).hexdigest()


def verify(path: str, service: str, sig: str) -> bool:
    """Whether ``sig`` is this notebook's signature of ``path`` and ``service``."""
    # Bytes: on a str with a non-ASCII character, compare_digest raises.
    return hmac.compare_digest(sign(path, service).encode(), sig.encode())


def _key() -> bytes:
    """The notebook's key, made on first use.

    The CLI and the orchestrator can race to make it. The key is written to a
    file of its own and linked into place, so the link decides and no reader
    sees a half-written key: the loser reads the winner's.
    """
    root = notebook_root()
    path = root / KEY_FILE
    try:
        return _checked(path.read_bytes(), path)
    except FileNotFoundError:
        pass
    key = secrets.token_bytes(KEY_BYTES)
    root.mkdir(parents=True, exist_ok=True)
    fd, draft = tempfile.mkstemp(dir=root, prefix=f".{KEY_FILE}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
        os.link(draft, path)
    except FileExistsError:
        return _checked(path.read_bytes(), path)
    finally:
        os.unlink(draft)
    return key


def _checked(key: bytes, path: os.PathLike) -> bytes:
    """``key``, or a raise when it is short: an empty key signs what anyone can sign."""
    if len(key) != KEY_BYTES:
        raise ValueError(f"{path} holds {len(key)} bytes, not a {KEY_BYTES}-byte key")
    return key
