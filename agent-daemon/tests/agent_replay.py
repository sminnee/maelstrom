"""The reducer replay that the daemon and CLI suites share."""

import dataclasses
import json
from pathlib import Path
from typing import Any

from mael_daemon.agent_model import AgentState, apply_event

FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_events"
STATES = Path(__file__).parents[1] / "fixtures" / "agent_state"


def replay(name: str, stop_before_control: bool = False) -> AgentState:
    """Feed one fixture through the reducer and return the final state.

    With ``stop_before_control`` the replay halts on the first ``control_request``
    without answering it — that is what the daemon sees while an agent waits.
    """
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        state = apply_event(state, event)
        if stop_before_control and event.get("type") == "control_request":
            break
    return state


def state_snapshot(state: AgentState) -> dict[str, Any]:
    """``state`` as plain JSON, for a golden to hold.

    ``recent``, the agent's and each subagent's, is cut to each event's type:
    the raw events are the fixture itself, and copying them would make the
    golden repeat it.
    """
    snapshot = dataclasses.asdict(state)
    for holder in (snapshot, *snapshot["subagents"].values()):
        holder["recent"] = [event.get("type") for event in holder["recent"]]
    return json.loads(json.dumps(snapshot, sort_keys=True))
