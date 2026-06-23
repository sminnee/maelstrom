"""Tests for maelstrom.worktree_launcher module."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from unittest.mock import patch

from maelstrom.worktree_launcher import (
    build_claude_command,
    build_task_launch_line,
    exec_claude,
    launch_claude_in_worktree,
    open_claude_workspace,
    open_worktree,
)


class TestOpenWorktree:
    """Tests for open_worktree function."""

    def test_open_worktree_success(self):
        """Test opening a worktree with a valid command."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code")
                mock_run.assert_called_once_with(["code", str(worktree_path)], check=True)

    def test_open_worktree_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(RuntimeError, match="Command not found"):
                    open_worktree(worktree_path, "nonexistent-command")

    def test_open_worktree_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.CalledProcessError(1, "code")
                with pytest.raises(RuntimeError, match="Failed to open worktree"):
                    open_worktree(worktree_path, "code")


class TestBuildClaudeCommand:
    """Tests for the pure command-builder (trailing ``claude`` argv only)."""

    def test_bare(self):
        assert build_claude_command() == ["claude"]

    def test_with_permission_mode(self):
        assert build_claude_command(permission_mode="plan") == [
            "claude",
            "--permission-mode",
            "plan",
        ]


class TestBuildTaskLaunchLine:
    """Tests for the ``mael task prompt <id> | claude`` pipeline builder."""

    def test_no_permission_mode(self):
        assert build_task_launch_line("proj", "t1") == (
            "mael task prompt t1 --project proj | claude"
        )

    def test_with_permission_mode(self):
        assert build_task_launch_line("proj", "t1", "plan") == (
            "mael task prompt t1 --project proj | claude --permission-mode plan"
        )

    def test_auto_permission_mode(self):
        assert build_task_launch_line("proj", "t1", "auto") == (
            "mael task prompt t1 --project proj | claude --permission-mode auto"
        )

    def test_quotes_ids_and_projects_with_spaces(self):
        assert build_task_launch_line("my proj", "task one") == (
            "mael task prompt 'task one' --project 'my proj' | claude"
        )


class TestExecClaude:
    """Tests for the execvp placement peer."""

    def test_cwd_none_does_not_chdir(self):
        with patch("maelstrom.worktree_launcher.os.chdir") as mock_chdir, \
             patch("maelstrom.worktree_launcher.os.execvp") as mock_execvp:
            exec_claude(["claude"], cwd=None)
            mock_chdir.assert_not_called()
            mock_execvp.assert_called_once_with("claude", ["claude"])

    def test_cwd_chdirs(self):
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.os.chdir") as mock_chdir, \
                 patch("maelstrom.worktree_launcher.os.execvp") as mock_execvp:
                exec_claude(["claude"], cwd=worktree_path)
                mock_chdir.assert_called_once_with(worktree_path)
                mock_execvp.assert_called_once_with("claude", ["claude"])

    def test_env_updates_environ(self):
        with patch("maelstrom.worktree_launcher.os.chdir"), \
             patch("maelstrom.worktree_launcher.os.execvp"), \
             patch.dict("maelstrom.worktree_launcher.os.environ", {}, clear=True):
            exec_claude(["claude"], cwd=None, env={"MAEL_TASK_ID": "x"})
            assert os.environ["MAEL_TASK_ID"] == "x"

    def test_pipeline_string_execs_via_sh(self):
        # A pipeline string runs through ``sh -c "exec ..."`` so the process is
        # replaced while stdin/stdout stay inherited (stdout = TTY → interactive).
        with patch("maelstrom.worktree_launcher.os.chdir"), \
             patch("maelstrom.worktree_launcher.os.execvp") as mock_execvp:
            exec_claude("mael task prompt t1 --project p | claude", cwd=None)
            mock_execvp.assert_called_once_with(
                "sh",
                ["sh", "-c", "exec mael task prompt t1 --project p | claude"],
            )


