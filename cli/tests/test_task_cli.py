"""Tests for the ``mael task`` CLI, against an InMemoryTaskTable.

The CLI is exercised via Click's ``CliRunner``. The CLI's table seams are
patched to return a shared :class:`InMemoryTaskTable` and ``resolve_project`` to
a fixed project, so no database or cwd resolution happens.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import click
import pytest
from click.testing import CliRunner
from domain_fixtures import FakeBuildRuns

from mael_cli import env_cli, task_cli, worktree_launcher
from mael_cli.integrations.linear_cli import cmd_plan
from mael_common.shell import describe
from mael_domain import session_discovery
from mael_domain import task as model
from mael_domain.comm_store import Comm, InMemoryCommStore
from mael_domain.context import GlobalConfig
from mael_domain.env import EnvRefresh
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.worktree import SyncResult, WorktreeSetup
from mael_domain.worktree_model import CopyBackResult


@pytest.fixture
def store(store, monkeypatch) -> InMemoryTaskTable:
    """Wire the CLI's table seams to the shared in-memory table.

    Still called ``store`` because several hundred tests name it that; what it
    yields is the :class:`InMemoryTaskTable` from ``tests/conftest.py``. Both
    CLI seams are pointed at that one instance, so a task created directly via
    ``model.create`` in a test body and a task read back through the CLI share
    one table.
    """

    async def _table():
        return store

    monkeypatch.setattr(task_cli, "_table", _table)
    monkeypatch.setattr(task_cli, "open_task_table", lambda: store)
    monkeypatch.setattr(task_cli, "resolve_project", lambda project: project or "p")
    return store


class ThreadedCliRunner(CliRunner):
    """A ``CliRunner`` that invokes the command on its own thread.

    ``AsyncGroup`` opens one event loop per invocation with ``asyncio.run``,
    which refuses to nest. These tests are themselves coroutines, so a plain
    ``invoke`` would call it with a loop already running and get a
    ``RuntimeError`` instead of the command's real result.

    Running the invocation in a worker thread gives it a thread with no loop,
    which is exactly the state a real ``mael`` process starts in — so the
    command takes the same path here as in production.
    """

    def invoke(self, *args, **kwargs):  # type: ignore[override]
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: super(ThreadedCliRunner, self).invoke(*args, **kwargs)
            ).result()


@pytest.fixture
def runner() -> CliRunner:
    return ThreadedCliRunner()


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Stub the launch collaborators of ``_run_task``.

    Returns a namespace with the mocked ``setup`` and ``session`` (the daemon
    launcher) callables, plus ``exec`` (the ``--cli`` ``exec_cmd`` peer), and
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
    # The start succeeds by default (True) so the task stays IN_PROGRESS; tests
    # that exercise the failure path override ``session.return_value = False``.
    # `AsyncMock`: the launcher is awaited now, and it records calls the same way.
    session = AsyncMock(return_value=True)
    exec_cmd = MagicMock()
    install = MagicMock()
    refresh = MagicMock(return_value=EnvRefresh(CopyBackResult(), changed=True))
    monkeypatch.setattr(task_cli, "setup_worktree_for_branch", setup)
    monkeypatch.setattr(env_cli, "refresh_env", refresh)
    monkeypatch.setattr(task_cli, "launch_agent_in_worktree", session)
    monkeypatch.setattr(task_cli, "exec_cmd", exec_cmd)
    monkeypatch.setattr(task_cli, "start_install_async", install)
    # No live session for the task by default — the duplicate-launch pre-check
    # would otherwise sweep live claude processes. Patch the sweep to empty so
    # `for_session_id` finds nothing.
    _patch_live_sessions(monkeypatch, [])
    return SimpleNamespace(
        setup=setup,
        session=session,
        exec=exec_cmd,
        install=install,
        refresh=refresh,
        wt_path=wt_path,
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
    make = sessions if callable(sessions) else (lambda: list(sessions))

    async def sweep():
        return make()

    monkeypatch.setattr(task_cli.session_discovery, "all_live_sessions", sweep)


# --- add: branch defaulting / override ---


#: The real sweep class, bound at import. `_patch_sweep` replaces the name on
#: the module, so it must build from this rather than re-reading the attribute
#: it just overwrote — an autouse fixture patches once before each test does.
_REAL_SESSION_SET = session_discovery.LiveSessionSet


class TestUpdateRename:
    @pytest.fixture(autouse=True)
    def _no_live_session(self, monkeypatch):
        # The --id re-key path sweeps for live claude processes. Inject an empty
        # sweep through the documented seam — LiveSessionSet(sessions=...) — so
        # no test shells out to `pgrep`/`lsof` and reads the real machine.
        self._patch_sweep(monkeypatch, [])

    @staticmethod
    def _patch_sweep(monkeypatch, sessions):
        monkeypatch.setattr(
            task_cli.session_discovery,
            "LiveSessionSet",
            lambda *a, **kw: _REAL_SESSION_SET(sessions),
        )

    async def test_update_id_rekeys_task(self, runner, store):
        old_id = (await model.create(store, project="p", title="E")).id
        # The field update lands on the re-keyed task.
        result = runner.invoke(
            task_cli.task,
            ["update", old_id, "--id", "new-id", "--branch", "choose/foo"],
        )
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", "new-id")).branch == "choose/foo"
        with pytest.raises(KeyError):
            await model.load(store, "p", old_id)
        assert "Renamed" in result.output
        assert "new-id" in result.output

    async def test_update_id_refuses_in_progress(self, runner, store):
        old_id = (await model.create(store, project="p", title="E")).id
        await model.move(store, "p", old_id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code != 0
        assert "in-progress" in result.output
        # Untouched.
        assert (await model.load(store, "p", old_id)).id == old_id

    async def test_update_id_refuses_live_session(self, runner, store, monkeypatch):
        old_id = (await model.create(store, project="p", title="E")).id
        # A live claude carrying this task's deterministic session id.
        self._patch_sweep(
            monkeypatch,
            [
                session_discovery.LiveSession(
                    pid=123,
                    cwd=Path("/tmp/wt"),
                    session_id=model.session_id_for("p", old_id),
                )
            ],
        )
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code != 0
        assert "live Claude session" in result.output
        assert "123" in result.output  # names the pid to close
        assert "changing its id" in result.output  # the action it refused
        assert (await model.load(store, "p", old_id)).id == old_id

    async def test_update_id_allows_a_live_session_of_another_task(
        self, runner, store, monkeypatch
    ):
        # Keyed on this task's own session id, not on worktree occupancy: a
        # sibling task's session shares the worktree and must not block.
        old_id = (await model.create(store, project="p", title="E")).id
        self._patch_sweep(
            monkeypatch,
            [
                session_discovery.LiveSession(
                    pid=123,
                    cwd=Path("/tmp/wt"),
                    session_id=model.session_id_for("p", "some-other-task"),
                )
            ],
        )
        result = runner.invoke(task_cli.task, ["update", old_id, "--id", "new-id"])
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", "new-id")).id == "new-id"

    async def test_update_same_id_applies_field_changes(self, runner, store):
        old_id = (await model.create(store, project="p", title="E")).id
        result = runner.invoke(
            task_cli.task,
            ["update", old_id, "still works", "--id", old_id],
        )
        assert result.exit_code == 0, result.output
        loaded = await model.load(store, "p", old_id)
        assert loaded.title == "still works"
        # Same-id is not a rename, so no "Renamed" line.
        assert "Renamed" not in result.output


# --- next: selection ---


class TestNext:
    async def test_prints_first_actionable(self, runner, store):
        a = await model.create(
            store, project="p", title="a", now="2026-06-08T12:00:00+00:00"
        )
        await model.create(
            store, project="p", title="b", now="2026-06-09T12:00:00+00:00"
        )
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == a.id

    async def test_branch_flag_no_match_no_fallback(self, runner, store):
        # Only a task on 'other' exists; -b restricts strictly with no fallback.
        await model.create(store, project="p", title="a", branch="other")
        result = runner.invoke(task_cli.task, ["next", "-b", "feat/x"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output


# --- list: actionable-by-default filtering ---


# --- rm ---


class TestRm:
    async def test_rm_deletes_task(self, runner, store):
        a = await model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["rm", a.id])
        assert result.exit_code == 0, result.output
        assert f"Deleted {a.id}" in result.output
        assert await store.load("p", a.id) is None

    def test_rm_unknown_task_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["rm", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output


# --- launch wiring: run / add --run / next --run ---


class TestRun:
    async def test_run_ensures_worktree_moves_and_launches(self, runner, store, launch):
        t = await model.create(
            store,
            project="p",
            title="Plan it",
            command="plan-task",
            mode="plan",
            content="do the thing",
        )
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output

        # Core fn called with the task's stored (generated) branch. The model
        # call is blocked in tests, so this is the deterministic fallback slug.
        assert launch.setup.call_args.args[2] == t.branch == "feat/plan"

        # Task is now in-progress.
        assert (await model.load(store, "p", t.id)).status == model.STATUS_IN_PROGRESS

        # Session launched with the right task id / mode / worktree / project.
        # The prompt is produced lazily by `mael task prompt` inside the pipeline,
        # so the launcher gets the id, not the built prompt.
        assert launch.session.call_args.args[0] == launch.wt_path
        kwargs = launch.session.call_args.kwargs
        assert kwargs["permission_mode"] == "plan"
        assert kwargs["env"]["MAEL_TASK_ID"] == t.id
        assert f"Running {t.id} on {t.branch}" in result.output
        assert "→ p/bravo (created)" in result.output

    async def test_run_refreshes_a_reused_worktrees_env(self, runner, store, launch):
        launch.setup.return_value = WorktreeSetup(
            path=launch.wt_path, name="bravo", action="reused"
        )
        t = await model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        launch.refresh.assert_called_once_with(
            ANY,
            "p",
            "bravo",
            launch.setup.call_args.args[0],
            launch.wt_path,
            force_restart=False,
        )
        assert "Regenerated .env for p/bravo." in result.output

    async def test_run_leaves_mains_env_alone(self, runner, store, launch):
        launch.setup.return_value = WorktreeSetup(
            path=launch.wt_path, name="_main", action="reused"
        )
        t = await model.create(store, project="p", title="Plan it")
        assert runner.invoke(task_cli.task, ["run", t.id]).exit_code == 0
        launch.refresh.assert_not_called()

    async def test_run_launches_when_the_refresh_fails(self, runner, store, launch):
        launch.setup.return_value = WorktreeSetup(
            path=launch.wt_path, name="bravo", action="reused"
        )
        launch.refresh.side_effect = OSError("boom")
        t = await model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        assert ".env not refreshed: boom" in result.output
        launch.session.assert_called_once()

    async def test_run_passes_the_tasks_model_to_the_launcher(
        self, runner, store, launch
    ):
        t = await model.create(store, project="p", title="Plan it", model="opus")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["model"] == "opus"

    async def test_run_without_a_model_passes_the_default(self, runner, store, launch):
        # A task that names no model launches on the default, never on "".
        t = await model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["model"] == model.DEFAULT_MODEL

    async def test_run_cli_passes_the_model_too(
        self, runner, store, launch, monkeypatch
    ):
        # The --cli launch builds its own launch line, so it needs its own
        # coverage: a model set on the task must reach that argv as well.
        calls = []
        monkeypatch.setattr(
            task_cli, "exec_cmd", lambda cmd, **kw: calls.append(describe(cmd))
        )
        t = await model.create(store, project="p", title="Plan it", model="opus")
        result = runner.invoke(task_cli.task, ["run", t.id, "--cli"])
        assert result.exit_code == 0, result.output
        assert "--model opus" in calls[0]

    async def test_run_rolls_back_to_todo_when_the_start_fails(
        self, runner, store, launch
    ):
        # The daemon did not start the agent. A task that never
        # launched must not be left in-progress: roll it back to TODO so the
        # next run retries, and log the reason.
        launch.session.return_value = False
        t = await model.create(store, project="p", title="Plan it")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", t.id)).status == model.STATUS_TODO
        assert f"Left {t.id} TODO" in result.output
        assert t.id in result.output

    async def test_run_blocks_and_leaves_the_task_todo_when_the_sync_fails(
        self, runner, store, launch
    ):
        # The worktree's branch could not be rebased onto origin/main, so the
        # session must not start on stale code. The failure is known before the
        # in-progress write, so the task simply never leaves TODO.
        launch.setup.return_value = WorktreeSetup(
            path=launch.wt_path,
            name="bravo",
            action="created",
            sync=SyncResult(
                success=False,
                branch="feat/x",
                message="Autorepair did not complete the rebase; aborted and restored.",
                had_conflicts=True,
                aborted=True,
            ),
        )
        t = await model.create(store, project="p", title="Plan it")

        result = runner.invoke(task_cli.task, ["run", t.id])

        assert result.exit_code != 0
        assert (await model.load(store, "p", t.id)).status == model.STATUS_TODO
        launch.session.assert_not_called()
        assert "Sync" in result.output
        assert t.id in result.output

    async def test_run_reports_a_failed_push_but_still_launches(
        self, runner, store, launch
    ):
        # sync_worktree reports a rejected push as success=True plus
        # push_failed. The rebase landed, so the session may start — but an
        # unattended run must still say the branch and its remote have diverged.
        launch.setup.return_value = WorktreeSetup(
            path=launch.wt_path,
            name="bravo",
            action="created",
            sync=SyncResult(
                success=True,
                branch="feat/x",
                message="Successfully rebased feat/x onto origin/main",
                push_message="Push failed: rejected (stale info)",
                push_failed=True,
            ),
        )
        t = await model.create(store, project="p", title="Plan it")

        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])

        assert result.exit_code == 0, result.output
        assert "Push failed" in result.stderr
        launch.session.assert_called_once()

    async def test_run_existing_task_resumes_stale_transcript(
        self, runner, store, launch, monkeypatch
    ):
        # The relaunch path (`task run <id>`) keeps resume-on-restart: an
        # already-existing task with an on-disk transcript for its session-id
        # launches with `--resume`. Confirms the `fresh` fix is scoped to the
        # create-then-run callers only.
        t = await model.create(store, project="p", title="Existing")
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        assert launch.session.call_args.kwargs["resume"] is True
        assert "(resuming)" in result.output

    def test_run_unknown_task_errors(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["run", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output
        launch.session.assert_not_called()

    async def test_run_missing_project_path_errors(
        self, runner, store, monkeypatch, tmp_path
    ):
        t = await model.create(store, project="p", title="t")
        missing = tmp_path / "absent"
        monkeypatch.setattr(
            task_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="p", project_path=missing),
        )
        _patch_live_sessions(monkeypatch, [])
        session = AsyncMock()
        monkeypatch.setattr(task_cli, "launch_agent_in_worktree", session)
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code != 0
        assert "not found" in result.output
        session.assert_not_called()


def _live_session(pid=1, cwd=Path("/work/tree"), session_id=None):
    """A minimal :class:`LiveSession` stand-in (always live by construction)."""
    from mael_domain.session_discovery import LiveSession

    return LiveSession(pid=pid, cwd=cwd, session_id=session_id)


class TestDuplicateLaunchPrecheck:
    async def test_run_refuses_when_this_tasks_session_is_live(
        self, runner, store, launch, monkeypatch
    ):
        # A live claude carrying *this task's* --session-id blocks the relaunch.
        t = await model.create(store, project="p", title="t")
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
        assert (await model.load(store, "p", t.id)).status == model.STATUS_TODO

    async def test_run_cli_also_refuses(self, runner, store, launch, monkeypatch):
        t = await model.create(store, project="p", title="t")
        sid = model.session_id_for("p", t.id)
        _patch_live_sessions(monkeypatch, [_live_session(pid=9, session_id=sid)])
        result = runner.invoke(task_cli.task, ["run", t.id, "--cli"])
        assert result.exit_code != 0
        assert "already has a live Claude session" in result.output
        launch.exec.assert_not_called()
        assert (await model.load(store, "p", t.id)).status == model.STATUS_TODO

    async def test_run_proceeds_when_no_live_session(
        self, runner, store, launch, monkeypatch
    ):
        # A finished task leaves no live process carrying its id — it must stay
        # re-runnable, so the guard does NOT block.
        t = await model.create(store, project="p", title="t")
        _patch_live_sessions(monkeypatch, [])
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert (await model.load(store, "p", t.id)).status == model.STATUS_IN_PROGRESS

    async def test_sibling_session_in_shared_worktree_does_not_block(
        self, runner, store, launch, monkeypatch
    ):
        # Two sibling tasks under one parent share a branch/worktree (one PR per
        # parent). A live session for sibling `.2` must NOT block launching `.3`:
        # they carry distinct --session-ids, so the guard keys only on `.3`'s own.
        parent = await model.create(store, project="p", title="parent")
        two = await model.create(store, project="p", title="two", parent=parent.id)
        three = await model.create(store, project="p", title="three", parent=parent.id)
        two_sid = model.session_id_for("p", two.id)
        # `.2` is live in the shared worktree; `.3` is not.
        _patch_live_sessions(
            monkeypatch,
            [_live_session(pid=111, cwd=Path("/work/shared"), session_id=two_sid)],
        )
        result = runner.invoke(task_cli.task, ["run", three.id, "--daemon"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert (
            await model.load(store, "p", three.id)
        ).status == model.STATUS_IN_PROGRESS
        # Relaunching `.2` itself, however, is still blocked.
        result2 = runner.invoke(task_cli.task, ["run", two.id])
        assert result2.exit_code != 0
        assert "already has a live Claude session" in result2.output


class TestReconcile:
    def _live(self, monkeypatch, store, mapping):
        async def _mapping(_store, _project):
            return mapping

        monkeypatch.setattr(task_cli, "_live_sessions_by_task", _mapping)

    def _ran(self, monkeypatch, tmp_path, ran_ids):
        # Stub transcript detection: `ran_ids` are the stale tasks that ran (a
        # transcript persists → finished → done); the rest never ran (→ todo).
        # Also stub resolve_context to a real (existing) project path, since the
        # reconcile CLI resolves it to locate worktrees before calling
        # `_ran_task_ids` — the test store has no real project of its own.
        monkeypatch.setattr(
            task_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="p", project_path=tmp_path),
        )

        async def _ran_ids(_table, _project, _path):
            return set(ran_ids)

        monkeypatch.setattr(task_cli, "_ran_task_ids", _ran_ids)

    def test_empty(self, runner, store, monkeypatch, tmp_path):
        self._live(monkeypatch, store, {})
        self._ran(monkeypatch, tmp_path, set())
        result = runner.invoke(task_cli.task, ["reconcile"])
        assert result.exit_code == 0, result.output
        assert "No in-progress tasks or live task sessions." in result.output

    async def test_dry_run_lists_states_and_hints(
        self, runner, store, monkeypatch, tmp_path
    ):
        # One OK, one finished (ran), one never-ran, one orphan.
        ok = await model.create(store, project="p", title="ok", id="t1")
        await model.move(store, "p", ok.id, model.STATUS_IN_PROGRESS)
        finished = await model.create(store, project="p", title="finished", id="t2")
        await model.move(store, "p", finished.id, model.STATUS_IN_PROGRESS)
        never = await model.create(store, project="p", title="never", id="t3")
        await model.move(store, "p", never.id, model.STATUS_IN_PROGRESS)
        orphan = await model.create(store, project="p", title="orphan", id="t4")  # todo
        self._live(
            monkeypatch,
            store,
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
        assert (
            await model.load(store, "p", finished.id)
        ).status == model.STATUS_IN_PROGRESS
        assert (
            await model.load(store, "p", never.id)
        ).status == model.STATUS_IN_PROGRESS
        assert (await model.load(store, "p", orphan.id)).status == model.STATUS_TODO

    async def test_fix_applies_corrections(self, runner, store, monkeypatch, tmp_path):
        # A finished stale task → done, a never-ran stale task → todo, and an
        # orphan session → in-progress.
        finished = await model.create(store, project="p", title="finished", id="t1")
        await model.move(store, "p", finished.id, model.STATUS_IN_PROGRESS)
        never = await model.create(store, project="p", title="never", id="t2")
        await model.move(store, "p", never.id, model.STATUS_IN_PROGRESS)
        orphan = await model.create(store, project="p", title="orphan", id="t3")  # todo
        self._live(monkeypatch, store, {orphan.id: _live_session(pid=3)})
        self._ran(monkeypatch, tmp_path, {finished.id})
        result = runner.invoke(task_cli.task, ["reconcile", "--fix"])
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", finished.id)).status == model.STATUS_DONE
        assert (await model.load(store, "p", never.id)).status == model.STATUS_TODO
        assert (
            await model.load(store, "p", orphan.id)
        ).status == model.STATUS_IN_PROGRESS

    async def test_fix_nothing_to_do(self, runner, store, monkeypatch, tmp_path):
        ok = await model.create(store, project="p", title="ok", id="t1")
        await model.move(store, "p", ok.id, model.STATUS_IN_PROGRESS)
        self._live(monkeypatch, store, {ok.id: _live_session(pid=1)})
        self._ran(monkeypatch, tmp_path, set())
        result = runner.invoke(task_cli.task, ["reconcile", "--fix"])
        assert result.exit_code == 0, result.output
        assert "Nothing to fix." in result.output


class TestLiveSessionsByTask:
    """The reconcile correlation builder — task-precise via session-id."""

    async def test_matches_only_the_owning_task(self, store, monkeypatch):
        # Two siblings share one worktree; only `.2` is live. The map must
        # attribute the session to `.2` alone, never to its sibling `.3`.
        parent = await model.create(store, project="p", title="parent")
        two = await model.create(store, project="p", title="two", parent=parent.id)
        three = await model.create(store, project="p", title="three", parent=parent.id)
        two_sid = model.session_id_for("p", two.id)
        _patch_live_sessions(
            monkeypatch,
            [_live_session(pid=111, cwd=Path("/work/shared"), session_id=two_sid)],
        )
        mapping = await task_cli._live_sessions_by_task(store, "p")
        assert two.id in mapping and mapping[two.id].pid == 111
        assert three.id not in mapping

    async def test_empty_when_no_live_sessions(self, store, monkeypatch):
        await model.create(store, project="p", title="t")
        _patch_live_sessions(monkeypatch, [])
        assert await task_cli._live_sessions_by_task(store, "p") == {}

    async def test_session_without_id_matches_nothing(self, store, monkeypatch):
        # A bare claude (no --session-id) never correlates to a task.
        await model.create(store, project="p", title="t")
        _patch_live_sessions(monkeypatch, [_live_session(pid=5, cwd=Path("/work/x"))])
        assert await task_cli._live_sessions_by_task(store, "p") == {}


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
    async def test_next_run_runs_the_actionable(self, runner, store, launch):
        a = await model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["next", "--run", "--daemon"])
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", a.id)).status == model.STATUS_IN_PROGRESS
        launch.session.assert_called_once()

    def test_next_run_no_actionable_errors(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["next", "--run"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output
        launch.session.assert_not_called()


class TestRunCli:
    """``--cli`` prepares the worktree, then runs the harness in this shell."""

    async def test_prepares_the_worktree_and_execs_there(
        self, runner, store, launch, monkeypatch
    ):
        monkeypatch.setattr(task_cli, "core_plugin_dir", lambda: Path("/core"))
        t = await model.create(
            store, project="p", title="Plan it", command="plan-task", mode="plan"
        )
        # A real exec never returns, so a write placed after it never lands.
        launch.exec.side_effect = SystemExit(0)
        result = runner.invoke(task_cli.task, ["run", t.id, "--cli"])
        assert result.exit_code == 0, result.output

        # The worktree is set up as for a daemon launch; the daemon is unused.
        assert launch.setup.call_args.args[2] == t.branch
        launch.session.assert_not_called()
        launch.install.assert_called_once_with(launch.wt_path)
        assert (await model.load(store, "p", t.id)).status == model.STATUS_IN_PROGRESS

        # Replace-execs the launch pipeline in the worktree with task env. The
        # env rides on the ``claude`` Command (right of the pipe) so the
        # interactive session inherits it — a front prefix would only reach
        # ``mael task prompt``.
        launch.exec.assert_called_once()
        sid = model.session_id_for("p", t.id)
        assert describe(launch.exec.call_args.args[0]) == (
            f"mael task prompt {t.id} --project p "
            f"| MAEL_TASK_ID={t.id} MAEL_TASK_PARENT={t.id} "
            f"MAEL_TASK_SESSION_ID={sid} "
            "claude --permission-mode plan --model opus "
            f"--plugin-dir /core --session-id {sid}"
        )
        kwargs = launch.exec.call_args.kwargs
        assert kwargs["cwd"] == launch.wt_path
        assert kwargs["env"]["MAEL_TASK_ID"] == t.id
        assert f"Running {t.id} on {t.branch}" in result.output

    async def test_resumes_from_the_worktrees_transcript(
        self, runner, store, launch, monkeypatch
    ):
        # The transcript lives under the worktree the session runs in, not
        # under the shell's cwd.
        looked_in = []

        def has_transcript(path, _sid):
            looked_in.append(path)
            return True

        monkeypatch.setattr(task_cli, "has_claude_transcript", has_transcript)
        t = await model.create(store, project="p", title="Existing")
        result = runner.invoke(task_cli.task, ["run", t.id, "--cli"])
        assert result.exit_code == 0, result.output
        assert looked_in == [launch.wt_path]
        assert "--resume" in describe(launch.exec.call_args.args[0])

    async def test_says_it_cannot_switch_the_execute_model(self, runner, store, launch):
        # --cli runs the harness in this shell with no daemon between, so
        # nothing performs the switch. Said rather than dropped silently: the
        # user asked for two models and would otherwise get one with no sign why.
        t = await model.create(
            store, project="p", title="Here", mode="plan", execute_model="sonnet"
        )
        result = runner.invoke(task_cli.task, ["run", t.id, "--cli"])
        assert result.exit_code == 0, result.output
        assert "Ignoring the execute model 'sonnet'" in result.output

    async def test_next_run_cli_execs_in_the_worktree(self, runner, store, launch):
        a = await model.create(store, project="p", title="a")
        result = runner.invoke(task_cli.task, ["next", "--run", "--cli"])
        assert result.exit_code == 0, result.output
        launch.session.assert_not_called()
        assert (await model.load(store, "p", a.id)).status == model.STATUS_IN_PROGRESS
        assert launch.exec.call_args.kwargs["cwd"] == launch.wt_path
        assert launch.exec.call_args.kwargs["env"]["MAEL_TASK_ID"] == a.id

    @pytest.mark.parametrize("command", ["run", "next"])
    async def test_a_driven_agent_refuses_cli(
        self, runner, store, launch, monkeypatch, command
    ):
        # A driven agent has no terminal for the harness to run in.
        monkeypatch.setenv("MAEL_HARNESS_TYPE", "daemon")
        t = await model.create(store, project="p", title="Go")
        args = ["run", t.id] if command == "run" else ["next", "--run"]
        result = runner.invoke(task_cli.task, [*args, "--cli"])
        assert result.exit_code != 0
        assert "this process has none" in result.output
        launch.setup.assert_not_called()
        launch.exec.assert_not_called()
        assert (await model.load(store, "p", t.id)).status == model.STATUS_TODO

    async def test_a_process_with_no_terminal_refuses_the_default(
        self, runner, store, launch, monkeypatch
    ):
        # An agent's tool call outside the daemon: no MAEL_HARNESS_TYPE, so the
        # default is --cli, but its stdin and stdout are pipes.
        monkeypatch.setattr(worktree_launcher, "_streams_are_terminals", lambda: False)
        t = await model.create(store, project="p", title="Go")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code != 0
        assert "Use --daemon" in result.output
        launch.setup.assert_not_called()
        launch.exec.assert_not_called()

    async def test_a_driven_agent_launches_on_the_daemon(
        self, runner, store, launch, monkeypatch
    ):
        monkeypatch.setenv("MAEL_HARNESS_TYPE", "daemon")
        t = await model.create(store, project="p", title="Go")
        result = runner.invoke(task_cli.task, ["run", t.id])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        launch.exec.assert_not_called()

    def test_here_is_removed(self, runner, store, launch):
        result = runner.invoke(task_cli.task, ["run", "x", "--here"])
        assert result.exit_code == 2
        assert "--here was removed; --cli runs in this shell." in result.output


class TestAddRunCli:
    """``task add --run --cli`` makes the task on the server, then runs it here."""

    @pytest.fixture
    def server(self, monkeypatch):
        """A stand-in orchestrator that answers the create with ``taskId``."""
        client = MagicMock()
        monkeypatch.setattr(task_cli.OrchestratorClient, "from_config", lambda: client)
        monkeypatch.setattr(task_cli, "_wait_for_operation", MagicMock())
        return client

    async def test_runs_the_new_task_in_its_worktree(
        self, runner, store, launch, server
    ):
        t = await model.create(store, project="p", title="New")
        server.post.return_value = {"taskId": f"p/{t.id}", "operationId": "op-1"}
        result = runner.invoke(
            task_cli.task, ["add", "New", "-p", "p", "--run", "--cli"]
        )
        assert result.exit_code == 0, result.output
        # The server only creates it: the launch runs in this shell.
        assert "launch" not in server.post.call_args.args[1]
        launch.session.assert_not_called()
        assert launch.exec.call_args.kwargs["cwd"] == launch.wt_path
        assert "--session-id" in describe(launch.exec.call_args.args[0])

    @pytest.mark.parametrize(
        ("args", "env", "message"),
        [
            (["--cli"], None, "--cli and --daemon need --run"),
            (["--run", "--cli"], "daemon", "this process has none"),
        ],
    )
    def test_refuses_before_it_makes_the_task(
        self, runner, launch, server, monkeypatch, args, env, message
    ):
        if env:
            monkeypatch.setenv("MAEL_HARNESS_TYPE", env)
        result = runner.invoke(task_cli.task, ["add", "New", "-p", "p", *args])
        assert result.exit_code != 0
        assert message in result.output
        server.post.assert_not_called()


class TestPrompt:
    """``mael task prompt <id>`` prints exactly build_prompt(task)."""

    async def test_prints_command_title_and_content(self, runner, store):
        t = await model.create(
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

    def test_unknown_task_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["prompt", "nope"])
        assert result.exit_code != 0
        assert "Task not found" in result.output


class TestDraft:
    def test_draft_writes_roundtrippable_file(self, runner, tmp_path):
        f = tmp_path / "draft-iter1.md"
        result = runner.invoke(
            task_cli.task,
            [
                "draft",
                str(f),
                "Execute: demo",
                "--mode",
                "auto",
                "--pre-action",
                "linear.in-progress",
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

    def test_draft_creates_the_parent_directory(self, runner, tmp_path):
        f = tmp_path / ".drafts" / "iter1.md"
        result = runner.invoke(task_cli.task, ["draft", str(f), "Execute: demo"])
        assert result.exit_code == 0, result.output
        assert model.Task.from_markdown(f.read_text()).title == "Execute: demo"

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

    async def test_draft_is_inert_until_promoted(self, runner, store, tmp_path):
        # The approval gate is structural: a draft never reaches the store, so
        # it is invisible to list/next/follow-end until promote loads it.
        result = runner.invoke(task_cli.task, ["draft", str(tmp_path / "d.md"), "T"])
        assert result.exit_code == 0, result.output
        assert await model.list_tasks(store, project="p") == []


class TestStatus:
    """The cases decided before the server is called. The rest are in
    ``tests/test_cli_over_api.py``."""

    def test_status_no_id_and_no_env_errors(self, runner, store, monkeypatch):
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["status", "done"])
        assert result.exit_code != 0
        assert "No task id" in result.output

    async def test_old_flat_command_gone(self, runner, store):
        t = await model.create(store, project="p", title="t")
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
    async def test_prints_bare_status(self, runner, store):
        # The status line embeds the output verbatim, so it must be the status
        # word alone — no label, no id.
        t = await model.create(store, project="p", title="t")
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS)
        result = runner.invoke(task_cli.task, ["get-status", t.id])
        assert result.exit_code == 0, result.output
        assert result.output == f"{model.STATUS_IN_PROGRESS}\n"

    async def test_env_fallback(self, runner, store, monkeypatch):
        t = await model.create(store, project="p", title="t")
        monkeypatch.setenv("MAEL_TASK_ID", t.id)
        result = runner.invoke(task_cli.task, ["get-status"])
        assert result.exit_code == 0, result.output
        assert result.output == f"{model.STATUS_TODO}\n"

    async def test_no_id_and_no_env_errors(self, runner, store, monkeypatch):
        await model.create(store, project="p", title="t")
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
    async def test_prints_id_and_status(self, runner, store, monkeypatch):
        t = await model.create(store, project="p", title="t")
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS)
        monkeypatch.setenv("MAEL_TASK_ID", t.id)
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == f"{t.id}:{model.STATUS_IN_PROGRESS}\n"

    async def test_outside_a_task_session_prints_nothing(
        self, runner, store, monkeypatch
    ):
        await model.create(store, project="p", title="t")
        # A prompt calls this on every redraw, so "no task" is an ordinary
        # answer: empty output, exit 0 — never an error.
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"

    def test_outside_a_project_dir_prints_nothing(self, runner, store, monkeypatch):
        # A prompt runs from anywhere, including outside any project, where
        # resolve_project raises. The store fixture stubs that call out, so
        # override it to get the real failure back.
        def _boom(project):
            raise ValueError("Could not determine project.")

        monkeypatch.setattr(task_cli, "resolve_project", _boom)
        monkeypatch.setenv("MAEL_TASK_ID", "2026-06-11.3")
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"

    async def test_vanished_task_prints_nothing(self, runner, store, monkeypatch):
        # The id outlives the task if it is deleted mid-session. The prompt must
        # still render, so degrade to empty rather than failing.
        await model.create(store, project="p", title="t")
        monkeypatch.setenv("MAEL_TASK_ID", "nope")
        result = runner.invoke(task_cli.task, ["current"])
        assert result.exit_code == 0, result.output
        assert result.output == "\n"


class TestEnvThreading:
    async def test_run_threads_task_id_and_parent_env(self, runner, store, launch):
        # A child task carries a parent; both ids should reach the session env.
        await model.create(
            store, project="p", title="Parent task", parent="linear.ME-1"
        )
        t = await model.create(store, project="p", title="Child", parent="linear.ME-1")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == t.id
        assert env["MAEL_TASK_PARENT"] == "linear.ME-1"

    async def test_run_self_parents_when_orphan(self, runner, store, launch):
        # A parentless task self-parents so the chain it emits nests under it
        # and shares its branch, rather than each child becoming a fresh orphan.
        t = await model.create(store, project="p", title="Orphan")
        result = runner.invoke(task_cli.task, ["run", t.id, "--daemon"])
        assert result.exit_code == 0, result.output
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == t.id
        assert env["MAEL_TASK_PARENT"] == t.id  # self-parent, not omitted


# --- list: BRANCH column ---


# --- update ---


class TestUpdate:
    async def test_update_title_and_branch(self, runner, store):
        t = await model.create(store, project="p", title="alpha")
        result = runner.invoke(
            task_cli.task, ["update", t.id, "new title", "--branch", "feat/foo"]
        )
        assert result.exit_code == 0, result.output
        assert f"Updated {t.id}" in result.output
        reloaded = await model.load(store, "p", t.id)
        assert reloaded.title == "new title"
        assert reloaded.branch == "feat/foo"

    async def test_update_content_from_stdin(self, runner, store):
        t = await model.create(store, project="p", title="alpha", content="old body")
        result = runner.invoke(
            task_cli.task,
            ["update", t.id, "--content-file", "-"],
            input="new body\n",
        )
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", t.id)).content == "new body"

    def test_update_unknown_id_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["update", "nope", "--branch", "x"])
        assert result.exit_code != 0
        assert "Task not found" in result.output


# --- duplicate (--from) ---


# --- templates + schedule metadata ---


class TestCommFlag:
    @pytest.fixture
    async def comms(self, monkeypatch):
        comms = InMemoryCommStore()
        for id in ("c1", "c2"):
            await comms.save(Comm(id=id, title=id))

        async def _comm_store():
            return comms

        monkeypatch.setattr(task_cli, "_comm_store", _comm_store)
        return comms

    async def test_update_replaces_and_an_empty_value_clears(
        self, runner, store, comms
    ):
        tid = (await model.create(store, project="p", title="T", comms=["c1"])).id
        runner.invoke(task_cli.task, ["update", tid, "--comm", "c2"])
        assert (await model.load(store, "p", tid)).comms == ["c2"]
        runner.invoke(task_cli.task, ["update", tid, "--comm", ""])
        assert (await model.load(store, "p", tid)).comms == []


class TestTemplates:
    async def test_template_invisible_to_next(self, runner, store):
        await model.create(
            store, project="p", title="Tmpl", status=model.STATUS_TEMPLATE
        )
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code != 0  # no actionable task

    async def test_update_schedule_round_trips(self, runner, store):
        tid = (
            await model.create(
                store, project="p", title="Tmpl", status=model.STATUS_TEMPLATE
            )
        ).id
        runner.invoke(task_cli.task, ["update", tid, "--schedule", "0 9 * * 1-5"])
        assert (await model.load(store, "p", tid)).schedule == "0 9 * * 1-5"
        result = runner.invoke(
            task_cli.task, ["update", tid, "--trigger", "gh-action/nightly.yml"]
        )
        assert result.exit_code == 0, result.output
        assert (await model.load(store, "p", tid)).trigger == "gh-action/nightly.yml"
        refused = runner.invoke(
            task_cli.task, ["update", tid, "--trigger", "nightly.yml"]
        )
        assert refused.exit_code != 0
        assert (await model.load(store, "p", tid)).trigger == "gh-action/nightly.yml"
        runner.invoke(task_cli.task, ["update", tid, "--trigger", ""])
        assert (await model.load(store, "p", tid)).trigger == ""


# --- add-scheduled ---
#
# `schedule.fire_due` owns which runs a tick makes (lib/domain/tests/test_schedule.py).
# These cover what the command adds: its output, the launch, and its flags.


@pytest.fixture(autouse=True)
def no_build_runs(monkeypatch):
    """No test reads real workflow runs: the read is unknown unless a test says."""
    monkeypatch.setattr(task_cli, "_build_runs", lambda: FakeBuildRuns(None))


NZ = ZoneInfo("Pacific/Auckland")


def _schedule_timezone(monkeypatch, name):
    """Read every schedule in the zone ``name``, as config.yaml would say."""
    config = GlobalConfig.from_dict({"schedule_timezone": name})
    monkeypatch.setattr(task_cli, "load_global_config", lambda: config)


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze the clock at 2026-06-18 10:00 NZ, read schedules in NZ, and return it.

    That is 2026-06-17 22:00 UTC, so a run keyed 2026-06-18 shows the zone applies.
    """
    frozen = datetime(2026, 6, 18, 10, 0, tzinfo=NZ)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen

    monkeypatch.setattr(task_cli, "datetime", FrozenDateTime)
    _schedule_timezone(monkeypatch, "Pacific/Auckland")
    return frozen


