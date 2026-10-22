"""E2E workflow tests for environment lifecycle.

Each test drives ``mael env`` through one critical path against real
processes. The edge cases live in ``lib/domain/tests/test_env.py`` and
``cli/tests/test_env_cli.py``.
"""

import pytest
from env_workflow_support import assert_process_dead, wait_for, write_procfile

from mael_cli.env_cli import env
from mael_domain.env import (
    is_service_alive,
    load_env_state,
    load_shared_state,
)
from mael_domain.env_store import JsonEnvStore


def _service_pids(project, worktree):
    """The pid of each running service, by name, from the env state record."""
    state = load_env_state(JsonEnvStore(), project, worktree)
    assert state is not None
    return {s.name: s.pid for s in state.services}


@pytest.mark.e2e
@pytest.mark.slow
class TestSingleEnvWorkflow:
    """Single-worktree workflow: start → status → logs → stop."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_single_env_workflow(self, test_project, cli_runner):
        proj = test_project
        target = ["-w", "testproj.alpha"]
        write_procfile(
            proj.worktree_path, {"web": "sh -c 'echo web-hello && sleep 3600'"}
        )

        result = cli_runner.invoke(env, ["start", "--skip-install", *target])
        assert result.exit_code == 0, result.output
        pid = _service_pids(proj.project_name, proj.worktree_name)["web"]
        assert is_service_alive(pid)

        result = cli_runner.invoke(env, ["status", *target])
        assert result.exit_code == 0, result.output
        assert "web" in result.output
        assert "running" in result.output

        def logs():
            out = cli_runner.invoke(env, ["logs", *target]).output
            return "web-hello" in out and out

        output = wait_for(logs)
        assert "=== Service started:" in output

        result = cli_runner.invoke(env, ["stop", *target])
        assert result.exit_code == 0, result.output
        assert_process_dead(pid)
        assert load_env_state(JsonEnvStore(), proj.project_name, "alpha") is None


@pytest.mark.e2e
@pytest.mark.slow
class TestMultiEnvWorkflow:
    """Two worktrees share one service; stop-all stops everything."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_multi_env_workflow(self, test_project, second_worktree, cli_runner):
        proj = test_project
        store = JsonEnvStore()
        procfile = {"web": "sleep 3600", "db-shared": "sleep 3600"}
        write_procfile(proj.worktree_path, procfile)
        write_procfile(second_worktree, procfile)

        result = cli_runner.invoke(
            env, ["start", "--skip-install", "-w", "testproj.alpha"]
        )
        assert result.exit_code == 0, result.output
        shared = load_shared_state(store, proj.project_name)
        assert shared is not None
        shared_pid = shared.services[0].pid
        assert is_service_alive(shared_pid)

        # The second worktree subscribes to the running shared service.
        result = cli_runner.invoke(
            env, ["start", "--skip-install", "-w", "testproj.bravo"]
        )
        assert result.exit_code == 0, result.output
        shared = load_shared_state(store, proj.project_name)
        assert set(shared.subscribers) == {"alpha", "bravo"}
        assert shared.services[0].pid == shared_pid

        web_pids = [
            _service_pids(proj.project_name, wt)["web"] for wt in ("alpha", "bravo")
        ]

        # A stop leaves the shared service to the remaining subscriber.
        result = cli_runner.invoke(env, ["stop", "-w", "testproj.alpha"])
        assert result.exit_code == 0, result.output
        assert_process_dead(web_pids[0])
        assert is_service_alive(shared_pid)

        result = cli_runner.invoke(env, ["stop-all"])
        assert result.exit_code == 0, result.output
        assert "Stopped 1 environment(s)." in result.output
        assert_process_dead(web_pids[1])
        assert_process_dead(shared_pid)
        assert load_shared_state(store, proj.project_name) is None
