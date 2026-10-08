"""Tests for self-management CLI commands (focus: self-update dep sync)."""

import asyncio
import pathlib
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import ANY, MagicMock, patch

import click
import pytest
from click.testing import CliRunner, Result

from mael_cli.admin_cli import (
    cmd_export_queue,
    cmd_self_update,
    resolve_install_root,
)
from mael_domain import task as task_model
from mael_domain.env import EnvRefresh, EnvState, ServiceVersionError, VersionChange
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_export import SqliteExportQueue
from mael_domain.task_table import SqliteTaskTable
from mael_domain.worktree_model import CopyBackResult


@pytest.fixture(autouse=True)
def _no_live_update_steps():
    """Keep self-update off the real `_main`: no tangier, install or restart.

    With no versions the update fails open and reinstalls, as it did before
    service versions existed.
    """
    with (
        patch("mael_cli.admin_cli.service_versions", return_value={}),
        patch("mael_cli.admin_cli.run_install_cmd"),
        patch("mael_cli.admin_cli.restart_changed", return_value=[]),
    ):
        yield


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout, stderr=stderr
    )


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


ROOT = pathlib.Path("/checkout/_main")


@dataclass
class SelfUpdateRun:
    result: Result
    run: MagicMock
    install: MagicMock
    restart: MagicMock


def _run_self_update(
    *,
    run_results,
    which_uv="/usr/bin/uv",
    cli_versions=None,
    changes=(),
    install_error=None,
    restart_error=None,
) -> SelfUpdateRun:
    """Invoke self-update on ``ROOT`` with every outside effect stubbed.

    ``run_results`` feeds the patched ``subprocess.run``: ``git pull`` first,
    then the ``uv tool install`` sync when it runs. ``cli_versions`` is the
    `cli` SHA bucket's hash before and after the pull, or an exception for a
    failed hash. Without it there is no hash, so the reinstall fails open.
    """
    versions = (
        {"return_value": {}}
        if cli_versions is None
        else {
            "side_effect": [
                v if isinstance(v, Exception) else {"cli": v} for v in cli_versions
            ]
        }
    )
    with (
        patch("mael_cli.admin_cli.resolve_install_root", return_value=ROOT),
        patch("mael_cli.admin_cli.Path.exists", return_value=True),
        patch("mael_cli.admin_cli.shutil.which", return_value=which_uv),
        patch("mael_cli.admin_cli.install_claude_integration", return_value=[]),
        patch("mael_cli.admin_cli.harden_global_config", return_value=[]),
        patch("mael_cli.admin_cli._write_daemon_root_shim", return_value=""),
        patch("mael_cli.admin_cli.service_versions", **versions),
        patch(
            "mael_cli.admin_cli.run_install_cmd", side_effect=install_error
        ) as install,
        patch(
            "mael_cli.admin_cli.restart_changed",
            return_value=list(changes),
            side_effect=restart_error,
        ) as restart,
        patch("mael_cli.admin_cli.subprocess.run", side_effect=run_results) as run,
    ):
        result = CliRunner().invoke(cmd_self_update)
    return SelfUpdateRun(result, run, install, restart)


