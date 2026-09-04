"""``apply_event``: the one way the orchestrator server's world changes."""

import pytest

from maelstrom.orchestrator.protocol import (
    apply_event,
    empty_world,
    initial_client_state,
    state_with,
)

NOW = "2026-09-01T00:00:00Z"


def make_task(**over):
    task = {
        "id": "NORT-7",
        "notebookId": "NORT-7",
        "project": "northwind",
        "title": "Add order export",
        "status": "todo",
        "command": "",
        "mode": "auto",
        "branch": "feat/orders",
        "parent": "",
        "follows": [],
        "priority": "normal",
        "model": "",
        "base": "",
        "content": "",
        "steps": [],
        "log": [],
        "created": NOW,
        "updated": NOW,
        "actionable": True,
    }
    task.update(over)
    return task


def test_an_upsert_adds_and_replaces_whole():
    state = initial_client_state()
    state = apply_event(
        state, {"type": "upsert", "kind": "task", "entity": make_task()}
    )
    assert state["world"]["tasks"]["NORT-7"]["status"] == "todo"
    state = apply_event(
        state, {"type": "upsert", "kind": "task", "entity": make_task(status="done")}
    )
    assert state["world"]["tasks"]["NORT-7"]["status"] == "done"


def test_remove_deletes_by_kind_and_id():
    world = empty_world()
    world["tasks"]["NORT-7"] = make_task()
    world["agents"]["agent-1"] = {"id": "agent-1"}
    state = state_with(world)
    state = apply_event(state, {"type": "remove", "kind": "agent", "id": "agent-1"})
    assert state["world"]["agents"] == {}
    assert "NORT-7" in state["world"]["tasks"]


def test_the_transcript_events_pass_through_without_being_stored():
    """The projection is relayed, not accumulated: the browser keeps the map.

    The server produces these events and sends them on. Holding them here too
    is what duplicated a revived agent's history, so nothing here holds them.
    """
    state = initial_client_state()
    for event in (
        {
            "type": "transcript.append",
            "agentId": "agent-1",
            "item": {"id": "i1", "ts": NOW, "type": "message", "role": "assistant"},
        },
        {
            "type": "transcript.update",
            "agentId": "agent-1",
            "itemId": "i1",
            "patch": {"status": "done"},
        },
        {"type": "transcript.truncated", "agentId": "agent-1"},
    ):
        assert apply_event(state, event) == state


def test_an_unknown_entity_kind_is_a_protocol_bug():
    state = initial_client_state()
    with pytest.raises(ValueError, match="widget"):
        apply_event(state, {"type": "upsert", "kind": "widget", "entity": {"id": "w"}})


def test_apply_event_does_not_mutate_its_input():
    before = initial_client_state()
    after = apply_event(
        before, {"type": "upsert", "kind": "task", "entity": make_task()}
    )
    assert before["world"]["tasks"] == {}
    assert "NORT-7" in after["world"]["tasks"]


def test_the_desk_is_an_entity_kind_with_its_own_table():
    entry = {"id": "askastro/2026-06-11.1", "addedAt": NOW}
    assert empty_world()["desk"] == {}
    state = initial_client_state()
    state = apply_event(state, {"type": "upsert", "kind": "desk", "entity": entry})
    assert state["world"]["desk"] == {"askastro/2026-06-11.1": entry}
    state = apply_event(
        state, {"type": "remove", "kind": "desk", "id": "askastro/2026-06-11.1"}
    )
    assert state["world"]["desk"] == {}


def test_a_task_row_is_a_task_less_its_detail_fields():
    """The row type is kept by hand; this is what stops it drifting from the task."""
    from maelstrom.orchestrator.protocol import (
        TASK_DETAIL_FIELDS,
        Task,
        TaskRow,
        task_row,
    )

    assert set(TaskRow.__annotations__) == set(Task.__annotations__) - set(
        TASK_DETAIL_FIELDS
    )
    task = make_task(content="prose", log=[{"ts": NOW, "text": "did it"}])
    assert set(task_row(task)) == set(TaskRow.__annotations__)
