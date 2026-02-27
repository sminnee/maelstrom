"""E2E tests for env list, list-all, and stop-all."""

import pytest

from maelstrom.env import is_service_alive, load_env_state, start_env, stop_env
from maelstrom.env_cli import env

from .conftest import assert_process_dead, write_procfile


@pytest.mark.e2e
class TestEnvList:
    """Test env list and list-all commands with real running services."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_list_shows_running_environments(self, test_project, second_worktree, cli_runner):
        """Scenario 8: list shows both alpha and bravo."""
        start_env(
            test_project.project_name, "alpha",
            test_project.worktree_path, skip_install=True,
        )
        start_env(
            test_project.project_name, "bravo",
            second_worktree, skip_install=True,
        )

        result = cli_runner.invoke(env, ["list", "testproj"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "bravo" in result.output

        stop_env(test_project.project_name, "alpha")
        stop_env(test_project.project_name, "bravo")

    def test_list_all_across_projects(self, isolated_maelstrom, cli_runner):
        """Scenario 9: list-all shows envs from multiple projects."""
        projects_dir = isolated_maelstrom.projects_dir

        # Create two projects
        for proj_name in ("proj1", "proj2"):
            proj_path = projects_dir / proj_name
            proj_path.mkdir()
            wt_path = proj_path / f"{proj_name}-alpha"
            wt_path.mkdir()
            write_procfile(wt_path, {"app": "sleep 3600"})
            (wt_path / ".maelstrom.yaml").write_text("port_names: []\n")
            (wt_path / ".env").write_text(f"WORKTREE=alpha\n")

            start_env(proj_name, "alpha", wt_path, skip_install=True)

        result = cli_runner.invoke(env, ["list-all"])
        assert result.exit_code == 0
        assert "proj1" in result.output
        assert "proj2" in result.output

        stop_env("proj1", "alpha")
        stop_env("proj2", "alpha")

    def test_stop_all(self, test_project, second_worktree, cli_runner):
        """Scenario 10: stop-all terminates everything."""
        state1 = start_env(
            test_project.project_name, "alpha",
            test_project.worktree_path, skip_install=True,
        )
        state2 = start_env(
            test_project.project_name, "bravo",
            second_worktree, skip_install=True,
        )
        pids = [state1.services[0].pid, state2.services[0].pid]
        assert all(is_service_alive(p) for p in pids)

        result = cli_runner.invoke(env, ["stop-all"])
        assert result.exit_code == 0
        assert "Stopped 2 environment(s)." in result.output

        # stop-all handles SIGTERM→wait→SIGKILL; reap zombies before checking
        for p in pids:
            assert_process_dead(p)

    def test_list_empty(self, test_project, cli_runner):
        """Scenario 11: list with nothing running."""
        result = cli_runner.invoke(env, ["list", "testproj"])
        assert result.exit_code == 0
        assert "No running environments" in result.output
