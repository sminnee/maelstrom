"""Tests for maelstrom.worktree_launcher module."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from unittest.mock import patch

from maelstrom.shell import Command, Pipeline, describe, run_cmd
from maelstrom.worktree_launcher import (
    build_claude_command,
    build_task_launch_line,
    launch_claude_in_worktree,
    open_claude_workspace,
    open_worktree,
)


class TestOpenWorktree:
    """Tests for open_worktree function."""

    def test_open_worktree_success(self):
        """Test opening a worktree with a valid command (routed via run_cmd)."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.run_cmd") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code")
                mock_run.assert_called_once_with(["code", str(worktree_path)])

    def test_open_worktree_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.run_cmd") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(RuntimeError, match="Command not found"):
                    open_worktree(worktree_path, "nonexistent-command")

    def test_open_worktree_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree_launcher.run_cmd") as mock_run:
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

    def test_with_session_id(self):
        assert build_claude_command(session_id="abc-123") == [
            "claude",
            "--session-id",
            "abc-123",
        ]

    def test_permission_mode_and_session_id(self):
        assert build_claude_command("plan", "abc-123") == [
            "claude",
            "--permission-mode",
            "plan",
            "--session-id",
            "abc-123",
        ]


class TestBuildTaskLaunchLine:
    """Tests for the ``mael task prompt <id> | claude`` pipeline builder."""

    def test_no_permission_mode(self):
        assert describe(build_task_launch_line("proj", "t1")) == (
            "mael task prompt t1 --project proj | claude"
        )

    def test_with_permission_mode(self):
        assert describe(build_task_launch_line("proj", "t1", "plan")) == (
            "mael task prompt t1 --project proj | claude --permission-mode plan"
        )

    def test_auto_permission_mode(self):
        assert describe(build_task_launch_line("proj", "t1", "auto")) == (
            "mael task prompt t1 --project proj | claude --permission-mode auto"
        )

    def test_session_id_appended(self):
        # session_id also rides as MAEL_SESSION_ID on the claude segment so the
        # session-channel can record the real id (the harness doesn't export
        # CLAUDE_SESSION_ID to subprocesses).
        assert describe(
            build_task_launch_line("proj", "t1", "plan", session_id="abc-123")
        ) == (
            "mael task prompt t1 --project proj | "
            "MAEL_SESSION_ID=abc-123 claude --permission-mode plan --session-id abc-123"
        )

    def test_quotes_ids_and_projects_with_spaces(self):
        assert describe(build_task_launch_line("my proj", "task one")) == (
            "mael task prompt 'task one' --project 'my proj' | claude"
        )

    def test_env_prefixes_claude_segment_not_prompt_segment(self):
        # The env must land on the ``claude`` segment (right of the pipe) so the
        # interactive session inherits it. A front-of-line prefix would only
        # reach ``mael task prompt`` (POSIX scopes it to the first command).
        line = describe(build_task_launch_line(
            "proj", "t1", "plan", env={"MAEL_TASK_ID": "t1"}
        ))
        left, right = line.split(" | ", 1)
        assert left == "mael task prompt t1 --project proj"
        assert right == "MAEL_TASK_ID=t1 claude --permission-mode plan"
        # The env assignment must NOT appear on the left (prompt) segment.
        assert "MAEL_TASK_ID=" not in left

    def test_no_env_leaves_segments_bare(self):
        line = describe(build_task_launch_line("proj", "t1", env=None))
        assert line == "mael task prompt t1 --project proj | claude"

    def test_env_reaches_claude_through_real_shell(self):
        # Semantic regression guard: run the produced line through ``sh -c`` with
        # a stub ``claude`` on PATH that echoes ``$MAEL_TASK_ID``. If a future
        # refactor moves the env prefix back to the front of the pipe, the var
        # won't reach the stub and this asserts the regression.
        with TemporaryDirectory() as tmpdir:
            stub_dir = Path(tmpdir)
            # Stub stands in for both ``claude`` and ``mael`` so the line runs
            # end to end without touching the real binaries.
            for name in ("claude", "mael"):
                stub = stub_dir / name
                stub.write_text('#!/bin/sh\necho "$MAEL_TASK_ID"\n')
                stub.chmod(0o755)
            line = describe(build_task_launch_line(
                "proj", "t1", env={"MAEL_TASK_ID": "t1"}
            ))
            env = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
            result = subprocess.run(
                ["sh", "-c", line],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            # The right-hand stub (``claude``) prints the task id it inherited.
            assert result.stdout.strip().splitlines()[-1] == "t1"


class TestRunCmdReplaceProcess:
    """Tests for ``run_cmd(..., replace_process=True)`` — the old exec_claude.

    Lives here because the launcher is the primary caller; ``run_cmd`` itself
    lives in the ``shell`` subsystem, so patching targets ``maelstrom.shell.os``.
    """

    # ``os.execvp`` never returns in reality; mocked it does, so it's given a
    # ``SystemExit`` side effect to halt before the fork-and-wait fallthrough —
    # the same way a real ``execvp`` would stop execution there.
    _STOP = SystemExit

    def test_argv_execs_directly_no_chdir(self):
        # A plain argv execs directly (no sh hop); cwd=None means no chdir.
        with patch("maelstrom.shell.os.chdir") as mock_chdir, \
             patch("maelstrom.shell.os.execvp",
                   side_effect=self._STOP) as mock_execvp:
            with pytest.raises(SystemExit):
                run_cmd(["claude"], cwd=None, replace_process=True)
            mock_chdir.assert_not_called()
            mock_execvp.assert_called_once_with("claude", ["claude"])

    def test_cwd_chdirs(self):
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.shell.os.chdir") as mock_chdir, \
                 patch("maelstrom.shell.os.execvp",
                       side_effect=self._STOP) as mock_execvp:
                with pytest.raises(SystemExit):
                    run_cmd(["claude"], cwd=worktree_path, replace_process=True)
                mock_chdir.assert_called_once_with(worktree_path)
                mock_execvp.assert_called_once_with("claude", ["claude"])

    def test_env_updates_environ(self):
        with patch("maelstrom.shell.os.chdir"), \
             patch("maelstrom.shell.os.execvp", side_effect=self._STOP), \
             patch.dict("maelstrom.shell.os.environ", {}, clear=True):
            with pytest.raises(SystemExit):
                run_cmd(["claude"], cwd=None, env={"MAEL_TASK_ID": "x"},
                        replace_process=True)
            assert os.environ["MAEL_TASK_ID"] == "x"

    def test_plain_command_execs_via_sh(self):
        # A Command (not a bare argv) goes through ``sh -c "exec ..."`` so the
        # shell replaces itself and nothing lingers.
        with patch("maelstrom.shell.os.chdir"), \
             patch("maelstrom.shell.os.execvp",
                   side_effect=self._STOP) as mock_execvp:
            with pytest.raises(SystemExit):
                run_cmd(Command(["claude"]), cwd=None, replace_process=True)
            mock_execvp.assert_called_once_with(
                "sh", ["sh", "-c", "exec claude"]
            )

    def test_pipeline_execs_via_sh(self):
        # A pipeline runs through ``sh -c "exec ..."`` so the process is
        # replaced while stdin/stdout stay inherited (stdout = TTY → interactive).
        expr = build_task_launch_line(
            "p", "t1", "plan", env={"MAEL_TASK_ID": "t1"}
        )
        with patch("maelstrom.shell.os.chdir"), \
             patch("maelstrom.shell.os.execvp",
                   side_effect=self._STOP) as mock_execvp:
            with pytest.raises(SystemExit):
                run_cmd(expr, cwd=None, replace_process=True)
            mock_execvp.assert_called_once_with(
                "sh",
                ["sh", "-c",
                 "exec mael task prompt t1 --project p "
                 "| MAEL_TASK_ID=t1 claude --permission-mode plan"],
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
                Command(
                    ["claude", "--permission-mode", "plan", "hi there"],
                    env={"MAEL_TASK_ID": "t1"},
                ),
            )
            assert placed is True
            mock_ensure.assert_called_once_with(
                "proj",
                "alpha",
                "/wt",
                command="MAEL_TASK_ID=t1 claude --permission-mode plan 'hi there'",
                install_cmd="npm install",
            )

    def test_passes_pipeline_to_seam_rendered(self):
        # A Pipeline carries the env on its ``claude`` Command (the right of the
        # pipe); ``open_claude_workspace`` renders it — env stays on the correct
        # segment structurally, so there's nothing to re-prefix at the front.
        expr = Pipeline([
            Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
            Command(
                ["claude", "--permission-mode", "plan"],
                env={"MAEL_TASK_ID": "t1"},
            ),
        ])
        with patch(
            "maelstrom.cmux.mael_layout.ensure_worktree_workspace",
            return_value=True,
        ) as mock_ensure, patch(
            "maelstrom.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            placed = open_claude_workspace("proj", "alpha", Path("/wt"), expr)
            assert placed is True
            assert mock_ensure.call_args.kwargs["command"] == (
                "mael task prompt t1 --project proj "
                "| MAEL_TASK_ID=t1 claude --permission-mode plan"
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
                 patch("maelstrom.worktree_launcher.run_cmd") as mock_run:
                launch_claude_in_worktree(
                    worktree_path, project="proj", worktree="alpha"
                )
                mock_open.assert_called_once()
                mock_run.assert_not_called()

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
                "maelstrom.worktree_launcher.run_cmd",
                side_effect=lambda *a, **k: order.append("exec"),
            ) as mock_run:
                launch_claude_in_worktree(
                    worktree_path,
                    project="proj",
                    worktree="alpha",
                    task_id="t1",
                    permission_mode="plan",
                    env={"MAEL_TASK_ID": "t1"},
                )
                # Non-cmux: install runs blocking, then replace-exec the pipeline.
                # The env rides on the ``claude`` Command (right of the pipe) so
                # the session inherits it; run_cmd still gets ``env`` too as the
                # os.environ.update backstop.
                mock_install.assert_called_once_with(worktree_path)
                mock_run.assert_called_once()
                expr, kwargs = (
                    mock_run.call_args.args[0],
                    mock_run.call_args.kwargs,
                )
                assert describe(expr) == (
                    "mael task prompt t1 --project proj "
                    "| MAEL_TASK_ID=t1 claude --permission-mode plan"
                )
                assert kwargs == {
                    "cwd": worktree_path,
                    "env": {"MAEL_TASK_ID": "t1"},
                    "replace_process": True,
                }
                assert order == ["install", "exec"]

    def test_execs_plain_claude_when_no_task(self):
        # cli.py opens a worktree with no task → plain ``claude`` argv, no pipeline.
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch(
                "maelstrom.worktree_launcher.open_claude_workspace", return_value=False
            ), patch("maelstrom.worktree_launcher.run_install_cmd"), patch(
                "maelstrom.worktree_launcher.run_cmd"
            ) as mock_run:
                launch_claude_in_worktree(
                    worktree_path, project="proj", worktree="alpha"
                )
                mock_run.assert_called_once()
                expr = mock_run.call_args.args[0]
                # Plain claude wrapped in a Command (empty env) → renders bare.
                assert describe(expr) == "claude"
                assert mock_run.call_args.kwargs == {
                    "cwd": worktree_path,
                    "env": None,
                    "replace_process": True,
                }
