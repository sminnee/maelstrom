"""Tests for ``mael git uncommit-branch``.

The command returns a branch to unstaged changes at its base tip, keeping the
chronological commits under a working-history ref. Its three git steps — the
stack-aware base, the rebase, and the reset — are exactly where a skill goes
wrong, so these tests drive real git rather than mocked argv.

The repo follows the source → bare-remote → working-clone pattern the sync tests
use, so ``origin`` is a real remote and the rebase inside the command can fetch.
"""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maelstrom import worktree as worktree_module
from maelstrom.base_store import InMemoryBaseStore
from maelstrom.cli import cli
from maelstrom.worktree import (
    delete_branch,
    get_commits_ahead,
    get_worktree_dirty_files,
    rebase_in_progress,
    uncommit_branch,
)
from maelstrom.worktree_model import (
    BaseRef,
    UncommitResult,
    WorktreeError,
    history_ref,
    history_ref_prefix,
)
from tests.git_helpers import create_commit, run_git, setup_git_repo


@pytest.fixture
def project_with_worktree():
    """A bare-clone project with a worktree on ``feature/work``.

    Yields ``(project_path, worktree_path)``.
    """
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        source_path = tmp / "source"
        source_path.mkdir()
        setup_git_repo(source_path)
        create_commit(source_path, "README.md", "# Test\n", "Initial commit")
        run_git(source_path, "branch", "-M", "main")

        remote_path = tmp / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True,
            capture_output=True,
        )

        project_path = tmp / "test-repo"
        project_path.mkdir()
        git_dir = project_path / ".git"
        subprocess.run(
            ["git", "clone", "--bare", str(remote_path), str(git_dir)],
            check=True,
            capture_output=True,
        )
        run_git(project_path, "config", "core.bare", "true")
        run_git(
            project_path,
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
        run_git(project_path, "config", "user.email", "test@test.com")
        run_git(project_path, "config", "user.name", "Test")
        run_git(project_path, "fetch", "origin")

        head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

        worktree_path = project_path / "test-repo-alpha"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "feature/work",
                str(worktree_path),
                "origin/main",
            ],
            cwd=project_path,
            check=True,
            capture_output=True,
        )
        run_git(worktree_path, "config", "user.email", "test@test.com")
        run_git(worktree_path, "config", "user.name", "Test")

        yield project_path, worktree_path


def _three_commits(worktree_path: Path) -> None:
    create_commit(worktree_path, "one.txt", "one\n", "feat: one")
    create_commit(worktree_path, "two.txt", "two\n", "feat: two")
    create_commit(worktree_path, "three.txt", "three\n", "feat: three")


def _history_refs(worktree_path: Path, branch: str) -> list[str]:
    result = run_git(
        worktree_path,
        "for-each-ref",
        "--format=%(refname)",
        history_ref_prefix(branch),
    )
    return [line for line in result.stdout.split("\n") if line]