class TestSelfUpdateDependencySync:
    """self-update must re-resolve dependencies after pulling new source.

    A `git pull` that introduces a new pyproject dependency leaves the installed
    environment missing the package until uv re-resolves it, so commands that
    import the new dep crash post-update. These tests pin the sync step.
    """

    def test_reinstalls_editable_tool_when_uv_present(self):
        update = _run_self_update(
            run_results=[
                _ok(stdout="Already up to date.\n"),
                _ok(stderr="Installed.\n"),
            ]
        )

        assert update.result.exit_code == 0, update.result.output
        # Second subprocess call is the dependency sync.
        sync_cmd = update.run.call_args_list[1].args[0]
        assert sync_cmd[:3] == ["/usr/bin/uv", "tool", "install"]
        # The CLI member, not the workspace root, which builds no package.
        assert sync_cmd[sync_cmd.index("--editable") + 1] == str(ROOT / "cli")
        assert "--reinstall" in sync_cmd
        # --force overwrites the live `mael` entrypoint; without it uv aborts.
        assert "--force" in sync_cmd
        assert "Update complete." in update.result.output

    def test_warns_and_skips_sync_when_uv_missing(self):
        # Only git pull runs; no sync call to make.
        update = _run_self_update(which_uv=None, run_results=[_ok()])

        assert update.result.exit_code == 0, update.result.output
        assert update.run.call_count == 1  # git pull only
        assert "skipping dependency sync" in update.result.output

    def test_warns_but_succeeds_when_sync_fails(self):
        # The pull already landed, so a failed sync must not abort the command.
        update = _run_self_update(
            run_results=[_ok(), _fail(stderr="resolution failed")]
        )

        assert update.result.exit_code == 0, update.result.output
        assert "dependency sync failed" in update.result.output
        assert "Update complete." in update.result.output

    def test_aborts_when_not_a_git_checkout(self):
        with patch("mael_cli.admin_cli.Path.exists", return_value=False):
            result = CliRunner().invoke(cmd_self_update)

        assert result.exit_code != 0
        assert "not installed from a git checkout" in result.output


class TestSelfUpdateRestartsOnlyWhatChanged:
    """An update reinstalls and restarts only what its pull moved."""

    def test_an_unmoved_cli_is_not_reinstalled(self):
        update = _run_self_update(cli_versions=["c1", "c1"], run_results=[_ok()])
        assert update.result.exit_code == 0, update.result.output
        assert update.run.call_count == 1  # git pull only
        assert "CLI unchanged" in update.result.output

    def test_a_moved_cli_is_reinstalled(self):
        update = _run_self_update(cli_versions=["c1", "c2"], run_results=[_ok(), _ok()])
        assert update.result.exit_code == 0, update.result.output
        sync_cmd = update.run.call_args_list[1].args[0]
        assert sync_cmd[:3] == ["/usr/bin/uv", "tool", "install"]

    def test_a_failed_hash_reinstalls(self):
        """Fail open: without a version, the CLI may have moved."""
        update = _run_self_update(
            cli_versions=[ServiceVersionError("tangier is not on PATH")] * 2,
            run_results=[_ok(), _ok()],
        )
        assert update.result.exit_code == 0, update.result.output
        sync_cmd = update.run.call_args_list[1].args[0]
        assert sync_cmd[:3] == ["/usr/bin/uv", "tool", "install"]

    def test_a_failed_install_command_skips_the_restart(self):
        """The install migrates the state database, so new code could refuse it."""
        update = _run_self_update(
            cli_versions=["c1", "c1"],
            run_results=[_ok()],
            install_error=subprocess.CalledProcessError(1, "sh"),
        )
        assert update.result.exit_code == 0, update.result.output
        assert "install_cmd failed" in update.result.output
        assert "mael self-env restart --install" in update.result.output
        update.restart.assert_not_called()
        assert "Update complete." in update.result.output

    def test_a_failed_restart_names_what_may_be_down(self):
        update = _run_self_update(
            cli_versions=["c1", "c1"],
            run_results=[_ok()],
            restart_error=TimeoutError("no address"),
        )
        assert update.result.exit_code == 0, update.result.output
        assert "mael self-env start" in update.result.output
        assert "Update complete." in update.result.output

    def test_the_install_command_runs_in_main(self):
        update = _run_self_update(cli_versions=["c1", "c1"], run_results=[_ok()])
        update.install.assert_called_once_with(ROOT)

    def test_the_changed_services_of_maelstrom_main_restart(self):
        update = _run_self_update(
            cli_versions=["c1", "c1"],
            run_results=[_ok()],
            changes=[VersionChange("agent-daemon", "a1", "a2")],
        )
        assert update.result.exit_code == 0, update.result.output
        update.restart.assert_called_once_with(ANY, "maelstrom", "_main", ROOT)
        output = update.result.output
        assert "agent-daemon: a1 → a2" in output
        assert "The agent daemon restarted. Mid-turn agents resume." in output

    def test_a_restart_without_the_daemon_says_nothing_of_agents(self):
        update = _run_self_update(
            cli_versions=["c1", "c1"],
            run_results=[_ok()],
            changes=[VersionChange("web", "w1", "w2")],
        )
        assert "web: w1 → w2" in update.result.output
        assert "agent daemon" not in update.result.output


