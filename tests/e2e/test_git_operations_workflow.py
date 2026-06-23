"""E2E workflow tests for tidy-branches."""

import subprocess

import pytest

from maelstrom.worktree import (
    create_worktree,
    get_current_branch,
    is_worktree_closed,
    list_local_branches,
    list_worktrees,
    merge_to_main,
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


@pytest.mark.e2e
class TestGitMergeWorkflow:
    """End-to-end merge_to_main against a real fixture repo."""

    def _advance_remote_main(self, gp, base, filename, content, message):
        """Push a new commit onto origin/main via a throwaway clone."""
        clone = base / "merge-source"
        if not clone.exists():
            subprocess.run(
                ["git", "clone", str(gp.remote_path), str(clone)],
                check=True, capture_output=True,
            )
            run_git(clone, "config", "user.email", "test@test.com")
            run_git(clone, "config", "user.name", "Test")
        create_commit(clone, filename, content, message)
        run_git(clone, "push", "origin", "main")

    def _origin_main_sha(self, gp):
        run_git(gp.project_path, "fetch", "origin")
        return run_git(gp.project_path, "rev-parse", "origin/main").stdout.strip()

    def test_merge_without_close(self, git_project):
        """A feature branch with a fixup! commit merges to main, fast-forwards, and pushes."""
        gp = git_project
        base = gp.projects_dir.parent

        # Build a feature branch in the alpha worktree with a fixup! commit.
        branch = "feature/merge-no-close"
        run_git(gp.worktree_path, "checkout", "-b", branch)
        assert get_current_branch(gp.worktree_path) == branch
        create_commit(gp.worktree_path, "feature.txt", "feature work", "Add feature")
        target_sha = run_git(gp.worktree_path, "rev-parse", "HEAD").stdout.strip()
        create_commit(gp.worktree_path, "feature.txt", "feature work refined", f"fixup! {target_sha}")

        # Advance origin/main so the rebase has to move the branch forward.
        self._advance_remote_main(gp, base, "main-update.txt", "main moved", "Main moves on")
        before_origin = self._origin_main_sha(gp)

        result = merge_to_main(gp.worktree_path, squash=True, close=False)
        assert result.success, result.message
        assert result.pushed

        # Local main points at the rebased branch tip.
        branch_tip = run_git(gp.worktree_path, "rev-parse", "HEAD").stdout.strip()
        local_main = run_git(gp.project_path, "rev-parse", "refs/heads/main").stdout.strip()
        assert local_main == branch_tip

        # origin/main advanced to the same tip.
        after_origin = self._origin_main_sha(gp)
        assert after_origin != before_origin
        assert after_origin == branch_tip

        # The fixup! was squashed — only one feature commit beyond the old main.
        log = run_git(
            gp.worktree_path, "log", "--oneline", f"{before_origin}..HEAD"
        ).stdout
        assert "fixup!" not in log
        assert sum(1 for line in log.splitlines() if line.strip()) == 1

        # Without --close, the branch is left checked out in the worktree.
        assert branch in list_local_branches(gp.project_path)
        assert get_current_branch(gp.worktree_path) == branch

    def test_merge_with_close(self, git_project):
        """--close detaches the worktree and deletes the branch locally and on origin."""
        gp = git_project
        base = gp.projects_dir.parent

        branch = "feature/merge-close"
        run_git(gp.worktree_path, "checkout", "-b", branch)
        create_commit(gp.worktree_path, "feature.txt", "feature work", "Add feature")
        # Publish the branch so the remote-delete path has something to remove.
        run_git(gp.worktree_path, "push", "origin", branch)

        self._advance_remote_main(gp, base, "main-update.txt", "main moved", "Main moves on")

        result = merge_to_main(gp.worktree_path, squash=True, close=True)
        assert result.success, result.message
        assert "closed worktree" in result.message

        # Worktree is detached at origin/main (closed/recyclable).
        worktrees = list_worktrees(gp.project_path)
        wt = next(w for w in worktrees if w.path == gp.worktree_path)
        assert is_worktree_closed(wt)

        # Branch is gone locally and on origin.
        assert branch not in list_local_branches(gp.project_path)
        run_git(gp.project_path, "fetch", "origin", "--prune")
        remote_check = run_git(
            gp.project_path, "rev-parse", "--verify", f"origin/{branch}", check=False
        )
        assert remote_check.returncode != 0
