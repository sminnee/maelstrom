"""E2E tests for env logs command."""

import time

import pytest

from maelstrom.env import start_env, stop_env
from maelstrom.env_cli import env

from .conftest import wait_for, write_procfile


@pytest.mark.e2e
class TestEnvLogs:
    """Test log creation and reading with real processes."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_logs_shows_service_output(self, test_project, cli_runner):
        """Scenario 12: logs contains output from the service."""
        write_procfile(test_project.worktree_path, {
            "logger": "sh -c 'echo hello && sleep 3600'",
        })

        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        # Wait for log output to appear
        log_path = (
            test_project.maelstrom_dir / "logs"
            / test_project.project_name / test_project.worktree_name
            / "logger.log"
        )
        wait_for(lambda: log_path.exists() and "hello" in log_path.read_text())

        result = cli_runner.invoke(env, ["logs", "testproj.alpha"])
        assert result.exit_code == 0
        assert "hello" in result.output

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_logs_n_flag(self, test_project, cli_runner):
        """Scenario 13: -n flag limits output lines."""
        write_procfile(test_project.worktree_path, {
            "counter": "sh -c 'for i in $(seq 1 20); do echo line$i; done && sleep 3600'",
        })

        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        # Wait for all lines to be written
        log_path = (
            test_project.maelstrom_dir / "logs"
            / test_project.project_name / test_project.worktree_name
            / "counter.log"
        )
        wait_for(lambda: log_path.exists() and "line20" in log_path.read_text())

        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "-n", "5"])
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert len(lines) <= 5

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_logs_specific_service(self, test_project, cli_runner):
        """Scenario 14: logs for a specific service."""
        write_procfile(test_project.worktree_path, {
            "web": "sh -c 'echo web-output && sleep 3600'",
            "worker": "sh -c 'echo worker-output && sleep 3600'",
        })

        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        # Wait for both logs
        log_dir = (
            test_project.maelstrom_dir / "logs"
            / test_project.project_name / test_project.worktree_name
        )
        wait_for(lambda: (log_dir / "web.log").exists() and "web-output" in (log_dir / "web.log").read_text())

        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "web"])
        assert result.exit_code == 0
        assert "web-output" in result.output
        # Should not have [web] prefix when filtering single service
        assert "[web]" not in result.output

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_logs_multi_service_prefixes(self, test_project, cli_runner):
        """Scenario 15: multi-service logs have [service] prefixes."""
        write_procfile(test_project.worktree_path, {
            "web": "sh -c 'echo web-hello && sleep 3600'",
            "worker": "sh -c 'echo worker-hello && sleep 3600'",
        })

        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        log_dir = (
            test_project.maelstrom_dir / "logs"
            / test_project.project_name / test_project.worktree_name
        )
        wait_for(lambda: (
            (log_dir / "web.log").exists()
            and "web-hello" in (log_dir / "web.log").read_text()
            and (log_dir / "worker.log").exists()
            and "worker-hello" in (log_dir / "worker.log").read_text()
        ))

        result = cli_runner.invoke(env, ["logs", "testproj.alpha"])
        assert result.exit_code == 0
        assert "[web]" in result.output
        assert "[worker]" in result.output

        stop_env(test_project.project_name, test_project.worktree_name)

    def test_logs_unknown_service(self, test_project, cli_runner):
        """Scenario 16: logs for nonexistent service gives error."""
        start_env(
            test_project.project_name,
            test_project.worktree_name,
            test_project.worktree_path,
            skip_install=True,
        )

        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "Not found" in result.output

        stop_env(test_project.project_name, test_project.worktree_name)
