"""Canonical Agent records."""

import json
from typing import Any, Protocol

from .harness_model import HARNESS_CLAUDE
from .state_db.db import StateDb


class AgentStore(Protocol):
    """The canonical records for Agents Maelstrom has started."""

    async def save(self, agent: dict[str, Any]) -> None: ...

    async def list(self) -> list[dict[str, Any]]: ...

    async def remove(self, agent_id: str) -> None: ...


class SqliteAgentStore:
    """Store the Agents Maelstrom has started."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def save(self, agent: dict[str, Any]) -> None:
        await self._db.upsert(
            "agents", str(agent["id"]), body=json.dumps(agent, sort_keys=True)
        )

    async def list(self) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        for row in await self._db.read_all("agents"):
            try:
                agent = json.loads(row["body"])
            except json.JSONDecodeError:
                continue
            if isinstance(agent, dict) and agent.get("id") == row["id"]:
                agents.append(agent)
        return agents

    async def remove(self, agent_id: str) -> None:
        await self._db.delete("agents", agent_id)


async def register_agent(
    store: AgentStore, agent_id: str, row: dict[str, Any], task_id: str
) -> None:
    """Adopt a live agent that has no Agent record: ``row`` is its daemon ``list`` entry.

    ``mael agent register`` reaches only the Claude agent daemon, so the
    harness is always ``claude``.
    """
    await store.save(
        {
            "id": agent_id,
            "harness": HARNESS_CLAUDE,
            "task_session_id": row.get("session", ""),
            "task_id": task_id,
            "cwd": row.get("cwd", ""),
            "model": row.get("model", ""),
            "mode": row.get("mode") or "normal",
        }
    )
