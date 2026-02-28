"""E2E tests for env start/stop/status lifecycle."""

import json
import os
import signal

import pytest

from maelstrom.env import is_service_alive, load_env_state, start_env, stop_env
from maelstrom.env_cli import env

from .conftest import assert_process_dead, write_procfile


@pytest.mark.e2e
class TestEnvStartStop:
    """Test the core start → status → stop lifecycle with real processes."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_start_creates_running_processes(self, test_project):
        """Scenario 1: start spawns real processes and writes state."""
        state = start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        # State returned with services
        assert len(state.services) == 1
        assert state.services[0].name == "web"

        # PID is alive
        pid = state.services[0].pid
        assert is_service_alive(pid)

        # State file persisted
        loaded = load_env_state(test_project.project_name, test_project.worktree_name)
        assert loaded is not None
        assert loaded.services[0].pid == pid

        # Log file exists
        log_path = test_project.maelstrom_dir / "logs" / test_project.project_name / test_project.worktree_name / "web.log"
        assert log_path.exists()
        content = log_path.read_text()
        assert "=== Service started:" in content

        # Clean up
        stop_env(test_project.project_name, test_project.worktree_name)

    def test_status_shows_live_info(self, test_project, cli_runner):
        """Scenario 2: status command shows running services."""
        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        result = cli_runner.invoke(env, ["status", "testproj.alpha"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "running" in result.output
        assert "UPTIME" in result.output

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_stop_terminates_and_cleans_up(self, test_project):
        """Scenario 3: stop kills processes and removes state file."""
        state = start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )
        pid = state.services[0].pid
        assert is_service_alive(pid)

        messages = stop_env(test_project.project_name, test_project.worktree_name)
        assert any("stopped" in m or "killed" in m for m in messages)

        # stop_env handles SIGTERM→wait→SIGKILL; reap zombie before checking
        assert_process_dead(pid)

        # State file removed
        assert load_env_state(test_project.project_name, test_project.worktree_name) is None

    def test_full_roundtrip_multiple_services(self, test_project):
        """Scenario 4: start/status/stop with two services."""
        write_procfile(test_project.worktree_path, {
            "web": "sleep 3600",
            "worker": "sleep 3600",
        })

        state = start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )
        assert len(state.services) == 2
        pids = [s.pid for s in state.services]
        assert all(is_service_alive(p) for p in pids)

        # Stop — reap zombies before checking liveness
        stop_env(test_project.project_name, test_project.worktree_name)
        for p in pids:
            assert_process_dead(p)

    def test_start_refuses_when_already_running(self, test_project):
        """Scenario 5: second start raises RuntimeError."""
        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        with pytest.raises(RuntimeError, match="already running"):
            start_env(
                test_project.project_name,
                test_project.worktree_name,
                test_project.worktree_path,
                skip_install=True,
            )

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_start_skip_install(self, test_project):
        """Scenario 6: --skip-install avoids running install_cmd."""
        # Config with an install_cmd that would fail
        (test_project.worktree_path / ".maelstrom.yaml").write_text(
            "port_names: []\ninstall_cmd: 'exit 1'\n"
        )

        # Should succeed because install is skipped
        state = start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )
        assert len(state.services) == 1

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_stop_handles_dead_processes(self, test_project):
        """Scenario 7: stop cleans up even if processes already dead."""
        state = start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )
        pid = state.services[0].pid

        # Kill externally and reap zombie
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        assert_process_dead(pid)

        # Stop should still succeed and clean up
        messages = stop_env(test_project.project_name, test_project.worktree_name)
        assert any("stopped" in m or "killed" in m for m in messages)
        assert load_env_state(test_project.project_name, test_project.worktree_name) is None


@pytest.mark.e2e
class TestEnvStartCli:
    """Test env start/stop via CLI runner."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_start_via_cli(self, test_project, cli_runner):
        """Start and stop via CLI commands."""
        result = cli_runner.invoke(env, ["start", "--skip-install", "testproj.alpha"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "running" in result.output

        result = cli_runner.invoke(env, ["stop", "testproj.alpha"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "stopped" in output or "killed" in output
