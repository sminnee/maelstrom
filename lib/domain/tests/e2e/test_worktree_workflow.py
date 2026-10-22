"""E2E workflow tests for the worktree lifecycle.

One critical path, on a fresh project. The edge cases live in the
component suites: ``test_worktree.py``, ``test_worktree_close.py``,
``test_worktree_sync.py`` and ``test_ports.py``.
"""

import pytest
from git_helpers import create_commit, push_to_origin_main, run_git

from mael_domain.ports import get_port_allocation
from mael_domain.worktree import (
    close_worktree,
    create_worktree,
    read_env_file,
    recycle_worktree,
    sync_worktree,
)


@pytest.mark.e2e
class TestWorktreeLifecycle:
    """create → commit → sync → merge → close frees the port → recycle."""

    def test_worktree_lifecycle(self, git_project):
        gp = git_project

        wt_path = create_worktree(gp.project_path, "feature/lifecycle-test")
        assert "bravo" in wt_path.name
        assert run_git(wt_path, "branch", "--show-current").stdout.strip() == (
            "feature/lifecycle-test"
        )
        port_base = get_port_allocation(gp.project_path, "bravo")
        assert port_base is not None
        assert read_env_file(wt_path)["PORT_BASE"] == str(port_base)

        create_commit(wt_path, "feature.txt", "feature content", "Feature commit")
        push_to_origin_main(
            gp.project_path, gp.remote_path, "main-update.txt", "main update"
        )

        sync_result = sync_worktree(wt_path)
        assert sync_result.success, sync_result.message
        assert not sync_result.had_conflicts
        assert (wt_path / "main-update.txt").exists()

        # Merge: origin/main takes the branch tip.
        run_git(wt_path, "push", "origin", "feature/lifecycle-test")
        tip = run_git(wt_path, "rev-parse", "HEAD").stdout.strip()
        run_git(gp.remote_path, "update-ref", "refs/heads/main", tip)
        run_git(gp.project_path, "fetch", "origin")

        close_result = close_worktree(wt_path)
        assert close_result.success, close_result.message
        assert get_port_allocation(gp.project_path, "bravo") is None

        recycled_path = recycle_worktree(wt_path, "feature/recycled")
        assert recycled_path == wt_path
        assert run_git(wt_path, "branch", "--show-current").stdout.strip() == (
            "feature/recycled"
        )
        assert "PORT_BASE" in read_env_file(wt_path)


@pytest.mark.e2e
class TestWorktreeSyncConflicts:
    """Sync with conflicts (separate test since conflict state is messy)."""

    def test_sync_with_conflicts(self, git_project):
        """Sync detects and reports conflicts."""
        gp = git_project

        wt_path = create_worktree(gp.project_path, "feature/conflict-test")

        # The same file changes on the feature branch and on origin/main.
        create_commit(wt_path, "README.md", "feature version", "Feature change")
        push_to_origin_main(
            gp.project_path, gp.remote_path, "README.md", "main version"
        )

        sync_result = sync_worktree(wt_path)
        assert not sync_result.success
        assert sync_result.had_conflicts

        # Clean up rebase state
        run_git(wt_path, "rebase", "--abort", check=False)
