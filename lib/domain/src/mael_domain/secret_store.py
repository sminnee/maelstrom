"""Storage for the **Secret store**: the sealed documents, and nothing that opens them.

A flat key->value store. A value is a JSON object that
:mod:`mael_domain.secrets` builds: the header, which holds the wrapped store
key, or the sealed secrets of one scope. No value holds a plaintext secret or
the unlocked key, so a backend needs no cipher.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol

from mael_common.util import harden_path

from .notebook_root import notebook_root

#: The folder under the notebook root that holds the store's files.
SECRETS_DIR = "secrets"


def secrets_dir(root: Path | None = None) -> Path:
    """The folder of the store's files.

    ``root`` defaults to :func:`~mael_domain.notebook_root.notebook_root`, so a
    dev orchestrator never opens the production store.
    """
    return (root if root is not None else notebook_root()) / SECRETS_DIR


class SecretStore(Protocol):
    """Sealed documents by key. A key is a POSIX relative path with no suffix."""

    def read(self, key: str) -> dict[str, Any] | None:
        """The document at ``key``, or ``None`` when there is none."""
        ...

    def write(self, key: str, value: dict[str, Any]) -> None:
        """Store ``value`` at ``key``, atomically for a persistent backend."""
        ...

    def keys(self) -> list[str]:
        """Each key that holds a document, sorted."""
        ...


class InMemorySecretStore(SecretStore):
    """A ``dict``-backed :class:`SecretStore` with no filesystem."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def read(self, key: str) -> dict[str, Any] | None:
        text = self._data.get(key)
        return None if text is None else json.loads(text)

    def write(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = json.dumps(value, sort_keys=True)

    def keys(self) -> list[str]:
        return sorted(self._data)


class FileSecretStore(SecretStore):
    """One JSON file per key under ``directory``, each private to the user.

    A file has mode 0600 from its first byte, and each folder has 0700. A
    write goes to a temporary file, synced to disk, that then replaces the old
    one. A crash or a power loss leaves the old document or the new one whole.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(key).read_text())
        except FileNotFoundError:
            return None

    def write(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        for folder in (self.directory, path.parent):
            folder.mkdir(parents=True, exist_ok=True)
            harden_path(folder, 0o700)
        # mkstemp makes the file 0600, under a name of its own.
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(value, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        folder_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder_fd)
        finally:
            os.close(folder_fd)

    def keys(self) -> list[str]:
        return sorted(
            path.relative_to(self.directory).with_suffix("").as_posix()
            for path in self.directory.rglob("*.json")
        )
