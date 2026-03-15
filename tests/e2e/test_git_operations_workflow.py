"""E2E workflow tests for git operations: review (squash fixups) and tidy-branches.

Consolidates review and tidy-branches tests into a single workflow.
"""

import subprocess

import pytest

from maelstrom.review import find_fixup_commits, squash_fixups
from maelstrom.worktree import (
    branch_exists_on_remote,
    create_worktree,
    list_local_branches,
    remove_worktree,
    tidy_branches,
)

from .conftest import create_commit, run_git, setup_git_repo
from tests.git_helpers import setup_origin_main


@pytest.mark.e2e
class TestGitOperationsWorkflow:
    """Review (squash fixups) and tidy-branches in a single workflow."""

    def test_review_and_tidy_workflow(self, git_project_module):
        """Exercise squash fixups and tidy-branches end-to-end."""
        gp = git_project_module

        # ============================================================
        # PART A: Squash fixups (uses isolated tmp repos to avoid
        # polluting the shared project)
        # ============================================================
        base = gp.projects_dir.parent

        # --- A1: Squash fixup commits ---
        repo = base / "review-repo"
        repo.mkdir()
        setup_git_repo(repo)
        create_commit(repo, "README.md", "# Test", "Initial commit")
        run_git(repo, "branch", "-M", "main")
        setup_origin_main(repo)

        run_git(repo, "checkout", "-b", "feature/squash-test")
        create_commit(repo, "feature.txt", "feature v1", "Add feature")
        create_commit(repo, "feature.txt", "feature v2", "fixup! Add feature")
        create_commit(repo, "feature.txt", "feature v3", "fixup! Add feature")

        result = squash_fixups(repo)
        assert result.success
        assert result.fixup_count == 2

        log_result = run_git(repo, "log", "--oneline", "refs/remotes/origin/main..HEAD")
        commits = [l for l in log_result.stdout.strip().splitlines() if l.strip()]
        assert len(commits) == 1
        assert "Add feature" in commits[0]

        # --- A2: find_fixup_commits ---
        repo2 = base / "fixup-repo"
        repo2.mkdir()
        setup_git_repo(repo2)
        create_commit(repo2, "README.md", "# Test", "Initial commit")
        run_git(repo2, "branch", "-M", "main")
        setup_origin_main(repo2)

        run_git(repo2, "checkout", "-b", "feature/find-fixups")
        create_commit(repo2, "a.txt", "a", "Normal commit")
        create_commit(repo2, "b.txt", "b", "fixup! Normal commit")

        fixups = find_fixup_commits(repo2)
        assert len(fixups) == 1
        sha, subject = fixups[0]
        assert subject == "fixup! Normal commit"
        assert len(sha) > 0

        # --- A3: Squash with no fixups is a no-op ---
        repo3 = base / "noop-repo"
        repo3.mkdir()
        setup_git_repo(repo3)
        create_commit(repo3, "README.md", "# Test", "Initial commit")
        run_git(repo3, "branch", "-M", "main")
        setup_origin_main(repo3)

        run_git(repo3, "checkout", "-b", "feature/no-fixups")
        create_commit(repo3, "a.txt", "a", "Normal commit")

        result = squash_fixups(repo3)
        assert result.success
        assert result.fixup_count == 0

        # ============================================================
        # PART B: Tidy branches (uses the shared git_project)
        # ============================================================

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
        branch = "feature/tidy-push-test"
        run_git(gp.project_path, "branch", branch)
        run_git(gp.project_path, "checkout", branch)
        create_commit(gp.project_path, "tidy-push.txt", "content", "Push test")
        run_git(gp.project_path, "push", "origin", branch)
        run_git(gp.project_path, "checkout", "--detach", "HEAD")

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
        branch = "feature/tidy-conflict"
        run_git(gp.project_path, "branch", branch)
        run_git(gp.project_path, "checkout", branch)
        create_commit(gp.project_path, "README.md", "conflict version", "Conflicting change")
        run_git(gp.project_path, "checkout", "--detach", "HEAD")

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
