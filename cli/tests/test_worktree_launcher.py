"""Tests for mael_cli.worktree_launcher module."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mael_agent.agent_transport import RecordingDaemonClient
from mael_cli.worktree_launcher import (
    AddContext,
    build_claude_command,
    detect_add_context,
    launch_add_in_worktree,
    launch_claude_in_worktree,
    open_claude_workspace,
    open_worktree,
    start_agent_in_worktree,
    start_install_async,
)
from mael_common.shell import Command, Pipeline, describe
from mael_domain.cmux.mael_layout import MaelCmux


class TestOpenWorktree:
    """Tests for open_worktree function."""

    def test_open_worktree_success(self):
        """Test opening a worktree with a valid command (routed via run_cmd)."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("mael_cli.worktree_launcher.run_cmd") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code")
                mock_run.assert_called_once_with(["code", str(worktree_path)])

    def test_open_worktree_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("mael_cli.worktree_launcher.run_cmd") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(RuntimeError, match="Command not found"):
                    open_worktree(worktree_path, "nonexistent-command")

    def test_open_worktree_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("mael_cli.worktree_launcher.run_cmd") as mock_run:
                mock_run.side_effect = subprocess.CalledProcessError(1, "code")
                with pytest.raises(RuntimeError, match="Failed to open worktree"):
                    open_worktree(worktree_path, "code")