class TestResolveInstallRoot:
    """The shared editable install points at `_main`, whoever runs the update.

    `self-update` used to derive its target from the running source, so running
    it from a worktree repointed the install at that worktree's half-written
    code — and every bare `mael` on the machine ran it.
    """

    def _checkout(self, tmp_path, name):
        """A checkout named `name`, returning the `mael_cli/` package dir."""
        module_dir = tmp_path / name / "cli" / "src" / "mael_cli"
        module_dir.mkdir(parents=True)
        return module_dir

    def test_a_worktree_resolves_to_its_sibling_main(self, tmp_path):
        module_dir = self._checkout(tmp_path, "maelstrom-lima")
        (tmp_path / "_main").mkdir()

        assert resolve_install_root(module_dir) == tmp_path / "_main"

    def test_main_resolves_to_itself(self, tmp_path):
        module_dir = self._checkout(tmp_path, "_main")

        assert resolve_install_root(module_dir) == tmp_path / "_main"

    def test_a_standalone_checkout_resolves_to_itself(self, tmp_path):
        # No `_main` beside it: an ordinary clone, which updates in place.
        module_dir = self._checkout(tmp_path, "maelstrom")

        assert resolve_install_root(module_dir) == tmp_path / "maelstrom"


class TestSelfEnv:
    """`mael self-env` is `mael env` aimed at the maelstrom project's `_main`."""

    def _invoke(self, args, projects_dir):
        from mael_cli.cli import cli

        with patch("mael_domain.context.load_global_config") as mock_global:
            mock_global.return_value = MagicMock(projects_dir=projects_dir)
            return CliRunner().invoke(cli, args)

    # Each verb, and the model function that must receive ("maelstrom", "_main").
    # `logs` and `open` take the target through a state lookup, not an argument,
    # so they assert on the project/worktree that lookup was given.
    VERBS = [
        ("start", "start_env"),
        ("stop", "stop_env"),
        ("restart", "restart_services"),
        ("status", "get_env_status"),
        ("reset", "refresh_env"),
        ("logs", "get_log_files"),
        ("open", "load_env_state"),
    ]

    @pytest.mark.parametrize("verb,target_fn", VERBS)
    def test_every_verb_targets_maelstrom_main(self, verb, target_fn, tmp_path):
        """Every wrapped verb names ("maelstrom", "_main") to the model."""
        (tmp_path / "maelstrom" / "_main").mkdir(parents=True)
        state = EnvState(
            project="maelstrom",
            worktree="_main",
            worktree_path=str(tmp_path / "maelstrom" / "_main"),
            started_at="2026-01-01T00:00:00+00:00",
            services=[],
        )

        defaults = {
            "start_env": state,
            "stop_env": [],
            "get_env_status": [],
            "load_env_state": state,
            "get_app_url": None,
            "get_log_files": {},
            "read_service_logs": "",
            "refresh_env": EnvRefresh(CopyBackResult(), changed=True),
            "restart_services": ([], state),
            "ensure_cmux_browser": None,
            "update_claude_local_md": None,
        }

        patches = {
            name: patch(f"mael_cli.env_cli.{name}", return_value=value)
            for name, value in defaults.items()
        }
        with ExitStack() as stack:
            mocks = {n: stack.enter_context(p) for n, p in patches.items()}
            result = self._invoke(["self-env", verb], tmp_path)

        assert result.exit_code == 0, result.output
        target = mocks[target_fn]
        assert target.called, f"{verb} never reached {target_fn}"
        args = target.call_args.args
        assert args[1:3] == ("maelstrom", "_main"), f"{verb} passed {args[1:3]}"

    def test_start_passes_the_main_worktree_path(self, tmp_path):
        """The resolved path is the `_main` folder, with no project prefix."""
        main_path = tmp_path / "maelstrom" / "_main"
        main_path.mkdir(parents=True)

        with (
            patch("mael_cli.env_cli.start_env") as start,
            patch("mael_cli.env_cli.get_env_status", return_value=[]),
            patch("mael_cli.env_cli.load_env_state", return_value=None),
            patch("mael_cli.env_cli.get_app_url", return_value=None),
        ):
            start.return_value = MagicMock(services=[])
            result = self._invoke(["self-env", "start"], tmp_path)

        assert result.exit_code == 0, result.output
        assert start.call_args.args[3] == main_path

    def test_it_wraps_exactly_the_verbs_that_take_a_worktree(self):
        """The `env` verbs with -w, and no others."""
        from mael_cli.admin_cli import cmd_self_env

        commands = cmd_self_env.list_commands(click.Context(cmd_self_env))

        assert set(commands) == {verb for verb, _ in self.VERBS}

    @pytest.mark.parametrize("verb", [verb for verb, _ in VERBS])
    def test_help_hides_the_fixed_target(self, verb, tmp_path):
        """Neither -w nor the deprecated TARGET shows in a wrapped verb's help."""
        result = self._invoke(["self-env", verb, "--help"], tmp_path)

        assert result.exit_code == 0, result.output
        assert "-w" not in result.output
        assert "TARGET" not in result.output

    @pytest.mark.parametrize("verb", [verb for verb, _ in VERBS])
    def test_the_fixed_target_cannot_be_redirected(self, verb, tmp_path):
        """A -w of the user's own is refused, not a silent retarget."""
        with patch("mael_cli.env_cli.resolve_context") as mock_ctx:
            result = self._invoke(["self-env", verb, "-w", "other.b"], tmp_path)

        assert result.exit_code != 0
        assert "No such option" in result.output
        assert not mock_ctx.called

    def test_a_stray_argument_names_itself_in_the_error(self, tmp_path):
        """The fixed target must not be blamed for the user's own typo."""
        (tmp_path / "maelstrom" / "_main").mkdir(parents=True)

        result = self._invoke(["self-env", "status", "typo"], tmp_path)

        assert result.exit_code != 0
        assert "typo" in result.output
        assert "maelstrom._main" not in result.output


