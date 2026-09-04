"""Tests for self-management CLI commands (focus: self-update dep sync)."""

import subprocess
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from maelstrom.admin_cli import cmd_self_update
from maelstrom.env import EnvState


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout, stderr=stderr
    )


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


class TestSelfUpdateDependencySync:
    """self-update must re-resolve dependencies after pulling new source.

    A `git pull` that introduces a new pyproject dependency leaves the installed
    environment missing the package until uv re-resolves it, so commands that
    import the new dep crash post-update. These tests pin the sync step.
    """

    def _run(self, which_uv, run_results):
        """Invoke self-update with git/install/harden stubbed out.

        ``run_results`` is the sequence of CompletedProcess values returned by
        the patched ``subprocess.run`` (first call is ``git pull``, second is
        the ``uv tool install`` sync when uv is present).
        """
        with (
            patch("maelstrom.admin_cli.Path.exists", return_value=True),
            patch("maelstrom.admin_cli.shutil.which", return_value=which_uv),
            patch("maelstrom.admin_cli.install_claude_integration", return_value=[]),
            patch("maelstrom.admin_cli.harden_global_config", return_value=[]),
            patch("maelstrom.admin_cli.subprocess.run", side_effect=run_results) as run,
        ):
            result = CliRunner().invoke(cmd_self_update)
        return result, run

    def test_reinstalls_editable_tool_when_uv_present(self):
        result, run = self._run(
            which_uv="/usr/bin/uv",
            run_results=[
                _ok(stdout="Already up to date.\n"),
                _ok(stderr="Installed.\n"),
            ],
        )

        assert result.exit_code == 0, result.output
        # Second subprocess call is the dependency sync.
        sync_cmd = run.call_args_list[1].args[0]
        assert sync_cmd[:3] == ["/usr/bin/uv", "tool", "install"]
        assert "--editable" in sync_cmd
        assert "--reinstall" in sync_cmd
        # --force overwrites the live `mael` entrypoint; without it uv aborts.
        assert "--force" in sync_cmd
        assert "Update complete." in result.output

    def test_warns_and_skips_sync_when_uv_missing(self):
        # Only git pull runs; no sync call to make.
        result, run = self._run(which_uv=None, run_results=[_ok()])

        assert result.exit_code == 0, result.output
        assert run.call_count == 1  # git pull only
        assert "uv" in result.output and "skipping dependency sync" in result.output

    def test_warns_but_succeeds_when_sync_fails(self):
        # The pull already landed, so a failed sync must not abort the command.
        result, _ = self._run(
            which_uv="/usr/bin/uv",
            run_results=[_ok(), _fail(stderr="resolution failed")],
        )

        assert result.exit_code == 0, result.output
        assert "dependency sync failed" in result.output
        assert "Update complete." in result.output

    def test_aborts_when_not_a_git_checkout(self):
        with patch("maelstrom.admin_cli.Path.exists", return_value=False):
            result = CliRunner().invoke(cmd_self_update)

        assert result.exit_code != 0
        assert "not installed from a git checkout" in result.output


class TestSelfEnv:
    """`mael self-env` is `mael env` aimed at the maelstrom project's `_main`."""

    def _invoke(self, args, projects_dir):
        from maelstrom.cli import cli

        with patch("maelstrom.context.load_global_config") as mock_global:
            mock_global.return_value = MagicMock(projects_dir=projects_dir)
            return CliRunner().invoke(cli, args)

    # Each verb, and the model function that must receive ("maelstrom", "_main").
    # `logs` and `open` take the target through a state lookup, not an argument,
    # so they assert on the project/worktree that lookup was given.
    VERBS = [
        ("start", "start_env"),
        ("stop", "stop_env"),
        ("restart", "start_env"),
        ("status", "get_env_status"),
        ("reset", "regenerate_and_restart_if_running"),
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
            "regenerate_and_restart_if_running": ([], None),
            "ensure_cmux_browser": None,
            "update_claude_local_md": None,
            "copy_back_new_env_vars": MagicMock(added={}, conflicts=[]),
        }

        patches = {
            name: patch(f"maelstrom.env_cli.{name}", return_value=value)
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
            patch("maelstrom.env_cli.start_env") as start,
            patch("maelstrom.env_cli.get_env_status", return_value=[]),
            patch("maelstrom.env_cli.load_env_state", return_value=None),
            patch("maelstrom.env_cli.get_app_url", return_value=None),
        ):
            start.return_value = MagicMock(services=[])
            result = self._invoke(["self-env", "start"], tmp_path)

        assert result.exit_code == 0, result.output
        assert start.call_args.args[3] == main_path

    def test_a_stray_argument_names_itself_in_the_error(self, tmp_path):
        """The fixed target must not be blamed for the user's own typo."""
        (tmp_path / "maelstrom" / "_main").mkdir(parents=True)

        result = self._invoke(["self-env", "status", "typo"], tmp_path)

        assert result.exit_code != 0
        assert "typo" in result.output
        assert "maelstrom._main" not in result.output
