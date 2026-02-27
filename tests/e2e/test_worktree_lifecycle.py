"""E2E tests for worktree lifecycle: create, list, sync, close, recycle, remove."""

import subprocess

import pytest

from maelstrom.ports import get_port_allocation, load_port_allocations
from maelstrom.worktree import (
    close_worktree,
    create_worktree,
    get_worktree_folder_name,
    list_worktrees,
    recycle_worktree,
    remove_worktree_by_path,
    sync_worktree,
    read_env_file,
)

from .conftest import create_commit, run_git


@pytest.mark.e2e
class TestCreateWorktree:
    """Test worktree creation with real git operations."""

    def test_create_worktree_new_branch(self, git_project):
        """Scenario 20: create_worktree creates a new branch and writes .env."""
        worktree_path = create_worktree(
            git_project.project_path, "feature/test-branch"
        )

        # Directory exists
        assert worktree_path.exists()
        assert worktree_path.is_dir()

        # It should be named bravo (alpha already exists)
        assert "bravo" in worktree_path.name

        # Branch is checked out
        result = run_git(worktree_path, "branch", "--show-current")
        assert "feature/test-branch" in result.stdout.strip()

        # .env written with PORT_BASE
        env_vars = read_env_file(worktree_path)
        assert "PORT_BASE" in env_vars
        assert "WORKTREE" in env_vars
        assert env_vars["WORKTREE"] == "bravo"

        # Port allocation recorded
        alloc = get_port_allocation(git_project.project_path, "bravo")
        assert alloc is not None
        assert alloc == int(env_vars["PORT_BASE"])

    def test_list_worktrees(self, git_project):
        """Scenario 21: list_worktrees returns created worktrees."""
        # Create a second worktree
        create_worktree(git_project.project_path, "feature/list-test")

        worktrees = list_worktrees(git_project.project_path)

        # Should have at least alpha and bravo
        names = [wt.path.name for wt in worktrees]
        assert any("alpha" in n for n in names)
        assert any("bravo" in n for n in names)


@pytest.mark.e2e
class TestCloseWorktree:
    """Test closing worktrees with real git operations."""

    def _push_and_merge_branch(self, git_project, worktree_path, branch):
        """Push a branch and simulate merging it to main on the remote."""
        # Push the branch
        run_git(worktree_path, "push", "origin", branch)

        # Simulate merge on remote: push the branch's commit to main
        # by updating the remote's main ref
        result = run_git(worktree_path, "rev-parse", "HEAD")
        commit_sha = result.stdout.strip()

        run_git(
            git_project.remote_path,
            "update-ref", "refs/heads/main", commit_sha,
        )

        # Fetch so our project knows about the updated main
        run_git(git_project.project_path, "fetch", "origin")

    def test_close_clean_worktree(self, git_project):
        """Scenario 22: close succeeds on a clean, merged worktree."""
        branch = "feature/close-test"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Make a commit and push+merge it
        create_commit(worktree_path, "close-test.txt", "content", "Test commit")
        self._push_and_merge_branch(git_project, worktree_path, branch)

        # Close should succeed
        result = close_worktree(worktree_path)
        assert result.success, f"Close failed: {result.message}"

        # Port freed
        nato_name = "bravo"  # second worktree
        alloc = get_port_allocation(git_project.project_path, nato_name)
        assert alloc is None

    def test_close_rejects_dirty(self, git_project):
        """Scenario 23: close fails when worktree has uncommitted changes."""
        branch = "feature/dirty-test"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Create a dirty file
        (worktree_path / "dirty.txt").write_text("uncommitted")
        run_git(worktree_path, "add", "dirty.txt")

        result = close_worktree(worktree_path)
        assert not result.success
        assert result.had_dirty_files

    def test_close_rejects_unpushed(self, git_project):
        """Scenario 24: close fails when worktree has unpushed commits."""
        branch = "feature/unpushed-test"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Make a commit but don't push
        create_commit(worktree_path, "unpushed.txt", "content", "Unpushed commit")

        result = close_worktree(worktree_path)
        assert not result.success
        assert result.had_unpushed_commits


