"""Tests for the ``mael task`` CLI, against an InMemoryStore.

The CLI is exercised via Click's ``CliRunner``. ``task_cli._store`` is patched
to return a shared :class:`InMemoryStore` and ``_resolve_project`` to a fixed
project, so no git or cwd resolution happens.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from maelstrom import task as model
from maelstrom import task_cli
from maelstrom.task_store import InMemoryStore
from maelstrom.worktree import WorktreeSetup


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    s = InMemoryStore()
    monkeypatch.setattr(task_cli, "_store", lambda: s)
    monkeypatch.setattr(task_cli, "_resolve_project", lambda project: project or "p")
    return s


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Stub the launch collaborators of ``_run_task``.

    Returns a namespace with the mocked ``setup`` and ``session`` callables and
    the worktree path the fake setup returns.
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
    session = MagicMock()
    monkeypatch.setattr(task_cli, "setup_worktree_for_branch", setup)
    monkeypatch.setattr(task_cli, "start_claude_session", session)
    return SimpleNamespace(setup=setup, session=session, wt_path=wt_path)


# --- add: branch defaulting / override ---


class TestAddBranch:
    def test_branch_defaults_to_task_slash_id(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "Smoke"])
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        assert model.load(store, "p", new_id).branch == f"task/{new_id}"

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

    def test_blocked_folder_with_deps_done_shows_by_default(self, runner, store):
        # A task sitting in blocked/ but with all follows done is actionable,
        # so it must show by default — we key off is_actionable, not the folder.
        dep = model.create(store, project="p", title="dep")
        model.move(store, "p", dep.id, model.STATUS_DONE)
        t = model.create(store, project="p", title="manually-blocked", follows=[dep.id])
        model.move(store, "p", t.id, model.STATUS_BLOCKED)

        result = runner.invoke(task_cli.task, ["list"])
        assert result.exit_code == 0, result.output
        assert t.id in result.output


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

        # Core fn called with the task's branch (defaults to task/<id>).
        assert launch.setup.call_args.args[2] == t.branch == f"task/{t.id}"

        # Task is now in-progress.
        assert model.load(store, "p", t.id).status == model.STATUS_IN_PROGRESS

        # Session launched with the right prompt / mode / worktree / project.
        kwargs = launch.session.call_args.kwargs
        assert kwargs["initial_prompt"] == model.build_prompt(t)
        assert kwargs["permission_mode"] == "plan"
        assert kwargs["project"] == "p"
        assert kwargs["worktree"] == "bravo"
        assert f"Running {t.id} on {t.branch}" in result.output
        assert "→ p/bravo (created)" in result.output

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
        session = MagicMock()
        monkeypatch.setattr(task_cli, "start_claude_session", session)
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code != 0
        assert "not found" in result.output
        session.assert_not_called()


class TestAddRun:
    def test_add_run_creates_then_moves_then_launches(self, runner, store, launch):
        # Capture the task status at launch time to prove move-before-launch.
        seen = {}

        def fake_session(*args, **kwargs):
            # At launch time the (only) task must already be in-progress —
            # i.e. model.move ran before start_claude_session.
            seen["in_progress"] = model.list_tasks(
                store, project="p", status=model.STATUS_IN_PROGRESS
            )

        launch.session.side_effect = fake_session

        result = runner.invoke(task_cli.task, ["add", "One shot", "--run"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()

        # Created task exists and ended up in-progress.
        assert model.load(store, "p", new_id).status == model.STATUS_IN_PROGRESS
        # The move ran BEFORE the launch.
        assert [t.id for t in seen["in_progress"]] == [new_id]
        launch.session.assert_called_once()

    def test_add_without_run_does_not_launch(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["add", "No launch"])
        assert result.exit_code == 0, result.output
        launch.session.assert_not_called()
        launch.setup.assert_not_called()

    def test_add_run_passes_default_branch(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["add", "Defaulted", "--run"])
        assert result.exit_code == 0, result.output
        new_id = result.output.splitlines()[0].strip()
        # branch defaults to task/<id> and is what the core fn receives.
        assert launch.setup.call_args.args[2] == f"task/{new_id}"


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
