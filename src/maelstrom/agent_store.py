"""Canonical Agent records."""

import json
from typing import Any, Protocol

from .harness_model import HARNESS_CLAUDE
from .state_db.db import StateDb


class AgentStore(Protocol):
    """The canonical records for Agents Maelstrom has started."""

    async def save(self, agent: dict[str, Any]) -> None: ...

    async def list(self) -> list[dict[str, Any]]: ...


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


class MilestoneStore(Protocol):
    """The ledger of what each agent had spent at each stage of the work."""

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]: ...

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]: ...


class SqliteMilestoneStore:
    """Store one snapshot per stage an agent reaches, with its delta.

    The delta is computed on write, against the agent's previous snapshot: the
    caller has the totals and no reason to read the ledger back.
    """

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]:
        """Write one snapshot, its delta included, and return it.

        A stage reached twice writes two rows rather than replacing one: the
        second run spent real tokens, and collapsing them would hide the spend.

        The row comes back because the caller has no other way to learn what
        the stage cost: the delta is computed here, against a previous
        snapshot only this store holds.
        """
        previous = await self.list(str(milestone["agent_id"]))
        row = _snapshot(
            milestone, previous[-1] if previous else None, len(previous) + 1
        )
        await self._db.upsert(
            "agent_milestones",
            row["id"],
            **{key: value for key, value in row.items() if key != "id"},
        )
        return _milestone_row(row)

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]:
        """Every snapshot, oldest first. One agent's with ``agent_id``, else all.

        The order is the order the stages were reached in, because the row id
        carries the ordinal and both reads sort by id.

        One agent's rows go through ``read_where``, which uses the table's
        ``agent_id`` index. :meth:`record` reads this on every write, so a scan
        here would make recording a milestone cost the whole ledger's history.
        """
        rows = (
            await self._db.read_where("agent_milestones", "agent_id", agent_id)
            if agent_id
            else await self._db.read_all("agent_milestones")
        )
        return [_milestone_row(row) for row in rows]


class InMemoryMilestoneStore:
    """A :class:`MilestoneStore` with no database, for a test and a bare server.

    Holds the rows :class:`SqliteMilestoneStore` would have written, in the
    same shape, so a reader cannot tell the two apart.
    """

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(milestone["agent_id"])
        previous = [row for row in self._rows if row["agent_id"] == agent_id]
        row = _snapshot(
            milestone, previous[-1] if previous else None, len(previous) + 1
        )
        self._rows.append(row)
        # Through `_milestone_row`, as `list` reads back and as the SQLite
        # backend returns: the two must stay indistinguishable.
        return _milestone_row(row)

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]:
        # Through `_milestone_row`, as the SQLite backend reads back: a caller
        # must not be able to tell the two apart. `recognised` is the field
        # that would otherwise differ, stored as an int and read as a bool.
        return [
            _milestone_row(row)
            for row in self._rows
            if not agent_id or row["agent_id"] == agent_id
        ]


def _snapshot(
    milestone: dict[str, Any], previous: dict[str, Any] | None, ordinal: int
) -> dict[str, Any]:
    """One ledger row: the cumulative figures, and the delta since ``previous``.

    The shape both backends store and :func:`_milestone_row` reads back, built
    here so the two cannot drift apart.
    """
    agent_id = str(milestone["agent_id"])
    own = _count(milestone.get("own_tokens"))
    sub = _count(milestone.get("subagent_tokens"))
    cost = float(milestone.get("cost_usd") or 0.0)
    return {
        "id": _milestone_id(agent_id, ordinal),
        "agent_id": agent_id,
        "name": str(milestone.get("name") or ""),
        "at": str(milestone.get("at") or ""),
        "recognised": 1 if milestone.get("recognised", True) else 0,
        "own_total": own,
        "sub_total": sub,
        "cost_usd": cost,
        "own_delta": _delta(own, _count_of(previous, "own_total")),
        "sub_delta": _delta(sub, _count_of(previous, "sub_total")),
        "cost_delta": max(cost - float(previous["cost_usd"] if previous else 0.0), 0.0),
    }


def _milestone_row(row: Any) -> dict[str, Any]:
    """One stored snapshot, in the shape the model and the report read."""
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "name": row["name"],
        "at": row["at"],
        "recognised": bool(row["recognised"]),
        "own_total": row["own_total"],
        "sub_total": row["sub_total"],
        "cost_usd": row["cost_usd"],
        "own_delta": row["own_delta"],
        "sub_delta": row["sub_delta"],
        "cost_delta": row["cost_delta"],
    }


def _milestone_id(agent_id: str, ordinal: int) -> str:
    """The row id: the agent and a zero-padded ordinal.

    Padded because ``read_all`` sorts by id as text, and ``10`` must not sort
    before ``2``.
    """
    return f"{agent_id}#{ordinal:04d}"


def _count(value: Any) -> int:
    """A token figure off a caller's dict. Anything that is not a count is 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _count_of(row: dict[str, Any] | None, key: str) -> int:
    return _count(row[key]) if row else 0


def _delta(now: int, before: int) -> int:
    """What was spent between two readings.

    Never negative: a daemon restart puts a running total back to 0, and a
    negative delta would read as a stage that gave tokens back.
    """
    return max(now - before, 0)
