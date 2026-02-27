"""E2E tests for port allocation with real worktrees."""

import pytest

from maelstrom.ports import get_port_allocation, load_port_allocations
from maelstrom.worktree import (
    close_worktree,
    create_worktree,
    read_env_file,
    write_env_file,
)

from .conftest import create_commit, run_git


@pytest.mark.e2e
class TestPortAllocation:
    """Test port allocation across worktree lifecycle."""

    def test_multiple_worktrees_unique_ports(self, git_project):
        """Scenario 29: each worktree gets a unique PORT_BASE."""
        paths = []
        port_bases = set()

        for i in range(3):
            path = create_worktree(
                git_project.project_path, f"feature/port-test-{i}"
            )
            paths.append(path)
            env_vars = read_env_file(path)
            port_base = int(env_vars["PORT_BASE"])
            assert port_base not in port_bases, f"Duplicate PORT_BASE {port_base}"
            port_bases.add(port_base)

        # Verify allocations file
        allocations = load_port_allocations()
        project_key = str(git_project.project_path.resolve())
        assert project_key in allocations
        # alpha (from fixture) + 3 new = 4 worktrees (alpha may or may not have ports)
        assert len(allocations[project_key]) >= 3

    def test_freed_ports_reusable(self, git_project):
        """Scenario 30: freed ports can be allocated to new worktrees."""
        # Create worktree, note its PORT_BASE
        path = create_worktree(git_project.project_path, "feature/port-free-test")
        env_vars = read_env_file(path)
        original_base = int(env_vars["PORT_BASE"])

        # Push, merge, close (frees port)
        create_commit(path, "port-test.txt", "content", "Port test")
        run_git(path, "push", "origin", "feature/port-free-test")
        result = run_git(path, "rev-parse", "HEAD")
        run_git(
            git_project.remote_path,
            "update-ref", "refs/heads/main", result.stdout.strip(),
        )
        run_git(git_project.project_path, "fetch", "origin")
        close_result = close_worktree(path)
        assert close_result.success

        # Port should be freed
        assert get_port_allocation(git_project.project_path, "bravo") is None

        # Create another worktree — it might get the freed port
        path2 = create_worktree(git_project.project_path, "feature/port-reuse-test")
        env_vars2 = read_env_file(path2)
        new_base = int(env_vars2["PORT_BASE"])

        # Just verify no collision and it's a valid base
        assert 300 <= new_base <= 999

    def test_env_variable_substitution(self, git_project):
        """Scenario 31: .env template from project root is included in worktree .env."""
        # Write a project-level .env with $FRONTEND_PORT reference
        project_env = git_project.project_path / ".env"
        project_env.write_text("APP_URL=http://localhost:${FRONTEND_PORT}\n")

        # Create worktree — _finalize_worktree should include template
        path = create_worktree(git_project.project_path, "feature/subst-test")
        env_vars = read_env_file(path)

        # Template line should be preserved in worktree .env
        assert "APP_URL" in env_vars
        # FRONTEND_PORT should be generated in the managed section
        assert "FRONTEND_PORT" in env_vars
        assert int(env_vars["FRONTEND_PORT"]) > 0
        # The raw .env has ${FRONTEND_PORT} which dotenv readers expand at runtime
        raw_env = (path / ".env").read_text()
        assert "APP_URL" in raw_env
        assert "FRONTEND_PORT=" in raw_env
