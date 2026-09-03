"""What a task launch settles before anything runs: the plan and its two guards.

Shared by ``mael task run`` and the orchestrator server, so both launch a task
with the same session id, environment, permission mode, branch and prompt.
"""

from pathlib import Path

import pytest

from maelstrom import task as model
from maelstrom.session_discovery import LiveSession, LiveSessionSet
from maelstrom.task_launch import (
    LaunchBlocked,
    check_not_live,
    check_synced,
    plan_launch,
)
from maelstrom.worktree import SyncResult, WorktreeSetup


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
    )
    plan = plan_launch("northwind", task)
    assert plan.session_id == model.session_id_for("northwind", "NORT-7.2")
    assert plan.env == {
        "MAEL_TASK_ID": "NORT-7.2",
        "MAEL_TASK_PARENT": "NORT-7",
        "MAEL_TASK_SESSION_ID": plan.session_id,
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
    assert plan.model is None


def test_check_not_live_refuses_a_task_with_a_live_session():
    session = LiveSession(pid=42, cwd=Path("/x"), session_id="s-1")
    live = LiveSessionSet([session])
    with pytest.raises(LaunchBlocked, match="pid 42"):
        check_not_live("NORT-7", "s-1", live)
    check_not_live("NORT-7", "other", live)


def test_check_synced_refuses_a_failed_sync_and_passes_a_reused_worktree():
    failed = WorktreeSetup(
        path=None,  # type: ignore[arg-type]
        name="alpha",
        action="recycled",
        sync=SyncResult(success=False, branch="b", message="conflict"),
    )
    with pytest.raises(LaunchBlocked, match="conflict"):
        check_synced("NORT-7", "b", failed)
    check_synced("NORT-7", "b", WorktreeSetup(path=None, name="alpha", action="reused"))  # type: ignore[arg-type]


def test_a_task_source_with_no_worktree_opener_refuses_to_launch(store):
    from maelstrom.orchestrator.sources import NotebookTaskSource

    model.create(store, project="p", title="x", id="T-1")
    source = NotebookTaskSource(store, lambda: ["p"])
    with pytest.raises(LaunchBlocked, match="cannot open worktrees"):
        source.launch("T-1", None)
    assert model.load(store, "p", "T-1").status == "todo"
