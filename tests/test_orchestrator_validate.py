"""Command validation, ported from ``web/src/protocol/validate.test.ts``.

The codes mirror the agent host's own refusals, so the server answers a bad
command the same way the fake backend does, before the host is touched.
"""

import pytest

from maelstrom.orchestrator.protocol import empty_world
from maelstrom.orchestrator.validate import validate_command
from tests.test_orchestrator_normalise import make_agent, make_document


def make_task(**over) -> dict:
    from maelstrom import task as model
    from maelstrom.orchestrator.world_build import task_entity

    task = task_entity(
        model.Task(id="NORT-7", title="Add order export", project="northwind"),
        actionable=True,
    )
    task.update(over)
    return task


def world_with(agents=(), tasks=(), documents=(), projects=(), desk=()):
    world = empty_world()
    for entry in desk:
        world["desk"][entry] = {"id": entry, "addedAt": "2026-09-04T09:00:00Z"}
    for a in agents:
        world["agents"][a["id"]] = a
    for t in tasks:
        world["tasks"][t["id"]] = t
    for d in documents:
        world["documents"][d["id"]] = d
    for p in projects:
        world["projects"][p["id"]] = p
    return world


WAITING_FOR_PLAN = make_agent(
    id="agent-1", state="awaiting-plan-review", pendingRequestId="req-1"
)


def code(error):
    return error["code"] if error else None


def test_unknown_id_for_an_agent_not_in_the_world():
    world = world_with(agents=[WAITING_FOR_PLAN])
    cmd = {"type": "agent.approve", "agentId": "ghost", "requestId": "req-1"}
    assert code(validate_command(world, cmd)) == "unknown_id"


def test_agent_exited_when_the_agent_has_gone():
    world = world_with(agents=[make_agent(state="exited", exitCode=0)])
    cmd = {"type": "agent.say", "agentId": "agent-1", "text": "hi"}
    assert code(validate_command(world, cmd)) == "agent_exited"


def test_a_resume_of_an_exited_agent_is_allowed():
    agent = make_agent(state="exited", exitCode=1)
    cmd = {"type": "agent.resume", "agentId": "agent-1"}
    assert validate_command(world_with(agents=[agent]), cmd) is None


def test_a_resume_of_a_running_agent_is_refused():
    # Two children on one session id would fight over one transcript.
    agent = make_agent(state="idle")
    cmd = {"type": "agent.resume", "agentId": "agent-1"}
    assert code(validate_command(world_with(agents=[agent]), cmd)) == "invalid"


def test_a_resume_of_an_unknown_agent_is_unknown_id():
    cmd = {"type": "agent.resume", "agentId": "ghost"}
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_not_waiting_when_there_is_no_pending_request():
    world = world_with(agents=[make_agent(state="processing")])
    cmd = {"type": "agent.approve", "agentId": "agent-1", "requestId": "req-1"}
    assert code(validate_command(world, cmd)) == "not_waiting"


def test_stale_request_when_the_request_is_not_the_pending_one():
    world = world_with(agents=[WAITING_FOR_PLAN])
    cmd = {"type": "agent.approve", "agentId": "agent-1", "requestId": "old"}
    assert code(validate_command(world, cmd)) == "stale_request"


def test_wrong_wait_kind_when_answering_a_permission_request():
    world = world_with(
        agents=[make_agent(state="awaiting-permission", pendingRequestId="req-2")]
    )
    cmd = {
        "type": "agent.answer",
        "agentId": "agent-1",
        "requestId": "req-2",
        "answers": {"Which?": "A"},
    }
    assert code(validate_command(world, cmd)) == "wrong_wait_kind"


def test_stale_version_when_approving_an_older_document_version():
    world = world_with(documents=[make_document(version=3)])
    cmd = {"type": "document.approve", "documentId": "doc-1", "version": 2}
    assert code(validate_command(world, cmd)) == "stale_version"


@pytest.mark.parametrize(
    "cmd",
    [
        {"type": "agent.say", "agentId": "agent-1", "text": "   "},
        {
            "type": "agent.deny",
            "agentId": "agent-1",
            "requestId": "req-1",
            "reason": " ",
        },
        {
            "type": "agent.answer",
            "agentId": "agent-1",
            "requestId": "req-1",
            "answers": {},
        },
    ],
)
def test_invalid_for_an_empty_message_reason_or_answer_set(cmd):
    agent = make_agent(
        state="awaiting-question"
        if cmd["type"] == "agent.answer"
        else "awaiting-permission",
        pendingRequestId="req-1",
    )
    assert code(validate_command(world_with(agents=[agent]), cmd)) == "invalid"


def test_invalid_for_launching_a_task_that_is_not_actionable():
    world = world_with(tasks=[make_task(actionable=False)])
    cmd = {"type": "agent.launch", "taskId": "northwind/NORT-7"}
    assert code(validate_command(world, cmd)) == "invalid"


