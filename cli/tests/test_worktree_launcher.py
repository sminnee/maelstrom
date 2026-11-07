"""Tests for mael_cli.worktree_launcher module."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from mael_agent.agent_transport import RecordingDaemonClient
from mael_cli.worktree_launcher import (
    build_claude_command,
    launch_add_in_worktree,
    launch_agent_in_worktree,
    open_worktree,
    start_agent_in_worktree,
    start_install_async,
)
from mael_domain.install_status import wait_for_install


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


@pytest.fixture(autouse=True)
def no_core_plugin(monkeypatch):
    """No core plugin, so an argv assertion does not depend on this checkout."""
    monkeypatch.setattr("mael_cli.worktree_launcher.core_plugin_dir", lambda: None)


class TestAddLauncher:
    def test_the_background_install_logs_its_output_in_the_worktree(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
        worktree = tmp_path / "proj" / "proj-bravo"
        worktree.mkdir(parents=True)
        (worktree / ".maelstrom.yaml").write_text('install_cmd: "pwd"\n')

        log = start_install_async(worktree)

        assert log == tmp_path / "home/.maelstrom/logs/proj/bravo/install.log"
        assert wait_for_install("proj", "bravo") is not None
        assert log.read_text().split() == [str(worktree)]

    @pytest.mark.asyncio
    async def test_regular_cli_runs_in_the_prepared_worktree(self, tmp_path):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                terminal=True,
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
                    "-c",
                    "approvals_reviewer=user",
                    "--model",
                    "gpt-5.6-terra",
                    "-c",
                    "model_reasoning_effort=medium",
                    "-c",
                    'plugins={"maelstrom@maelstrom"={enabled=true}}',
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
                terminal=True,
                harness="cli",
                model=model,
            )
        assert run.call_args.args[0] == command

    @pytest.mark.asyncio
    async def test_regular_cli_loads_the_core_plugin(self, tmp_path, monkeypatch):
        plugin = tmp_path / "plugins" / "maelstrom"
        monkeypatch.setattr(
            "mael_cli.worktree_launcher.core_plugin_dir", lambda: plugin
        )
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                terminal=True,
                harness="cli",
            )
        argv = run.call_args.args[0]
        assert argv[argv.index("--plugin-dir") + 1] == str(plugin)

    @pytest.mark.asyncio
    async def test_omitted_model_uses_claude_opus(self, tmp_path):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path,
                terminal=True,
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
    async def test_daemon_starts_an_agent(self, tmp_path):
        with (
            patch(
                "mael_cli.worktree_launcher.start_agent_in_worktree",
                return_value="agent-1",
            ) as start,
            patch("mael_cli.worktree_launcher.subprocess.run") as run,
        ):
            assert await launch_add_in_worktree(
                tmp_path,
                terminal=True,
                harness="daemon",
            )
        start.assert_awaited_once_with(tmp_path, model=None, execute_model=None)
        run.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_agent_runs_a_subshell_in_the_worktree(self, tmp_path):
        with (
            patch.dict(os.environ, {"SHELL": "/bin/zsh"}),
            patch("mael_cli.worktree_launcher.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            assert await launch_add_in_worktree(
                tmp_path, terminal=True, harness="cli", no_agent=True
            )
        run.assert_called_once_with(["/bin/zsh"], cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_no_agent_without_a_terminal_stops_after_the_worktree(self, tmp_path):
        with patch("mael_cli.worktree_launcher.subprocess.run") as run:
            assert await launch_add_in_worktree(
                tmp_path, terminal=False, harness="cli", no_agent=True
            )
        run.assert_not_called()


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


class TestLaunchAgent:
    """A daemon launch starts the agent, starts the installer, prints attach."""

    async def _launch(self, client, capsys):
        with (
            patch("mael_agent.agent_transport.client_factory", lambda **_: client),
            patch("mael_cli.worktree_launcher.start_install_async") as mock_install,
        ):
            started = await launch_agent_in_worktree(Path("/wt/alpha"))
        return started, mock_install, capsys.readouterr()

    async def test_starts_the_agent_and_prints_attach(self, capsys):
        client = RecordingDaemonClient(replies=[{"ok": True, "id": "a7"}])
        started, mock_install, out = await self._launch(client, capsys)

        assert started is True
        assert [c["cmd"] for c in client.calls] == ["start"]
        mock_install.assert_called_once_with(Path("/wt/alpha"))
        assert "mael agent attach a7" in out.out

    async def test_a_failed_start_returns_false_with_the_reason(self, capsys):
        client = RecordingDaemonClient(replies=[{"error": "connection refused"}])
        started, mock_install, out = await self._launch(client, capsys)

        assert started is False
        mock_install.assert_not_called()
        assert "connection refused" in out.err
        assert "Agent started" not in out.out
