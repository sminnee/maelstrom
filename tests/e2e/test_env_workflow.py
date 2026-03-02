"""E2E workflow tests for environment lifecycle.

Consolidates env start/stop, listing, logs, and shared services into
two workflow tests that each do a single start→use→stop cycle.
"""

import os
import signal

import pytest

from maelstrom.env import (
    is_service_alive,
    load_env_state,
    load_shared_state,
    start_env,
    stop_env,
    stop_all_envs,
)
from maelstrom.env_cli import env

from .conftest import assert_process_dead, wait_for, write_procfile


@pytest.mark.e2e
class TestSingleEnvWorkflow:
    """Full single-worktree workflow: start → status → logs → stop."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_single_env_workflow(self, test_project, cli_runner):
        """Exercise the full env lifecycle in a single test."""
        proj = test_project

        # --- Phase 1: Start and verify ---
        state = start_env(
            proj.project_name, proj.worktree_name,
            proj.worktree_path, skip_install=True,
        )
        assert len(state.services) == 1
        assert state.services[0].name == "web"
        pid = state.services[0].pid
        assert is_service_alive(pid)

        # State file persisted
        loaded = load_env_state(proj.project_name, proj.worktree_name)
        assert loaded is not None
        assert loaded.services[0].pid == pid

        # Log file created with startup marker
        log_path = (
            proj.maelstrom_dir / "logs"
            / proj.project_name / proj.worktree_name / "web.log"
        )
        assert log_path.exists()
        assert "=== Service started:" in log_path.read_text()

        # --- Phase 2: Status CLI ---
        result = cli_runner.invoke(env, ["status", "testproj.alpha"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "running" in result.output
        assert "UPTIME" in result.output

        # --- Phase 3: Already-running check ---
        with pytest.raises(RuntimeError, match="already running"):
            start_env(
                proj.project_name, proj.worktree_name,
                proj.worktree_path, skip_install=True,
            )

        # --- Phase 4: Stop and verify cleanup ---
        messages = stop_env(proj.project_name, proj.worktree_name)
        assert any("stopped" in m or "killed" in m for m in messages)
        assert_process_dead(pid)
        assert load_env_state(proj.project_name, proj.worktree_name) is None

        # --- Phase 5: Skip-install with failing install_cmd ---
        (proj.worktree_path / ".maelstrom.yaml").write_text(
            "port_names: []\ninstall_cmd: 'exit 1'\n"
        )
        write_procfile(proj.worktree_path, {"web": "sleep 3600"})
        state = start_env(
            proj.project_name, proj.worktree_name,
            proj.worktree_path, skip_install=True,
        )
        assert len(state.services) == 1
        stop_env(proj.project_name, proj.worktree_name)

        # --- Phase 6: Multi-service with logs ---
        (proj.worktree_path / ".maelstrom.yaml").write_text("port_names: []\n")
        write_procfile(proj.worktree_path, {
            "web": "sh -c 'echo web-hello && sleep 3600'",
            "worker": "sh -c 'echo worker-hello && sleep 3600'",
        })
        state = start_env(
            proj.project_name, proj.worktree_name,
            proj.worktree_path, skip_install=True,
        )
        assert len(state.services) == 2
        pids = [s.pid for s in state.services]
        assert all(is_service_alive(p) for p in pids)

        # Wait for log output
        log_dir = (
            proj.maelstrom_dir / "logs"
            / proj.project_name / proj.worktree_name
        )
        wait_for(lambda: (
            (log_dir / "web.log").exists()
            and "web-hello" in (log_dir / "web.log").read_text()
            and (log_dir / "worker.log").exists()
            and "worker-hello" in (log_dir / "worker.log").read_text()
        ))

        # Multi-service logs show prefixes
        result = cli_runner.invoke(env, ["logs", "testproj.alpha"])
        assert result.exit_code == 0
        assert "[web]" in result.output
        assert "[worker]" in result.output

        # Single-service log filter
        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "web"])
        assert result.exit_code == 0
        assert "web-hello" in result.output
        assert "[web]" not in result.output

        # Unknown service error
        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "Not found" in result.output

        # Stop multi-service
        stop_env(proj.project_name, proj.worktree_name)
        for p in pids:
            assert_process_dead(p)

        # --- Phase 7: Logs -n flag ---
        write_procfile(proj.worktree_path, {
            "counter": "sh -c 'for i in $(seq 1 20); do echo line$i; done && sleep 3600'",
        })
        start_env(
            proj.project_name, proj.worktree_name,
            proj.worktree_path, skip_install=True,
        )
        counter_log = log_dir / "counter.log"
        wait_for(lambda: counter_log.exists() and "line20" in counter_log.read_text())

        result = cli_runner.invoke(env, ["logs", "testproj.alpha", "-n", "5"])
        assert result.exit_code == 0
        lines = [l for l in result.output.strip().splitlines() if l.strip()]
        assert len(lines) <= 5

        stop_env(proj.project_name, proj.worktree_name)

        # --- Phase 8: Stop handles already-dead processes ---
        write_procfile(proj.worktree_path, {"web": "sleep 3600"})
        state = start_env(
            proj.project_name, proj.worktree_name,
            proj.worktree_path, skip_install=True,
        )
        pid = state.services[0].pid

        # Kill externally
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        assert_process_dead(pid)

        messages = stop_env(proj.project_name, proj.worktree_name)
        assert any("stopped" in m or "killed" in m for m in messages)
        assert load_env_state(proj.project_name, proj.worktree_name) is None

        # --- Phase 9: Start/stop via CLI ---
        write_procfile(proj.worktree_path, {"web": "sleep 3600"})
        result = cli_runner.invoke(env, ["start", "--skip-install", "testproj.alpha"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "running" in result.output

        result = cli_runner.invoke(env, ["stop", "testproj.alpha"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "stopped" in output or "killed" in output


@pytest.mark.e2e
class TestMultiEnvWorkflow:
    """Multi-worktree workflow: shared services, listing, stop-all."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    def test_multi_env_workflow(self, test_project, second_worktree, cli_runner, isolated_maelstrom):
        """Exercise multi-env lifecycle with shared services in a single test."""
        proj = test_project
        bravo_path = second_worktree

        # Set up shared services on both worktrees
        procfile = {"web": "sleep 3600", "db-shared": "sleep 3600"}
        write_procfile(proj.worktree_path, procfile)
        write_procfile(bravo_path, procfile)

        # --- Phase 1: Start alpha, verify shared service ---
        state_alpha = start_env(
            proj.project_name, "alpha",
            proj.worktree_path, skip_install=True,
        )
        assert len(state_alpha.services) == 1  # Only local "web"
        assert state_alpha.services[0].name == "web"

        shared = load_shared_state(proj.project_name)
        assert shared is not None
        assert len(shared.services) == 1
        assert shared.services[0].name == "db-shared"
        shared_pid = shared.services[0].pid
        assert is_service_alive(shared_pid)
        assert "alpha" in shared.subscribers

        # --- Phase 2: Start bravo, verify shared NOT re-spawned ---
        state_bravo = start_env(
            proj.project_name, "bravo",
            bravo_path, skip_install=True,
        )
        assert len(state_bravo.services) == 1  # Only local "web"

        shared = load_shared_state(proj.project_name)
        assert set(shared.subscribers) == {"alpha", "bravo"}
        assert shared.services[0].pid == shared_pid  # Same PID

        # --- Phase 3: List shows both ---
        result = cli_runner.invoke(env, ["list", "testproj"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "bravo" in result.output

        # --- Phase 4: Stop alpha, shared survives ---
        messages = stop_env(proj.project_name, "alpha")
        assert is_service_alive(shared_pid)
        shared = load_shared_state(proj.project_name)
        assert shared is not None
        assert shared.subscribers == ["bravo"]
        assert any("still used by" in m for m in messages)

        # --- Phase 5: Stop bravo, shared dies ---
        stop_env(proj.project_name, "bravo")
        assert_process_dead(shared_pid)
        assert load_shared_state(proj.project_name) is None

        # --- Phase 6: List-all across multiple projects ---
        projects_dir = isolated_maelstrom.projects_dir
        for extra_proj in ("proj1", "proj2"):
            p = projects_dir / extra_proj
            p.mkdir()
            wt = p / f"{extra_proj}-alpha"
            wt.mkdir()
            write_procfile(wt, {"app": "sleep 3600"})
            (wt / ".maelstrom.yaml").write_text("port_names: []\n")
            (wt / ".env").write_text("WORKTREE=alpha\n")
            start_env(extra_proj, "alpha", wt, skip_install=True)

        result = cli_runner.invoke(env, ["list-all"])
        assert result.exit_code == 0
        assert "proj1" in result.output
        assert "proj2" in result.output

        stop_env("proj1", "alpha")
        stop_env("proj2", "alpha")

        # --- Phase 7: Stop-all ---
        # Restart alpha and bravo
        write_procfile(proj.worktree_path, {"web": "sleep 3600"})
        write_procfile(bravo_path, {"web": "sleep 3600"})
        state1 = start_env(
            proj.project_name, "alpha",
            proj.worktree_path, skip_install=True,
        )
        state2 = start_env(
            proj.project_name, "bravo",
            bravo_path, skip_install=True,
        )
        pids = [state1.services[0].pid, state2.services[0].pid]
        assert all(is_service_alive(p) for p in pids)

        result = cli_runner.invoke(env, ["stop-all"])
        assert result.exit_code == 0
        assert "Stopped 2 environment(s)." in result.output

        for p in pids:
            assert_process_dead(p)

        # --- Phase 8: List empty ---
        result = cli_runner.invoke(env, ["list", "testproj"])
        assert result.exit_code == 0
        assert "No running environments" in result.output