def test_unknown_id_for_launching_a_task_not_in_the_world():
    cmd = {"type": "agent.launch", "taskId": "northwind/NORT-7"}
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_invalid_for_approving_a_document_not_awaiting_review():
    world = world_with(documents=[make_document(status="approved")])
    cmd = {"type": "document.approve", "documentId": "doc-1", "version": 1}
    assert code(validate_command(world, cmd)) == "invalid"


def test_invalid_for_an_unknown_command_type():
    assert code(validate_command(empty_world(), {"type": "nope"})) == "invalid"


def test_accepts_a_well_formed_approve_of_the_pending_request():
    world = world_with(agents=[WAITING_FOR_PLAN])
    cmd = {"type": "agent.approve", "agentId": "agent-1", "requestId": "req-1"}
    assert validate_command(world, cmd) is None


def test_accepts_a_launch_of_an_actionable_task():
    world = world_with(tasks=[make_task()])
    assert (
        validate_command(world, {"type": "agent.launch", "taskId": "northwind/NORT-7"})
        is None
    )


def test_unknown_id_for_adding_a_task_not_in_the_world_to_the_desk():
    cmd = {"type": "desk.add", "id": "task:northwind/NORT-7"}
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_unknown_id_for_adding_an_agent_not_in_the_world_to_the_desk():
    cmd = {"type": "desk.add", "id": "agent:ghost"}
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_unknown_id_for_adding_an_id_that_carries_no_kind():
    world = world_with(tasks=[make_task()])
    cmd = {"type": "desk.add", "id": "northwind/NORT-7"}
    assert code(validate_command(world, cmd)) == "unknown_id"


def test_unknown_id_for_removing_a_task_that_is_not_on_the_desk():
    world = world_with(tasks=[make_task()])
    cmd = {"type": "desk.remove", "id": "task:northwind/NORT-7"}
    assert code(validate_command(world, cmd)) == "unknown_id"


def test_accepts_adding_a_task_that_is_on_the_desk_already():
    world = world_with(tasks=[make_task()], desk=["task:northwind/NORT-7"])
    cmd = {"type": "desk.add", "id": "task:northwind/NORT-7"}
    assert validate_command(world, cmd) is None


def test_accepts_adding_a_free_agent_to_the_desk():
    world = world_with(agents=[make_agent(id="agent-1")])
    cmd = {"type": "desk.add", "id": "agent:agent-1"}
    assert validate_command(world, cmd) is None


def test_accepts_removing_a_task_that_is_on_the_desk():
    world = world_with(tasks=[make_task()], desk=["task:northwind/NORT-7"])
    cmd = {"type": "desk.remove", "id": "task:northwind/NORT-7"}
    assert validate_command(world, cmd) is None


def test_accepts_a_status_move_of_a_task_it_knows():
    world = world_with(tasks=[make_task()])
    cmd = {"type": "task.setStatus", "taskId": "northwind/NORT-7", "status": "done"}
    assert validate_command(world, cmd) is None


def test_unknown_id_for_a_status_move_of_a_task_not_in_the_world():
    cmd = {"type": "task.setStatus", "taskId": "northwind/NORT-7", "status": "done"}
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_invalid_for_a_status_the_notebook_has_no_folder_for():
    world = world_with(tasks=[make_task()])
    cmd = {"type": "task.setStatus", "taskId": "northwind/NORT-7", "status": "finished"}
    assert code(validate_command(world, cmd)) == "invalid"


def test_accepts_an_update_that_changes_one_field():
    world = world_with(tasks=[make_task()])
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"branch": "x"},
    }
    assert validate_command(world, cmd) is None


def test_unknown_id_for_an_update_of_a_task_not_in_the_world():
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"title": "x"},
    }
    assert code(validate_command(empty_world(), cmd)) == "unknown_id"


def test_invalid_for_an_update_that_changes_nothing():
    world = world_with(tasks=[make_task()])
    cmd = {"type": "task.update", "taskId": "northwind/NORT-7", "fields": {}}
    assert code(validate_command(world, cmd)) == "invalid"


def test_invalid_for_an_update_that_blanks_the_title():
    world = world_with(tasks=[make_task()])
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"title": "  "},
    }
    assert code(validate_command(world, cmd)) == "invalid"


def test_invalid_for_a_mode_the_notebook_cannot_launch():
    world = world_with(tasks=[make_task()])
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"mode": "turbo"},
    }
    assert code(validate_command(world, cmd)) == "invalid"


def test_invalid_for_a_priority_the_notebook_has_no_rank_for():
    world = world_with(tasks=[make_task()])
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"priority": "urgent"},
    }
    assert code(validate_command(world, cmd)) == "invalid"


def test_a_null_field_is_no_edit():
    world = world_with(tasks=[make_task()])
    cmd = {
        "type": "task.update",
        "taskId": "northwind/NORT-7",
        "fields": {"branch": None},
    }
    assert code(validate_command(world, cmd)) == "invalid"