class TestUncommitCollapsesTheBranch:
    """Three commits ahead become zero, and their diff is unstaged."""

    def test_the_commits_collapse(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        assert result.commits == 3
        assert get_commits_ahead(worktree_path, "origin/main") == 0

    def test_the_changes_are_unstaged_in_the_working_tree(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        uncommit_branch(worktree_path)

        assert sorted(get_worktree_dirty_files(worktree_path)) == [
            "one.txt",
            "three.txt",
            "two.txt",
        ]

    def test_the_history_ref_holds_the_chronology(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        subjects = run_git(
            worktree_path, "log", "--format=%s", "-3", result.history_ref
        ).stdout.split("\n")
        assert subjects[:3] == ["feat: three", "feat: two", "feat: one"]

    def test_the_history_survives_a_prune(self, project_with_worktree):
        """The ref is what keeps the collapsed commits reachable."""
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        result = uncommit_branch(worktree_path)
        run_git(worktree_path, "gc", "--prune=now")

        assert (
            run_git(worktree_path, "rev-parse", "--verify", result.history_ref).stdout
        ).strip()

    def test_the_stat_describes_what_is_now_unstaged(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        assert "one.txt" in result.stat
        assert "3 files changed" in result.stat

    def test_two_runs_give_two_history_refs(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

        first = uncommit_branch(worktree_path)
        run_git(worktree_path, "add", "-A")
        run_git(worktree_path, "commit", "-m", "feat: redone")
        second = uncommit_branch(worktree_path)

        assert first.history_ref != second.history_ref
        assert len(_history_refs(worktree_path, "feature/work")) == 2


class TestUncommitRefuses:
    """It fails at the first bad step, and changes nothing on refusal."""

    def test_a_dirty_file_refuses(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)
        (worktree_path / "scratch.txt").write_text("wip\n")

        with pytest.raises(WorktreeError):
            uncommit_branch(worktree_path)

        assert get_commits_ahead(worktree_path, "origin/main") == 3
        assert _history_refs(worktree_path, "feature/work") == []

    def test_a_managed_file_does_not_count_as_dirty(self, project_with_worktree):
        """``.env`` is maelstrom's, so it never blocks the command."""
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)
        (worktree_path / ".env").write_text("PORT=3000\n")

        assert uncommit_branch(worktree_path).commits == 3

    def test_a_branch_the_rebase_empties_refuses(self, project_with_worktree):
        """The count that matters is the one after the rebase.

        The pre-rebase check reads a possibly stale ``origin/<base>``. A branch
        whose commits already landed upstream passes it, and the rebase then
        leaves nothing to uncommit.
        """
        project_path, worktree_path = project_with_worktree
        create_commit(worktree_path, "x.txt", "x\n", "feat: x")
        run_git(worktree_path, "push", "-q", "origin", "feature/work:main")
        # Leave the remote-tracking ref behind, as it is before a fetch.
        run_git(
            worktree_path,
            "update-ref",
            "refs/remotes/origin/main",
            run_git(worktree_path, "rev-parse", "HEAD~1").stdout.strip(),
        )

        with pytest.raises(WorktreeError):
            uncommit_branch(worktree_path)

        assert _history_refs(worktree_path, "feature/work") == []

    def test_no_commit_ahead_refuses(self, project_with_worktree):
        _, worktree_path = project_with_worktree

        with pytest.raises(WorktreeError):
            uncommit_branch(worktree_path)

        assert _history_refs(worktree_path, "feature/work") == []


class TestUncommitWhenTheResetFails:
    """A failed reset leaves no history ref behind.

    The ref is written before the reset, so a reset that fails would otherwise
    leave a history for work that is still committed — and a retry would write a
    second one.
    """

    def test_it_raises_and_removes_the_ref_it_wrote(self, project_with_worktree):
        _, worktree_path = project_with_worktree
        _three_commits(worktree_path)

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

        assert _history_refs(worktree_path, "feature/work") == []
        assert get_commits_ahead(worktree_path, "origin/main") == 3


class TestUncommitOnAConflict:
    """The rebase always runs, and a conflict leaves the worktree as it was."""

    def _conflicting_branch(self, project_path: Path, worktree_path: Path) -> str:
        """Move ``origin/main`` on to a commit that conflicts with this branch."""
        create_commit(worktree_path, "shared.txt", "mine\n", "feat: my line")
        head = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()

        # A second worktree writes the conflicting change and pushes it.
        other = project_path / "other"
        subprocess.run(
            ["git", "worktree", "add", "-b", "other", str(other), "origin/main"],
            cwd=project_path,
            check=True,
            capture_output=True,
        )
        run_git(other, "config", "user.email", "test@test.com")
        run_git(other, "config", "user.name", "Test")
        create_commit(other, "shared.txt", "theirs\n", "feat: their line")
        run_git(other, "push", "-q", "origin", "other:main")
        run_git(worktree_path, "fetch", "-q", "origin")
        return head

    def test_a_conflict_raises_and_writes_no_history_ref(self, project_with_worktree):
        project_path, worktree_path = project_with_worktree
        head = self._conflicting_branch(project_path, worktree_path)

        with pytest.raises(WorktreeError, match="conflicts"):
            uncommit_branch(worktree_path)

        assert run_git(worktree_path, "rev-parse", "HEAD").stdout.strip() == head
        assert _history_refs(worktree_path, "feature/work") == []

    def test_a_conflict_leaves_no_rebase_in_progress(self, project_with_worktree):
        project_path, worktree_path = project_with_worktree
        self._conflicting_branch(project_path, worktree_path)

        with pytest.raises(WorktreeError):
            uncommit_branch(worktree_path)

        assert not rebase_in_progress(worktree_path)
        assert get_worktree_dirty_files(worktree_path) == []


class TestUncommitUsesTheStackAwareBase:
    """A stacked branch resets to the base tip of its base, not of main."""

    def test_a_stacked_branch_keeps_its_bases_commits(self, project_with_worktree):
        project_path, worktree_path = project_with_worktree

        # A base branch with its own commit, pushed to origin.
        run_git(worktree_path, "checkout", "-q", "-b", "feat/base")
        create_commit(worktree_path, "base.txt", "base\n", "feat: base work")
        run_git(worktree_path, "push", "-q", "origin", "feat/base")
        run_git(worktree_path, "fetch", "-q", "origin")

        run_git(worktree_path, "checkout", "-q", "-b", "feat/child")
        _three_commits(worktree_path)

        store = InMemoryBaseStore()
        store.write("feat/child", BaseRef(branch="feat/base"))
        result = uncommit_branch(worktree_path, store=store)

        assert result.base == "feat/base"
        assert result.commits == 3
        # The base's commit is still committed, not collapsed into the tree.
        assert (worktree_path / "base.txt").exists()
        assert "base.txt" not in get_worktree_dirty_files(worktree_path)


class TestUncommitBranchCommand:
    """``mael git uncommit-branch`` reports what it did, or exits 1 on refusal."""

    def _run(self, worktree_path: Path, result=None, error=None):
        target = "maelstrom.git_cli.uncommit_branch"
        patched = (
            patch(target, side_effect=error)
            if error is not None
            else patch(target, return_value=result)
        )
        with (
            patch(
                "maelstrom.git_cli.resolve_context",
                return_value=SimpleNamespace(worktree_path=worktree_path),
            ),
            patched,
        ):
            return CliRunner().invoke(cli, ["git", "uncommit-branch"])

    def test_it_reports_the_base_the_ref_the_count_and_the_stat(self, tmp_path):
        result = self._run(
            tmp_path,
            result=UncommitResult(
                base="feat/parent",
                history_ref="refs/mael/history/feat/child/20260908T121500Z",
                commits=3,
                stat=" one.txt | 1 +\n 1 file changed, 1 insertion(+)",
            ),
        )

        assert result.exit_code == 0
        assert "feat/parent" in result.output
        assert "refs/mael/history/feat/child/20260908T121500Z" in result.output
        assert "3" in result.output
        assert "one.txt" in result.output

    def test_a_git_failure_exits_1_rather_than_raising(self, tmp_path):
        """The reads before the checks can fail on a broken worktree."""
        result = self._run(
            tmp_path,
            error=subprocess.CalledProcessError(
                128, "git", stderr="fatal: not a git repository"
            ),
        )

        assert result.exit_code == 1
        assert "not a git repository" in result.output

    def test_a_refusal_exits_1_with_the_message(self, tmp_path):
        result = self._run(tmp_path, error=WorktreeError("nothing to uncommit."))

        assert result.exit_code == 1
        assert "nothing to uncommit." in result.output


class TestDeleteBranchPrunesTheWorkingHistory:
    """The working history goes when its branch goes.

    This covers ``mael tidy-branches``, ``mael sync --close`` and
    ``mael git merge --close`` at once — all three delete through here.
    """

    def _write_history(self, project_path: Path, branch: str, stamp: str) -> str:
        ref = history_ref(branch, stamp)
        run_git(project_path, "update-ref", ref, "HEAD")
        return ref

    def test_the_branchs_history_refs_go_with_it(self, project_with_worktree):
        project_path, worktree_path = project_with_worktree
        self._write_history(project_path, "feature/work", "20260908T120000Z")
        self._write_history(project_path, "feature/work", "20260908T130000Z")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=project_path,
            check=True,
            capture_output=True,
        )

        delete_branch(project_path, "feature/work")

        assert _history_refs(project_path, "feature/work") == []

    def test_another_branchs_history_is_left_alone(self, project_with_worktree):
        project_path, worktree_path = project_with_worktree
        self._write_history(project_path, "feature/work", "20260908T120000Z")
        kept = self._write_history(project_path, "feature/other", "20260908T120000Z")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=project_path,
            check=True,
            capture_output=True,
        )

        delete_branch(project_path, "feature/work")

        assert _history_refs(project_path, "feature/other") == [kept]