class TestOpenClaudeWorkspace:
    """Tests for the cmux new-workspace placement peer.

    open_claude_workspace now delegates entirely to the policy seam
    mael_layout.ensure_worktree_workspace (which owns the cmux-detection and
    create-vs-reuse logic, tested in test_mael_layout.py). These tests guard the
    translation: how it builds the command/install args and returns the seam's
    placed result.
    """

    def test_returns_false_without_project_or_worktree(self):
        # A workspace can't be named without project+worktree → no placement.
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace"
        ) as mock_ensure:
            placed = open_claude_workspace(
                None, "alpha", Path("/wt"), ["claude", "hi"]
            )
            assert placed is False
            mock_ensure.assert_not_called()

    def test_returns_seam_result(self):
        # Outside cmux the seam returns False; open_claude_workspace passes it on.
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace",
            return_value=False,
        ), patch(
            "maelstrom.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            placed = open_claude_workspace(
                "proj", "alpha", Path("/wt"), ["claude", "hi"]
            )
            assert placed is False

    def test_passes_shell_line_and_install_to_seam(self):
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace",
            return_value=True,
        ) as mock_ensure, patch(
            "maelstrom.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd="npm install"),
        ):
            placed = open_claude_workspace(
                "proj",
                "alpha",
                Path("/wt"),
                ["claude", "--permission-mode", "plan", "hi there"],
                env={"MAEL_TASK_ID": "t1"},
            )
            assert placed is True
            mock_ensure.assert_called_once_with(
                "proj",
                "alpha",
                "/wt",
                command="MAEL_TASK_ID=t1 claude --permission-mode plan 'hi there'",
                install_cmd="npm install",
            )

    def test_passes_env_prefixed_pipeline_to_seam(self):
        # A pipeline string body gets the env prefix prepended verbatim (no
        # re-quoting of the pipeline itself).
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace",
            return_value=True,
        ) as mock_ensure, patch(
            "maelstrom.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            placed = open_claude_workspace(
                "proj",
                "alpha",
                Path("/wt"),
                "mael task prompt t1 --project proj | claude --permission-mode plan",
                env={"MAEL_TASK_ID": "t1"},
            )
            assert placed is True
            assert mock_ensure.call_args.kwargs["command"] == (
                "MAEL_TASK_ID=t1 mael task prompt t1 --project proj "
                "| claude --permission-mode plan"
            )

    def test_empty_install_cmd_passed_as_none(self):
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace",
            return_value=True,
        ) as mock_ensure, patch(
            "maelstrom.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            open_claude_workspace("proj", "alpha", Path("/wt"), ["claude", "hi"])
            assert mock_ensure.call_args.kwargs["install_cmd"] is None


class TestLaunchClaudeInWorktree:
    """Guards the old workspace-or-exec-in-worktree composition."""

    def test_uses_workspace_when_cmux_succeeds(self):
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch(
                "maelstrom.worktree_launcher.open_claude_workspace", return_value=True
            ) as mock_open, \
                 patch("maelstrom.worktree_launcher.exec_claude") as mock_exec:
                launch_claude_in_worktree(
                    worktree_path, project="proj", worktree="alpha"
                )
                mock_open.assert_called_once()
                mock_exec.assert_not_called()

    def test_execs_task_pipeline_when_no_workspace(self):
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            order = []
            with patch(
                "maelstrom.worktree_launcher.open_claude_workspace", return_value=False
            ), patch(
                "maelstrom.worktree_launcher.run_install_cmd",
                side_effect=lambda *a, **k: order.append("install"),
            ) as mock_install, patch(
                "maelstrom.worktree_launcher.exec_claude",
                side_effect=lambda *a, **k: order.append("exec"),
            ) as mock_exec:
                launch_claude_in_worktree(
                    worktree_path,
                    project="proj",
                    worktree="alpha",
                    task_id="t1",
                    permission_mode="plan",
                    env={"MAEL_TASK_ID": "t1"},
                )
                # Non-cmux: install runs blocking, then exec the pipeline string.
                mock_install.assert_called_once_with(worktree_path)
                mock_exec.assert_called_once_with(
                    "mael task prompt t1 --project proj "
                    "| claude --permission-mode plan",
                    cwd=worktree_path,
                    env={"MAEL_TASK_ID": "t1"},
                )
                assert order == ["install", "exec"]

    def test_execs_plain_claude_when_no_task(self):
        # cli.py opens a worktree with no task → plain ``claude`` argv, no pipeline.
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch(
                "maelstrom.worktree_launcher.open_claude_workspace", return_value=False
            ), patch("maelstrom.worktree_launcher.run_install_cmd"), patch(
                "maelstrom.worktree_launcher.exec_claude"
            ) as mock_exec:
                launch_claude_in_worktree(
                    worktree_path, project="proj", worktree="alpha"
                )
                mock_exec.assert_called_once_with(
                    ["claude"], cwd=worktree_path, env=None
                )
