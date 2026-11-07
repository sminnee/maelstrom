"""The API key files: one per scope, under the notebook root, mode 0600.

The server mints the keys at start and writes them here. A client on the host
reads its key from here. Access to the host shell is the root of trust.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from ..notebook_root import notebook_root
from .model import SCOPES, ApiKey, Scope, hash_secret, new_key
from .store import AuthStore

#: The folder under the notebook root that holds the key files.
AUTH_DIR = "auth"


def key_path(scope: Scope, root: Path | None = None) -> Path:
    """The file that holds the key of ``scope``.

    ``root`` defaults to :func:`~mael_domain.notebook_root.notebook_root`.
    """
    return (root if root is not None else notebook_root()) / AUTH_DIR / f"{scope}.key"


def read_key(scope: Scope, root: Path | None = None) -> str | None:
    """The key of ``scope`` from its file, or ``None`` when there is no file."""
    try:
        return key_path(scope, root).read_text().strip() or None
    except FileNotFoundError:
        return None


async def ensure_keys(store: AuthStore, root: Path | None = None) -> list[Scope]:
    """Mint the key of each scope whose file is missing or not in ``store``.

    A new key replaces the old key of its scope, so a deleted file revokes the
    key it held. Returns the scopes that got a new key.
    """
    minted: list[Scope] = []
    for scope in SCOPES:
        key = read_key(scope, root)
        stored = await store.read_api_key(scope)
        if key is not None and stored is not None and stored.hash == hash_secret(key):
            continue
        key = new_key()
        _write_private(key_path(scope, root), key + "\n")
        await store.save_api_key(
            ApiKey(
                hash=hash_secret(key),
                scope=scope,
                created=datetime.now(timezone.utc).isoformat(),
            )
        )
        minted.append(scope)
    return minted


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` with mode 0600, from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(text)
    tmp.replace(path)
