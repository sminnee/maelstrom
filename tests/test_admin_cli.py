"""Tests for self-management CLI commands (focus: self-update dep sync)."""

import subprocess

from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.admin_cli import cmd_self_update


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


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
            run_results=[_ok(stdout="Already up to date.\n"), _ok(stderr="Installed.\n")],
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
