"""Storage layer for the task notebook.

Tasks are stored as a flat key->text store where keys are POSIX-style relative
paths of the form ``<project>/<status>/<id>.md``. The folder is the status.

Two backends are provided:

- ``InMemoryStore`` — a ``dict``-backed store with no git or filesystem; used by
  the task unit-test suite for fast, deterministic tests.
- ``GitFileStore`` — maps keys to files under a root directory and commits every
  mutation to a git repo (lazily initialised), so the task notebook is fully
  versioned.
"""

import subprocess
from pathlib import Path
from typing import Protocol

from .context import get_maelstrom_dir


class TaskStore(Protocol):
    """A flat key->text store.

    Keys are POSIX relative paths (``<project>/<status>/<id>.md``). The
    ``message`` argument on mutating operations is the commit subject for
    backends that version their contents.
    """

    def list_dir(self, prefix: str) -> list[str]:
        """Return all keys that start with ``prefix``."""
        ...

    def read(self, key: str) -> str | None:
        """Return the text stored at ``key``, or ``None`` if it does not exist."""
        ...

    def write(self, key: str, text: str, *, message: str) -> None:
        """Store ``text`` at ``key``, recording ``message`` as the commit subject."""
        ...

    def delete(self, key: str, *, message: str) -> None:
        """Remove ``key``, recording ``message`` as the commit subject."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        ...


class InMemoryStore:
    """A ``dict``-backed :class:`TaskStore` with no git or filesystem."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def list_dir(self, prefix: str) -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]

    def read(self, key: str) -> str | None:
        return self._data.get(key)

    def write(self, key: str, text: str, *, message: str) -> None:
        self._data[key] = text

    def delete(self, key: str, *, message: str) -> None:
        self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._data


class GitFileStore:
    """A :class:`TaskStore` backed by files under ``root``, versioned with git.

    The git repo is initialised lazily on first mutation, with a local
    ``user.name``/``user.email`` so it works in CI environments without global
    git config. Every ``write``/``delete`` stages all changes and commits with
    the provided message; a commit with nothing staged is a no-op (not an error).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_maelstrom_dir() / "tasks"

    # --- private git helpers (quiet, non-raising) ---

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        """Run a git command in ``root``, quietly, without raising on failure."""
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=False,
            capture_output=True,
            text=True,
        )

    def _ensure_repo(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init")
            self._git("config", "user.name", "maelstrom")
            self._git("config", "user.email", "maelstrom@localhost")

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        # Commit only if there is something staged; otherwise this is a no-op.
        if self._git("diff", "--cached", "--quiet").returncode != 0:
            self._git("commit", "-m", message, "--no-verify")

    def _path(self, key: str) -> Path:
        return self.root / key

    # --- TaskStore protocol ---

    def list_dir(self, prefix: str) -> list[str]:
        if not self.root.is_dir():
            return []
        keys: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(".git/") or rel == ".git":
                continue
            if rel.startswith(prefix):
                keys.append(rel)
        return keys

    def read(self, key: str) -> str | None:
        path = self._path(key)
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return None

    def write(self, key: str, text: str, *, message: str) -> None:
        self._ensure_repo()
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self._commit(message)

    def delete(self, key: str, *, message: str) -> None:
        self._ensure_repo()
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self._commit(message)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
