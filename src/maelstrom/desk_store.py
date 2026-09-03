"""Storage layer for the desk.

The desk is the set of tasks the user has put on the canvas. It is one table
keyed by wire task id, so unlike :mod:`maelstrom.env_store` there is no key
space: :meth:`load` and :meth:`save` move the whole table.

Two backends are provided:

- :class:`InMemoryDeskStore` — a store with no filesystem, for tests.
- :class:`JsonDeskStore` — one file, written atomically through
  :func:`maelstrom.util.atomic_write_json`, so a crash mid-write can never
  leave a truncated desk.

A per-user desk later becomes ``desks/<user>.json``, without changing the
file's format.
"""

import json
import logging
from pathlib import Path
from typing import Any, Protocol

from .context import get_maelstrom_dir
from .util import atomic_write_json

log = logging.getLogger(__name__)

#: The stored desk: wire task id to that task's entry. The entry's own shape is
#: the wire's, which this layer neither reads nor names.
DeskTable = dict[str, Any]


def get_desk_path() -> Path:
    """Where the desk is kept."""
    return get_maelstrom_dir() / "desk.json"


class DeskStore(Protocol):
    """The desk table, loaded and saved whole."""

    def load(self) -> DeskTable:
        """The stored table. ``{}`` when there is none, or it cannot be read."""
        ...

    def save(self, table: DeskTable) -> None:
        """Store ``table``, replacing whatever was there."""
        ...


class InMemoryDeskStore:
    """A :class:`DeskStore` with no filesystem.

    The table is copied on the way in and out through a JSON round trip, so a
    caller cannot change stored state through a shared reference — the same
    load-fresh semantics the persistent backend has.
    """

    def __init__(self) -> None:
        self._text = "{}"

    def load(self) -> DeskTable:
        return json.loads(self._text)

    def save(self, table: DeskTable) -> None:
        self._text = json.dumps(table, sort_keys=True)


class JsonDeskStore:
    """A :class:`DeskStore` backed by one JSON file.

    The path defaults to :func:`get_desk_path` and is resolved lazily, so a
    test that redirects ``get_maelstrom_dir`` is honoured. A file that cannot
    be read loads as an empty desk, logged: a desk is a convenience, and
    refusing to start over a corrupt one would help nobody. The log is what
    tells an unreadable desk apart from no desk at all, because the next save
    writes over whatever could not be read.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else get_desk_path()

    def load(self) -> DeskTable:
        try:
            with open(self.path) as f:
                table = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            log.warning("desk at %s could not be read", self.path, exc_info=True)
            return {}
        if not isinstance(table, dict):
            return {}
        # The file is state a user can edit, so an entry the wire would refuse
        # is dropped here rather than published to every client.
        return {k: v for k, v in table.items() if _is_entry(v)}

    def save(self, table: DeskTable) -> None:
        atomic_write_json(self.path, table)


def _is_entry(value: Any) -> bool:
    """Whether ``value`` is a desk entry the wire can carry."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("addedAt"), str)
    )
