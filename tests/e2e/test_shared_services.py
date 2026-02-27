"""E2E tests for shared service lifecycle."""

import pytest

from maelstrom.env import (
    is_service_alive,
    load_env_state,
    load_shared_state,
    start_env,
    stop_env,
)

from .conftest import assert_process_dead, write_procfile


@pytest.mark.e2e
class TestSharedServices:
    """Test shared service subscriber tracking with real processes."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, process_cleanup):
        pass

    @pytest.fixture
    def shared_project(self, test_project, second_worktree):
        """Set up both worktrees with shared services in Procfile."""
        procfile = {"web": "sleep 3600", "db-shared": "sleep 3600"}
        write_procfile(test_project.worktree_path, procfile)
        write_procfile(second_worktree, procfile)
        return test_project, second_worktree

    def test_shared_started_once_subscribed_by_second(self, shared_project):
        """Scenario 17: shared service spawned once, second worktree subscribes."""
        project, bravo_path = shared_project

        # Start alpha — spawns both web and db-shared
        state_alpha = start_env(
            project.project_name, "alpha",
            project.worktree_path, skip_install=True,
        )
        assert len(state_alpha.services) == 1  # Only local "web"
        assert state_alpha.services[0].name == "web"

        # Shared state should exist
        shared = load_shared_state(project.project_name)
        assert shared is not None
        assert len(shared.services) == 1
        assert shared.services[0].name == "db-shared"
        shared_pid = shared.services[0].pid
        assert is_service_alive(shared_pid)
        assert "alpha" in shared.subscribers

        # Start bravo — should subscribe to existing shared
        state_bravo = start_env(
            project.project_name, "bravo",
            bravo_path, skip_install=True,
        )
        assert len(state_bravo.services) == 1  # Only local "web"

        # Shared state should now have both subscribers
        shared = load_shared_state(project.project_name)
        assert shared is not None
        assert set(shared.subscribers) == {"alpha", "bravo"}
        # Same PID — not re-spawned
        assert shared.services[0].pid == shared_pid

        stop_env(project.project_name, "alpha")
        stop_env(project.project_name, "bravo")

    def test_shared_survives_first_subscriber_stop(self, shared_project):
        """Scenario 18: shared service stays alive when first subscriber stops."""
        project, bravo_path = shared_project

        start_env(project.project_name, "alpha", project.worktree_path, skip_install=True)
        start_env(project.project_name, "bravo", bravo_path, skip_install=True)

        shared = load_shared_state(project.project_name)
        shared_pid = shared.services[0].pid

        # Stop alpha
        messages = stop_env(project.project_name, "alpha")

        # Shared service still alive
        assert is_service_alive(shared_pid)
        shared = load_shared_state(project.project_name)
        assert shared is not None
        assert shared.subscribers == ["bravo"]
        assert any("still used by" in m for m in messages)

        stop_env(project.project_name, "bravo")

    def test_shared_stopped_when_last_subscriber_stops(self, shared_project):
        """Scenario 19: shared service stopped when last subscriber stops."""
        project, bravo_path = shared_project

        start_env(project.project_name, "alpha", project.worktree_path, skip_install=True)
        start_env(project.project_name, "bravo", bravo_path, skip_install=True)

        shared = load_shared_state(project.project_name)
        shared_pid = shared.services[0].pid

        # Stop alpha (first subscriber)
        stop_env(project.project_name, "alpha")
        assert is_service_alive(shared_pid)

        # Stop bravo (last subscriber)
        stop_env(project.project_name, "bravo")

        # stop_env handles SIGTERM→wait→SIGKILL; reap zombie before checking
        assert_process_dead(shared_pid)

        # Shared state file should be removed
        assert load_shared_state(project.project_name) is None
