"""Storage layer for environment state.

Environment state is stored as a flat key->value store where keys are
POSIX-style relative paths under ``~/.maelstrom/envs/`` of the form
``<project>/<worktree>.json`` (per-worktree state) and ``<project>/_shared.json``
(project-wide shared state). The *value* is a JSON-serializable object (the
``asdict`` of an :class:`~maelstrom.env.EnvState` / ``SharedEnvState``); the
backend owns serialization, so the model never touches ``json`` for persistence.

Two backends are provided:

- :class:`InMemoryEnvStore` — a ``dict``-backed store with no filesystem; used by
  the env unit-test suite for fast, deterministic tests.
- :class:`JsonEnvStore` — maps keys to files under ``get_state_dir()`` and writes
  each value atomically (write-to-temp-then-``os.replace`` via
  :func:`maelstrom.util.atomic_write_json`), so a crash mid-write can never leave
  a truncated state file.

Unlike :class:`~maelstrom.task_store.TaskStore` there is no ``transaction()``:
env has no batched-multi-key call site, so the per-key atomic write is the whole
contract.
"""

import json
from pathlib import Path
from typing import Any, Protocol

from .context import get_maelstrom_dir
from .util import atomic_write_json


def get_state_dir() -> Path:
    """Return the directory for environment state files."""
    return get_maelstrom_dir() / "envs"


class EnvStore(Protocol):
    """A flat key->value store for environment state.

    Keys are POSIX relative paths (``<project>/<worktree>.json`` or
    ``<project>/_shared.json``). Values are JSON-serializable objects; persistent
    backends serialize them with ``indent=2, sort_keys=True`` so on-disk files
    have stable diffs and round-trip identically.
    """

    def read(self, key: str) -> Any | None:
        """Return the value stored at ``key``, or ``None`` if it does not exist."""
        ...

    def write(self, key: str, value: Any) -> None:
        """Store ``value`` at ``key`` (atomically for persistent backends)."""
        ...

    def delete(self, key: str) -> None:
        """Remove ``key``. A no-op if it does not exist."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        ...

    def list_dir(self, prefix: str) -> list[str]:
        """Return all keys that start with ``prefix``."""
        ...


class InMemoryEnvStore:
    """A ``dict``-backed :class:`EnvStore` with no filesystem.

    Stored values are deep-copied on the way in and out (via a JSON round-trip)
    so callers can't mutate persisted state through a shared reference — matching
    the load-fresh semantics of the persistent backend. ``read`` never sees
    malformed JSON because ``write`` is the only writer and always produces valid
    JSON, so (unlike :class:`JsonEnvStore`) it has no corrupt-data branch.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def read(self, key: str) -> Any | None:
        text = self._data.get(key)
        return None if text is None else json.loads(text)

    def write(self, key: str, value: Any) -> None:
        self._data[key] = json.dumps(value, sort_keys=True)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._data

    def list_dir(self, prefix: str) -> list[str]:
        return [k for k in self._data if k.startswith(prefix)]


class JsonEnvStore:
    """An :class:`EnvStore` backed by JSON files under ``root``.

    The root defaults to :func:`get_state_dir` (``~/.maelstrom/envs``) and is
    resolved lazily so test isolation that redirects ``get_maelstrom_dir`` is
    honoured. Every :meth:`write` goes through
    :func:`maelstrom.util.atomic_write_json`, so a crash mid-write can never leave
    a truncated file; the ``indent=2, sort_keys=True`` defaults keep on-disk JSON
    byte-identical to the previous direct ``json.dump`` writes.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else get_state_dir()

    def _path(self, key: str) -> Path:
        return self.root / key

    def read(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None

    def write(self, key: str, value: Any) -> None:
        atomic_write_json(self._path(key), value)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            return

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_dir(self, prefix: str) -> list[str]:
        if not self.root.is_dir():
            return []
        keys: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(prefix):
                keys.append(rel)
        return keys
