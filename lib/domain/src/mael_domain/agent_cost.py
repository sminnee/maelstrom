"""Where an agent's token spend went, stage by stage.

Model layer, per ``docs/dev/architecture-patterns.md``: a pure function over
ledger rows a caller has already read, so ``mael agent cost`` is left a printer.

Each row is cumulative *and* a delta: the cumulative figure says what the agent
had spent by that stage, the delta what the stage itself cost. The deltas sum to
the total, which is what makes the report answer "where did the burn go".

``$`` is parent-only and a subagent's spend is in tokens — see
``docs/dev/agent-daemon.md``, "A turn".
"""

from typing import Any, TypedDict


class Stage(TypedDict):
    """One milestone: what the agent had spent, and what the stage cost."""

    name: str
    at: str
    #: Whether the name is one the flow declares. A name outside it is reported
    #: as the agent wrote it, so a typo is visible rather than silently lost.
    recognised: bool
    #: Own and subagent tokens by this stage, summed.
    total_tokens: int
    #: What this stage alone consumed, own and subagent.
    delta_tokens: int
    own_delta: int
    subagent_delta: int
    cost_delta: float


class AgentCost(TypedDict):
    """One agent's spend, and the stages it passed through."""

    agent_id: str
    own_tokens: int
    subagent_tokens: int
    #: The tree's total: the two figures above, summed. They are disjoint.
    total_tokens: int
    cost_usd: float
    #: Always ``True``: the dollar figure covers the agent's own requests and
    #: not its subagents'. A ``--json`` reader reads it rather than assuming.
    cost_is_parent_only: bool
    stages: list[Stage]


def build_cost_report(rows: list[dict[str, Any]]) -> list[AgentCost]:
    """What each agent in ``rows`` spent, and where.

    Agents come out in the order the ledger first names them, and each one's
    stages in the order they were reached — which is the order the rows arrive
    in, because the ledger's ids carry the ordinal.

    An agent's totals are its last snapshot's, not a sum of its deltas: the
    snapshot is the reading the host gave, where a sum could drift if a
    milestone were ever recorded out of order.
    """
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_agent.setdefault(str(row["agent_id"]), []).append(row)
    return [_agent_cost(agent_id, snaps) for agent_id, snaps in by_agent.items()]


def empty_cost_report(agent_id: str) -> AgentCost:
    """The report for an agent whose ledger is empty: zeroed, with no stages.

    Beside :func:`build_cost_report` so the two constructions of
    :class:`AgentCost` stay in step.
    """
    return {
        "agent_id": agent_id,
        "own_tokens": 0,
        "subagent_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_is_parent_only": True,
        "stages": [],
    }


def _agent_cost(agent_id: str, snapshots: list[dict[str, Any]]) -> AgentCost:
    last = snapshots[-1]
    own, sub = last["own_total"], last["sub_total"]
    return {
        "agent_id": agent_id,
        "own_tokens": own,
        "subagent_tokens": sub,
        "total_tokens": own + sub,
        "cost_usd": float(last["cost_usd"]),
        "cost_is_parent_only": True,
        "stages": [_stage(snapshot) for snapshot in snapshots],
    }


def _stage(snapshot: dict[str, Any]) -> Stage:
    own_delta, sub_delta = snapshot["own_delta"], snapshot["sub_delta"]
    return {
        "name": str(snapshot["name"]),
        "at": str(snapshot["at"]),
        "recognised": bool(snapshot["recognised"]),
        "total_tokens": snapshot["own_total"] + snapshot["sub_total"],
        "delta_tokens": own_delta + sub_delta,
        "own_delta": own_delta,
        "subagent_delta": sub_delta,
        "cost_delta": float(snapshot["cost_delta"]),
    }
