"""Tests for the ``mael task`` CLI, against an InMemoryStore.

The CLI is exercised via Click's ``CliRunner``. ``task_cli._store`` is patched
to return a shared :class:`InMemoryStore` and ``_resolve_project`` to a fixed
project, so no git or cwd resolution happens.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

from maelstrom import task as model
from maelstrom import task_cli
from maelstrom.integrations.linear import cmd_plan
from maelstrom.shell import describe
from maelstrom.task_store import InMemoryStore
from maelstrom.worktree import WorktreeSetup


@pytest.fixture
def store(store, monkeypatch) -> InMemoryStore:
    # Consume the shared task-store fixture (tests/conftest.py) and wire the CLI
    # seams to it. The real open_index() derives a SqliteTaskIndex from the store's
    # on-disk root; an InMemoryStore has none. Point open_index at the SAME in-memory
    # index the model uses by default (conftest's autouse fixture set
    # ``model._DEFAULT_INDEX``), so a task created directly via ``model.create`` in
    # a test body and a task read back through the CLI share one index — otherwise
    # two separate ``:memory:`` dbs would disagree (both look "fresh" since
    # ``InMemoryStore.head()`` is always None).
    from maelstrom import task as model

    monkeypatch.setattr(task_cli, "_store", lambda: store)
    monkeypatch.setattr(task_cli, "open_index", lambda _store: model._DEFAULT_INDEX)
    monkeypatch.setattr(task_cli, "_resolve_project", lambda project: project or "p")
    return store


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Stub the launch collaborators of ``_run_task``.

    Returns a namespace with the mocked ``setup`` and ``session`` (the
    worktree-placement wrapper) callables, plus ``exec`` (the --here
    ``run_cmd(..., replace_process=True)`` peer), and the worktree path the fake
    setup returns.
    """
    project_path = tmp_path / "proj"
    project_path.mkdir()
    monkeypatch.setattr(
        task_cli,
        "resolve_context",
        lambda *a, **k: SimpleNamespace(project="p", project_path=project_path),
    )
    wt_path = tmp_path / "proj-bravo"
    setup = MagicMock(
        return_value=WorktreeSetup(path=wt_path, name="bravo", action="created")
    )
    # Placement succeeds by default (True) so the task stays IN_PROGRESS; tests
    # that exercise the failure path override ``session.return_value = False``.
    session = MagicMock(return_value=True)
    run_cmd = MagicMock()
    ensure_cmux = MagicMock(return_value=True)
    monkeypatch.setattr(task_cli, "setup_worktree_for_branch", setup)
    monkeypatch.setattr(task_cli, "launch_claude_in_worktree", session)
    monkeypatch.setattr(task_cli, "run_cmd", run_cmd)
    monkeypatch.setattr(task_cli, "ensure_cmux_running", ensure_cmux)
    # No live session for the task by default — the duplicate-launch pre-check
    # would otherwise sweep live claude processes. Patch the sweep to empty so
    # `for_session_id` finds nothing.
    _patch_live_sessions(monkeypatch, [])
    return SimpleNamespace(
        setup=setup, session=session, exec=run_cmd,
        ensure_cmux=ensure_cmux, wt_path=wt_path,
    )


def _patch_live_sessions(monkeypatch, sessions):
    """Make the run-guard's ``LiveSessionSet`` sweep return ``sessions``.

    The guard constructs ``session_discovery.LiveSessionSet()`` and calls
    ``for_session_id``; patching the swept list drives it without touching real
    ``pgrep``/``lsof``/``ps``.

    ``sessions`` may be a list, or a zero-arg callable returning one — the latter
    for cases where the session ids aren't known until the command under test has
    created the tasks (see the load-many batch tests).
    """
    monkeypatch.setattr(
        task_cli.session_discovery,
        "all_live_sessions",
        sessions if callable(sessions) else lambda: list(sessions),
    )


# --- add: branch defaulting / override ---


