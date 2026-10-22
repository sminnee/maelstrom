"""Tests for ``uncommit_branch``.

The function returns a branch to unstaged changes at its base tip, keeping the
chronological commits under a working-history ref. These tests drive real git
rather than mocked argv. The collapse it shares with ``squash_branch`` — the
refusals, the stack-aware base, the rebase and its conflict — is tested in
``test_worktree_squash.py``; this file tests the reset and what follows it.

The repo follows the source → bare-remote → working-clone pattern the sync tests
use, so ``origin`` is a real remote and the rebase inside the function can fetch.
The command is tested in ``cli/tests/test_uncommit_branch.py``.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from git_helpers import (
    create_commit,
    history_refs,
    run_git,
    three_commits,
)

from mael_domain import worktree as worktree_module
from mael_domain.worktree import (
    delete_branch,
    get_commits_ahead,
    get_worktree_dirty_files,
    squash_branch,
    uncommit_branch,
)
from mael_domain.worktree_model import (
    CollapsedCommit,
    WorktreeError,
    history_ref,
)


class TestUncommitCollapsesTheBranch:
    """Three commits ahead become zero, and their diff is unstaged."""

    def test_the_commits_collapse(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        assert result.commits == 3
        assert get_commits_ahead(worktree_path, "origin/main") == 0

    def test_the_changes_are_unstaged_in_the_working_tree(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        uncommit_branch(worktree_path)

        assert sorted(get_worktree_dirty_files(worktree_path)) == [
            "one.txt",
            "three.txt",
            "two.txt",
        ]

    def test_after_a_squash_it_reports_the_build_messages_nested(
        self, collapsible_project
    ):
        """The path `/present` takes: review squashes, then present uncommits."""
        _, worktree_path = collapsible_project
        one = create_commit(worktree_path, "one.txt", "one\n", "feat: one\n\nWhy one.")
        two = create_commit(worktree_path, "two.txt", "two\n", "feat: two")
        squashed = squash_branch(worktree_path).sha
        fix = create_commit(worktree_path, "fix.txt", "fix\n", "wip: review fixes")

        result = uncommit_branch(worktree_path)

        assert result.collapsed == (
            CollapsedCommit(
                squashed,
                "wip: squashed for review\n\n"
                f"- {one[:12]} feat: one\n\n  Why one.\n"
                f"- {two[:12]} feat: two",
            ),
            CollapsedCommit(fix, "wip: review fixes"),
        )

    def test_the_history_survives_a_prune(self, collapsible_project):
        """The ref is what keeps the collapsed commits reachable."""
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = uncommit_branch(worktree_path)
        run_git(worktree_path, "gc", "--prune=now")

        assert (
            run_git(worktree_path, "rev-parse", "--verify", result.history_ref).stdout
        ).strip()

    def test_the_stat_describes_what_is_now_unstaged(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        assert "one.txt" in result.stat
        assert "3 files changed" in result.stat

    def test_two_runs_give_two_history_refs(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        first = uncommit_branch(worktree_path)
        run_git(worktree_path, "add", "-A")
        run_git(worktree_path, "commit", "-m", "feat: redone")
        second = uncommit_branch(worktree_path)

        assert first.history_ref != second.history_ref
        assert len(history_refs(worktree_path, "feature/work")) == 2


class TestUncommitIgnoresManagedFiles:
    """The refusals are ``_collapse_branch``'s; ``test_worktree_squash.py`` owns them."""

    def test_a_managed_file_does_not_count_as_dirty(self, collapsible_project):
        """``.env`` is maelstrom's, so it never blocks the command."""
        _, worktree_path = collapsible_project
        three_commits(worktree_path)
        (worktree_path / ".env").write_text("PORT=3000\n")

        assert uncommit_branch(worktree_path).commits == 3


class TestUncommitWhenTheResetFails:
    """A failed reset leaves no history ref behind.

    The ref is written before the reset, so a reset that fails would otherwise
    leave a history for work that is still committed — and a retry would write a
    second one.
    """

    def test_it_raises_and_removes_the_ref_it_wrote(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        failed = subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="index locked"
        )
        real = worktree_module.run_git

        def fail_the_reset(cmd, *args, **kwargs):
            if cmd[:1] == ["reset"]:
                return failed
            return real(cmd, *args, **kwargs)

        with patch.object(worktree_module, "run_git", side_effect=fail_the_reset):
            with pytest.raises(WorktreeError, match="index locked"):
                uncommit_branch(worktree_path)

        assert history_refs(worktree_path, "feature/work") == []
        assert get_commits_ahead(worktree_path, "origin/main") == 3


class TestDeleteBranchPrunesTheWorkingHistory:
    """The working history goes when its branch goes.

    This covers ``mael tidy-branches``, ``mael sync --close`` and
    ``mael git merge --close`` at once — all three delete through here.
    """

    def _write_history(self, project_path: Path, branch: str, stamp: str) -> str:
        ref = history_ref(branch, stamp)
        run_git(project_path, "update-ref", ref, "HEAD")
        return ref

    def test_the_branchs_history_refs_go_with_it(self, collapsible_project):
        project_path, worktree_path = collapsible_project
        self._write_history(project_path, "feature/work", "20260908T120000Z")
        self._write_history(project_path, "feature/work", "20260908T130000Z")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=project_path,
            check=True,
            capture_output=True,
        )

        delete_branch(project_path, "feature/work")

        assert history_refs(project_path, "feature/work") == []

    def test_another_branchs_history_is_left_alone(self, collapsible_project):
        project_path, worktree_path = collapsible_project
        self._write_history(project_path, "feature/work", "20260908T120000Z")
        kept = self._write_history(project_path, "feature/other", "20260908T120000Z")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=project_path,
            check=True,
            capture_output=True,
        )

        delete_branch(project_path, "feature/work")

        assert history_refs(project_path, "feature/other") == [kept]
