"""What a task launch settles before anything runs: the plan and its two guards.

Shared by ``mael task run`` and the orchestrator server, so both launch a task
with the same session id, environment, permission mode, branch and prompt.
"""

from pathlib import Path

import pytest

from mael_domain import task as model
from mael_domain.session_discovery import LiveSession, LiveSessionSet
from mael_domain.task_launch import (
    LaunchBlocked,
    check_not_live,
    check_synced,
    plan_launch,
    prepare_launch,
)
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.worktree import SyncResult, WorktreeSetup


def test_plan_launch_derives_everything_from_the_task():
    task = model.Task(
        id="NORT-7.2",
        title="Add export",
        project="northwind",
        command="plan-task",
        mode="auto",
        parent="NORT-7",
        branch="feat/orders",
        model="claude-opus-5",
        content="Do it.",
        sessions=[
            model.TaskSession("c-1", "claude"),
            model.TaskSession("thread-1", "codex"),
        ],
    )
    plan = plan_launch("northwind", task)
    assert plan.session_id == "c-1"
    assert plan.env == {
        "MAEL_TASK_ID": "NORT-7.2",
        "MAEL_TASK_PARENT": "NORT-7",
        "MAEL_TASK_PROJECT": "northwind",
    }
    assert plan.permission_mode == "auto"
    assert plan.branch == "feat/orders"
    assert plan.model == "claude-opus-5"
    assert plan.prompt == "/plan-task Add export\n\nDo it."


def test_a_parentless_task_self_parents_and_a_normal_mode_has_no_flag():
    task = model.Task(id="NORT-9", title="x", project="northwind", mode="normal")
    plan = plan_launch("northwind", task)
    assert plan.env["MAEL_TASK_PARENT"] == "NORT-9"
    assert plan.permission_mode is None
    assert plan.branch == model.default_branch("NORT-9", "")


def test_a_task_that_names_no_model_launches_on_the_default():
    """The notebook stores no model, so the launch picks one.

    Storing the default instead would pin every task written before the
    default moved, so the choice is made here, where the session starts.
    """
    task = model.Task(id="NORT-9", title="x", project="northwind")
    assert plan_launch("northwind", task).model == model.DEFAULT_MODEL


def test_the_execute_model_travels_raw():
    """Unlike ``model``, no default is substituted: empty means "no switch", and
    a fallback to DEFAULT_MODEL here would switch every planning session."""
    task = model.Task(
        id="NORT-9", title="x", project="northwind", execute_model="claude:sonnet"
    )
    assert plan_launch("northwind", task).execute_model == "claude:sonnet"


def test_a_task_naming_no_execute_model_plans_an_empty_one():
    task = model.Task(id="NORT-9", title="x", project="northwind")
    assert plan_launch("northwind", task).execute_model == ""


def test_a_launch_on_a_codex_model_writes_a_codex_prompt():
    """The launch's model, which may override the task's, picks the harness."""
    task = model.Task(
        id="NORT-9", title="x", project="northwind", command="plan-task", mode="auto"
    )
    plan = plan_launch("northwind", task, model_name="codex:sol")
    assert plan.model == "codex:sol"
    assert plan.prompt == "Load the `plan-task` skill, then: x"


async def test_a_new_session_gets_a_new_id_and_names_the_earlier_ones():
    """A relaunch resumes the newest session. A New session does not."""
    table = InMemoryTaskTable()
    task = await model.create(table, project="northwind", title="x", content="Do it.")
    first = await prepare_launch(table, "northwind", task)
    again = await prepare_launch(table, "northwind", task)
    assert again.session_id == first.session_id

    renewed = await prepare_launch(table, "northwind", task, new_session=True)

    assert renewed.session_id not in ("", first.session_id)
    stored = await model.load(table, "northwind", task.id)
    assert [s.id for s in stored.sessions] == [first.session_id, renewed.session_id]
    assert model.current_session(stored, "claude") == renewed.session_id
    assert renewed.prompt == (
        "/planning x\n\nDo it.\n\nThis is a new session of the task. Its earlier sessions, "
        f"oldest first: {first.session_id}. Read their transcripts if you need "
        "what they did."
    )
    assert "earlier sessions" not in again.prompt


async def test_a_new_codex_session_resumes_no_thread():
    """Codex makes its own thread id, so a New session names none."""
    table = InMemoryTaskTable()
    task = await model.create(table, project="northwind", title="x", model="codex:sol")
    await model.add_session(table, "northwind", task.id, "thread-1", "codex")
    task = await model.load(table, "northwind", task.id)

    plan = await prepare_launch(table, "northwind", task, new_session=True)

    assert plan.session_id == ""
    assert "oldest first: thread-1." in plan.prompt
    assert [s.id for s in task.sessions] == ["thread-1"]


def test_check_not_live_refuses_a_task_with_a_live_session():
    session = LiveSession(pid=42, cwd=Path("/x"), session_id="s-1")
    live = LiveSessionSet([session])
    with pytest.raises(LaunchBlocked, match="pid 42"):
        check_not_live("NORT-7", "s-1", live)
    check_not_live("NORT-7", "other", live)


def test_check_synced_refuses_a_failed_sync_and_passes_one_that_never_ran():
    failed = WorktreeSetup(
        path=None,  # type: ignore[arg-type]
        name="alpha",
        action="recycled",
        sync=SyncResult(success=False, branch="b", message="conflict"),
    )
    with pytest.raises(LaunchBlocked, match="conflict"):
        check_synced("NORT-7", "b", failed)
    check_synced("NORT-7", "b", WorktreeSetup(path=None, name="alpha", action="reused"))  # type: ignore[arg-type]
