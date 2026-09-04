"""The client-state reducer the orchestrator server shares with the web UI.

Ports of ``web/src/protocol/reducer.test.ts``. The same frames must yield the
same state on both sides, or the server's snapshot and the client's replay
would disagree.
"""

import pytest

from maelstrom.orchestrator.protocol import (
    apply_event,
    apply_server_event,
    empty_world,
    initial_client_state,
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


def frame(seq, event):
    return {"seq": seq, "ts": NOW, "event": event}


def snapshot(world=None):
    return {
        "type": "snapshot",
        "world": world if world is not None else empty_world(),
    }


def test_a_snapshot_replaces_the_world_and_records_its_seq():
    world = empty_world()
    world["tasks"]["NORT-7"] = make_task()
    state = apply_server_event(initial_client_state(), frame(1, snapshot(world)))
    assert state["world"]["tasks"]["NORT-7"]["title"] == "Add order export"
    assert state["lastSeq"] == 1


def test_an_upsert_adds_and_replaces_whole():
    state = apply_server_event(initial_client_state(), frame(1, snapshot()))
    state = apply_server_event(
        state, frame(2, {"type": "upsert", "kind": "task", "entity": make_task()})
    )
    assert state["world"]["tasks"]["NORT-7"]["status"] == "todo"
    state = apply_server_event(
        state,
        frame(
            3, {"type": "upsert", "kind": "task", "entity": make_task(status="done")}
        ),
    )
    assert state["world"]["tasks"]["NORT-7"]["status"] == "done"


def test_a_frame_not_newer_than_the_last_is_dropped():
    state = apply_server_event(initial_client_state(), frame(5, snapshot()))
    state = apply_server_event(
        state, frame(5, {"type": "upsert", "kind": "task", "entity": make_task()})
    )
    state = apply_server_event(
        state, frame(3, {"type": "upsert", "kind": "task", "entity": make_task()})
    )
    assert state["world"]["tasks"] == {}
    assert state["lastSeq"] == 5


def test_a_snapshot_is_a_new_epoch_whatever_its_seq():
    """A restarted server counts from 1 again; its snapshot must still land."""
    state = apply_server_event(initial_client_state(), frame(500, snapshot()))
    world = empty_world()
    world["tasks"]["NORT-7"] = make_task()
    state = apply_server_event(state, frame(1, snapshot(world)))
    assert state["world"]["tasks"]["NORT-7"]["title"] == "Add order export"
    assert state["lastSeq"] == 1
    state = apply_server_event(
        state, frame(2, {"type": "remove", "kind": "task", "id": "NORT-7"})
    )
    assert state["world"]["tasks"] == {}


def test_remove_deletes_by_kind_and_id():
    world = empty_world()
    world["tasks"]["NORT-7"] = make_task()
    world["agents"]["agent-1"] = {"id": "agent-1"}
    state = apply_server_event(initial_client_state(), frame(1, snapshot(world)))
    state = apply_server_event(
        state, frame(2, {"type": "remove", "kind": "agent", "id": "agent-1"})
    )
    assert state["world"]["agents"] == {}
    assert "NORT-7" in state["world"]["tasks"]


def test_the_transcript_events_pass_through_without_being_stored():
    """The projection is relayed, not accumulated: the browser keeps the map.

    The server produces these events and sends them on. Holding them here too
    is what duplicated a revived agent's history, so nothing here holds them.
    """
    state = apply_event(initial_client_state(), snapshot())
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


def test_an_error_event_is_kept_with_its_seq():
    state = apply_event(initial_client_state(), {"type": "error", "message": "boom"}, 9)
    assert state["errors"] == [{"seq": 9, "message": "boom", "agentId": None}]


def test_an_unknown_entity_kind_is_a_protocol_bug():
    state = apply_event(initial_client_state(), snapshot())
    with pytest.raises(ValueError, match="widget"):
        apply_event(state, {"type": "upsert", "kind": "widget", "entity": {"id": "w"}})


def test_apply_event_does_not_mutate_its_input():
    before = apply_event(initial_client_state(), snapshot())
    after = apply_event(
        before, {"type": "upsert", "kind": "task", "entity": make_task()}
    )
    assert before["world"]["tasks"] == {}
    assert "NORT-7" in after["world"]["tasks"]


def test_the_desk_is_an_entity_kind_with_its_own_table():
    entry = {"id": "askastro/2026-06-11.1", "addedAt": NOW}
    assert empty_world()["desk"] == {}
    state = apply_server_event(initial_client_state(), frame(1, snapshot()))
    state = apply_server_event(
        state, frame(2, {"type": "upsert", "kind": "desk", "entity": entry})
    )
    assert state["world"]["desk"] == {"askastro/2026-06-11.1": entry}
    state = apply_server_event(
        state,
        frame(3, {"type": "remove", "kind": "desk", "id": "askastro/2026-06-11.1"}),
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
