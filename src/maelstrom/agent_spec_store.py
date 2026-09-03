"""Storage layer for spawn records: what it takes to start each agent again.

One :class:`~maelstrom.agent_model.AgentSpec` per agent, keyed by agent id. See
``docs/dev/agent-daemon.md`` for why the record holds what it does.

Follows the storage-layer shape in ``docs/dev/architecture-patterns.md``: a
Protocol, an in-memory backend for tests, and a JSON-file backend.

Records outlive the daemon on purpose. A record still marked ``running`` when a
daemon starts is an agent that daemon should bring back; ``stop`` deletes the
record, so a deliberate stop is not resumed.
"""

import json
from pathlib import Path
from typing import Protocol

from .agent_model import AgentSpec, spec_from_dict, spec_to_dict
from .context import get_maelstrom_dir
from .util import atomic_write_json, harden_path


def get_spec_dir() -> Path:
    """The directory holding spawn records, beside the socket and the log."""
    return get_maelstrom_dir() / "agents"


class AgentSpecStore(Protocol):
    """The spawn records on this machine, keyed by agent id."""

    def read(self, agent_id: str) -> AgentSpec | None:
        """The record for ``agent_id``, or ``None`` when there is none."""
        ...

    def write(self, spec: AgentSpec) -> None:
        """Store ``spec``, replacing any record with the same agent id."""
        ...

    def delete(self, agent_id: str) -> None:
        """Remove ``agent_id``'s record. A no-op when there is none."""
        ...

    def list(self) -> list[AgentSpec]:
        """Every stored record."""
        ...


class InMemoryAgentSpecStore:
    """A ``dict``-backed :class:`AgentSpecStore` with no filesystem.

    Records are frozen dataclasses, so there is nothing to deep-copy: a caller
    cannot mutate a stored record through the reference it gets back.
    """

    def __init__(self) -> None:
        self._data: dict[str, AgentSpec] = {}

    def read(self, agent_id: str) -> AgentSpec | None:
        return self._data.get(agent_id)

    def write(self, spec: AgentSpec) -> None:
        self._data[spec.agent_id] = spec

    def delete(self, agent_id: str) -> None:
        self._data.pop(agent_id, None)

    def list(self) -> list[AgentSpec]:
        return list(self._data.values())


class JsonAgentSpecStore:
    """An :class:`AgentSpecStore` backed by ``<root>/<agent_id>.json``.

    The root defaults to :func:`get_spec_dir` (``~/.maelstrom/agents``) and is
    resolved lazily, so test isolation that redirects ``get_maelstrom_dir`` is
    honoured. One file per agent, written through
    :func:`maelstrom.util.atomic_write_json` — a crash mid-write cannot leave a
    record a restart would then fail to read.

    A file that will not parse is skipped rather than raised: one truncated
    record must not stop the daemon restoring the agents whose records survived.

    Records are written owner-only. A record holds the caller's ``env``
    verbatim, and the socket contract puts no allowlist on it, so a secret a
    client passes to ``start`` must not land world-readable — it only lived in
    process memory before the record existed.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else get_spec_dir()

    def _path(self, agent_id: str) -> Path:
        return self.root / f"{agent_id}.json"

    def read(self, agent_id: str) -> AgentSpec | None:
        return self._load(self._path(agent_id))

    def write(self, spec: AgentSpec) -> None:
        path = self._path(spec.agent_id)
        atomic_write_json(path, spec_to_dict(spec))
        # After the write, not before: `atomic_write_json` replaces the file, so
        # a mode set on the old one would not survive.
        for target, mode in ((self.root, 0o700), (path, 0o600)):
            try:
                harden_path(target, mode)
            except OSError:
                pass  # a record the daemon can still read is better than none

    def delete(self, agent_id: str) -> None:
        try:
            self._path(agent_id).unlink()
        except FileNotFoundError:
            return

    def list(self) -> list[AgentSpec]:
        if not self.root.is_dir():
            return []
        specs = [self._load(path) for path in sorted(self.root.glob("*.json"))]
        return [spec for spec in specs if spec is not None]

    @staticmethod
    def _load(path: Path) -> AgentSpec | None:
        try:
            with open(path) as handle:
                return spec_from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
