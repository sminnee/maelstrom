"""The cost report: what an agent spent, and where the spend went.

Model layer, per ``docs/dev/architecture-patterns.md``: the report is built
from rows a caller has already read, so it is exercisable without a database
and the CLI stays a printer.
"""

import asyncio

from maelstrom.agent_cost import build_cost_report
from maelstrom.agent_store import InMemoryMilestoneStore


async def seeded(*snapshots: tuple[str, str, int, int, float]) -> list[dict]:
    """A ledger holding ``snapshots``: agent, stage, own, subagent, dollars."""
    store = InMemoryMilestoneStore()
    for agent_id, name, own, sub, cost in snapshots:
        await store.record(
            {
                "agent_id": agent_id,
                "name": name,
                "at": "2026-09-21T10:00:00Z",
                "recognised": True,
                "own_tokens": own,
                "subagent_tokens": sub,
                "cost_usd": cost,
            }
        )
    return await store.list()


THREE_STAGES = (
    ("a1", "planned", 10_000, 0, 0.5),
    ("a1", "built", 60_000, 20_000, 2.0),
    ("a1", "green", 75_000, 30_000, 2.6),
)


def test_the_report_states_what_an_agent_spent_and_where():
    """The whole shape, so a new key cannot join it untested."""
    rows = asyncio.run(seeded(("a1", "green", 40_000, 15_000, 1.25)))
    assert build_cost_report(rows) == [
        {
            "agent_id": "a1",
            "own_tokens": 40_000,
            # Disjoint from the above, so the total is their sum.
            "subagent_tokens": 15_000,
            "total_tokens": 55_000,
            "cost_usd": 1.25,
            # No price table, so a subagent's spend is never in dollars.
            "cost_is_parent_only": True,
            "stages": [
                {
                    "name": "green",
                    "at": "2026-09-21T10:00:00Z",
                    "recognised": True,
                    "total_tokens": 55_000,
                    "delta_tokens": 55_000,
                    "own_delta": 40_000,
                    "subagent_delta": 15_000,
                    "cost_delta": 1.25,
                }
            ],
        }
    ]


def test_each_stage_reports_its_delta_and_the_running_total():
    """Where the burn went: a stage's own cost, beside the total by then."""
    [agent] = build_cost_report(asyncio.run(seeded(*THREE_STAGES)))
    assert [s["name"] for s in agent["stages"]] == ["planned", "built", "green"]
    assert [s["delta_tokens"] for s in agent["stages"]] == [10_000, 70_000, 25_000]
    assert [s["total_tokens"] for s in agent["stages"]] == [10_000, 80_000, 105_000]


def test_the_stage_deltas_sum_to_the_agents_total():
    """The property the report exists to hold up."""
    [agent] = build_cost_report(asyncio.run(seeded(*THREE_STAGES)))
    assert sum(s["delta_tokens"] for s in agent["stages"]) == agent["total_tokens"]


def test_two_agents_are_reported_separately():
    rows = asyncio.run(
        seeded(("a1", "green", 40_000, 0, 1.0), ("a2", "green", 8_000, 0, 0.2))
    )
    report = build_cost_report(rows)
    assert [a["agent_id"] for a in report] == ["a1", "a2"]
    assert [a["total_tokens"] for a in report] == [40_000, 8_000]


def test_an_unrecognised_stage_is_reported_and_flagged():
    """Never dropped: a typo must be visible rather than costing a snapshot."""
    store = InMemoryMilestoneStore()

    async def scenario():
        await store.record(
            {
                "agent_id": "a1",
                "name": "deployed",
                "at": "2026-09-21T10:00:00Z",
                "recognised": False,
                "own_tokens": 5_000,
                "subagent_tokens": 0,
                "cost_usd": 0.1,
            }
        )
        return await store.list()

    [agent] = build_cost_report(asyncio.run(scenario()))
    [stage] = agent["stages"]
    assert stage["name"] == "deployed"
    assert stage["recognised"] is False


def test_an_empty_ledger_reports_no_agents():
    assert build_cost_report([]) == []
