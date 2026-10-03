"""What a task launch settles before anything runs: the plan, the session and
the two guards.

Shared by ``mael task run`` and the orchestrator server, so both launch a task
with the same environment, permission mode, branch and prompt, and both choose
its session the same way.
"""

from pathlib import Path

import pytest

from mael_domain import task as model
from mael_domain.session_discovery import LiveSession, LiveSessionSet
from mael_domain.task_launch import (
    LaunchBlocked,
    check_not_live,
    check_synced,
    choose_session,
    plan_launch,
)
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
    )
    plan = plan_launch("northwind", task)
    # The row id the launch's Agent record names: the link back to the task.
    assert plan.task == "northwind/NORT-7.2"
    assert plan.env == {
        "MAEL_TASK_ID": "NORT-7.2",
        "MAEL_TASK_PARENT": "NORT-7",
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


def records(*session_ids: str) -> list[dict]:
    """A task's Agent records, newest first, as ``for_task`` answers."""
    return [{"id": f"a{n}", "session_id": s} for n, s in enumerate(session_ids)]


def test_a_task_with_no_record_starts_a_new_session():
    choice = choose_session([], lambda _: True, mint=lambda: "minted")
    assert (choice.session_id, choice.resume) == ("minted", False)


def test_a_task_resumes_its_newest_session_that_has_a_transcript():
    """A launch that failed leaves a record and no transcript. It is skipped."""
    choice = choose_session(
        records("s-3", "s-2", "s-1"),
        lambda session_id: session_id in ("s-2", "s-1"),
        mint=lambda: "minted",
    )
    assert (choice.session_id, choice.resume) == ("s-2", True)


def test_a_task_whose_sessions_left_no_transcript_starts_a_new_one():
    """The id is never reused: a new session takes a new id."""
    choice = choose_session(records("s-1"), lambda _: False, mint=lambda: "minted")
    assert (choice.session_id, choice.resume) == ("minted", False)


def test_a_record_with_no_session_is_never_resumed():
    """A Codex launch records no session id, so there is nothing to resume."""
    choice = choose_session(records(""), lambda _: True, mint=lambda: "minted")
    assert (choice.session_id, choice.resume) == ("minted", False)


def test_a_fresh_launch_ignores_a_transcript():
    choice = choose_session(
        records("s-1"), lambda _: True, fresh=True, mint=lambda: "minted"
    )
    assert (choice.session_id, choice.resume) == ("minted", False)


def test_two_new_sessions_take_different_ids():
    first = choose_session([], lambda _: False)
    second = choose_session([], lambda _: False)
    assert first.session_id != second.session_id


def test_check_not_live_refuses_a_task_with_any_of_its_sessions_live():
    """A task has one session per record, and any of them blocks a launch."""
    session = LiveSession(pid=42, cwd=Path("/x"), session_id="s-1")
    live = LiveSessionSet([session])
    with pytest.raises(LaunchBlocked, match="pid 42"):
        check_not_live("NORT-7", records("s-2", "s-1"), live)
    check_not_live("NORT-7", records("s-2", "other"), live)
    # A task never launched has no record, and nothing blocks it.
    check_not_live("NORT-7", [], live)


def test_a_record_with_no_session_does_not_match_a_bare_claude():
    """A bare ``claude`` reports no session id. That is not this task's session."""
    live = LiveSessionSet([LiveSession(pid=42, cwd=Path("/x"), session_id=None)])
    check_not_live("NORT-7", records(""), live)


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
