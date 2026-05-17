"""E2E workflow tests for tidy-branches."""

import subprocess

import pytest

from maelstrom.worktree import (
    create_worktree,
    list_local_branches,
    remove_worktree,
    tidy_branches,
)

from .conftest import create_commit, run_git


@pytest.mark.e2e
class TestGitOperationsWorkflow:
    """End-to-end tidy-branches workflow."""

    def test_tidy_workflow(self, git_project_module):
        """Exercise tidy-branches end-to-end."""
        gp = git_project_module
        base = gp.projects_dir.parent

        # --- B1: Merged branch → deleted ---
        run_git(gp.project_path, "branch", "feature/tidy-merged")

        branches_before = list_local_branches(gp.project_path)
        assert "feature/tidy-merged" in branches_before

        results = tidy_branches(gp.project_path)
        branches_after = list_local_branches(gp.project_path)
        assert "feature/tidy-merged" not in branches_after

        merged_results = [r for r in results if r.branch == "feature/tidy-merged"]
        assert len(merged_results) == 1
        assert merged_results[0].action == "deleted"
        assert merged_results[0].success

        # --- B2: Unmerged branch with remote → pushed ---
        # Use alpha worktree for checkout/commit (project root is bare)
        branch = "feature/tidy-push-test"
        original_branch = run_git(gp.worktree_path, "branch", "--show-current").stdout.strip()
        run_git(gp.worktree_path, "checkout", "-b", branch)
        create_commit(gp.worktree_path, "tidy-push.txt", "content", "Push test")
        run_git(gp.worktree_path, "push", "origin", branch)
        run_git(gp.worktree_path, "checkout", original_branch)

        # Add a new commit on main so the branch needs rebasing
        source_clone = base / "tidy-source"
        if not source_clone.exists():
            subprocess.run(
                ["git", "clone", str(gp.remote_path), str(source_clone)],
                check=True, capture_output=True,
            )
            run_git(source_clone, "config", "user.email", "test@test.com")
            run_git(source_clone, "config", "user.name", "Test")
        create_commit(source_clone, "main-tidy.txt", "main update", "Main update for tidy")
        run_git(source_clone, "push", "origin", "main")
        run_git(gp.project_path, "fetch", "origin")

        results = tidy_branches(gp.project_path)
        pushed_results = [r for r in results if r.branch == branch]
        assert len(pushed_results) == 1
        assert pushed_results[0].action == "pushed"
        assert pushed_results[0].success

        # --- B3: Conflicting branch → skipped ---
        # Use alpha worktree for checkout/commit (project root is bare)
        branch = "feature/tidy-conflict"
        original_branch = run_git(gp.worktree_path, "branch", "--show-current").stdout.strip()
        run_git(gp.worktree_path, "checkout", "-b", branch)
        create_commit(gp.worktree_path, "README.md", "conflict version", "Conflicting change")
        run_git(gp.worktree_path, "checkout", original_branch)

        # Modify README.md on main via remote
        conflict_clone = base / "conflict-tidy-source"
        subprocess.run(
            ["git", "clone", str(gp.remote_path), str(conflict_clone)],
            check=True, capture_output=True,
        )
        run_git(conflict_clone, "config", "user.email", "test@test.com")
        run_git(conflict_clone, "config", "user.name", "Test")
        create_commit(conflict_clone, "README.md", "main conflict version", "Conflicting main change")
        run_git(conflict_clone, "push", "origin", "main")
        run_git(gp.project_path, "fetch", "origin")

        results = tidy_branches(gp.project_path)
        conflict_results = [r for r in results if r.branch == branch]
        assert len(conflict_results) == 1
        assert conflict_results[0].action == "skipped_conflicts"

        # --- B4: Checked-out branch → skipped ---
        branch = "feature/tidy-checked-out"
        worktree_path = create_worktree(gp.project_path, branch)

        results = tidy_branches(gp.project_path)
        checked_out_results = [r for r in results if r.branch == branch]
        assert len(checked_out_results) == 1
        assert checked_out_results[0].action == "skipped_checked_out"

        remove_worktree(gp.project_path, branch)
