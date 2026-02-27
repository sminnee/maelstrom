"""E2E tests for tidy-branches command."""

import subprocess

import pytest

from maelstrom.worktree import (
    branch_exists_on_remote,
    create_worktree,
    list_local_branches,
    remove_worktree,
    tidy_branches,
)

from .conftest import create_commit, run_git


@pytest.mark.e2e
class TestTidyBranches:
    """Test tidy-branches with real git repos and multiple branch states."""

    def test_deletes_merged_branch(self, git_project):
        """Scenario 35: merged branches are deleted locally and remotely."""
        # Create a branch at the same commit as main (already merged)
        run_git(git_project.project_path, "branch", "feature/merged")

        branches_before = list_local_branches(git_project.project_path)
        assert "feature/merged" in branches_before

        results = tidy_branches(git_project.project_path)

        branches_after = list_local_branches(git_project.project_path)
        assert "feature/merged" not in branches_after

        # Find the result for our branch
        merged_results = [r for r in results if r.branch == "feature/merged"]
        assert len(merged_results) == 1
        assert merged_results[0].action == "deleted"
        assert merged_results[0].success

    def test_pushes_unmerged_branch_with_remote(self, git_project):
        """Scenario 36: unmerged branches with remote tracking are force-pushed."""
        branch = "feature/push-test"

        # Create branch locally with a commit
        run_git(git_project.project_path, "branch", branch)
        run_git(git_project.project_path, "checkout", branch)
        create_commit(git_project.project_path, "push-test.txt", "content", "Push test")

        # Push to create remote tracking
        run_git(git_project.project_path, "push", "origin", branch)

        # Go back to a detached state so tidy doesn't skip it
        run_git(git_project.project_path, "checkout", "--detach", "HEAD")

        # Add a new commit on main (so the branch needs rebasing)
        source_clone = git_project.projects_dir.parent / "tidy-source"
        subprocess.run(
            ["git", "clone", str(git_project.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "main-tidy.txt", "main update", "Main update for tidy")
        run_git(source_clone, "push", "origin", "main")

        # Fetch so project knows about updated main
        run_git(git_project.project_path, "fetch", "origin")

        results = tidy_branches(git_project.project_path)

        pushed_results = [r for r in results if r.branch == branch]
        assert len(pushed_results) == 1
        assert pushed_results[0].action == "pushed"
        assert pushed_results[0].success

    def test_skips_branch_with_conflicts(self, git_project):
        """Scenario 37: branches that conflict with main are skipped."""
        branch = "feature/conflict-tidy"

        # Create branch with a change to README.md
        run_git(git_project.project_path, "branch", branch)
        run_git(git_project.project_path, "checkout", branch)
        create_commit(git_project.project_path, "README.md", "conflict version", "Conflicting change")

        # Go back to detached state
        run_git(git_project.project_path, "checkout", "--detach", "HEAD")

        # Modify README.md on main via remote
        source_clone = git_project.projects_dir.parent / "conflict-tidy-source"
        subprocess.run(
            ["git", "clone", str(git_project.remote_path), str(source_clone)],
            check=True, capture_output=True,
        )
        run_git(source_clone, "config", "user.email", "test@test.com")
        run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "README.md", "main conflict version", "Conflicting main change")
        run_git(source_clone, "push", "origin", "main")

        run_git(git_project.project_path, "fetch", "origin")

        results = tidy_branches(git_project.project_path)

        conflict_results = [r for r in results if r.branch == branch]
        assert len(conflict_results) == 1
        assert conflict_results[0].action == "skipped_conflicts"

    def test_skips_checked_out_branch(self, git_project):
        """Scenario 38: branches checked out in a worktree are skipped."""
        branch = "feature/checked-out"

        # Create a worktree that checks out this branch
        worktree_path = create_worktree(git_project.project_path, branch)

        results = tidy_branches(git_project.project_path)

        checked_out_results = [r for r in results if r.branch == branch]
        assert len(checked_out_results) == 1
        assert checked_out_results[0].action == "skipped_checked_out"

        # Clean up the worktree
        remove_worktree(git_project.project_path, branch)
