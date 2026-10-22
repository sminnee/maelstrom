"""E2E workflow test for merging a branch to main."""

import pytest
from git_helpers import create_commit, push_to_origin_main, run_git

from mael_domain.worktree import (
    get_current_branch,
    is_worktree_closed,
    list_local_branches,
    list_worktrees,
    merge_to_main,
)


@pytest.mark.e2e
class TestGitMergeWorkflow:
    """End-to-end merge_to_main against a real fixture repo."""

    def _origin_main_sha(self, gp):
        run_git(gp.project_path, "fetch", "origin")
        return run_git(gp.project_path, "rev-parse", "origin/main").stdout.strip()

    def test_merge_without_close(self, git_project):
        """A feature branch with a fixup! commit merges to main, fast-forwards, and pushes."""
        gp = git_project

        # Build a feature branch in the alpha worktree with a fixup! commit.
        branch = "feature/merge-no-close"
        run_git(gp.worktree_path, "checkout", "-b", branch)
        assert get_current_branch(gp.worktree_path) == branch
        create_commit(gp.worktree_path, "feature.txt", "feature work", "Add feature")
        target_sha = run_git(gp.worktree_path, "rev-parse", "HEAD").stdout.strip()
        create_commit(
            gp.worktree_path,
            "feature.txt",
            "feature work refined",
            f"fixup! {target_sha}",
        )

        # Advance origin/main so the rebase has to move the branch forward.
        push_to_origin_main(
            gp.project_path, gp.remote_path, "main-update.txt", "main moved"
        )
        before_origin = self._origin_main_sha(gp)

        result = merge_to_main(gp.worktree_path, squash=True, close=False)
        assert result.success, result.message
        assert result.pushed

        # Local main points at the rebased branch tip.
        branch_tip = run_git(gp.worktree_path, "rev-parse", "HEAD").stdout.strip()
        local_main = run_git(
            gp.project_path, "rev-parse", "refs/heads/main"
        ).stdout.strip()
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

        # _main holds main, so the merge must move its working tree too. Moving
        # the ref alone would leave the merged files staged as deletions there.
        main_wt = gp.project_path / "_main"
        assert run_git(main_wt, "status", "--short").stdout.strip() == ""
        assert (main_wt / "feature.txt").exists()

    def test_merge_with_close(self, git_project):
        """--close detaches the worktree and deletes the branch locally and on origin."""
        gp = git_project

        branch = "feature/merge-close"
        run_git(gp.worktree_path, "checkout", "-b", branch)
        create_commit(gp.worktree_path, "feature.txt", "feature work", "Add feature")
        # Publish the branch so the remote-delete path has something to remove.
        run_git(gp.worktree_path, "push", "origin", branch)

        push_to_origin_main(
            gp.project_path, gp.remote_path, "main-update.txt", "main moved"
        )

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