@pytest.mark.e2e
class TestRecycleWorktree:
    """Test recycling closed worktrees."""

    def test_recycle_closed_worktree(self, git_project):
        """Scenario 25: recycling switches branch and reclaims ports."""
        branch = "feature/recycle-orig"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Record original PORT_BASE
        env_vars = read_env_file(worktree_path)
        original_port_base = env_vars.get("PORT_BASE")

        # Push, merge, and close
        create_commit(worktree_path, "recycle.txt", "content", "Recycle commit")
        run_git(worktree_path, "push", "origin", branch)

        # Simulate merge on remote
        result = run_git(worktree_path, "rev-parse", "HEAD")
        commit_sha = result.stdout.strip()
        run_git(git_project.remote_path, "update-ref", "refs/heads/main", commit_sha)
        run_git(git_project.project_path, "fetch", "origin")

        close_result = close_worktree(worktree_path)
        assert close_result.success, f"Close failed: {close_result.message}"

        # Recycle to new branch
        recycled_path = recycle_worktree(worktree_path, "feature/recycle-new")
        assert recycled_path == worktree_path

        # New branch is checked out
        result = run_git(worktree_path, "branch", "--show-current")
        assert "feature/recycle-new" in result.stdout.strip()

        # Port should be reclaimed (same PORT_BASE if available)
        if original_port_base:
            new_env = read_env_file(worktree_path)
            assert new_env.get("PORT_BASE") is not None


@pytest.mark.e2e
class TestRemoveWorktree:
    """Test removing worktrees."""

    def test_remove_worktree(self, git_project):
        """Scenario 26: remove deletes directory and frees ports."""
        worktree_path = create_worktree(git_project.project_path, "feature/remove-test")
        assert worktree_path.exists()

        # Record port allocation
        alloc_before = get_port_allocation(git_project.project_path, "bravo")
        assert alloc_before is not None

        # remove_worktree_by_path expects the full folder name
        folder_name = get_worktree_folder_name(git_project.project_name, "bravo")
        remove_worktree_by_path(git_project.project_path, folder_name)

        # Directory gone
        assert not worktree_path.exists()

        # Port freed
        alloc_after = get_port_allocation(git_project.project_path, "bravo")
        assert alloc_after is None


@pytest.mark.e2e
class TestSyncWorktree:
    """Test syncing worktrees."""

    def test_sync_no_conflicts(self, git_project):
        """Scenario 27: sync rebases cleanly."""
        branch = "feature/sync-test"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Add a commit on the feature branch
        create_commit(worktree_path, "feature.txt", "feature", "Feature commit")

        # Add a commit to origin/main via the remote
        # Clone the remote, commit, push
        source_clone = git_project.projects_dir.parent / "sync-source"
        subprocess.run(
            ["git", "clone", str(git_project.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "main-update.txt", "main update", "Main update")
        run_git(source_clone, "push", "origin", "main")

        # Sync should succeed
        result = sync_worktree(worktree_path)
        assert result.success, f"Sync failed: {result.message}"
        assert not result.had_conflicts

    def test_sync_with_conflicts(self, git_project):
        """Scenario 28: sync detects conflicts."""
        branch = "feature/conflict-test"
        worktree_path = create_worktree(git_project.project_path, branch)

        # Modify the same file on the feature branch
        create_commit(worktree_path, "README.md", "feature version", "Feature change")

        # Modify the same file on origin/main
        source_clone = git_project.projects_dir.parent / "conflict-source"
        subprocess.run(
            ["git", "clone", str(git_project.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "README.md", "main version", "Main change")
        run_git(source_clone, "push", "origin", "main")

        # Sync should report conflicts
        result = sync_worktree(worktree_path)
        assert not result.success
        assert result.had_conflicts

        # Clean up rebase state
        run_git(worktree_path, "rebase", "--abort", check=False)