class TestSelfUpdateWritesTheDaemonRootShim:
    """The `mael` on PATH must know the everyday daemon's root.

    That `mael` is a uv entrypoint. It reads no `.env`, and `UV_ENV_FILE` only
    applies to `uv run` in a directory that has one. So a bare `mael agent
    list` in any other directory has no root and no daemon to reach. The shim
    supplies `_main`'s root, and only when nothing else already did — a
    worktree's `.env` and the root a daemon gives its children both still win.
    """

    def _update(
        self, tmp_path, entrypoint_body="#!/bin/sh\nexec real\n", entrypoint=None
    ):
        from mael_cli.admin_cli import cmd_self_update

        if entrypoint is None:
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            entrypoint = bin_dir / "mael"
            entrypoint.write_text(entrypoint_body)
        entrypoint.chmod(0o755)
        with (
            patch("mael_cli.admin_cli.Path.exists", return_value=True),
            patch("mael_cli.admin_cli.shutil.which", return_value="/usr/bin/uv"),
            patch("mael_cli.admin_cli.install_claude_integration", return_value=[]),
            patch("mael_cli.admin_cli.harden_global_config", return_value=[]),
            patch(
                "mael_cli.admin_cli.subprocess.run",
                side_effect=[_ok(), _ok()],
            ),
            patch("mael_cli.admin_cli.mael_path", return_value=str(entrypoint)),
        ):
            result = CliRunner().invoke(cmd_self_update)
        return result, entrypoint

    def test_the_shim_names_the_main_worktrees_root(self, tmp_path):
        result, entrypoint = self._update(tmp_path)
        assert result.exit_code == 0, result.output
        assert "MAEL_AGENT_ROOT" in entrypoint.read_text()
        assert ".maelstrom/daemons/_main" in entrypoint.read_text()

    def test_the_shim_names_the_notebook_root(self, tmp_path):
        result, entrypoint = self._update(tmp_path)
        assert result.exit_code == 0, result.output
        assert (
            f'export MAEL_NOTEBOOK_ROOT="${{MAEL_NOTEBOOK_ROOT:-{pathlib.Path.home() / ".maelstrom"}}}"'
            in entrypoint.read_text()
        )
        assert "export MAEL_PRODUCTION=1" in entrypoint.read_text()

    def test_the_shim_defers_to_a_root_already_set(self, tmp_path):
        """A worktree's `uv run mael`, and a command inside a driven session,
        both arrive with a root already set. Overriding either would send them
        to the wrong daemon."""
        _, entrypoint = self._update(tmp_path)
        assert "${MAEL_AGENT_ROOT:-" in entrypoint.read_text()

    def test_the_shim_execs_the_real_entrypoint(self, tmp_path):
        _, entrypoint = self._update(tmp_path)
        text = entrypoint.read_text()
        assert "exec " in text
        assert '"$@"' in text

    def test_the_shim_stays_executable(self, tmp_path):
        _, entrypoint = self._update(tmp_path)
        assert entrypoint.stat().st_mode & 0o111

    def test_an_unwritable_entrypoint_warns_rather_than_aborting(self, tmp_path):
        """The pull and the dependency sync have already landed by this point,
        so a `mael` this command cannot rewrite must not fail the update."""
        result, _ = self._update(tmp_path)
        assert result.exit_code == 0

        missing = tmp_path / "bin" / "absent"
        with (
            patch("mael_cli.admin_cli.Path.exists", return_value=True),
            patch("mael_cli.admin_cli.shutil.which", return_value="/usr/bin/uv"),
            patch("mael_cli.admin_cli.install_claude_integration", return_value=[]),
            patch("mael_cli.admin_cli.harden_global_config", return_value=[]),
            patch("mael_cli.admin_cli.subprocess.run", side_effect=[_ok(), _ok()]),
            patch("mael_cli.admin_cli.mael_path", return_value=str(missing)),
        ):
            result = CliRunner().invoke(cmd_self_update)
        assert result.exit_code == 0, result.output
        assert "could not point `mael`" in result.output
        assert "MAEL_AGENT_ROOT" in result.output
        assert "Update complete." in result.output

    def test_a_venv_entrypoint_is_left_alone(self, tmp_path):
        """`uv run mael self-update` inside a worktree resolves `mael` to that
        worktree's .venv. Shimming there would corrupt the venv and pin its
        `uv run mael` to _main's root — the very PATH-resolution bug this whole
        change exists to stop."""
        venv_bin = tmp_path / "wt" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        entrypoint = venv_bin / "mael"
        entrypoint.write_text("#!/bin/sh\nexec real\n")
        result, _ = self._update(tmp_path, entrypoint=entrypoint)

        assert result.exit_code == 0, result.output
        assert entrypoint.read_text() == "#!/bin/sh\nexec real\n"
        assert not (venv_bin / "mael-real").exists()
        assert "not the `mael` on your PATH" in result.output

    def test_a_failed_write_puts_the_entrypoint_back(self, tmp_path, monkeypatch):
        """The rename happens before the write. A failure between them would
        leave the user with no `mael` at all, and a warning they cannot act on
        because the command it names is gone."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        entrypoint = bin_dir / "mael"
        entrypoint.write_text("#!/bin/sh\nexec real\n")

        real_write = pathlib.Path.write_text

        def explode(self, *args, **kwargs):
            if self.name == "mael":
                raise OSError("disk full")
            return real_write(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "write_text", explode)
        result, _ = self._update(tmp_path, entrypoint=entrypoint)

        assert result.exit_code == 0, result.output
        assert entrypoint.exists(), "self-update must not remove the user's mael"
        assert entrypoint.read_text() == "#!/bin/sh\nexec real\n"

    def test_a_second_update_does_not_nest_the_shim(self, tmp_path):
        """self-update runs repeatedly. A shim wrapping a shim would grow one
        exec deeper every time."""
        _, entrypoint = self._update(tmp_path)
        first = entrypoint.read_text()
        with (
            patch("mael_cli.admin_cli.Path.exists", return_value=True),
            patch("mael_cli.admin_cli.shutil.which", return_value="/usr/bin/uv"),
            patch("mael_cli.admin_cli.install_claude_integration", return_value=[]),
            patch("mael_cli.admin_cli.harden_global_config", return_value=[]),
            patch("mael_cli.admin_cli.subprocess.run", side_effect=[_ok(), _ok()]),
            patch("mael_cli.admin_cli.mael_path", return_value=str(entrypoint)),
        ):
            CliRunner().invoke(cmd_self_update)
        assert entrypoint.read_text() == first


class TestExportQueue:
    """`mael admin export-queue` reports what the markdown export still owes."""

    @pytest.mark.usefixtures("migrated_notebook")
    def test_a_caught_up_export_says_so(self):
        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_it_reports_the_depth_and_the_oldest_entry(self, migrated_notebook):
        """The two numbers that tell a stalled drain from a busy notebook."""

        db = open_state_db(migrated_notebook / "state.db")
        try:
            table = SqliteTaskTable(db)
            asyncio.run(
                task_model.create(
                    table, project="northwind", title="Ship it", id="NORT-7"
                )
            )
        finally:
            db.close()

        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code == 0, result.output
        assert "owes 1 task(s)" in result.output
        assert "oldest has waited since" in result.output

    def test_rebuild_queues_every_task(self, migrated_notebook):
        """The recovery path: a file that went missing without its row moving.

        A drained queue is the starting state, because that is when a lost
        export file is unrecoverable — no later save re-queues it.
        """

        db = open_state_db(migrated_notebook / "state.db")
        try:
            table = SqliteTaskTable(db)
            for id in ("NORT-7", "NORT-8"):
                asyncio.run(
                    task_model.create(table, project="northwind", title=id, id=id)
                )
            asyncio.run(SqliteExportQueue(db).queue_all([]))
            for id in ("NORT-7", "NORT-8"):
                asyncio.run(SqliteExportQueue(db).clear(f"northwind/{id}"))
            assert asyncio.run(SqliteExportQueue(db).depth()) == 0
        finally:
            db.close()

        result = CliRunner().invoke(cmd_export_queue, ["--rebuild"])

        assert result.exit_code == 0, result.output
        assert "Queued 2 task(s)" in result.output

        db = open_state_db(migrated_notebook / "state.db")
        try:
            queued = asyncio.run(SqliteExportQueue(db).pending())
        finally:
            db.close()
        assert [entry.id for entry in queued] == [
            "northwind/NORT-7",
            "northwind/NORT-8",
        ]
        assert [entry.path for entry in queued] == [
            "northwind/todo/NORT-7.md",
            "northwind/todo/NORT-8.md",
        ]

    @pytest.mark.usefixtures("migrated_notebook")
    def test_rebuild_on_an_empty_notebook_queues_nothing(self):
        result = CliRunner().invoke(cmd_export_queue, ["--rebuild"])

        assert result.exit_code == 0, result.output
        assert "Queued 0 task(s)" in result.output

    def test_a_database_behind_this_build_is_refused(self, tmp_path, monkeypatch):
        """An ordinary open never migrates, so the check names the command."""
        monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
        open_state_db(tmp_path / "state.db").close()

        result = CliRunner().invoke(cmd_export_queue, [])

        assert result.exit_code != 0
        assert "bin/install" in result.output
