"""Tests for tidy_branches functionality."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.worktree import (
    branch_exists_on_remote,
    create_worktree,
    delete_branch,
    is_branch_merged,
    list_local_branches,
    remove_worktree,
    tidy_branches,
)


class TestListLocalBranches:
    """Tests for list_local_branches function."""

    def test_lists_branches(self):
        """Test listing all local branches."""
        with TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )

            # Create initial commit
            (project_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=project_path, check=True, capture_output=True
            )

            # Create feature branches
            subprocess.run(
                ["git", "branch", "feature/one"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "feature/two"],
                cwd=project_path, check=True, capture_output=True
            )

            branches = list_local_branches(project_path)

            assert "main" in branches
            assert "feature/one" in branches
            assert "feature/two" in branches
            assert len(branches) == 3


class TestBranchExistsOnRemote:
    """Tests for branch_exists_on_remote function."""

    @pytest.fixture
    def git_repo_with_remote(self):
        """Create a git repository with a remote for testing."""
        with TemporaryDirectory() as tmpdir:
            # Create remote
            remote_path = Path(tmpdir) / "remote.git"
            source_path = Path(tmpdir) / "source"
            source_path.mkdir()

            subprocess.run(["git", "init"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=source_path, check=True, capture_output=True
            )
            (source_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=source_path, check=True, capture_output=True
            )

            # Clone as bare
            subprocess.run(
                ["git", "clone", "--bare", str(source_path), str(remote_path)],
                check=True, capture_output=True
            )

            # Clone to project
            project_path = Path(tmpdir) / "project"
            subprocess.run(
                ["git", "clone", str(remote_path), str(project_path)],
                check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )

            yield project_path

    def test_main_exists_on_remote(self, git_repo_with_remote):
        """Test that main branch exists on remote."""
        assert branch_exists_on_remote(git_repo_with_remote, "main") is True

    def test_nonexistent_branch(self, git_repo_with_remote):
        """Test that non-existent branch returns False."""
        assert branch_exists_on_remote(git_repo_with_remote, "nonexistent") is False


class TestIsBranchMerged:
    """Tests for is_branch_merged function."""

    def test_branch_at_same_commit(self):
        """Test that branch at same commit is considered merged."""
        with TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )
            (project_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=project_path, check=True, capture_output=True
            )

            # Create branch at same commit
            subprocess.run(
                ["git", "branch", "feature/test"],
                cwd=project_path, check=True, capture_output=True
            )

            assert is_branch_merged(project_path, "feature/test", "main") is True

    def test_branch_ahead(self):
        """Test that branch with extra commits is not merged."""
        with TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )
            (project_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=project_path, check=True, capture_output=True
            )

            # Create branch and add commit
            subprocess.run(
                ["git", "checkout", "-b", "feature/test"],
                cwd=project_path, check=True, capture_output=True
            )
            (project_path / "new.txt").write_text("new")
            subprocess.run(["git", "add", "."], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "New commit"],
                cwd=project_path, check=True, capture_output=True
            )

            assert is_branch_merged(project_path, "feature/test", "main") is False


class TestDeleteBranch:
    """Tests for delete_branch function."""

    def test_deletes_local_branch(self):
        """Test deleting a local branch."""
        with TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            subprocess.run(["git", "init"], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )
            (project_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=project_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "feature/test"],
                cwd=project_path, check=True, capture_output=True
            )

            local_deleted, remote_deleted = delete_branch(project_path, "feature/test")

            assert local_deleted is True
            assert remote_deleted is False
            assert "feature/test" not in list_local_branches(project_path)


class TestTidyBranchesIntegration:
    """Integration tests for tidy_branches function."""

    @pytest.fixture
    def git_repo_with_remote(self):
        """Create a bare git repository with a remote for testing.

        This mimics maelstrom's actual structure:
        - Project root has a .git subdirectory (bare clone)
        - Worktrees are created as subdirectories
        """
        with TemporaryDirectory() as tmpdir:
            # Create a "remote" bare repository with initial content
            remote_path = Path(tmpdir) / "remote.git"
            source_path = Path(tmpdir) / "source"
            source_path.mkdir()

            # Initialize source repo with a commit
            subprocess.run(["git", "init"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=source_path, check=True, capture_output=True
            )
            (source_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=source_path, check=True, capture_output=True
            )

            # Clone as bare to create the remote
            subprocess.run(
                ["git", "clone", "--bare", str(source_path), str(remote_path)],
                check=True, capture_output=True
            )

            # Create project directory with bare clone structure (like maelstrom does)
            project_path = Path(tmpdir) / "test-repo"
            project_path.mkdir()

            # Clone as bare into .git subdirectory
            git_dir = project_path / ".git"
            subprocess.run(
                ["git", "clone", "--bare", str(remote_path), str(git_dir)],
                check=True, capture_output=True
            )

            # Configure the bare repo to work with worktrees
            subprocess.run(
                ["git", "config", "core.bare", "false"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )

            # Fetch to get remote tracking refs
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=project_path, check=True, capture_output=True
            )

            yield project_path

    def test_tidy_deletes_merged_branch(self, git_repo_with_remote):
        """Test that a branch at the same commit as main is deleted."""
        # Create a branch at the same commit as main (merged)
        subprocess.run(
            ["git", "branch", "feature/merged"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Verify branch exists
        branches_before = list_local_branches(git_repo_with_remote)
        assert "feature/merged" in branches_before

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch was deleted
        branches_after = list_local_branches(git_repo_with_remote)
        assert "feature/merged" not in branches_after

        # Verify result
        merged_result = next((r for r in results if r.branch == "feature/merged"), None)
        assert merged_result is not None
        assert merged_result.action == "deleted"
        assert merged_result.deleted_local is True

    def test_tidy_skips_checked_out_branch(self, git_repo_with_remote):
        """Test that a branch checked out in a worktree is skipped."""
        # Create a worktree with a branch
        _worktree_path = create_worktree(git_repo_with_remote, "feature/checked-out")

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch still exists
        branches = list_local_branches(git_repo_with_remote)
        assert "feature/checked-out" in branches

        # Verify result
        skipped_result = next((r for r in results if r.branch == "feature/checked-out"), None)
        assert skipped_result is not None
        assert skipped_result.action == "skipped_checked_out"

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/checked-out")

    def test_tidy_rebases_local_only_branch(self, git_repo_with_remote):
        """Test that a local-only branch with commits is rebased but not pushed."""
        # Create a branch with a commit
        subprocess.run(
            ["git", "checkout", "-b", "feature/local-only"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        (git_repo_with_remote / "local.txt").write_text("local change")
        subprocess.run(
            ["git", "add", "."],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Local commit"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        # Checkout a detached state so we're not on the branch
        subprocess.run(
            ["git", "checkout", "--detach", "origin/main"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch still exists (not deleted because it has commits)
        branches = list_local_branches(git_repo_with_remote)
        assert "feature/local-only" in branches

        # Verify result
        rebased_result = next((r for r in results if r.branch == "feature/local-only"), None)
        assert rebased_result is not None
        assert rebased_result.action == "rebased"
        assert rebased_result.success is True

    def test_tidy_pushes_branch_with_remote(self, git_repo_with_remote):
        """Test that a branch with remote is rebased and pushed."""
        # Create a branch with a commit and push it
        subprocess.run(
            ["git", "checkout", "-b", "feature/with-remote"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        (git_repo_with_remote / "remote.txt").write_text("remote change")
        subprocess.run(
            ["git", "add", "."],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Remote commit"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "feature/with-remote"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        # Checkout detached to not be on the branch
        subprocess.run(
            ["git", "checkout", "--detach", "origin/main"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch still exists
        branches = list_local_branches(git_repo_with_remote)
        assert "feature/with-remote" in branches

        # Verify result
        pushed_result = next((r for r in results if r.branch == "feature/with-remote"), None)
        assert pushed_result is not None
        assert pushed_result.action == "pushed"
        assert pushed_result.success is True

    def test_tidy_skips_branch_with_conflicts(self, git_repo_with_remote):
        """Test that a branch with rebase conflicts is skipped."""
        # Create a commit on main first
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        (git_repo_with_remote / "conflict.txt").write_text("main version")
        subprocess.run(
            ["git", "add", "."],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Main commit"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Create a branch from before that commit with a conflicting change
        subprocess.run(
            ["git", "checkout", "-b", "feature/conflict", "origin/main~1"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        (git_repo_with_remote / "conflict.txt").write_text("branch version")
        subprocess.run(
            ["git", "add", "."],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "Conflicting commit"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        # Checkout detached
        subprocess.run(
            ["git", "checkout", "--detach", "origin/main"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch still exists (not deleted due to conflicts)
        branches = list_local_branches(git_repo_with_remote)
        assert "feature/conflict" in branches

        # Verify result
        conflict_result = next((r for r in results if r.branch == "feature/conflict"), None)
        assert conflict_result is not None
        assert conflict_result.action == "skipped_conflicts"
        assert conflict_result.success is True  # Not a failure, just conflicts

    def test_tidy_deletes_remote_branch_when_merged(self, git_repo_with_remote):
        """Test that remote branch is also deleted when local is merged."""
        # Create and push a branch
        subprocess.run(
            ["git", "branch", "feature/merged-remote"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "feature/merged-remote"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )

        # Verify remote branch exists
        assert branch_exists_on_remote(git_repo_with_remote, "feature/merged-remote") is True

        # Run tidy
        results = tidy_branches(git_repo_with_remote)

        # Verify branch was deleted (both local and remote)
        branches = list_local_branches(git_repo_with_remote)
        assert "feature/merged-remote" not in branches

        # Refresh remote refs
        subprocess.run(
            ["git", "fetch", "origin", "--prune"],
            cwd=git_repo_with_remote, check=True, capture_output=True
        )
        assert branch_exists_on_remote(git_repo_with_remote, "feature/merged-remote") is False

        # Verify result
        merged_result = next((r for r in results if r.branch == "feature/merged-remote"), None)
        assert merged_result is not None
        assert merged_result.action == "deleted"
        assert merged_result.deleted_local is True
        assert merged_result.deleted_remote is True

    def test_tidy_no_feature_branches(self, git_repo_with_remote):
        """Test that tidy returns empty list when no feature branches exist."""
        results = tidy_branches(git_repo_with_remote)
        assert results == []