class TestAddLauncher:
    def test_daemon_context_takes_precedence_over_cmux(self):
        with (
            patch.dict(os.environ, {"MAEL_HARNESS_TYPE": "daemon"}),
            patch("mael_cli.worktree_launcher.current_client") as cmux,
        ):
            assert detect_add_context() == AddContext.DAEMON
        cmux.assert_not_called()

    def test_install_starts_asynchronously_in_the_worktree(self, tmp_path):
        with (
            patch("mael_cli.worktree_launcher.load_config_or_default") as config,
            patch("mael_cli.worktree_launcher.subprocess.Popen") as popen,
        ):
            config.return_value.install_cmd = "uv sync"
            assert start_install_async(tmp_path)
        popen.assert_called_once_with(
            ["sh", "-c", "uv sync"], cwd=tmp_path, start_new_session=True
        )

    @pytest.mark.asyncio
    async def test_regular_cli_runs_in_the_prepared_worktree(self, tmp_path):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.REGULAR,
                harness="cli",
            )
        assert run.call_args.kwargs["cwd"] == tmp_path

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "command"),
        [
            ("opus", ["claude", "--model", "opus"]),
            (
                "codex:terra",
                [
                    "codex",
                    "--sandbox",
                    "workspace-write",
                    "--model",
                    "gpt-5.6-terra",
                    "-c",
                    "model_reasoning_effort=medium",
                ],
            ),
            ("opencode:kimi", ["opencode", "--model", "kimi"]),
        ],
    )
    async def test_regular_cli_uses_the_selected_model_command(
        self, tmp_path, model, command
    ):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.REGULAR,
                harness="cli",
                model=model,
            )
        assert run.call_args.args[0] == command

    @pytest.mark.asyncio
    async def test_omitted_model_uses_claude_opus(self, tmp_path):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.REGULAR,
                harness="cli",
            )
        assert run.call_args.args[0] == ["claude", "--model", "opus"]

    @pytest.mark.asyncio
    async def test_daemon_receives_the_claude_model_alias(self, tmp_path):
        client = RecordingDaemonClient(replies=[{"id": "agent-1"}])
        prompt = tmp_path / "agent-prompt.md"
        with (
            patch("mael_cli.worktree_launcher.daemon_client", return_value=client),
            patch("mael_cli.worktree_launcher.agent_prompt_file", return_value=prompt),
        ):
            assert (
                await start_agent_in_worktree(tmp_path, model="claude:sonnet")
                == "agent-1"
            )
        assert client.calls == [
            {
                "cmd": "start",
                "cwd": str(tmp_path),
                "model": "sonnet",
                "resume": False,
                "system_prompt_file": str(prompt),
            }
        ]

    @pytest.mark.asyncio
    async def test_the_daemon_receives_the_execute_model_unresolved(self, tmp_path):
        """Raw, not an alias: the daemon resolves it when the plan is approved,
        and nothing spends it until then."""
        client = RecordingDaemonClient(replies=[{"id": "agent-1"}])
        with patch("mael_cli.worktree_launcher.daemon_client", return_value=client):
            assert (
                await start_agent_in_worktree(
                    tmp_path, model="claude:opus", execute_model="claude:sonnet"
                )
                == "agent-1"
            )
        assert client.calls[0]["execute_model"] == "claude:sonnet"

    @pytest.mark.asyncio
    async def test_no_execute_model_sends_no_key(self, tmp_path):
        """The no-op guarantee, at the wire: an older daemon reads the same
        payload it always did."""
        client = RecordingDaemonClient(replies=[{"id": "agent-1"}])
        with patch("mael_cli.worktree_launcher.daemon_client", return_value=client):
            await start_agent_in_worktree(tmp_path, model="claude:opus")
        assert "execute_model" not in client.calls[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["codex:terra", "opencode:kimi"])
    async def test_daemon_rejects_non_claude_models(self, tmp_path, model):
        with pytest.raises(ValueError, match="daemon is not available"):
            await start_agent_in_worktree(tmp_path, model=model)

    @pytest.mark.asyncio
    async def test_regular_daemon_starts_an_agent_without_cmux(self, tmp_path):
        with (
            patch(
                "mael_cli.worktree_launcher.start_agent_in_worktree",
                return_value="agent-1",
            ) as start,
            patch("mael_cli.worktree_launcher.subprocess.run") as run,
        ):
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.REGULAR,
                harness="daemon",
            )
        start.assert_awaited_once_with(tmp_path, model=None, execute_model=None)
        run.assert_not_called()

    @pytest.mark.asyncio
    async def test_driven_cli_leaves_the_worktree_when_cmux_is_unavailable(
        self, tmp_path
    ):
        with (
            patch("mael_cli.worktree_launcher.ensure_cmux_running", return_value=False),
            patch("mael_cli.worktree_launcher.start_agent_in_worktree") as start,
        ):
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.DAEMON,
                harness="cli",
            )
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmux_daemon_attaches_the_started_agent(self, tmp_path, fake_cmux):
        with (
            patch("mael_cli.worktree_launcher.load_config_or_default") as config,
            patch(
                "mael_cli.worktree_launcher.start_agent_in_worktree",
                return_value="agent-1",
            ),
        ):
            config.return_value.install_cmd = "uv sync"
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.CMUX,
                harness="daemon",
            )
        # The installer shell is ready before the agent tab attaches.
        texts = fake_cmux.texts()
        assert texts.index("uv sync\n") < texts.index("mael agent attach agent-1\n")
        assert fake_cmux.tabs("proj-alpha")[0] == ["Claude", "Claude"]

    @pytest.mark.asyncio
    async def test_cmux_cli_uses_the_selected_model_command(self, tmp_path, fake_cmux):
        with patch("mael_cli.worktree_launcher.load_config_or_default") as config:
            config.return_value.install_cmd = ""
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.CMUX,
                harness="cli",
                model="codex:terra",
            )
        assert fake_cmux.sent[-1][1] == (
            "codex --sandbox workspace-write --model gpt-5.6-terra -c "
            "model_reasoning_effort=medium\n"
        )

    @pytest.mark.asyncio
    async def test_cmux_no_agent_uses_one_shell_surface(self, tmp_path, fake_cmux):
        with (
            patch("mael_cli.worktree_launcher.load_config_or_default") as config,
            patch("mael_cli.worktree_launcher.start_agent_in_worktree") as agent,
        ):
            config.return_value.install_cmd = "uv sync"
            assert await launch_add_in_worktree(
                tmp_path,
                "proj",
                "alpha",
                context=AddContext.CMUX,
                harness="cli",
                no_agent=True,
            )
        assert fake_cmux.tabs("proj-alpha") == [["Terminal"]]
        assert fake_cmux.texts() == [f"cd {tmp_path}\n", "uv sync\n"]
        agent.assert_not_called()


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

    def test_resume_emits_resume_flag(self):
        # A previously-started session is reattached with --resume, not recreated
        # with --session-id (which would fail "session already exists").
        assert build_claude_command(session_id="abc-123", resume=True) == [
            "claude",
            "--resume",
            "abc-123",
        ]

    def test_resume_false_still_creates(self):
        assert build_claude_command(session_id="abc-123", resume=False) == [
            "claude",
            "--session-id",
            "abc-123",
        ]

    def test_resume_with_permission_mode(self):
        assert build_claude_command("plan", "abc-123", resume=True) == [
            "claude",
            "--permission-mode",
            "plan",
            "--resume",
            "abc-123",
        ]

    def test_with_model(self):
        assert build_claude_command(model="opus") == ["claude", "--model", "opus"]

    def test_model_is_free_form_passthrough(self):
        # No validation enum — a full id passes through verbatim, exactly as an
        # alias does; `claude` itself rejects a bad value.
        assert build_claude_command(model="claude-opus-5") == [
            "claude",
            "--model",
            "claude-opus-5",
        ]

    def test_empty_model_omits_the_flag(self):
        # An unset model must leave `claude` on the user's configured default,
        # never on a `--model ""` that would fail.
        assert build_claude_command(model="") == ["claude"]
        assert build_claude_command(model=None) == ["claude"]

    def test_model_with_permission_mode_and_session_id(self):
        assert build_claude_command("plan", "abc-123", model="opus") == [
            "claude",
            "--permission-mode",
            "plan",
            "--model",
            "opus",
            "--session-id",
            "abc-123",
        ]


