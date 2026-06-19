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

    Returns a namespace with the mocked ``setup`` and ``session`` (the
    worktree-placement wrapper) callables, plus ``exec`` (the --here exec peer),
    and the worktree path the fake setup returns.
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
    exec_claude = MagicMock()
    monkeypatch.setattr(task_cli, "setup_worktree_for_branch", setup)
    monkeypatch.setattr(task_cli, "launch_claude_in_worktree", session)
    monkeypatch.setattr(task_cli, "exec_claude", exec_claude)
    return SimpleNamespace(
        setup=setup, session=session, exec=exec_claude, wt_path=wt_path
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

        # Core fn called with the task's stored (generated) branch. The model
        # call is blocked in tests, so this is the deterministic fallback slug.
        assert launch.setup.call_args.args[2] == t.branch == "feat/plan"

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
        monkeypatch.setattr(task_cli, "launch_claude_in_worktree", session)
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
            # i.e. model.move ran before launch_claude_in_worktree.
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
            lambda s, p, i: calls.append((s, p, i)) or (None, True),
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
            lambda s, p, i: calls.append(i) or (None, True),
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
            lambda s, p, i: order.append("edit") or (None, True),
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

        # Execs claude in the current shell (cwd=None) with the task env.
        launch.exec.assert_called_once()
        kwargs = launch.exec.call_args.kwargs
        assert kwargs["cwd"] is None
        assert kwargs["env"]["MAEL_TASK_ID"] == t.id
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

    def test_run_omits_parent_env_when_orphan(self, runner, store, launch):
        t = model.create(store, project="p", title="Orphan")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == t.id
        assert "MAEL_TASK_PARENT" not in env


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
        from datetime import datetime, timezone

        _make_template(
            store,
            schedule="0 9 * * *",
            last_run="2026-06-11T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        # Freeze "now" inside the command.
        import maelstrom.task_cli as tc
        real_dt = datetime

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 6, 18, 10, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code == 0, result.output
        run = model.load(store, "p", "maintenance.2026-06-18")
        assert run.parent == "maintenance"
        # Exactly one run (catch-up is a single boundary, not 7).
        runs = [
            t for t in model.list_tasks(store, project="p")
            if t.parent == "maintenance"
        ]
        assert len(runs) == 1
        # Watermark advanced to today's 09:00 boundary.
        tmpl = model.load(store, "p", "maintenance")
        assert tmpl.last_run == "2026-06-18T09:00:00+00:00"

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
            if t.parent == "maintenance"
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
