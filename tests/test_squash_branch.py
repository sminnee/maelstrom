"""Tests for ``mael git squash-branch`` and the ``--remote``/``--local`` scope.

``squash_branch`` collapses a branch into one commit and leaves it committed;
``uncommit_branch`` does the same and then resets. Both take a scope: ``remote``
takes the whole branch, ``local`` takes only what was never pushed.

The scope is the part a skill gets wrong, and it is decided by real refs —
whether ``origin/<branch>`` exists, what it points at, what the rebase drops. So
these drive real git, as ``test_uncommit_branch.py`` does.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maelstrom import worktree as worktree_module
from maelstrom.base_store import InMemoryBaseStore
from maelstrom.cli import cli
from maelstrom.worktree import (
    get_commits_ahead,
    get_worktree_dirty_files,
    rebase_in_progress,
    squash_branch,
    uncommit_branch,
)
from maelstrom.worktree_model import (
    SQUASH_MESSAGE,
    BaseRef,
    SquashResult,
    SquashScope,
    WorktreeError,
)
from tests.git_helpers import create_commit, history_refs, run_git, three_commits


def _subjects(worktree_path: Path, rev_range: str) -> list[str]:
    """The commit subjects in ``rev_range``, newest first."""
    result = run_git(worktree_path, "log", "--format=%s", rev_range)
    return [line for line in result.stdout.split("\n") if line]


def _push_branch(worktree_path: Path) -> None:
    """Publish ``feature/work`` so ``origin/<branch>`` exists."""
    run_git(worktree_path, "push", "-q", "origin", "feature/work")
    run_git(worktree_path, "fetch", "-q", "origin")


class TestSquashCollapsesTheWholeBranch:
    """The default scope takes every commit ahead of the base fork point."""

    def test_three_commits_become_one(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = squash_branch(worktree_path)

        assert result.commits == 3
        assert get_commits_ahead(worktree_path, "origin/main") == 1

    def test_the_work_stays_committed(self, collapsible_project):
        """The point of squash over uncommit: nothing is left unstaged."""
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        squash_branch(worktree_path)

        assert get_worktree_dirty_files(worktree_path) == []
        assert (worktree_path / "one.txt").exists()
        assert (worktree_path / "three.txt").exists()

    def test_the_squashed_commit_carries_the_marker(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = squash_branch(worktree_path)

        assert _subjects(worktree_path, "origin/main..HEAD") == [SQUASH_MESSAGE]
        assert result.sha == run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()

    def test_the_history_ref_holds_the_chronology(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = squash_branch(worktree_path)

        assert _subjects(worktree_path, result.history_ref)[:3] == [
            "feat: three",
            "feat: two",
            "feat: one",
        ]

    def test_the_tree_is_unchanged_by_the_collapse(self, collapsible_project):
        """The invariant: a squash rewrites history, never content."""
        _, worktree_path = collapsible_project
        three_commits(worktree_path)
        before = run_git(worktree_path, "rev-parse", "HEAD^{tree}").stdout.strip()

        squash_branch(worktree_path)

        after = run_git(worktree_path, "rev-parse", "HEAD^{tree}").stdout.strip()
        assert after == before

    def test_it_reports_the_scope_it_used(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        assert squash_branch(worktree_path).scope == "remote"


class TestSquashLocalScope:
    """``--local`` collapses only what ``origin/<branch>`` does not already hold."""

    def test_it_collapses_only_the_unpushed_commits(self, collapsible_project):
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "pushed.txt", "pushed\n", "feat: pushed")
        _push_branch(worktree_path)
        create_commit(worktree_path, "new-one.txt", "1\n", "feat: new one")
        create_commit(worktree_path, "new-two.txt", "2\n", "feat: new two")

        result = squash_branch(worktree_path, scope="local")

        assert result.commits == 2
        assert result.scope == "local"

    def test_the_pushed_commit_keeps_its_own_subject(self, collapsible_project):
        """The already-reviewed part of the branch must survive intact."""
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "pushed.txt", "pushed\n", "feat: pushed")
        _push_branch(worktree_path)
        create_commit(worktree_path, "new-one.txt", "1\n", "feat: new one")
        create_commit(worktree_path, "new-two.txt", "2\n", "feat: new two")

        squash_branch(worktree_path, scope="local")

        assert _subjects(worktree_path, "origin/main..HEAD") == [
            SQUASH_MESSAGE,
            "feat: pushed",
        ]

    def test_a_never_pushed_branch_behaves_as_remote(self, collapsible_project):
        """No ``origin/<branch>`` means nothing is pushed, so take it all."""
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = squash_branch(worktree_path, scope="local")

        assert result.commits == 3
        assert _subjects(worktree_path, "origin/main..HEAD") == [SQUASH_MESSAGE]

    def test_a_never_pushed_stacked_branch_keeps_its_bases_commits(
        self, collapsible_project
    ):
        """The fallback is the fork point, not ``origin/main``.

        ``get_local_only_commits`` falls back to ``origin/main``, which on a
        stacked branch would sweep the base's commits into the squash. This is
        why that helper is not reused.
        """
        _, worktree_path = collapsible_project
        run_git(worktree_path, "checkout", "-q", "-b", "feat/base")
        create_commit(worktree_path, "base.txt", "base\n", "feat: base work")
        run_git(worktree_path, "push", "-q", "origin", "feat/base")
        run_git(worktree_path, "fetch", "-q", "origin")

        run_git(worktree_path, "checkout", "-q", "-b", "feat/child")
        three_commits(worktree_path)

        store = InMemoryBaseStore()
        store.write("feat/child", BaseRef(branch="feat/base"))
        result = squash_branch(worktree_path, scope="local", store=store)

        assert result.commits == 3
        assert _subjects(worktree_path, "origin/feat/base..HEAD") == [SQUASH_MESSAGE]

    def test_a_fully_pushed_branch_refuses(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)
        _push_branch(worktree_path)

        with pytest.raises(WorktreeError, match="already pushed"):
            squash_branch(worktree_path, scope="local")

        assert history_refs(worktree_path, "feature/work") == []

    def test_a_fixup_aimed_at_a_pushed_commit_refuses(self, collapsible_project):
        """The squash runs before any autosquash, so such a fixup is lost.

        Its subject disappears into the squashed commit and never reaches its
        target, while its content silently lands anyway — so refuse rather than
        collapse it.
        """
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "one.txt", "one\n", "feat: one")
        pushed = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()
        _push_branch(worktree_path)

        create_commit(worktree_path, "two.txt", "two\n", "feat: two")
        (worktree_path / "one.txt").write_text("one\nfix\n")
        run_git(worktree_path, "add", "-A")
        run_git(worktree_path, "commit", "-q", f"--fixup={pushed}")

        with pytest.raises(WorktreeError, match="fixup"):
            squash_branch(worktree_path, scope="local")

        assert history_refs(worktree_path, "feature/work") == []
        assert get_commits_ahead(worktree_path, "origin/feature/work") == 2

    def test_a_fixup_is_matched_by_side_not_by_subject_text(self, collapsible_project):
        """A repeated subject must not disguise an outbound fixup.

        Subjects repeat on a real branch — ``wip``, ``fix tests``. Deciding a
        fixup's target by subject text alone lets an unpushed commit stand in for
        the pushed one it duplicates, and the collapse then discards the fixup
        silently.
        """
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "a.txt", "1\n", "feat: same subject")
        pushed = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()
        _push_branch(worktree_path)

        # An unpushed commit repeats the pushed commit's subject.
        create_commit(worktree_path, "c.txt", "2\n", "feat: same subject")
        (worktree_path / "a.txt").write_text("1\nfix\n")
        run_git(worktree_path, "add", "-A")
        run_git(worktree_path, "commit", "-q", f"--fixup={pushed}")

        with pytest.raises(WorktreeError, match="fixup"):
            squash_branch(worktree_path, scope="local")

        assert history_refs(worktree_path, "feature/work") == []

    def test_a_fixup_aimed_inside_the_range_is_allowed(self, collapsible_project):
        """Such a fixup collapses into the same commit as its target anyway."""
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "one.txt", "one\n", "feat: one")
        _push_branch(worktree_path)

        create_commit(worktree_path, "two.txt", "two\n", "feat: two")
        target = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()
        (worktree_path / "two.txt").write_text("two\nmore\n")
        run_git(worktree_path, "add", "-A")
        run_git(worktree_path, "commit", "-q", f"--fixup={target}")

        result = squash_branch(worktree_path, scope="local")

        assert result.commits == 2
        assert (worktree_path / "two.txt").read_text() == "two\nmore\n"


class TestSquashRefuses:
    """It fails at the first bad step, and changes nothing on refusal."""

    def test_a_dirty_file_refuses(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)
        (worktree_path / "scratch.txt").write_text("wip\n")

        with pytest.raises(WorktreeError):
            squash_branch(worktree_path)

        assert get_commits_ahead(worktree_path, "origin/main") == 3
        assert history_refs(worktree_path, "feature/work") == []

    def test_no_commit_ahead_refuses(self, collapsible_project):
        _, worktree_path = collapsible_project

        with pytest.raises(WorktreeError):
            squash_branch(worktree_path)

        assert history_refs(worktree_path, "feature/work") == []

    def test_a_branch_the_rebase_empties_refuses(self, collapsible_project):
        """A branch whose commits already landed upstream has nothing left.

        Git skips the squashed commit as previously applied, so the count that
        decides this is the one after the rebase.
        """
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "x.txt", "x\n", "feat: x")
        run_git(worktree_path, "push", "-q", "origin", "feature/work:main")
        run_git(
            worktree_path,
            "update-ref",
            "refs/remotes/origin/main",
            run_git(worktree_path, "rev-parse", "HEAD~1").stdout.strip(),
        )

        with pytest.raises(WorktreeError):
            squash_branch(worktree_path)

        assert history_refs(worktree_path, "feature/work") == []

    def test_a_conflict_raises_and_writes_no_history_ref(self, collapsible_project):
        project_path, worktree_path = collapsible_project
        create_commit(worktree_path, "shared.txt", "mine\n", "feat: my line")
        head = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()

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

        with pytest.raises(WorktreeError, match="conflicts"):
            squash_branch(worktree_path)

        assert run_git(worktree_path, "rev-parse", "HEAD").stdout.strip() == head
        assert history_refs(worktree_path, "feature/work") == []
        assert not rebase_in_progress(worktree_path)


class TestSquashWhenTheCommitFails:
    """A failed commit leaves no history ref and an unmoved HEAD."""

    def test_it_raises_and_removes_the_ref_it_wrote(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)
        head = run_git(worktree_path, "rev-parse", "HEAD").stdout.strip()

        failed = subprocess.CompletedProcess(
            args=["git"], returncode=1, stdout="", stderr="index locked"
        )
        real = worktree_module.run_git

        def fail_the_commit(cmd, *args, **kwargs):
            if cmd[:1] == ["commit"]:
                return failed
            return real(cmd, *args, **kwargs)

        with patch.object(worktree_module, "run_git", side_effect=fail_the_commit):
            with pytest.raises(WorktreeError, match="index locked"):
                squash_branch(worktree_path)

        assert history_refs(worktree_path, "feature/work") == []
        assert run_git(worktree_path, "rev-parse", "HEAD").stdout.strip() == head


class TestUncommitTakesTheSameScope:
    """``uncommit_branch`` is the squash plus a reset, and scopes identically."""

    def test_local_leaves_the_pushed_commit_committed(self, collapsible_project):
        _, worktree_path = collapsible_project
        create_commit(worktree_path, "pushed.txt", "pushed\n", "feat: pushed")
        _push_branch(worktree_path)
        create_commit(worktree_path, "new-one.txt", "1\n", "feat: new one")

        result = uncommit_branch(worktree_path, scope="local")

        assert result.scope == "local"
        assert result.commits == 1
        assert _subjects(worktree_path, "origin/main..HEAD") == ["feat: pushed"]
        assert get_worktree_dirty_files(worktree_path) == ["new-one.txt"]

    def test_remote_stays_the_default(self, collapsible_project):
        _, worktree_path = collapsible_project
        three_commits(worktree_path)

        result = uncommit_branch(worktree_path)

        assert result.scope == "remote"
        assert get_commits_ahead(worktree_path, "origin/main") == 0


class TestSquashBranchCommand:
    """``mael git squash-branch`` reports what it did, or exits 1 on refusal."""

    def _run(self, worktree_path: Path, args=None, result=None, error=None):
        target = "maelstrom.git_cli.squash_branch"
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
            patched as mock,
        ):
            invoked = CliRunner().invoke(cli, ["git", "squash-branch", *(args or [])])
        return invoked, mock

    def _result(self, scope: SquashScope = "remote"):
        return SquashResult(
            base="feat/parent",
            history_ref="refs/mael/history/feat/child/20260908T121500Z",
            commits=3,
            stat=" one.txt | 1 +\n 1 file changed, 1 insertion(+)",
            scope=scope,
            sha="abc1234",
            collapse_point="0000000",
        )

    def test_it_reports_the_base_the_ref_the_count_and_the_stat(self, tmp_path):
        invoked, _ = self._run(tmp_path, result=self._result())

        assert invoked.exit_code == 0
        assert invoked.output == (
            "Base: feat/parent\n"
            "Working history: refs/mael/history/feat/child/20260908T121500Z\n"
            "Squashed 3 commits into abc1234.\n"
            "\n"
            " one.txt | 1 +\n"
            " 1 file changed, 1 insertion(+)\n"
        )

    def test_it_defaults_to_the_remote_scope(self, tmp_path):
        _, mock = self._run(tmp_path, result=self._result())

        assert mock.call_args.kwargs["scope"] == "remote"

    def test_local_passes_the_local_scope(self, tmp_path):
        _, mock = self._run(tmp_path, args=["--local"], result=self._result("local"))

        assert mock.call_args.kwargs["scope"] == "local"

    def test_a_refusal_exits_1_with_the_message(self, tmp_path):
        invoked, _ = self._run(
            tmp_path, error=WorktreeError("every commit is already pushed.")
        )

        assert invoked.exit_code == 1
        assert "every commit is already pushed." in invoked.output

    def test_a_git_failure_exits_1_rather_than_raising(self, tmp_path):
        invoked, _ = self._run(
            tmp_path,
            error=subprocess.CalledProcessError(
                128, "git", stderr="fatal: not a git repository"
            ),
        )

        assert invoked.exit_code == 1
        assert "not a git repository" in invoked.output


class TestUncommitBranchCommandScope:
    """``mael git uncommit-branch`` takes the same flag pair."""

    def _run(self, worktree_path: Path, args=None):
        from maelstrom.worktree_model import UncommitResult

        with (
            patch(
                "maelstrom.git_cli.resolve_context",
                return_value=SimpleNamespace(worktree_path=worktree_path),
            ),
            patch(
                "maelstrom.git_cli.uncommit_branch",
                return_value=UncommitResult(
                    base="main",
                    history_ref="refs/mael/history/feature/work/20260908T121500Z",
                    commits=1,
                    stat="",
                ),
            ) as mock,
        ):
            CliRunner().invoke(cli, ["git", "uncommit-branch", *(args or [])])
        return mock

    def test_it_defaults_to_the_remote_scope(self, tmp_path):
        assert self._run(tmp_path).call_args.kwargs["scope"] == "remote"

    def test_local_passes_the_local_scope(self, tmp_path):
        mock = self._run(tmp_path, args=["--local"])

        assert mock.call_args.kwargs["scope"] == "local"