class TestAddBranch:
    def test_branch_defaults_to_generated_slug(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "Smoke"])
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        # Generated from the title; with the model call blocked in tests this is
        # the deterministic fallback slug.
        assert model.load(store, "p", new_id).branch == "feat/smoke"

    def test_branch_override(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "On branch", "--branch", "fix/login"]
        )
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        assert model.load(store, "p", new_id).branch == "fix/login"

    def test_command_and_mode_recorded(self, runner, store):
        result = runner.invoke(
            task_cli.task,
            ["add", "Plan it", "--command", "plan-task", "--mode", "plan"],
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.command == "plan-task"
        assert t.mode == "plan"

    def test_plain_task_defaults_to_plan_mode(self, runner, store):
        # New tasks default to plan mode (DEFAULT_MODE).
        result = runner.invoke(task_cli.task, ["add", "Just do it"])
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.mode == "plan"

    def test_explicit_normal_mode_overrides_default(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "Just do it", "--mode", "normal"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.mode == "normal"


# --- add/update: lifecycle action flags ---


class TestActionFlags:
    def test_add_stores_post_action(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "E", "--post-action", "linear.done"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.post_action == "linear.done"

    def test_add_stores_pre_action(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "E", "--pre-action", "linear.in-progress"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.pre_action == "linear.in-progress"

    def test_update_retrofits_post_action(self, runner, store):
        new_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        result = runner.invoke(
            task_cli.task,
            ["update", new_id, "--post-action", "linear.done"],
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", new_id).post_action == "linear.done"

    def test_update_can_clear_post_action(self, runner, store):
        new_id = runner.invoke(
            task_cli.task, ["add", "E", "--post-action", "linear.done"]
        ).output.strip()
        result = runner.invoke(
            task_cli.task, ["update", new_id, "--post-action", ""]
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", new_id).post_action == ""

    def test_update_omitting_action_leaves_it(self, runner, store):
        new_id = runner.invoke(
            task_cli.task, ["add", "E", "--post-action", "linear.done"]
        ).output.strip()
        # An unrelated update must not wipe the action.
        runner.invoke(task_cli.task, ["update", new_id, "--branch", "x"])
        assert model.load(store, "p", new_id).post_action == "linear.done"


class TestUpdateRename:
    @pytest.fixture(autouse=True)
    def _no_live_session(self, monkeypatch):
        # The --id re-key path consults the session registry; default to "no
        # live session" so it doesn't read the real ~/.maelstrom registry.
        monkeypatch.setattr(
            task_cli.session_store, "find_live_session_for_task", lambda *a, **k: None
        )

    def test_update_id_rekeys_task(self, runner, store):
        old_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", "new-id").id == "new-id"
        with pytest.raises(KeyError):
            model.load(store, "p", old_id)
        assert "Renamed" in result.output
        assert "new-id" in result.output

    def test_update_id_with_branch_in_one_call(self, runner, store):
        old_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        result = runner.invoke(
            task_cli.task,
            ["update", old_id, "--id", "new-id", "--branch", "choose/foo"],
        )
        assert result.exit_code == 0, result.output
        loaded = model.load(store, "p", "new-id")
        assert loaded.branch == "choose/foo"

    def test_update_id_rewrites_dependent_follows(self, runner, store):
        a = runner.invoke(task_cli.task, ["add", "A"]).output.strip()
        b = runner.invoke(
            task_cli.task, ["add", "B", "--follow", a]
        ).output.strip()
        result = runner.invoke(task_cli.task, ["update", a, "--id", "new-a"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", b).follows == ["new-a"]

    def test_update_id_not_found_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["update", "nope", "--id", "new-id"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_update_id_refuses_in_progress(self, runner, store):
        old_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        model.move(store, "p", old_id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code != 0
        assert "in-progress" in result.output
        # Untouched.
        assert model.load(store, "p", old_id).id == old_id

    def test_update_id_refuses_live_session(self, runner, store, monkeypatch):
        old_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        monkeypatch.setattr(
            task_cli.session_store,
            "find_live_session_for_task",
            lambda *a, **k: {"pid": 123},
        )
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code != 0
        assert "open Claude session" in result.output
        assert model.load(store, "p", old_id).id == old_id

    def test_update_same_id_applies_field_changes(self, runner, store):
        old_id = runner.invoke(task_cli.task, ["add", "E"]).output.strip()
        result = runner.invoke(
            task_cli.task,
            ["update", old_id, "still works", "--id", old_id],
        )
        assert result.exit_code == 0, result.output
        loaded = model.load(store, "p", old_id)
        assert loaded.title == "still works"
        # Same-id is not a rename, so no "Renamed" line.
        assert "Renamed" not in result.output


class TestStatusFiresActions:
    def test_status_done_fires_post_action(self, runner, store, monkeypatch):
        from maelstrom.integrations import linear

        calls = []
        monkeypatch.setattr(
            linear, "set_issue_status", lambda i, s: calls.append((i, s))
        )
        new_id = runner.invoke(
            task_cli.task,
            [
                "add",
                "E",
                "--parent",
                "linear.NORT-12",
                "--post-action",
                "linear.done",
            ],
        ).output.strip()
        result = runner.invoke(task_cli.task, ["status", "done", new_id])
        assert result.exit_code == 0, result.output
        assert calls == [("NORT-12", "done")]


class TestStatusDoneFollowerHint:
    def test_done_suggests_actionable_follower(self, runner, store):
        a = model.create(store, project="p", title="a")
        b = model.create(store, project="p", title="Plan next step", follows=[a.id])
        result = runner.invoke(task_cli.task, ["status", "done", a.id])
        assert result.exit_code == 0, result.output
        assert "mael task next --run will run the following task" in result.output
        assert f"{b.id} - Plan next step" in result.output

    def test_done_no_follower_is_silent(self, runner, store):
        a = model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["status", "done", a.id])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == f"{a.id} -> done"

    def test_done_in_progress_follower_reports_running(self, runner, store):
        a = model.create(store, project="p", title="a")
        b = model.create(store, project="p", title="Plan next step", follows=[a.id])
        model.move(store, "p", b.id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["status", "done", a.id])
        assert result.exit_code == 0, result.output
        assert "already in-progress" in result.output
        assert f"{b.id} - Plan next step" in result.output
        assert "mael task next --run" not in result.output

    def test_cancel_does_not_suggest_follower(self, runner, store):
        a = model.create(store, project="p", title="a")
        model.create(store, project="p", title="b", follows=[a.id])
        result = runner.invoke(task_cli.task, ["status", "cancel", a.id])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == f"{a.id} -> cancelled"


# --- next: selection ---


class TestNext:
    def test_no_tasks_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output

    def test_prints_first_actionable(self, runner, store):
        a = model.create(store, project="p", title="a")
        model.create(store, project="p", title="b")
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == a.id

    def test_skips_blocked(self, runner, store):
        a = model.create(store, project="p", title="a")
        b = model.create(store, project="p", title="b", follows=[a.id])
        model.move(store, "p", a.id, "done")
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code == 0, result.output
        # a is done (terminal); b is now the next actionable.
        assert result.output.strip() == b.id

    def test_filters_by_parent(self, runner, store):
        parent = model.create(store, project="p", title="parent")
        child = model.create(store, project="p", title="child", parent=parent.id)
        result = runner.invoke(task_cli.task, ["next", "--parent", parent.id])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == child.id

    def test_branch_flag_restricts_to_branch(self, runner, store):
        # a has the lower id but is on another branch.
        model.create(store, project="p", title="a", branch="other")
        b = model.create(store, project="p", title="b", branch="feat/x")
        result = runner.invoke(task_cli.task, ["next", "-b", "feat/x"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == b.id

    def test_branch_flag_no_match_no_fallback(self, runner, store):
        # Only a task on 'other' exists; -b restricts strictly with no fallback.
        model.create(store, project="p", title="a", branch="other")
        result = runner.invoke(task_cli.task, ["next", "-b", "feat/x"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output


# --- list: actionable-by-default filtering ---


class TestList:
    def test_no_tasks(self, runner, store):
        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert "No tasks." in result.output

    def test_default_hides_blocked_and_terminal_shows_actionable(self, runner, store):
        a = model.create(store, project="p", title="alpha")  # actionable
        b = model.create(store, project="p", title="beta", follows=[a.id])  # blocked
        done = model.create(store, project="p", title="finished")
        model.move(store, "p", done.id, model.STATUS_DONE)
        cancelled = model.create(store, project="p", title="dropped")
        model.move(store, "p", cancelled.id, model.STATUS_CANCELLED)

        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert a.id in result.output
        assert b.id not in result.output
        assert done.id not in result.output
        assert cancelled.id not in result.output

    def test_default_in_progress_gated_by_actionability(self, runner, store):
        dep = model.create(store, project="p", title="dep")
        blocked_ip = model.create(
            store, project="p", title="blocked-in-prog", follows=[dep.id]
        )
        model.move(store, "p", blocked_ip.id, model.STATUS_IN_PROGRESS)

        ready_dep = model.create(store, project="p", title="ready-dep")
        model.move(store, "p", ready_dep.id, model.STATUS_DONE)
        ready_ip = model.create(
            store, project="p", title="ready-in-prog", follows=[ready_dep.id]
        )
        model.move(store, "p", ready_ip.id, model.STATUS_IN_PROGRESS)

        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        # in-progress but deps incomplete -> hidden; deps done -> shown.
        assert blocked_ip.id not in result.output
        assert ready_ip.id in result.output

    def test_all_todo_shows_actionable_and_blocked_hides_terminal(self, runner, store):
        a = model.create(store, project="p", title="alpha")
        b = model.create(store, project="p", title="beta", follows=[a.id])  # blocked
        done = model.create(store, project="p", title="finished")
        model.move(store, "p", done.id, model.STATUS_DONE)

        result = runner.invoke(task_cli.task, ["list", "--all-todo"])
        assert result.exit_code == 0, result.output
        assert a.id in result.output
        assert b.id in result.output
        assert done.id not in result.output

    def test_all_shows_terminal_too(self, runner, store):
        a = model.create(store, project="p", title="alpha")
        b = model.create(store, project="p", title="beta", follows=[a.id])
        done = model.create(store, project="p", title="finished")
        model.move(store, "p", done.id, model.STATUS_DONE)
        cancelled = model.create(store, project="p", title="dropped")
        model.move(store, "p", cancelled.id, model.STATUS_CANCELLED)

        result = runner.invoke(task_cli.task, ["list", "--all"])
        assert result.exit_code == 0, result.output
        for t in (a, b, done, cancelled):
            assert t.id in result.output

    def test_actionable_column_only_in_all_views(self, runner, store):
        model.create(store, project="p", title="alpha")

        default = runner.invoke(task_cli.task, ["list"])
        assert "ACTIONABLE" not in default.output

        all_todo = runner.invoke(task_cli.task, ["list", "--all-todo"])
        assert "ACTIONABLE" in all_todo.output

        all_ = runner.invoke(task_cli.task, ["list", "--all"])
        assert "ACTIONABLE" in all_.output

    def test_blocked_folder_hidden_by_default_even_with_deps_done(self, runner, store):
        # blocked/ parks a task by hand: it never launches, so it stays out of
        # the default view even when every id it follows is done. --all-todo is
        # the flag that reveals it.
        dep = model.create(store, project="p", title="dep")
        model.move(store, "p", dep.id, model.STATUS_DONE)
        t = model.create(store, project="p", title="manually-blocked", follows=[dep.id])
        model.move(store, "p", t.id, model.STATUS_BLOCKED)

        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert t.id not in result.output

        all_todo = runner.invoke(task_cli.task, ["list", "--all-todo"])
        assert t.id in all_todo.output


# --- rm ---


class TestRm:
    def test_rm_deletes_task(self, runner, store):
        a = model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["rm", a.id])
        assert result.exit_code == 0, result.output
        assert f"Deleted {a.id}" in result.output
        assert model.find_key(store, "p", a.id) is None

    def test_rm_unknown_task_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["rm", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output

    def test_rm_strips_dependents_follows(self, runner, store):
        a = model.create(store, project="p", title="a")
        b = model.create(store, project="p", title="b", follows=[a.id])
        result = runner.invoke(task_cli.task, ["rm", a.id])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", b.id).follows == []


# --- launch wiring: run / add --run / next --run ---


class TestRun:
    def test_run_ensures_worktree_moves_and_launches(self, runner, store, launch):
        t = model.create(
            store,
            project="p",
            title="Plan it",
            command="plan-task",
            mode="plan",
            content="do the thing",
        )
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output

        # Core fn called with the task's stored (generated) branch. The model
        # call is blocked in tests, so this is the deterministic fallback slug.
        assert launch.setup.call_args.args[2] == t.branch == "feat/plan"

        # Task is now in-progress.
        assert model.load(store, "p", t.id).status == model.STATUS_IN_PROGRESS

        # Session launched with the right task id / mode / worktree / project.
        # The prompt is produced lazily by `mael task prompt` inside the pipeline,
        # so the launcher gets the id, not the built prompt.
        kwargs = launch.session.call_args.kwargs
        assert kwargs["task_id"] == t.id
        assert kwargs["permission_mode"] == "plan"
        assert kwargs["project"] == "p"
        assert kwargs["worktree"] == "bravo"
        assert f"Running {t.id} on {t.branch}" in result.output
        assert "→ p/bravo (created)" in result.output

    def test_run_passes_the_tasks_model_to_the_launcher(
        self, runner, store, launch
    ):
        t = model.create(store, project="p", title="Plan it", model="opus")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["model"] == "opus"

    def test_run_without_a_model_passes_none(self, runner, store, launch):
        # Empty must reach the launcher as None (no --model), not as "".
        t = model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["model"] is None

    def test_run_here_passes_the_model_too(
        self, runner, store, launch, monkeypatch
    ):
        # The --here placement builds its own launch line, so it needs its own
        # coverage: a model set on the task must reach that argv as well.
        calls = []
        monkeypatch.setattr(
            task_cli, "run_cmd", lambda cmd, **kw: calls.append(describe(cmd))
        )
        t = model.create(store, project="p", title="Plan it", model="opus")
        result = runner.invoke(task_cli.task, ["run", t.id, "--here"])
        assert result.exit_code == 0, result.output
        assert "--model opus" in calls[0]

    def test_run_rolls_back_to_todo_when_placement_fails(
        self, runner, store, launch
    ):
        # cmux couldn't be reached, so no session opened. A task that never
        # launched must not be left in-progress: roll it back to TODO so the
        # next run retries, and log the reason.
        launch.session.return_value = False
        t = model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).status == model.STATUS_TODO
        assert "cmux unavailable" in result.output
        assert t.id in result.output

    def test_run_existing_task_resumes_stale_transcript(
        self, runner, store, launch, monkeypatch
    ):
        # The relaunch path (`task run <id>`) keeps resume-on-restart: an
        # already-existing task with an on-disk transcript for its session-id
        # launches with `--resume`. Confirms the `fresh` fix is scoped to the
        # create-then-run callers only.
        t = model.create(store, project="p", title="Existing")
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["resume"] is True
        assert "(resuming)" in result.output

    def test_run_unknown_task_errors(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["run", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output
        launch.session.assert_not_called()

    def test_run_missing_project_path_errors(self, runner, store, monkeypatch, tmp_path):
        t = model.create(store, project="p", title="t")
        missing = tmp_path / "absent"
        monkeypatch.setattr(
            task_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="p", project_path=missing),
        )
        _patch_live_sessions(monkeypatch, [])
        session = MagicMock()
        monkeypatch.setattr(task_cli, "launch_claude_in_worktree", session)
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code != 0
        assert "not found" in result.output
        session.assert_not_called()


def _live_session(pid=1, cwd=Path("/work/tree"), session_id=None):
    """A minimal :class:`LiveSession` stand-in (always live by construction)."""
    from maelstrom.session_discovery import LiveSession

    return LiveSession(pid=pid, cwd=cwd, session_id=session_id)


class TestDuplicateLaunchPrecheck:
    def test_run_refuses_when_this_tasks_session_is_live(
        self, runner, store, launch, monkeypatch
    ):
        # A live claude carrying *this task's* --session-id blocks the relaunch.
        t = model.create(store, project="p", title="t")
        sid = model.session_id_for("p", t.id)
        _patch_live_sessions(
            monkeypatch,
            [_live_session(pid=4242, cwd=Path("/work/tree-bravo"), session_id=sid)],
        )
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code != 0
        assert "already has a live Claude session" in result.output
        assert "4242" in result.output
        # Aborts before any launch or status move.
        launch.session.assert_not_called()
        launch.setup.assert_not_called()
        assert model.load(store, "p", t.id).status == model.STATUS_TODO

    def test_run_here_also_refuses(self, runner, store, launch, monkeypatch):
        t = model.create(store, project="p", title="t")
        sid = model.session_id_for("p", t.id)
        _patch_live_sessions(monkeypatch, [_live_session(pid=9, session_id=sid)])
        result = runner.invoke(task_cli.task, ["run", t.id, "--here"])
        assert result.exit_code != 0
        assert "already has a live Claude session" in result.output
        launch.exec.assert_not_called()
        assert model.load(store, "p", t.id).status == model.STATUS_TODO

    def test_run_proceeds_when_no_live_session(
        self, runner, store, launch, monkeypatch
    ):
        # A finished task leaves no live process carrying its id — it must stay
        # re-runnable, so the guard does NOT block.
        t = model.create(store, project="p", title="t")
        _patch_live_sessions(monkeypatch, [])
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert model.load(store, "p", t.id).status == model.STATUS_IN_PROGRESS

    def test_sibling_session_in_shared_worktree_does_not_block(
        self, runner, store, launch, monkeypatch
    ):
        # Two sibling tasks under one parent share a branch/worktree (one PR per
        # parent). A live session for sibling `.2` must NOT block launching `.3`:
        # they carry distinct --session-ids, so the guard keys only on `.3`'s own.
        parent = model.create(store, project="p", title="parent")
        two = model.create(store, project="p", title="two", parent=parent.id)
        three = model.create(store, project="p", title="three", parent=parent.id)
        two_sid = model.session_id_for("p", two.id)
        # `.2` is live in the shared worktree; `.3` is not.
        _patch_live_sessions(
            monkeypatch,
            [_live_session(pid=111, cwd=Path("/work/shared"), session_id=two_sid)],
        )
        result = runner.invoke(task_cli.task, ["run", three.id])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert model.load(store, "p", three.id).status == model.STATUS_IN_PROGRESS
        # Relaunching `.2` itself, however, is still blocked.
        result2 = runner.invoke(task_cli.task, ["run", two.id])
        assert result2.exit_code != 0
        assert "already has a live Claude session" in result2.output


class TestReconcile:
    def _live(self, monkeypatch, store, mapping):
        monkeypatch.setattr(
            task_cli, "_live_sessions_by_task", lambda s, p: mapping
        )

    def _ran(self, monkeypatch, tmp_path, ran_ids):
        # Stub transcript detection: `ran_ids` are the stale tasks that ran (a
        # transcript persists → finished → done); the rest never ran (→ todo).
        # Also stub resolve_context to a real (existing) project path, since the
        # reconcile CLI resolves it to locate worktrees before calling
        # `_ran_task_ids` — the test store has no real project of its own.
        monkeypatch.setattr(
            task_cli, "resolve_context",
            lambda *a, **k: SimpleNamespace(project="p", project_path=tmp_path),
        )
        monkeypatch.setattr(
            task_cli, "_ran_task_ids", lambda s, p, pp: set(ran_ids)
        )

    def test_empty(self, runner, store, monkeypatch, tmp_path):
        self._live(monkeypatch, store, {})
        self._ran(monkeypatch, tmp_path, set())
        result = runner.invoke(task_cli.task, ["reconcile"])
        assert result.exit_code == 0, result.output
        assert "No in-progress tasks or live task sessions." in result.output

    def test_dry_run_lists_states_and_hints(self, runner, store, monkeypatch, tmp_path):
        # One OK, one finished (ran), one never-ran, one orphan.
        ok = model.create(store, project="p", title="ok", id="t1")
        model.move(store, "p", ok.id, model.STATUS_IN_PROGRESS)
        finished = model.create(store, project="p", title="finished", id="t2")
        model.move(store, "p", finished.id, model.STATUS_IN_PROGRESS)
        never = model.create(store, project="p", title="never", id="t3")
        model.move(store, "p", never.id, model.STATUS_IN_PROGRESS)
        orphan = model.create(store, project="p", title="orphan", id="t4")  # todo
        self._live(
            monkeypatch, store,
            {
                ok.id: _live_session(pid=1),
                orphan.id: _live_session(pid=4),
            },
        )
        self._ran(monkeypatch, tmp_path, {finished.id})  # t2 ran, t3 never did
        result = runner.invoke(task_cli.task, ["reconcile"])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output
        assert "FINISHED" in result.output
        assert "NEVER RAN" in result.output
        assert "NO TASK" in result.output
        assert "→ done" in result.output
        assert "→ todo" in result.output
        assert "re-run with --fix" in result.output
        # Nothing changed in dry-run.
        assert model.load(store, "p", finished.id).status == model.STATUS_IN_PROGRESS
        assert model.load(store, "p", never.id).status == model.STATUS_IN_PROGRESS
        assert model.load(store, "p", orphan.id).status == model.STATUS_TODO

    def test_fix_applies_corrections(self, runner, store, monkeypatch, tmp_path):
        # A finished stale task → done, a never-ran stale task → todo, and an
        # orphan session → in-progress.
        finished = model.create(store, project="p", title="finished", id="t1")
        model.move(store, "p", finished.id, model.STATUS_IN_PROGRESS)
        never = model.create(store, project="p", title="never", id="t2")
        model.move(store, "p", never.id, model.STATUS_IN_PROGRESS)
        orphan = model.create(store, project="p", title="orphan", id="t3")  # todo
        self._live(monkeypatch, store, {orphan.id: _live_session(pid=3)})
        self._ran(monkeypatch, tmp_path, {finished.id})
        result = runner.invoke(task_cli.task, ["reconcile", "--fix"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", finished.id).status == model.STATUS_DONE
        assert model.load(store, "p", never.id).status == model.STATUS_TODO
        assert model.load(store, "p", orphan.id).status == model.STATUS_IN_PROGRESS

    def test_fix_nothing_to_do(self, runner, store, monkeypatch, tmp_path):
        ok = model.create(store, project="p", title="ok", id="t1")
        model.move(store, "p", ok.id, model.STATUS_IN_PROGRESS)
        self._live(monkeypatch, store, {ok.id: _live_session(pid=1)})
        self._ran(monkeypatch, tmp_path, set())
        result = runner.invoke(task_cli.task, ["reconcile", "--fix"])
        assert result.exit_code == 0, result.output
        assert "Nothing to fix." in result.output


class TestLiveSessionsByTask:
    """The reconcile correlation builder — task-precise via session-id."""

    def test_matches_only_the_owning_task(self, store, monkeypatch):
        # Two siblings share one worktree; only `.2` is live. The map must
        # attribute the session to `.2` alone, never to its sibling `.3`.
        parent = model.create(store, project="p", title="parent")
        two = model.create(store, project="p", title="two", parent=parent.id)
        three = model.create(store, project="p", title="three", parent=parent.id)
        two_sid = model.session_id_for("p", two.id)
        _patch_live_sessions(
            monkeypatch,
            [_live_session(pid=111, cwd=Path("/work/shared"), session_id=two_sid)],
        )
        mapping = task_cli._live_sessions_by_task(store, "p")
        assert two.id in mapping and mapping[two.id].pid == 111
        assert three.id not in mapping

    def test_empty_when_no_live_sessions(self, store, monkeypatch):
        model.create(store, project="p", title="t")
        _patch_live_sessions(monkeypatch, [])
        assert task_cli._live_sessions_by_task(store, "p") == {}

    def test_session_without_id_matches_nothing(self, store, monkeypatch):
        # A bare claude (no --session-id) never correlates to a task.
        model.create(store, project="p", title="t")
        _patch_live_sessions(
            monkeypatch, [_live_session(pid=5, cwd=Path("/work/x"))]
        )
        assert task_cli._live_sessions_by_task(store, "p") == {}


class TestAddRun:
    def test_add_run_creates_then_moves_then_launches(self, runner, store, launch):
        # Capture the task status at launch time to prove move-before-launch.
        seen = {}

        def fake_session(*args, **kwargs):
            # At launch time the (only) task must already be in-progress —
            # i.e. model.move ran before launch_claude_in_worktree.
            seen["in_progress"] = model.list_tasks(
                store, project="p", status=model.STATUS_IN_PROGRESS
            )
            return True  # placement succeeded → task stays in-progress

        launch.session.side_effect = fake_session

        result = runner.invoke(task_cli.task, ["add", "One shot", "--run"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()

        # Created task exists and ended up in-progress.
        assert model.load(store, "p", new_id).status == model.STATUS_IN_PROGRESS
        # The move ran BEFORE the launch.
        assert [t.id for t in seen["in_progress"]] == [new_id]
        launch.session.assert_called_once()

    def test_add_run_never_resumes_stale_transcript(
        self, runner, store, launch, monkeypatch
    ):
        # A task created by `add --run` is brand-new: even with a stale
        # transcript present it launches with `--session-id` (create), never
        # `--resume`.
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        result = runner.invoke(task_cli.task, ["add", "One shot", "--run"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert launch.session.call_args.kwargs["resume"] is False
        assert "(resuming)" not in result.output

    def test_add_without_run_does_not_launch(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["add", "No launch"])
        assert result.exit_code == 0, result.output
        launch.session.assert_not_called()
        launch.setup.assert_not_called()

    def test_add_run_passes_generated_branch(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["add", "Defaulted", "--run"])
        assert result.exit_code == 0, result.output
        # The branch is generated from the title; with the model call blocked in
        # tests it falls back to the deterministic slug, which is what the core
        # launch fn receives.
        assert launch.setup.call_args.args[2] == "feat/defaulted"


class TestAddEdit:
    def test_edit_opens_editor_after_create(self, runner, store, monkeypatch):
        calls = []
        monkeypatch.setattr(
            task_cli.model,
            "edit_in_editor",
            lambda s, p, i, **kw: calls.append((s, p, i)) or (None, True),
        )
        result = runner.invoke(task_cli.task, ["add", "Hand authored", "--edit"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        # Editor opened exactly once on the freshly created task.
        assert calls == [(store, "p", new_id)]

    def test_edit_short_flag(self, runner, store, monkeypatch):
        calls = []
        monkeypatch.setattr(
            task_cli.model,
            "edit_in_editor",
            lambda s, p, i, **kw: calls.append(i) or (None, True),
        )
        result = runner.invoke(task_cli.task, ["add", "Quick", "-e"])
        assert result.exit_code == 0, result.output
        assert len(calls) == 1

    def test_no_edit_does_not_open_editor(self, runner, store, monkeypatch):
        edit = MagicMock(return_value=(None, False))
        monkeypatch.setattr(task_cli.model, "edit_in_editor", edit)
        result = runner.invoke(task_cli.task, ["add", "No edit"])
        assert result.exit_code == 0, result.output
        edit.assert_not_called()

    def test_edit_then_run_edits_before_launch(
        self, runner, store, launch, monkeypatch
    ):
        order = []
        launch.session.side_effect = lambda *a, **k: order.append("launch")
        monkeypatch.setattr(
            task_cli.model,
            "edit_in_editor",
            lambda s, p, i, **kw: order.append("edit") or (None, True),
        )
        result = runner.invoke(task_cli.task, ["add", "Both", "--edit", "--run"])
        assert result.exit_code == 0, result.output
        assert order == ["edit", "launch"]

    def test_edit_reports_broken_editor_cleanly(self, runner, store, monkeypatch):
        monkeypatch.setattr(
            task_cli.model,
            "edit_in_editor",
            MagicMock(side_effect=RuntimeError("editor exploded")),
        )
        result = runner.invoke(task_cli.task, ["add", "Boom", "--edit"])
        assert result.exit_code != 0
        assert "editor exploded" in result.output


class TestAddShortFlags:
    def test_short_project(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "T", "-p", "maelstrom"])
        assert result.exit_code == 0, result.output

    def test_short_branch(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "T", "-b", "fix/login"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        assert model.load(store, "p", new_id).branch == "fix/login"

    def test_short_parent_capital_p(self, runner, store):
        parent = model.create(store, project="p", title="parent")
        result = runner.invoke(task_cli.task, ["add", "child", "-P", parent.id])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        assert model.load(store, "p", new_id).parent == parent.id


class TestAddModel:
    def test_model_flag_sets_the_field(self, runner, store):
        # No short flag by design: -m is --mode's.
        result = runner.invoke(task_cli.task, ["add", "T", "--model", "opus"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        assert model.load(store, "p", new_id).model == "opus"


def _block_option_keys() -> set[str]:
    """The block-settable field keys that must appear as CLI flags."""
    return {
        f.key
        for f in model.TASK_FIELDS
        if f.block and f.key not in task_cli._NON_OPTION_BLOCK_KEYS
    }


class TestBlockTaskOptionsParity:
    """The mechanism that keeps the CLI vocabulary and _BLOCK_KEYS from drifting.

    Commit 497e63a fixed exactly this class of drift for the key tables ("the
    same field set was hand-listed in four places … which is how ``branch`` came
    to be missing"). These assertions make a new block-settable field fail loudly
    instead of silently going missing from every task-creating command.
    """

    def test_every_block_field_has_an_option_row(self):
        missing = _block_option_keys() - set(task_cli._BLOCK_OPTIONS)
        assert not missing, f"add a _BLOCK_OPTIONS row for: {sorted(missing)}"

    def test_no_stale_option_rows(self):
        # The converse: a row for a field no longer block-settable (or renamed)
        # would silently render a flag the model can't accept.
        stale = set(task_cli._BLOCK_OPTIONS) - _block_option_keys()
        assert not stale, f"remove stale _BLOCK_OPTIONS rows: {sorted(stale)}"

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(task_cli.task_add, id="task-add"),
            pytest.param(cmd_plan, id="linear-plan"),
        ],
    )
    def test_command_exposes_every_block_field(self, command):
        names = {p.name for p in command.params}
        expected = {
            f.attr
            for f in model.TASK_FIELDS
            if f.block and f.key not in task_cli._NON_OPTION_BLOCK_KEYS
        }
        assert expected <= names
        # The follow keys ride as an explicit addendum, mirroring _BLOCK_KEYS.
        assert {"follows", "follow_ends"} <= names

    def test_short_flags_are_preserved(self):
        # The pre-existing shorts are behaviour: -c/-m/-b/-P must keep working
        # after the hand-written options became derived ones.
        opts = {p.name: p for p in task_cli.task_add.params}
        assert "-c" in opts["command"].opts
        assert "-m" in opts["mode"].opts
        assert "-b" in opts["branch"].opts
        assert "-P" in opts["parent"].opts

    def test_priority_keeps_its_choices(self):
        opts = {p.name: p for p in task_cli.task_add.params}
        assert isinstance(opts["priority"].type, click.Choice)
        assert tuple(opts["priority"].type.choices) == model.PRIORITIES

    def test_help_lists_every_block_flag(self, runner):
        result = runner.invoke(task_cli.task, ["add", "--help"])
        assert result.exit_code == 0, result.output
        for key in _block_option_keys():
            assert f"--{key}" in result.output

    def test_update_exposes_every_updatable_block_field(self):
        # `task update` is the edit-time sibling derivation; it must cover every
        # field that carries update_help, so the same drift can't reappear there.
        names = {p.name for p in task_cli.task_update.params}
        expected = {
            spec.attr
            for spec, opt in task_cli._option_specs()
            if opt.update_help is not None
        }
        assert expected <= names
        # `model` is the field this commit added — assert it by name so the test
        # states the concrete regression, not just the abstract rule.
        assert "model" in names

    def test_update_options_default_to_none(self):
        # Unset must stay distinguishable from an explicit '' so `task update`
        # can leave a field alone vs clear it.
        opts = {p.name: p for p in task_cli.task_update.params}
        for spec, opt in task_cli._option_specs():
            if opt.update_help is None:
                continue
            assert opts[spec.attr].default is None, spec.key

    def test_update_omits_create_only_fields(self):
        # `parent` has no update_help: re-parenting moves id and branch, which is
        # `--id`'s job, so it must not appear as a field edit.
        names = {p.name for p in task_cli.task_update.params}
        assert "parent" not in names


class TestNextRun:
    def test_next_run_runs_the_actionable(self, runner, store, launch):
        a = model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["next", "--run"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", a.id).status == model.STATUS_IN_PROGRESS
        launch.session.assert_called_once()

    def test_next_run_no_actionable_errors(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["next", "--run"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output
        launch.session.assert_not_called()


class TestRunHere:
    def test_run_here_skips_worktree_and_execs_in_cwd(self, runner, store, launch):
        t = model.create(
            store, project="p", title="Plan it", command="plan-task", mode="plan"
        )
        result = runner.invoke(task_cli.task, ["run", t.id, "--here"])
        assert result.exit_code == 0, result.output

        # No worktree reconciliation; the worktree-placement wrapper is unused.
        launch.setup.assert_not_called()
        launch.session.assert_not_called()

        # Task still moves to in-progress (parity with --run).
        assert model.load(store, "p", t.id).status == model.STATUS_IN_PROGRESS

        # Replace-execs the launch pipeline in the current shell (cwd=None) with
        # task env. The env rides on the ``claude`` Command (right of the pipe) so
        # the interactive session inherits it — a front prefix would only reach
        # ``mael task prompt``.
        launch.exec.assert_called_once()
        command = launch.exec.call_args.args[0]
        # Orphan task self-parents, so MAEL_TASK_PARENT rides alongside the id.
        # The deterministic --session-id pins the task's Claude session and is
        # also exported as MAEL_TASK_SESSION_ID for the session-channel registry.
        sid = model.session_id_for("p", t.id)
        assert describe(command) == (
            f"mael task prompt {t.id} --project p "
            f"| MAEL_TASK_ID={t.id} MAEL_TASK_PARENT={t.id} "
            f"MAEL_TASK_SESSION_ID={sid} "
            f"claude --permission-mode plan --session-id {sid}"
        )
        kwargs = launch.exec.call_args.kwargs
        assert kwargs["cwd"] is None
        assert kwargs["env"]["MAEL_TASK_ID"] == t.id
        assert kwargs["env"]["MAEL_TASK_PARENT"] == t.id
        assert kwargs["replace_process"] is True
        assert f"Running {t.id} here (current shell)" in result.output

    def test_add_run_here(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["add", "Here go", "--run", "--here"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        launch.setup.assert_not_called()
        launch.session.assert_not_called()
        assert model.load(store, "p", new_id).status == model.STATUS_IN_PROGRESS
        assert launch.exec.call_args.kwargs["cwd"] is None
        assert launch.exec.call_args.kwargs["env"]["MAEL_TASK_ID"] == new_id

    def test_next_run_here(self, runner, store, launch):
        a = model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["next", "--run", "--here"])
        assert result.exit_code == 0, result.output
        launch.setup.assert_not_called()
        launch.session.assert_not_called()
        assert model.load(store, "p", a.id).status == model.STATUS_IN_PROGRESS
        assert launch.exec.call_args.kwargs["cwd"] is None
        assert launch.exec.call_args.kwargs["env"]["MAEL_TASK_ID"] == a.id


class TestPrompt:
    """``mael task prompt <id>`` prints exactly build_prompt(task)."""

    def test_prints_command_title_and_content(self, runner, store):
        t = model.create(
            store,
            project="p",
            title="Plan it",
            command="plan-task",
            content="do the thing",
        )
        result = runner.invoke(task_cli.task, ["prompt", t.id])
        assert result.exit_code == 0, result.output
        assert result.output == model.build_prompt(t)
        assert result.output == "/plan-task Plan it\n\ndo the thing"

    def test_no_command_no_content(self, runner, store):
        t = model.create(store, project="p", title="Bare task")
        result = runner.invoke(task_cli.task, ["prompt", t.id])
        assert result.exit_code == 0, result.output
        assert result.output == model.build_prompt(t)
        assert result.output == "Bare task"

    def test_content_without_command(self, runner, store):
        t = model.create(store, project="p", title="Exec it", content="run plan")
        result = runner.invoke(task_cli.task, ["prompt", t.id])
        assert result.exit_code == 0, result.output
        assert result.output == "Exec it\n\nrun plan"

    def test_unknown_task_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["prompt", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output


class TestContentFile:
    def test_content_file_reads_stdin_on_dash(self, runner, store):
        result = runner.invoke(
            task_cli.task,
            ["add", "Piped", "--content-file", "-"],
            input="brief from stdin\n",
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert "brief from stdin" in t.content

    def test_content_file_reads_path(self, runner, store, tmp_path):
        f = tmp_path / "brief.md"
        f.write_text("brief from file")
        result = runner.invoke(
            task_cli.task, ["add", "FromFile", "--content-file", str(f)]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.content == "brief from file"

    def test_content_file_missing_path_errors(self, runner, store, tmp_path):
        missing = tmp_path / "nope.md"
        result = runner.invoke(
            task_cli.task, ["add", "Missing", "--content-file", str(missing)]
        )
        assert result.exit_code != 0
        assert "Content file not found" in result.output


class TestDraft:
    def test_draft_writes_roundtrippable_file(self, runner, tmp_path):
        f = tmp_path / "draft-iter1.md"
        result = runner.invoke(
            task_cli.task,
            [
                "draft", str(f), "Execute: demo",
                "--mode", "auto", "--pre-action", "linear.in-progress",
            ],
        )
        assert result.exit_code == 0, result.output
        t = model.Task.from_markdown(f.read_text())
        assert t.title == "Execute: demo"
        assert t.mode == "auto"
        assert t.pre_action == "linear.in-progress"
        # Identity fields stay empty: the draft is not in the notebook.
        assert t.id == ""
        assert t.created == ""
        assert t.follows == []

    def test_draft_requires_title(self, runner, tmp_path):
        result = runner.invoke(task_cli.task, ["draft", str(tmp_path / "d.md")])
        assert result.exit_code != 0
        assert "title" in result.output.lower()
        assert not (tmp_path / "d.md").exists()

    def test_draft_refuses_overwrite_without_force(self, runner, tmp_path):
        f = tmp_path / "d.md"
        f.write_text("sculpted by hand\n")
        result = runner.invoke(task_cli.task, ["draft", str(f), "T"])
        assert result.exit_code != 0
        assert "--force" in result.output
        assert f.read_text() == "sculpted by hand\n"

    def test_draft_force_overwrites(self, runner, tmp_path):
        f = tmp_path / "d.md"
        f.write_text("old\n")
        result = runner.invoke(task_cli.task, ["draft", str(f), "T", "--force"])
        assert result.exit_code == 0, result.output
        assert model.Task.from_markdown(f.read_text()).title == "T"

    def test_draft_content_file(self, runner, tmp_path):
        src = tmp_path / "body.md"
        src.write_text("The plan body.\n")
        f = tmp_path / "d.md"
        result = runner.invoke(
            task_cli.task, ["draft", str(f), "T", "--content-file", str(src)]
        )
        assert result.exit_code == 0, result.output
        assert model.Task.from_markdown(f.read_text()).content == "The plan body."

    def test_draft_rejects_follow_flags(self, runner, tmp_path):
        # Chain wiring happens at promote time; draft has no --follow.
        result = runner.invoke(
            task_cli.task, ["draft", str(tmp_path / "d.md"), "T", "--follow", "x"]
        )
        assert result.exit_code != 0

    def test_draft_is_inert_until_promoted(self, runner, store, tmp_path):
        # The approval gate is structural: a draft never reaches the store, so
        # it is invisible to list/next/follow-end until promote loads it.
        result = runner.invoke(task_cli.task, ["draft", str(tmp_path / "d.md"), "T"])
        assert result.exit_code == 0, result.output
        assert model.list_tasks(store, project="p") == []


class TestPromote:
    def _draft(self, runner, tmp_path, name="d.md", title="Execute: demo", *args):
        f = tmp_path / name
        result = runner.invoke(task_cli.task, ["draft", str(f), title, *args])
        assert result.exit_code == 0, result.output
        return f

    def test_promote_creates_todo_task_and_consumes_file(
        self, runner, store, tmp_path
    ):
        f = self._draft(
            runner, tmp_path, "d.md", "Execute: demo",
            "--mode", "auto", "--command", "plan-next-step",
            "--pre-action", "linear.in-progress", "--model", "opus",
        )
        result = runner.invoke(task_cli.task, ["promote", str(f)])
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        t = model.load(store, "p", new_id)
        assert t.status == "todo"
        assert t.title == "Execute: demo"
        assert t.mode == "auto"
        assert t.command == "plan-next-step"
        assert t.pre_action == "linear.in-progress"
        assert t.model == "opus"
        # Promotion consumes the draft — it has moved into the notebook.
        assert not f.exists()

    def test_promote_body_content_becomes_task_content(
        self, runner, store, tmp_path
    ):
        f = self._draft(runner, tmp_path)
        text = f.read_text().replace(
            "## Content\n\n", "## Content\n\nThe sculpted plan.\n"
        )
        f.write_text(text)
        result = runner.invoke(task_cli.task, ["promote", str(f)])
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.content == "The sculpted plan."

    def test_promote_flag_overrides_file_field(self, runner, store, tmp_path):
        f = self._draft(runner, tmp_path, "d.md", "T", "--mode", "auto")
        result = runner.invoke(
            task_cli.task, ["promote", str(f), "--mode", "normal"]
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", result.output.strip()).mode == "normal"

    def test_promote_wires_follow(self, runner, store, tmp_path):
        first = model.create(store, project="p", title="first")
        f = self._draft(runner, tmp_path)
        result = runner.invoke(
            task_cli.task, ["promote", str(f), "--follow", first.id]
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", result.output.strip()).follows == [first.id]

    def test_promote_follow_end_wildcard_appends_to_parent_chain(
        self, runner, store, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
        existing = model.create(
            store, project="p", title="prev", parent="linear.NORT-9"
        )
        f = self._draft(runner, tmp_path)
        result = runner.invoke(
            task_cli.task, ["promote", str(f), "--follow-end", "*"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.parent == "linear.NORT-9"
        assert t.follows == [existing.id]

    def test_promote_missing_file_errors(self, runner, store, tmp_path):
        result = runner.invoke(
            task_cli.task, ["promote", str(tmp_path / "absent.md")]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_promote_bad_frontmatter_leaves_file(self, runner, store, tmp_path):
        f = tmp_path / "d.md"
        f.write_text('---\ntitle: "unclosed\n---\n\nBody.\n')
        result = runner.invoke(task_cli.task, ["promote", str(f)])
        assert result.exit_code != 0
        assert f.exists()
        assert model.list_tasks(store, project="p") == []

    def test_promote_missing_title_leaves_file(self, runner, store, tmp_path):
        f = tmp_path / "d.md"
        f.write_text("---\nmode: auto\n---\n\n## Content\n\nBody.\n")
        result = runner.invoke(task_cli.task, ["promote", str(f)])
        assert result.exit_code != 0
        assert "title" in result.output.lower()
        assert f.exists()
        assert model.list_tasks(store, project="p") == []


class TestLoadMany:
    def test_creates_chain_with_block_follow(self, runner, store, tmp_path):
        f = tmp_path / "plan.md"
        f.write_text(
            "Preamble: only action is `mael task load-many <file>`.\n"
            "\n"
            "---CREATE TASK iter1---\n"
            "title: First step\n"
            "---\n"
            "## Scope\n"
            "do the first thing\n"
            "---CREATE TASK tail---\n"
            "title: Plan next step\n"
            "command: plan-next-step\n"
            "follow: iter1\n"
            "---\n"
            "## Remaining\n"
            "the rest\n"
        )
        result = runner.invoke(task_cli.task, ["load-many", str(f)])
        assert result.exit_code == 0, result.output
        # Two ids printed, one per line.
        lines = [ln for ln in result.output.strip().split("\n") if ln]
        assert len(lines) == 2
        first_id = lines[0].split("\t")[0]
        second_id = lines[1].split("\t")[0]
        # The second task follows the first (block name resolved to real id).
        second = model.load(store, "p", second_id)
        assert second.follows == [first_id]
        assert second.command == "plan-next-step"
        assert "the rest" in second.content

    def test_reads_stdin_on_dash(self, runner, store):
        text = "---CREATE TASK a---\ntitle: From stdin\n---\nbody\n"
        result = runner.invoke(task_cli.task, ["load-many", "-"], input=text)
        assert result.exit_code == 0, result.output
        line = result.output.strip().split("\n")[0]
        t = model.load(store, "p", line.split("\t")[0])
        assert t.title == "From stdin"

    def test_bad_file_unknown_key_exits_nonzero(self, runner, store, tmp_path):
        f = tmp_path / "bad.md"
        f.write_text("---CREATE TASK a---\ntitle: A\nfollows: b\n---\nbody\n")
        result = runner.invoke(task_cli.task, ["load-many", str(f)])
        assert result.exit_code != 0
        assert "Unknown key" in result.output

    def test_blocks_default_parent_from_env(self, runner, store, monkeypatch, tmp_path):
        # With MAEL_TASK_PARENT set and no `parent:` in the block, the created
        # task nests under that parent, and follow-end:* appends to its siblings.
        monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
        existing = model.create(store, project="p", title="prev", parent="linear.NORT-9")
        f = tmp_path / "plan.md"
        f.write_text(
            "---CREATE TASK step---\ntitle: Step\nfollow-end: \"*\"\n---\nbody\n"
        )
        result = runner.invoke(task_cli.task, ["load-many", str(f)])
        assert result.exit_code == 0, result.output
        created = model.load(store, "p", result.output.split("\t")[0])
        assert created.parent == "linear.NORT-9"
        assert created.follows == [existing.id]

    def _two_block_plan(self, tmp_path):
        f = tmp_path / "plan.md"
        f.write_text(
            "---CREATE TASK iter1---\n"
            "title: First step\n"
            "---\n"
            "do the first thing\n"
            "---CREATE TASK tail---\n"
            "title: Plan next step\n"
            "follow: iter1\n"
            "---\n"
            "the rest\n"
        )
        return f

    def test_load_many_run_launches_head_task(self, runner, store, launch, tmp_path):
        f = self._two_block_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.output.strip().split("\n") if ln]
        # Two task lines + the announcement line.
        head_id = lines[0].split("\t")[0]
        tail_id = lines[1].split("\t")[0]
        assert any(head_id in ln and "do *not* work on it" in ln for ln in lines)
        # This plan is a genuine chain (tail follows iter1), so only the head is
        # unblocked — one launch even though --run is now multi-launch.
        launch.session.assert_called_once()
        assert launch.session.call_args.kwargs["task_id"] == head_id
        # Move-before-launch parity: head is in-progress, tail still blocked.
        assert model.load(store, "p", head_id).status == model.STATUS_IN_PROGRESS
        assert model.load(store, "p", tail_id).status == model.STATUS_TODO

    def test_load_many_run_head_never_resumes_stale_transcript(
        self, runner, store, launch, tmp_path, monkeypatch
    ):
        # Regression: the head is a brand-new task, so even when a stale
        # transcript for its deterministic session-id already sits in the
        # reused worktree, it must launch with `--session-id` (create), never
        # `--resume`. Before the `fresh=True` fix this launched with resume=True
        # and the tab died immediately.
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        f = self._two_block_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert launch.session.call_args.kwargs["resume"] is False
        assert "(resuming)" not in result.output

    def test_load_many_without_run_does_not_launch(
        self, runner, store, launch, tmp_path
    ):
        f = self._two_block_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f)])
        assert result.exit_code == 0, result.output
        launch.session.assert_not_called()
        launch.setup.assert_not_called()

    def test_load_many_run_announces_head_and_not_implement(
        self, runner, store, launch, tmp_path
    ):
        f = self._two_block_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        head_id = result.output.strip().split("\n")[0].split("\t")[0]
        announce = next(
            ln for ln in result.output.split("\n") if "separate claude session" in ln
        )
        assert head_id in announce
        assert "do *not* work on it yourself" in announce

    def _three_independent_plan(self, tmp_path):
        """Three blocks with no ``follow`` — all three are unblocked at once."""
        f = tmp_path / "parallel.md"
        f.write_text(
            "---CREATE TASK one---\ntitle: One\n---\nfirst\n"
            "---CREATE TASK two---\ntitle: Two\n---\nsecond\n"
            "---CREATE TASK three---\ntitle: Three\n---\nthird\n"
        )
        return f

    def test_load_many_run_launches_every_unblocked_task(
        self, runner, store, launch, tmp_path
    ):
        f = self._three_independent_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        ids = [ln.split("\t")[0] for ln in result.output.strip().split("\n")[:3]]
        assert launch.session.call_count == 3
        # Launched in `created` order, so the head still goes first.
        assert [c.kwargs["task_id"] for c in launch.session.call_args_list] == ids
        for tid in ids:
            assert model.load(store, "p", tid).status == model.STATUS_IN_PROGRESS

    def test_load_many_run_leaves_followers_queued(
        self, runner, store, launch, tmp_path
    ):
        # Two independent blocks + one following `two`: only the independents are
        # actionable, so the follower stays in todo/ for `task next --run`.
        f = tmp_path / "mixed.md"
        f.write_text(
            "---CREATE TASK one---\ntitle: One\n---\nfirst\n"
            "---CREATE TASK two---\ntitle: Two\n---\nsecond\n"
            "---CREATE TASK three---\ntitle: Three\nfollow: two\n---\nthird\n"
        )
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        ids = [ln.split("\t")[0] for ln in result.output.strip().split("\n")[:3]]
        assert launch.session.call_count == 2
        assert [c.kwargs["task_id"] for c in launch.session.call_args_list] == ids[:2]
        assert model.load(store, "p", ids[2]).status == model.STATUS_TODO

    def test_load_many_run_here_launches_head_only(
        self, runner, store, launch, tmp_path
    ):
        # --here execvp's, so a loop is impossible by construction: head only.
        f = self._three_independent_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run", "--here"])
        assert result.exit_code == 0, result.output
        ids = [ln.split("\t")[0] for ln in result.output.strip().split("\n")[:3]]
        launch.exec.assert_called_once()
        launch.session.assert_not_called()
        assert model.load(store, "p", ids[0]).status == model.STATUS_IN_PROGRESS
        assert model.load(store, "p", ids[1]).status == model.STATUS_TODO

    def test_load_many_run_continues_past_a_failed_launch(
        self, runner, store, launch, tmp_path, monkeypatch
    ):
        # A live session on the *second* task trips the duplicate-launch guard.
        # The other two must still launch, and the failure must be reported.
        f = self._three_independent_plan(tmp_path)

        def sweep():
            # Resolved lazily: the ids don't exist until load-many has created
            # them, which happens after this fixture is installed.
            # Unfiltered by status: earlier tasks in the batch have already moved
            # out of todo/ by the time later ones sweep.
            all_ids = sorted(t.id for t in model.list_tasks(store, project="p"))
            second = all_ids[1:2]
            return [
                _live_session(pid=77, session_id=model.session_id_for("p", i))
                for i in second
            ]

        _patch_live_sessions(monkeypatch, sweep)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code != 0
        ids = [ln.split("\t")[0] for ln in result.output.strip().split("\n")[:3]]
        assert launch.session.call_count == 2
        assert [c.kwargs["task_id"] for c in launch.session.call_args_list] == [
            ids[0],
            ids[2],
        ]
        assert f"warning: {ids[1]}" in result.output
        assert "1 of 3 tasks failed to launch" in result.output
        assert model.load(store, "p", ids[1]).status == model.STATUS_TODO

    def test_load_many_run_continues_past_a_runtime_error(
        self, runner, store, launch, tmp_path
    ):
        # Worktree/port allocation raises RuntimeError, not ClickException —
        # e.g. allocate_port_base exhausting the 300-999 range, which a batch
        # launch can genuinely provoke. One task's failure must not abandon the
        # rest, and the task must stay in todo/ (the raise precedes the status
        # move).
        f = self._three_independent_plan(tmp_path)
        calls: list[str] = []

        def setup(*a, **kw):
            calls.append("x")
            if len(calls) == 2:
                raise RuntimeError("No available port ranges found")
            return WorktreeSetup(path=launch.wt_path, name="bravo", action="created")

        launch.setup.side_effect = setup
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code != 0
        ids = [ln.split("\t")[0] for ln in result.output.strip().split("\n")[:3]]
        assert launch.session.call_count == 2
        assert [c.kwargs["task_id"] for c in launch.session.call_args_list] == [
            ids[0],
            ids[2],
        ]
        assert f"warning: {ids[1]} — No available port ranges found" in result.output
        assert "1 of 3 tasks failed to launch" in result.output
        assert model.load(store, "p", ids[1]).status == model.STATUS_TODO

    def test_load_many_run_starts_cmux_once_for_the_batch(
        self, runner, store, launch, tmp_path
    ):
        f = self._three_independent_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        assert launch.session.call_count == 3
        launch.ensure_cmux.assert_called_once()

    def test_load_many_run_then_next_skips_head(self, runner, store, launch, tmp_path):
        # A+B end-to-end: load-many --run marks the head in-progress, so
        # `task next` steps past it. Here the only follow-up is blocked behind
        # the head, so next has nothing actionable.
        f = self._two_block_plan(tmp_path)
        result = runner.invoke(task_cli.task, ["load-many", str(f), "--run"])
        assert result.exit_code == 0, result.output
        head_id = result.output.strip().split("\n")[0].split("\t")[0]
        nxt = model.next_task(store, "p")
        assert nxt is None or nxt.id != head_id


class TestAddParentDefault:
    def test_add_defaults_parent_from_env(self, runner, store, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
        result = runner.invoke(task_cli.task, ["add", "Child"])
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.parent == "linear.NORT-9"

    def test_explicit_parent_overrides_env(self, runner, store, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
        result = runner.invoke(
            task_cli.task, ["add", "Child", "--parent", "linear.OTHER"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.parent == "linear.OTHER"

    def test_add_follow_end_wildcard(self, runner, store, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
        prev = model.create(store, project="p", title="prev", parent="linear.NORT-9")
        result = runner.invoke(
            task_cli.task, ["add", "Next", "--follow-end", "*"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.follows == [prev.id]


class TestStatus:
    @pytest.mark.parametrize(
        "sub,status",
        [
            ("start", model.STATUS_IN_PROGRESS),
            ("done", model.STATUS_DONE),
            ("cancel", model.STATUS_CANCELLED),
            ("block", model.STATUS_BLOCKED),
        ],
    )
    def test_status_with_id_moves_task(self, runner, store, sub, status):
        t = model.create(store, project="p", title="t")
        result = runner.invoke(task_cli.task, ["status", sub, t.id])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).status == status
        assert f"{t.id} -> {status}" in result.output

    def test_status_todo_moves_task_back(self, runner, store):
        t = model.create(store, project="p", title="t")
        model.move(store, "p", t.id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["status", "todo", t.id])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).status == model.STATUS_TODO
        assert f"{t.id} -> {model.STATUS_TODO}" in result.output

    def test_status_env_fallback(self, runner, store, monkeypatch):
        t = model.create(store, project="p", title="t")
        monkeypatch.setenv("MAEL_TASK_ID", t.id)
        result = runner.invoke(task_cli.task, ["status", "done"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).status == model.STATUS_DONE

    def test_status_no_id_and_no_env_errors(self, runner, store, monkeypatch):
        model.create(store, project="p", title="t")
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["status", "done"])
        assert result.exit_code != 0
        assert "No task id" in result.output

    def test_status_unknown_id_errors(self, runner, store, monkeypatch):
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["status", "done", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output

    def test_old_flat_command_gone(self, runner, store):
        t = model.create(store, project="p", title="t")
        result = runner.invoke(task_cli.task, ["done", t.id])
        assert result.exit_code != 0

    def test_group_help_describes_each_subcommand(self, runner):
        # _status_command generates these, so the help text has to reach the
        # decorator: Click reads it when it builds the Command.
        result = runner.invoke(task_cli.task, ["status", "--help"])
        assert result.exit_code == 0, result.output
        assert "Move a task to in-progress." in result.output
        assert "Park a task as a reusable template." in result.output


class TestGetStatus:
    def test_prints_bare_status(self, runner, store):
        # The status line embeds the output verbatim, so it must be the status
        # word alone — no label, no id.
        t = model.create(store, project="p", title="t")
        model.move(store, "p", t.id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["get-status", t.id])
        assert result.exit_code == 0, result.output
        assert result.output == f"{model.STATUS_IN_PROGRESS}\n"

    def test_env_fallback(self, runner, store, monkeypatch):
        t = model.create(store, project="p", title="t")
        monkeypatch.setenv("MAEL_TASK_ID", t.id)
        result = runner.invoke(task_cli.task, ["get-status"])
        assert result.exit_code == 0, result.output
        assert result.output == f"{model.STATUS_TODO}\n"

    def test_no_id_and_no_env_errors(self, runner, store, monkeypatch):
        model.create(store, project="p", title="t")
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["get-status"])
        assert result.exit_code != 0
        assert "No task id" in result.output

    def test_unknown_id_errors(self, runner, store, monkeypatch):
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["get-status", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output


class TestCurrent:
    def test_prints_id_and_status(self, runner, store, monkeypatch):
        t = model.create(store, project="p", title="t")
        model.move(store, "p", t.id, model.STATUS_IN_PROGRESS)
        monkeypatch.setenv("MAEL_TASK_ID", t.id)
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == f"{t.id}:{model.STATUS_IN_PROGRESS}\n"

    def test_outside_a_task_session_prints_nothing(self, runner, store, monkeypatch):
        model.create(store, project="p", title="t")
        # A prompt calls this on every redraw, so "no task" is an ordinary
        # answer: empty output, exit 0 — never an error.
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"

    def test_outside_a_project_dir_prints_nothing(self, runner, store, monkeypatch):
        # A prompt runs from anywhere, including outside any project, where
        # _resolve_project raises. The store fixture stubs that call out, so
        # override it to get the real failure back.
        def _boom(project):
            raise ValueError("Could not determine project.")

        monkeypatch.setattr(task_cli, "_resolve_project", _boom)
        monkeypatch.setenv("MAEL_TASK_ID", "2026-06-11.3")
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"

    def test_vanished_task_prints_nothing(self, runner, store, monkeypatch):
        # The id outlives the task if it is deleted mid-session. The prompt must
        # still render, so degrade to empty rather than failing.
        model.create(store, project="p", title="t")
        monkeypatch.setenv("MAEL_TASK_ID", "nope")
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"


class TestEnvThreading:
    def test_run_threads_task_id_and_parent_env(self, runner, store, launch):
        # A child task carries a parent; both ids should reach the session env.
        model.create(store, project="p", title="Parent task", parent="linear.ME-1")
        t = model.create(
            store, project="p", title="Child", parent="linear.ME-1"
        )
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == t.id
        assert env["MAEL_TASK_PARENT"] == "linear.ME-1"

    def test_run_self_parents_when_orphan(self, runner, store, launch):
        # A parentless task self-parents so the chain it emits nests under it
        # and shares its branch, rather than each child becoming a fresh orphan.
        t = model.create(store, project="p", title="Orphan")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == t.id
        assert env["MAEL_TASK_PARENT"] == t.id  # self-parent, not omitted


# --- list: BRANCH column ---


class TestListBranch:
    def test_branch_column_shows_default_when_blank(self, runner, store):
        t = model.create(store, project="p", title="alpha")
        # Force a blank branch to exercise the inferred fallback.
        model.update(store, "p", t.id, branch="")
        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert "BRANCH" in result.output
        assert f"task/{t.id}" in result.output

    def test_branch_column_shows_explicit_branch(self, runner, store):
        t = model.create(store, project="p", title="alpha", branch="feat/foo")
        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert "feat/foo" in result.output

    def test_branch_column_in_all_views(self, runner, store):
        model.create(store, project="p", title="alpha", branch="feat/bar")
        for args in (["list"], ["list", "--all-todo"], ["list", "--all"]):
            result = runner.invoke(task_cli.task, args)
            assert "BRANCH" in result.output, args


# --- update ---


class TestUpdate:
    def test_update_branch(self, runner, store):
        t = model.create(store, project="p", title="alpha")
        result = runner.invoke(
            task_cli.task, ["update", t.id, "--branch", "feat/foo"]
        )
        assert result.exit_code == 0, result.output
        assert f"Updated {t.id}" in result.output
        assert model.load(store, "p", t.id).branch == "feat/foo"

    def test_update_title_via_positional(self, runner, store):
        t = model.create(store, project="p", title="old")
        result = runner.invoke(task_cli.task, ["update", t.id, "new title"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).title == "new title"

    def test_update_content_from_stdin(self, runner, store):
        t = model.create(store, project="p", title="alpha", content="old body")
        result = runner.invoke(
            task_cli.task,
            ["update", t.id, "--content-file", "-"],
            input="new body\n",
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).content == "new body"

    def test_update_command_and_mode(self, runner, store):
        t = model.create(store, project="p", title="alpha", command="plan-task")
        result = runner.invoke(
            task_cli.task,
            ["update", t.id, "--command", "execute", "--mode", "plan"],
        )
        assert result.exit_code == 0, result.output
        reloaded = model.load(store, "p", t.id)
        assert reloaded.command == "execute"
        assert reloaded.mode == "plan"

    def test_update_unknown_id_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["update", "nope", "--branch", "x"])
        assert result.exit_code != 0
        assert "Task not found" in result.output

    def test_update_bumps_updated(self, runner, store):
        t = model.create(store, project="p", title="alpha", now="2020-01-01T00:00:00")
        runner.invoke(task_cli.task, ["update", t.id, "--branch", "feat/foo"])
        assert model.load(store, "p", t.id).updated != "2020-01-01T00:00:00"

    def test_update_omitted_fields_untouched(self, runner, store):
        t = model.create(
            store, project="p", title="keep", branch="b", content="body"
        )
        runner.invoke(task_cli.task, ["update", t.id, "--branch", "b2"])
        reloaded = model.load(store, "p", t.id)
        assert reloaded.title == "keep"
        assert reloaded.content == "body"
        assert reloaded.branch == "b2"


# --- duplicate (--from) ---


class TestDuplicate:
    def test_from_copies_recipe(self, runner, store):
        src = model.create(
            store, project="p", title="Orig", command="plan-task",
            mode="auto", content="the body",
        )
        result = runner.invoke(task_cli.task, ["add", "--from", src.id])
        assert result.exit_code == 0, result.output
        new = model.load(store, "p", result.output.strip())
        assert new.id != src.id
        assert new.title == "Orig"
        assert new.command == "plan-task"
        assert new.mode == "auto"
        assert new.content == "the body"
        assert new.status == model.STATUS_TODO

    def test_from_overrides_win(self, runner, store):
        src = model.create(store, project="p", title="Orig", command="plan-task")
        result = runner.invoke(
            task_cli.task,
            ["add", "New title", "--from", src.id, "--command", "other"],
        )
        new = model.load(store, "p", result.output.strip())
        assert new.title == "New title"
        assert new.command == "other"

    def test_source_untouched(self, runner, store):
        src = model.create(store, project="p", title="Orig", content="x")
        runner.invoke(task_cli.task, ["add", "--from", src.id])
        again = model.load(store, "p", src.id)
        assert again.title == "Orig"
        assert again.content == "x"

    def test_from_works_from_template_status(self, runner, store):
        src = model.create(
            store, project="p", title="Tmpl", status=model.STATUS_TEMPLATE,
            id="tmpl",
        )
        result = runner.invoke(task_cli.task, ["add", "--from", src.id])
        assert result.exit_code == 0, result.output
        new = model.load(store, "p", result.output.strip())
        assert new.title == "Tmpl"
        assert new.status == model.STATUS_TODO

    def test_from_unknown_id_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "--from", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output

    def test_title_required_without_from(self, runner, store):
        result = runner.invoke(task_cli.task, ["add"])
        assert result.exit_code != 0
        assert "title is required" in result.output.lower()


# --- priority ---


class TestPriority:
    def test_add_records_priority(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "urgent", "--priority", "high"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", result.output.strip()).priority == "high"

    def test_add_defaults_to_medium(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "normal"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", result.output.strip()).priority == "medium"

    def test_add_rejects_bogus_priority(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "x", "--priority", "bogus"])
        assert result.exit_code != 0

    def test_from_inherits_source_priority(self, runner, store):
        src = model.create(store, project="p", title="Orig", priority="critical")
        result = runner.invoke(task_cli.task, ["add", "--from", src.id])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", result.output.strip()).priority == "critical"

    def test_from_priority_override_wins(self, runner, store):
        src = model.create(store, project="p", title="Orig", priority="critical")
        result = runner.invoke(
            task_cli.task, ["add", "--from", src.id, "--priority", "low"]
        )
        assert model.load(store, "p", result.output.strip()).priority == "low"

    def test_update_priority(self, runner, store):
        t = model.create(store, project="p", title="alpha")
        result = runner.invoke(
            task_cli.task, ["update", t.id, "--priority", "critical"]
        )
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).priority == "critical"

    def test_update_rejects_bogus_priority(self, runner, store):
        t = model.create(store, project="p", title="alpha")
        result = runner.invoke(task_cli.task, ["update", t.id, "--priority", "bogus"])
        assert result.exit_code != 0

    def test_show_prints_priority(self, runner, store):
        t = model.create(store, project="p", title="alpha", priority="high")
        result = runner.invoke(task_cli.task, ["show", t.id])
        assert result.exit_code == 0, result.output
        assert "priority: high" in result.output

    def test_update_sets_the_model(self, runner, store):
        t = model.create(store, project="p", title="alpha")
        result = runner.invoke(task_cli.task, ["update", t.id, "--model", "opus"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).model == "opus"

    def test_update_clears_the_model_with_empty_string(self, runner, store):
        t = model.create(store, project="p", title="alpha", model="opus")
        result = runner.invoke(task_cli.task, ["update", t.id, "--model", ""])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).model == ""

    def test_update_without_model_leaves_it_alone(self, runner, store):
        # The default=None "unset" semantics: touching another field must not
        # blank the model.
        t = model.create(store, project="p", title="alpha", model="opus")
        result = runner.invoke(task_cli.task, ["update", t.id, "--branch", "x"])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", t.id).model == "opus"

    def test_show_prints_model_when_set(self, runner, store):
        t = model.create(store, project="p", title="alpha", model="opus")
        result = runner.invoke(task_cli.task, ["show", t.id])
        assert result.exit_code == 0, result.output
        assert "model:   opus" in result.output

    def test_show_omits_model_when_unset(self, runner, store):
        # Empty means "inherit the user's default" — nothing to report, so the
        # line is suppressed like parent/schedule.
        t = model.create(store, project="p", title="alpha")
        result = runner.invoke(task_cli.task, ["show", t.id])
        assert result.exit_code == 0, result.output
        assert "model:" not in result.output

    def test_list_orders_critical_above_low(self, runner, store):
        low = model.create(store, project="p", title="low one", priority="low")
        crit = model.create(store, project="p", title="crit one", priority="critical")
        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert "PRIORITY" in result.output
        # The critical task's row must appear before the low one's.
        assert result.output.index(crit.id) < result.output.index(low.id)


# --- templates + schedule metadata ---


class TestTemplates:
    def test_add_template_parks_in_template_status(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "Morning", "--template", "--schedule", "0 9 * * *"]
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.status == model.STATUS_TEMPLATE
        assert t.schedule == "0 9 * * *"

    def test_template_invisible_to_next(self, runner, store):
        runner.invoke(task_cli.task, ["add", "Tmpl", "--template"])
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code != 0  # no actionable task

    def test_template_invisible_to_default_list(self, runner, store):
        tid = runner.invoke(
            task_cli.task, ["add", "Tmpl", "--template"]
        ).output.strip()
        result = runner.invoke(task_cli.task, ["list"])
        assert tid not in result.output

    def test_template_listed_with_status_filter(self, runner, store):
        tid = runner.invoke(
            task_cli.task, ["add", "Tmpl", "--template"]
        ).output.strip()
        result = runner.invoke(task_cli.task, ["list", "--status", "template"])
        assert tid in result.output

    def test_update_schedule_round_trips(self, runner, store):
        tid = runner.invoke(
            task_cli.task, ["add", "Tmpl", "--template"]
        ).output.strip()
        runner.invoke(
            task_cli.task, ["update", tid, "--schedule", "0 9 * * 1-5"]
        )
        assert model.load(store, "p", tid).schedule == "0 9 * * 1-5"

    def test_status_template_parks_existing_task(self, runner, store):
        tid = runner.invoke(task_cli.task, ["add", "Existing"]).output.strip()
        result = runner.invoke(task_cli.task, ["status", "template", tid])
        assert result.exit_code == 0, result.output
        assert model.load(store, "p", tid).status == model.STATUS_TEMPLATE

    def test_template_from_duplicate(self, runner, store):
        src = model.create(store, project="p", title="Base", command="plan-task")
        result = runner.invoke(
            task_cli.task, ["add", "--from", src.id, "--template"]
        )
        new = model.load(store, "p", result.output.strip())
        assert new.status == model.STATUS_TEMPLATE
        assert new.command == "plan-task"


# --- add-scheduled (catch-up / idempotency / launch) ---


def _make_template(store, *, schedule, last_run="", created):
    return model.create(
        store, project="p", title="Maintenance", command="",
        schedule=schedule, last_run=last_run,
        status=model.STATUS_TEMPLATE, id="maintenance", now=created,
    )


class TestAddScheduled:
    def test_one_run_created_and_watermark_advances(self, runner, store, monkeypatch):
        from datetime import datetime

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-11T09:00:00",
            created="2026-06-01T00:00:00",
        )
        # Freeze "now" to a fixed *local* wall-clock — the command uses
        # datetime.now().astimezone(), so the scheduler matches local time.
        real_dt = datetime
        frozen_local = real_dt(2026, 6, 18, 10, 0).astimezone()

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_local

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code == 0, result.output
        run = model.load(store, "p", "maintenance.2026-06-18")
        # The run is parentless: its dot-id names it under the template, but its
        # empty parent lets it root its own chain (follow-ups nest under the run).
        assert run.parent == ""
        assert run.id == "maintenance.2026-06-18"
        # Exactly one run (catch-up is a single boundary, not 7).
        runs = [
            t for t in model.list_tasks(store, project="p")
            if t.id.startswith("maintenance.")
        ]
        assert len(runs) == 1
        # Watermark advanced to today's 09:00 *local* boundary.
        tmpl = model.load(store, "p", "maintenance")
        expected = real_dt(2026, 6, 18, 9, 0).astimezone().isoformat()
        assert tmpl.last_run == expected

    def test_idempotent_second_call_creates_nothing(self, runner, store, monkeypatch):
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert "No scheduled tasks due." in result.output
        runs = [
            t for t in model.list_tasks(store, project="p")
            if t.id.startswith("maintenance.")
        ]
        assert len(runs) == 1

    def test_not_due_creates_nothing(self, runner, store, monkeypatch):
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert "No scheduled tasks due." in result.output

    def test_run_is_timestamped(self, runner, store, monkeypatch):
        """Every run emits a dated header so schedule.log records when it fired."""
        from datetime import datetime

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00",
            created="2026-06-01T00:00:00",
        )
        real_dt = datetime
        frozen_local = real_dt(2026, 6, 18, 10, 0).astimezone()

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_local

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        # Header is the first line, carries the *local* ISO firing time (no
        # nothing-due template here so the timestamp prefixes the run).
        stamp = frozen_local.isoformat(timespec="seconds")
        assert result.output.startswith(f"[{stamp}] add-scheduled")

    def test_run_inherits_template_branch(self, runner, store, monkeypatch):
        """A scheduled run lands on the template's own branch, not task/<tmpl-id>."""
        from datetime import datetime, timezone

        model.create(
            store, project="p", title="Maintenance", command="",
            schedule="0 9 * * *", last_run="2026-06-17T09:00:00+00:00",
            branch="chore/maint", status=model.STATUS_TEMPLATE,
            id="maintenance", now="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code == 0, result.output
        run = model.load(store, "p", "maintenance.2026-06-18")
        assert run.parent == ""
        assert run.branch == "chore/maint"

    def test_run_launches_into_workspace(self, runner, store, monkeypatch, launch):
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        # The run is parentless, so the launched session exports its own id as the
        # chain parent — follow-ups nest under the run, not the template.
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == "maintenance.2026-06-18"
        assert env["MAEL_TASK_PARENT"] == "maintenance.2026-06-18"

    def test_run_never_resumes_stale_transcript(
        self, runner, store, monkeypatch, launch
    ):
        # A scheduled run is a freshly-created task, so it launches with
        # `--session-id` (create) even when a stale transcript is present.
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert launch.session.call_args.kwargs["resume"] is False

    def test_run_ensures_cmux_once_and_attempts_every_due_run(
        self, runner, store, monkeypatch, launch
    ):
        # Two due templates fire in one --run pass: cmux is started ONCE for the
        # batch, and BOTH runs are attempted (the launch loop is never abandoned
        # — the old execvp-in-loop bug is gone by construction).
        from datetime import datetime, timezone

        for tmpl_id in ("maint-a", "maint-b"):
            model.create(
                store, project="p", title="Maintenance", command="",
                schedule="0 9 * * *", last_run="2026-06-17T09:00:00+00:00",
                status=model.STATUS_TEMPLATE, id=tmpl_id,
                now="2026-06-01T00:00:00+00:00",
            )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        # One app-start for the whole batch.
        launch.ensure_cmux.assert_called_once()
        # Both due runs launched — the loop completed.
        assert launch.session.call_count == 2
        launched_ids = {
            c.kwargs["task_id"] for c in launch.session.call_args_list
        }
        assert launched_ids == {"maint-a.2026-06-18", "maint-b.2026-06-18"}

    def test_here_run_still_execs_and_skips_ensure_cmux(
        self, runner, store, monkeypatch, launch
    ):
        # `--run --here` runs Claude in the current shell (execvp), so it must
        # NOT start the cmux app and NOT go through the workspace launcher.
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(
            task_cli.task, ["add-scheduled", "-p", "p", "--run", "--here"]
        )
        assert result.exit_code == 0, result.output
        launch.ensure_cmux.assert_not_called()
        launch.session.assert_not_called()
        launch.exec.assert_called_once()
        # replace_process exec in the current shell.
        assert launch.exec.call_args.kwargs["replace_process"] is True