async def _make_template(store, id="maintenance", **fields):
    """A daily 09:00 template whose last run was the day before ``frozen_now``."""
    fields = {
        "schedule": "0 9 * * *",
        "last_run": datetime(2026, 6, 17, 9, 0, tzinfo=NZ).isoformat(),
        **fields,
    }
    return await model.create(
        store,
        project="p",
        title="Maintenance",
        command="",
        status=model.STATUS_TEMPLATE,
        id=id,
        now="2026-06-01T00:00:00+00:00",
        **fields,
    )


class TestAddScheduled:
    async def test_a_due_template_fires_and_prints_its_run(
        self, runner, store, frozen_now
    ):
        await _make_template(store)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code == 0, result.output
        assert "p/maintenance.2026-06-18\tMaintenance" in result.output
        assert await model.load(store, "p", "maintenance.2026-06-18") is not None

    async def test_nothing_due_prints_the_stamp_the_warnings_and_says_so(
        self, runner, store, frozen_now
    ):
        """The stamp is how schedule.log records that the scheduler ran."""
        await _make_template(store, last_run=frozen_now.isoformat())
        await _make_template(store, id="nightly", schedule="", trigger="nightly.yml")
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code == 0, result.output
        stamp = frozen_now.isoformat(timespec="seconds")
        assert result.output.startswith(f"[{stamp}] add-scheduled (Pacific/Auckland)")
        assert "warning: p/nightly: unknown trigger 'nightly.yml'" in result.output
        assert "No scheduled tasks due." in result.output

    async def test_run_launches_a_fresh_session_rooted_at_the_run(
        self, runner, store, monkeypatch, launch, frozen_now
    ):
        await _make_template(store)
        # A scheduled run is a new task, so a stale transcript is not resumed.
        monkeypatch.setattr(task_cli, "has_claude_transcript", lambda *a: True)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        launch.session.assert_called_once()
        assert launch.session.call_args.kwargs["resume"] is False
        # The run is parentless, so the launched session exports its own id as the
        # chain parent — follow-ups nest under the run, not the template.
        env = launch.session.call_args.kwargs["env"]
        assert env["MAEL_TASK_ID"] == "maintenance.2026-06-18"
        assert env["MAEL_TASK_PARENT"] == "maintenance.2026-06-18"

    async def test_run_attempts_every_due_run_on_the_daemon(
        self, runner, store, monkeypatch, launch, frozen_now
    ):
        # Two due templates fire in one --run pass, and BOTH runs are attempted.
        # The env names the CLI, to prove the daemon ignores it.
        monkeypatch.setenv("MAEL_HARNESS_TYPE", "cli")
        for tmpl_id in ("maint-a", "maint-b"):
            await _make_template(store, id=tmpl_id)
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        launched = sorted(
            c.kwargs["env"]["MAEL_TASK_ID"] for c in launch.session.call_args_list
        )
        assert launched == ["maint-a.2026-06-18", "maint-b.2026-06-18"]
        launch.exec.assert_not_called()

    async def test_a_refused_run_does_not_abandon_the_next(
        self, runner, store, launch, frozen_now
    ):
        # A codex template cannot run on the daemon default. Its refusal is a
        # warning; the Claude template after it still launches.
        await _make_template(store, id="maint-a", model="codex:gpt-5")
        await _make_template(store, id="maint-b")
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p", "--run"])
        assert result.exit_code == 0, result.output
        assert "warning: maint-a.2026-06-18" in result.output
        launched = [
            c.kwargs["env"]["MAEL_TASK_ID"] for c in launch.session.call_args_list
        ]
        assert launched == ["maint-b.2026-06-18"]

    async def test_an_unknown_zone_fails_and_fires_nothing(
        self, runner, store, monkeypatch, frozen_now
    ):
        await _make_template(store)
        _schedule_timezone(monkeypatch, "Mars/Olympus")
        result = runner.invoke(task_cli.task, ["add-scheduled", "-p", "p"])
        assert result.exit_code != 0
        assert "schedule_timezone in config.yaml: unknown zone Mars/Olympus" in (
            result.output
        )
        assert await store.load("p", "maintenance.2026-06-18") is None

    def _list_templates(self, runner, monkeypatch):
        row = {
            "project": "p",
            "notebookId": "maintenance",
            "status": model.STATUS_TEMPLATE,
            "parent": "",
            "priority": "2",
            "created": "2026-06-01T00:00:00+00:00",
            "actionable": False,
            "schedule": "0 9 * * *",
            "trigger": "",
            "branch": "",
            "title": "Maintenance",
        }
        monkeypatch.setattr(task_cli, "_fetch_rows", lambda: [row])
        monkeypatch.setattr(task_cli, "resolve_project", lambda p: "p")
        result = runner.invoke(task_cli.task, ["list", "--status", "template"])
        assert result.exit_code == 0, result.output
        return result.output

    def test_the_template_listing_shows_the_zone(self, runner, monkeypatch, frozen_now):
        output = self._list_templates(runner, monkeypatch)
        assert "2026-06-19T09:00+12:00" in output
        assert output.rstrip().endswith("Schedules read in Pacific/Auckland.")

    def test_the_template_listing_names_an_unknown_zone(
        self, runner, monkeypatch, frozen_now
    ):
        _schedule_timezone(monkeypatch, "Mars/Olympus")
        output = self._list_templates(runner, monkeypatch)
        assert "(unknown zone)" in output
        assert output.rstrip().endswith("Schedules read in unknown zone Mars/Olympus.")

    def test_conflicting_harness_flags_are_refused(self, runner, store, launch):
        result = runner.invoke(
            task_cli.task, ["add-scheduled", "-p", "p", "--run", "--cli", "--daemon"]
        )
        assert result.exit_code != 0
        assert "--cli conflicts with --daemon" in result.output
        launch.session.assert_not_called()

    def test_run_cli_is_refused(self, runner, store, launch):
        # --run can fire several runs, and a --cli exec runs only one.
        result = runner.invoke(
            task_cli.task, ["add-scheduled", "-p", "p", "--run", "--cli"]
        )
        assert result.exit_code != 0
        assert "--cli runs one in this shell" in result.output
        launch.session.assert_not_called()