class TestOpenClaudeWorkspace:
    """Tests for the cmux new-workspace placement peer.

    open_claude_workspace hands the rendered command and the install command to
    WorktreeWorkspace.open_for_agent, whose create-vs-reuse logic is tested in
    test_mael_layout.py. These tests guard the translation.
    """

    def test_returns_false_without_project_or_worktree(self, fake_cmux):
        # A workspace can't be named without project+worktree → no placement.
        placed = open_claude_workspace(None, "alpha", Path("/wt"), ["claude", "hi"])
        assert placed is False
        assert fake_cmux.list_workspaces() == []

    def test_false_outside_cmux(self):
        with patch.object(MaelCmux, "current", return_value=None):
            placed = open_claude_workspace(
                "proj", "alpha", Path("/wt"), ["claude", "hi"]
            )
        assert placed is False

    def test_passes_shell_line_and_install_to_the_workspace(self, fake_cmux):
        with patch(
            "mael_cli.worktree_launcher.load_config_or_default",
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
        texts = fake_cmux.texts()
        assert "npm install\n" in texts
        assert texts[-1] == (
            "MAEL_TASK_ID=t1 claude --permission-mode plan 'hi there'\n"
        )

    def test_passes_pipeline_rendered(self, fake_cmux):
        # A Pipeline carries the env on its ``claude`` Command (the right of the
        # pipe); ``open_claude_workspace`` renders it — env stays on the correct
        # segment structurally, so there's nothing to re-prefix at the front.
        expr = Pipeline(
            [
                Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
                Command(
                    ["claude", "--permission-mode", "plan"],
                    env={"MAEL_TASK_ID": "t1"},
                ),
            ]
        )
        with patch(
            "mael_cli.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            assert open_claude_workspace("proj", "alpha", Path("/wt"), expr)
        assert fake_cmux.sent[-1][1] == (
            "mael task prompt t1 --project proj "
            "| MAEL_TASK_ID=t1 claude --permission-mode plan\n"
        )

    def test_empty_install_cmd_runs_nothing(self, fake_cmux):
        with patch(
            "mael_cli.worktree_launcher.load_config_or_default",
            return_value=SimpleNamespace(install_cmd=""),
        ):
            open_claude_workspace("proj", "alpha", Path("/wt"), ["claude", "hi"])
        # The shell pane gets its cd and no installer.
        shell = fake_cmux.surface_ref("proj-alpha", 1)
        assert [text for ref, text in fake_cmux.sent if ref == shell] == ["cd /wt\n"]


class TestDaemonLaunchAttach:
    """``attach`` decides whether a started daemon agent gets a cmux pane."""

    async def _launch(self, client, capsys, *, attach):
        with (
            patch("mael_agent.agent_transport.client_factory", lambda **_: client),
            patch(
                "mael_cli.worktree_launcher.ensure_cmux_running", return_value=True
            ) as mock_cmux,
            patch(
                "mael_cli.worktree_launcher.open_claude_workspace", return_value=True
            ) as mock_open,
            patch("mael_cli.worktree_launcher.start_install_async") as mock_install,
        ):
            placed = await launch_claude_in_worktree(
                Path("/wt/alpha"),
                project="proj",
                worktree="alpha",
                task_id="t1",
                harness="daemon",
                attach=attach,
            )
        out = capsys.readouterr()
        return placed, mock_cmux, mock_open, mock_install, out

    async def test_attached_places_a_pane_that_attaches(self, capsys):
        client = RecordingDaemonClient(replies=[{"ok": True, "id": "a7"}])
        placed, mock_cmux, mock_open, mock_install, _ = await self._launch(
            client, capsys, attach=True
        )

        assert placed is True
        mock_cmux.assert_called_once()
        assert describe(mock_open.call_args.args[3]) == "mael agent attach a7"
        mock_install.assert_not_called()  # the pane's shell runs it

    async def test_headless_starts_the_agent_and_touches_no_cmux(self, capsys):
        client = RecordingDaemonClient(replies=[{"ok": True, "id": "a7"}])
        placed, mock_cmux, mock_open, mock_install, out = await self._launch(
            client, capsys, attach=False
        )

        assert placed is True
        assert [c["cmd"] for c in client.calls] == ["start"]
        mock_cmux.assert_not_called()
        mock_open.assert_not_called()
        mock_install.assert_called_once_with(Path("/wt/alpha"))
        assert "mael agent attach a7" in out.out

    async def test_headless_failed_start_returns_false_with_the_reason(self, capsys):
        client = RecordingDaemonClient(replies=[{"error": "connection refused"}])
        placed, mock_cmux, mock_open, mock_install, out = await self._launch(
            client, capsys, attach=False
        )

        assert placed is False
        mock_cmux.assert_not_called()
        mock_open.assert_not_called()
        mock_install.assert_not_called()
        assert "connection refused" in out.err
        assert "Agent started" not in out.out
