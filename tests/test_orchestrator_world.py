"""``WorldState``: the server's world, changed only by applying events."""

import pytest

from maelstrom.orchestrator.world import WorldState


def upsert(task_id: str, status: str = "todo") -> dict:
    return {
        "type": "upsert",
        "kind": "task",
        "entity": {"id": task_id, "status": status},
    }


def test_apply_puts_each_upsert_in_its_table_and_a_remove_takes_it_out():
    state = WorldState()
    state.apply([upsert("T-1"), upsert("T-2")])
    assert set(state.world["tasks"]) == {"T-1", "T-2"}
    state.apply(
        [upsert("T-1", "done"), {"type": "remove", "kind": "task", "id": "T-2"}]
    )
    assert state.world["tasks"] == {"T-1": {"id": "T-1", "status": "done"}}


def test_an_earlier_state_is_not_changed_by_a_later_apply():
    state = WorldState()
    state.apply([upsert("T-1")])
    before = state.state
    state.apply([upsert("T-2")])
    assert set(before["world"]["tasks"]) == {"T-1"}


def test_transcript_events_leave_the_world_alone():
    state = WorldState()
    state.apply([{"type": "transcript.append", "agentId": "ag1", "item": {"id": "i1"}}])
    assert state.world["agents"] == {}


def test_an_unknown_event_is_a_protocol_bug():
    with pytest.raises(ValueError, match="Unknown server event"):
        WorldState().apply([{"type": "snapshot", "world": {}}])
