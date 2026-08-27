"""Real-git tests for stacked bases: rebase, cascade, and collapse.

Extends the source → bare-remote → bare-clone-project → linked-worktree pattern of
``tests/test_sync_flags.py`` into a two-worktree ``project_with_stack``, so a child
branch can be stacked on a parent branch and both can be driven independently.

The scenarios here are the empirical ones the design rests on. Plain
``git rebase origin/main`` already handles a merged base and unrelated drift —
patch-id detection drops the parent's commits even through a squash merge. It fails
on **review churn**: a parent amended after the child last re-stacked leaves the
child holding a stale copy whose patch-id no longer matches. That is the normal path
in this repo (every ``--fixup`` + ``mael gh create-pr --squash`` cycle rewrites the
parent), which is what ``base_tip`` and the ``--onto`` form exist for.
"""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from maelstrom.base_store import GitConfigBaseStore
from maelstrom.worktree import (
    current_stack_tip,
    get_current_branch,
    list_local_branches,
    recycle_worktree,
    remote_branch_ages,
    setup_worktree_for_branch,
    squash_worktree,
    sync_worktree,
    tidy_branches,
)
from maelstrom.worktree_model import BaseRef, StackTip
from tests.git_helpers import create_commit, run_git, setup_git_repo


def _head(path: Path) -> str:
    return run_git(path, "rev-parse", "HEAD").stdout.strip()


def _log(path: Path) -> list[str]:
    """Subject lines of every commit on HEAD, newest first."""
    return run_git(path, "log", "--format=%s").stdout.split("\n")


def _commits_ahead_of_main(path: Path) -> int:
    out = run_git(path, "rev-list", "--count", "origin/main..HEAD").stdout.strip()
    return int(out)


@pytest.fixture
def project_with_stack():
    """A project with two worktrees: ``alpha`` (parent) and ``bravo`` (child).

    Both branches start at ``origin/main`` and are pushed, so either can be used as
    a base. Yields ``(project_path, parent_wt, child_wt, remote_path)``.
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

        head_sha = _head(project_path)
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

        worktrees = {}
        for nato, branch in (("alpha", "feat/parent"), ("bravo", "feat/child")):
            path = project_path / f"test-repo-{nato}"
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(path), "origin/main"],
                cwd=project_path,
                check=True,
                capture_output=True,
            )
            run_git(path, "config", "user.email", "test@test.com")
            run_git(path, "config", "user.name", "Test")
            run_git(path, "push", "origin", f"{branch}:{branch}")
            worktrees[nato] = path
        run_git(project_path, "fetch", "origin")

        maelstrom_dir = tmp / "maelstrom-home"
        maelstrom_dir.mkdir()
        with patch("maelstrom.context.get_maelstrom_dir", return_value=maelstrom_dir):
            yield project_path, worktrees["alpha"], worktrees["bravo"], remote_path


def _push(path: Path, branch: str, *, force: bool = False) -> None:
    args = ["push"] + (["--force"] if force else []) + ["origin", f"{branch}:{branch}"]
    run_git(path, *args)
    run_git(path, "fetch", "origin")


def _advance_origin_main(
    project_path: Path, remote_path: Path, name: str = "upstream.txt"
) -> None:
    """Add an unrelated commit to origin/main and fetch it."""
    with TemporaryDirectory() as tmpdir:
        clone = Path(tmpdir) / "pusher"
        subprocess.run(
            ["git", "clone", str(remote_path), str(clone)],
            check=True,
            capture_output=True,
        )
        run_git(clone, "config", "user.email", "test@test.com")
        run_git(clone, "config", "user.name", "Test")
        create_commit(clone, name, "upstream\n", f"Upstream {name}")
        run_git(clone, "push", "origin", "HEAD:main")
    run_git(project_path, "fetch", "origin")


def _squash_merge_to_main(project_path: Path, remote_path: Path, branch: str) -> None:
    """Squash-merge ``branch`` into main and delete it on the remote.

    Matches this repo's GitHub settings: squash merge, delete branch on merge. The
    child's stored ``base_tip`` is what makes that survivable.
    """
    with TemporaryDirectory() as tmpdir:
        clone = Path(tmpdir) / "merger"
        subprocess.run(
            ["git", "clone", str(remote_path), str(clone)],
            check=True,
            capture_output=True,
        )
        run_git(clone, "config", "user.email", "test@test.com")
        run_git(clone, "config", "user.name", "Test")
        run_git(clone, "merge", "--squash", f"origin/{branch}")
        run_git(clone, "commit", "-m", f"Squashed {branch} (#1)")
        run_git(clone, "push", "origin", "HEAD:main")
        run_git(clone, "push", "origin", "--delete", branch)
    run_git(project_path, "fetch", "origin", "--prune")


# ---------------------------------------------------------------------------
# The default path must not move
# ---------------------------------------------------------------------------


class TestDefaultBaseIsUnchanged:
    """The guard that keeps every unstacked worktree byte-identical."""

    def test_no_base_runs_the_exact_pre_stacking_argv(self, project_with_stack):
        """With no base set, the rebase argv is what it always was.

        Written first and deliberately literal. Stacking is a reframe of the
        existing auto-rebase, so a worktree that never opts in must not be able
        to tell the feature exists.
        """
        _, parent, _, _ = project_with_stack
        create_commit(parent, "feature.txt", "feature\n", "Feature commit")

        seen: list[list[str]] = []
        real = subprocess.run

        def spy(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "rebase"]:
                seen.append(list(cmd))
            return real(cmd, *args, **kwargs)

        with patch("maelstrom.shell.subprocess.run", side_effect=spy):
            result = squash_worktree(parent, skip_fetch=True, squash=False)

        assert result.success is True
        assert seen == [["git", "rebase", "--autostash", "origin/main"]]
        assert result.message == "Successfully rebased feat/parent onto origin/main"
        assert result.base == "main"
        assert result.base_collapsed is False

    def test_no_base_does_not_record_a_tip(self, project_with_stack):
        """An unstacked branch writes nothing to config — no store, no side effect."""
        project_path, parent, _, _ = project_with_stack
        create_commit(parent, "feature.txt", "feature\n", "Feature commit")

        squash_worktree(parent, skip_fetch=True, squash=False)

        assert GitConfigBaseStore(project_path).all() == {}

    def test_no_base_does_not_prune(self, project_with_stack):
        """``--prune`` deletes stale remote refs — a visible side effect.

        It is gated to non-default syncs so the default stays byte-identical.
        """
        _, parent, _, _ = project_with_stack
        seen: list[list[str]] = []
        real = subprocess.run

        def spy(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "fetch"]:
                seen.append(list(cmd))
            return real(cmd, *args, **kwargs)

        with patch("maelstrom.shell.subprocess.run", side_effect=spy):
            squash_worktree(parent, squash=False)

        assert seen, "expected a fetch"
        assert all("--prune" not in cmd for cmd in seen)


# ---------------------------------------------------------------------------
# Stacking: re-stack, cascade, and the three empirical scenarios
# ---------------------------------------------------------------------------


class TestStackedRebase:
    """A child branch stacked on a parent branch."""

    def _stack(self, project_path: Path, child: Path) -> GitConfigBaseStore:
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        return store

    def test_a_stacked_child_rebases_onto_its_parent(self, project_with_stack):
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        self._stack(project_path, child)

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.success is True
        assert result.base == "feat/parent"
        assert "Parent commit" in _log(child)
        assert _commits_ahead_of_main(child) == 2

    def test_a_successful_rebase_records_the_base_tip(self, project_with_stack):
        """The single write site. Everything downstream depends on it being here."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = self._stack(project_path, child)

        squash_worktree(child, skip_fetch=True, squash=False)

        parent_tip = run_git(
            project_path, "rev-parse", "origin/feat/parent"
        ).stdout.strip()
        assert store.read("feat/child") == BaseRef(branch="feat/parent", tip=parent_tip)

    def test_the_parents_new_work_cascades_into_the_child(self, project_with_stack):
        """The whole point: the auto-rebase maintains the stack for free."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        self._stack(project_path, child)
        squash_worktree(child, skip_fetch=True, squash=False)

        create_commit(parent, "parent2.txt", "more\n", "Parent second commit")
        _push(parent, "feat/parent")

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.success is True
        assert "Parent second commit" in _log(child)
        assert _log(child)[0] == "Child commit"
        assert _commits_ahead_of_main(child) == 3

    def test_unrelated_drift_on_main_does_not_disturb_the_child(
        self, project_with_stack
    ):
        project_path, parent, child, remote_path = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        self._stack(project_path, child)
        squash_worktree(child, skip_fetch=True, squash=False)

        _advance_origin_main(project_path, remote_path)

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.success is True
        assert _log(child)[0] == "Child commit"

    def test_an_amended_parent_rebases_cleanly(self, project_with_stack):
        """The scenario that justifies ``base_tip`` — and the one plain rebase fails.

        Every review cycle in this repo amends the parent: findings become
        ``--fixup`` commits and ``mael gh create-pr --squash`` autosquashes them
        while rebasing. The child then holds a stale copy of a commit whose
        patch-id no longer matches, so a plain rebase replays it and conflicts.
        ``--onto origin/feat/parent <base_tip>`` replays only the child's own work.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "shared.txt", "v1\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        self._stack(project_path, child)
        squash_worktree(child, skip_fetch=True, squash=False)

        # Review churn: the parent's commit is rewritten in place.
        (parent / "shared.txt").write_text("v2\n")
        run_git(parent, "add", "shared.txt")
        run_git(parent, "commit", "--amend", "-m", "Parent commit (reviewed)")
        _push(parent, "feat/parent", force=True)

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.success is True, result.message
        assert result.had_conflicts is False
        assert _log(child)[0] == "Child commit"
        assert "Parent commit (reviewed)" in _log(child)
        assert (child / "shared.txt").read_text() == "v2\n"
        assert _commits_ahead_of_main(child) == 2

    def test_a_stale_base_tip_fails_the_amended_case(self, project_with_stack):
        """Proves the re-recording is load-bearing.

        Pinning the tip to its first value — as it would be if the recording were
        removed — puts the amended-parent case back into conflict. Without this
        test, the one write site could be deleted and every other test would pass.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "shared.txt", "v1\n", "Parent commit")
        _push(parent, "feat/parent")
        first_tip = run_git(
            project_path, "rev-parse", "origin/feat/parent"
        ).stdout.strip()
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        squash_worktree(child, skip_fetch=True, squash=False)

        # An extra parent commit the child picks up, so the recorded tip moves on.
        create_commit(parent, "shared.txt", "v1\nextra\n", "Parent second commit")
        _push(parent, "feat/parent")
        squash_worktree(child, skip_fetch=True, squash=False)

        # Simulate a store that stopped re-recording: pin the tip to the first value.
        store.write("feat/child", BaseRef(branch="feat/parent", tip=first_tip))
        (parent / "shared.txt").write_text("rewritten\n")
        run_git(parent, "add", "shared.txt")
        run_git(parent, "commit", "--amend", "-m", "Parent second (reviewed)")
        _push(parent, "feat/parent", force=True)

        result = squash_worktree(
            child, skip_fetch=True, squash=False, abort_on_conflict=True
        )

        assert result.success is False
        assert result.had_conflicts is True


class TestBaseTipSafetyGuard:
    """``--onto`` with a tip that is not an ancestor of HEAD would drop commits."""

    def test_a_tip_not_in_history_falls_back_to_a_plain_rebase(
        self, project_with_stack
    ):
        """A wrong tip must degrade safely, never silently discard work.

        ``git rebase --onto X <upstream>`` replays only ``<upstream>..HEAD``. If
        ``<upstream>`` is not an ancestor of HEAD, that range is not this branch's
        own work and commits vanish with no error. Checking ancestry first turns a
        silent data-loss bug into a degraded-but-safe path.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        orphan = create_commit(parent, "orphan.txt", "orphan\n", "Orphan commit")
        run_git(parent, "reset", "--hard", "HEAD~1")

        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent", tip=orphan))

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.success is True, result.message
        assert "Child commit" in _log(child)
        assert "Parent commit" in _log(child)


# ---------------------------------------------------------------------------
# Collapse
# ---------------------------------------------------------------------------


class TestCollapse:
    """What happens when the base merges, or is abandoned."""

    def test_a_merged_base_collapses_onto_main(self, project_with_stack):
        project_path, parent, child, remote_path = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        squash_worktree(child, skip_fetch=True, squash=False)

        _squash_merge_to_main(project_path, remote_path, "feat/parent")

        result = squash_worktree(child, squash=False)

        assert result.success is True, result.message
        assert result.base_collapsed is True
        assert _commits_ahead_of_main(child) == 1
        assert _log(child)[0] == "Child commit"
        assert store.read("feat/child") == BaseRef()

    def test_an_abandoned_base_collapses_the_same_way(self, project_with_stack):
        """Abandoned and merged are handled identically, and silently."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        squash_worktree(child, skip_fetch=True, squash=False)

        run_git(parent, "push", "origin", "--delete", "feat/parent")

        result = squash_worktree(child, squash=False)

        assert result.success is True, result.message
        assert result.base_collapsed is True
        assert store.read("feat/child") == BaseRef()

    def test_a_stacked_sync_prunes_so_a_deleted_base_is_seen(self, project_with_stack):
        """Collapse needs pruned refs, so a stacked sync fetches with ``--prune``.

        Without it a deleted base leaves a stale ``origin/<base>`` behind and the
        child stays pinned to a branch nobody will ever merge.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        GitConfigBaseStore(project_path).write(
            "feat/child", BaseRef(branch="feat/parent")
        )
        squash_worktree(child, skip_fetch=True, squash=False)

        seen: list[list[str]] = []
        real = subprocess.run

        def spy(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "fetch"]:
                seen.append(list(cmd))
            return real(cmd, *args, **kwargs)

        with patch("maelstrom.shell.subprocess.run", side_effect=spy):
            squash_worktree(child, squash=False)

        assert any("--prune" in cmd for cmd in seen)

    def test_skip_fetch_defers_rather_than_collapsing_on_a_missing_ref(
        self, project_with_stack
    ):
        """``sync-all`` and the autorepair second pass skip the fetch.

        An absent ``origin/<base>`` may just be unfetched there, so collapsing
        would flatten a live stack. Defer to the next real sync instead.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        squash_worktree(child, skip_fetch=True, squash=False)

        # Drop the local remote-tracking ref without touching the remote.
        run_git(project_path, "update-ref", "-d", "refs/remotes/origin/feat/parent")

        result = squash_worktree(child, skip_fetch=True, squash=False)

        assert result.base_collapsed is False
        assert store.read("feat/child").branch == "feat/parent"


# ---------------------------------------------------------------------------
# sync_worktree — the pushing wrapper
# ---------------------------------------------------------------------------


class TestSyncWithBase:
    """``sync_worktree`` carries the base through to close_if_empty."""

    def test_close_if_empty_measures_against_the_base_not_main(
        self, project_with_stack
    ):
        """A child identical to its parent is empty, even though main has moved on.

        Measuring against ``origin/main`` would call it non-empty and leave a
        worktree open on a branch with nothing of its own.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))

        result = sync_worktree(child, skip_fetch=True, close_if_empty=True)

        assert result.success is True, result.message
        assert result.closed is True
        assert "feat/parent" in result.message

    def test_a_child_with_its_own_work_is_not_closed(self, project_with_stack):
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        GitConfigBaseStore(project_path).write(
            "feat/child", BaseRef(branch="feat/parent")
        )

        result = sync_worktree(child, skip_fetch=True, close_if_empty=True)

        assert result.closed is False
        assert get_current_branch(child) == "feat/child"


# ---------------------------------------------------------------------------
# Branch-deletion guards
# ---------------------------------------------------------------------------


class TestTidyBranchesRespectsStacks:
    """``tidy_branches`` rebases every branch onto main and deletes merged ones.

    Left alone it is the sharpest interaction in the whole design: it would flatten
    a stack it knows nothing about, and delete the branch a child is stacked on.
    """

    def test_a_base_branch_is_not_flattened_or_deleted(self, project_with_stack):
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        GitConfigBaseStore(project_path).write(
            "feat/child", BaseRef(branch="feat/parent")
        )
        squash_worktree(child, skip_fetch=True, squash=False)

        results = tidy_branches(project_path)

        assert "feat/parent" in list_local_branches(project_path)
        parent_result = next(r for r in results if r.branch == "feat/parent")
        assert parent_result.action == "skipped_base"

    def test_a_merged_base_branch_is_still_protected(self, project_with_stack):
        """A base that looks merged must survive until its child has collapsed.

        Deleting it here would strand the child on a ref that no longer resolves.
        """
        project_path, parent, child, remote_path = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        GitConfigBaseStore(project_path).write(
            "feat/child", BaseRef(branch="feat/parent")
        )
        _advance_origin_main(project_path, remote_path)

        # Free the parent's worktree so tidy would otherwise process the branch.
        run_git(parent, "checkout", "--detach", "origin/main")

        tidy_branches(project_path)

        assert "feat/parent" in list_local_branches(project_path)

    def test_an_unstacked_branch_is_tidied_as_before(self, project_with_stack):
        """The guard is narrow: a branch nobody is based on still gets tidied."""
        project_path, parent, child, _ = project_with_stack
        run_git(parent, "checkout", "--detach", "origin/main")
        run_git(child, "checkout", "--detach", "origin/main")

        results = tidy_branches(project_path)

        parent_result = next(r for r in results if r.branch == "feat/parent")
        assert parent_result.action != "skipped_base"

    def test_a_stacked_child_branch_is_not_flattened(self, project_with_stack):
        """Tidy guards the base side of a link; the child side needs it too.

        An unattended ``git rebase origin/main`` on a stacked child flattens it off
        its base and leaves its stored tip pointing at a commit no longer in the
        branch, which is exactly the state ``base_tip`` exists to prevent.
        """
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        create_commit(child, "child.txt", "child\n", "Child commit")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent"))
        squash_worktree(child, skip_fetch=True, squash=False)
        recorded = store.read("feat/child")

        # Free both worktrees so tidy would otherwise process the branches.
        run_git(child, "checkout", "--detach", "origin/main")
        run_git(parent, "checkout", "--detach", "origin/main")

        results = tidy_branches(project_path)

        child_result = next(r for r in results if r.branch == "feat/child")
        assert child_result.action == "skipped_base"
        assert store.read("feat/child") == recorded


class TestCloseIfEmptyProtectsABase:
    """A branch another branch is stacked on must never be deleted out from under it."""

    def test_an_empty_base_branch_is_closed_but_kept(self, project_with_stack):
        project_path, parent, child, _ = project_with_stack
        GitConfigBaseStore(project_path).write(
            "feat/child", BaseRef(branch="feat/parent")
        )

        result = sync_worktree(parent, skip_fetch=True, close_if_empty=True)

        assert result.success is True, result.message
        assert result.closed is True
        assert "feat/parent" in list_local_branches(project_path)
        assert result.deleted_remote is False

    def test_an_empty_unstacked_branch_is_still_deleted(self, project_with_stack):
        """The guard is narrow: nothing stacked on it means the old behaviour holds."""
        project_path, parent, _, _ = project_with_stack

        result = sync_worktree(parent, skip_fetch=True, close_if_empty=True)

        assert result.closed is True
        assert "feat/parent" not in list_local_branches(project_path)


# ---------------------------------------------------------------------------
# The stack tip
# ---------------------------------------------------------------------------


class TestRemoteBranchAges:
    """One ``for-each-ref`` answers both "does it exist?" and "how old is it?"."""

    def test_lists_every_remote_branch_with_an_age(self, project_with_stack):
        project_path, _, _, _ = project_with_stack

        ages = remote_branch_ages(project_path)

        assert set(ages) >= {"main", "feat/parent", "feat/child"}
        assert all(isinstance(v, int) and v >= 0 for v in ages.values())

    def test_a_deleted_branch_drops_out(self, project_with_stack):
        project_path, parent, _, _ = project_with_stack
        run_git(parent, "push", "origin", "--delete", "feat/parent")
        run_git(project_path, "fetch", "origin", "--prune")

        assert "feat/parent" not in remote_branch_ages(project_path)

    def test_origin_head_is_not_reported_as_a_branch(self, project_with_stack):
        """``refs/remotes/origin/HEAD`` is a symref, not a branch anyone can stack on."""
        project_path, _, _, _ = project_with_stack

        assert "HEAD" not in remote_branch_ages(project_path)


class TestStackTipStore:
    """The project's stack tip, and how ``mael add`` uses it."""

    def test_the_tip_defaults_to_main(self, project_with_stack):
        project_path, _, _, _ = project_with_stack

        assert GitConfigBaseStore(project_path).read_stack_tip() == "main"

    def test_the_tip_self_heals_when_its_branch_is_deleted(self, project_with_stack):
        """A merged or abandoned tip must never become the base of new work."""
        project_path, parent, _, _ = project_with_stack
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")
        run_git(parent, "push", "origin", "--delete", "feat/parent")
        run_git(project_path, "fetch", "origin", "--prune")

        tip = current_stack_tip(project_path, store)

        assert tip.branch == "main"
        assert tip.healed is True
        # The heal is persisted, so the next call does not have to re-derive it.
        assert store.read_stack_tip() == "main"

    def test_a_live_tip_is_returned_unchanged(self, project_with_stack):
        project_path, _, _, _ = project_with_stack
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        tip = current_stack_tip(project_path, store)

        assert tip == StackTip("feat/parent")


class TestStackByDefault:
    """`setup_worktree_for_branch` bases new work on the stack tip, then advances it."""

    def _new_branch(self, project_path, branch, **kwargs):
        return setup_worktree_for_branch(
            project_path, project_path.name, branch, run_install=False, **kwargs
        )

    def test_a_new_branch_bases_on_the_stack_tip(self, project_with_stack):
        project_path, parent, _, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        self._new_branch(project_path, "feat/new")

        assert store.read("feat/new").branch == "feat/parent"

    def test_a_new_branch_starts_from_its_base_not_main(self, project_with_stack):
        """A child starts stacked rather than needing an immediate re-stack."""
        project_path, parent, _, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        GitConfigBaseStore(project_path).write_stack_tip("feat/parent")

        setup = self._new_branch(project_path, "feat/new")

        assert "Parent commit" in _log(setup.path)

    def test_the_tip_advances_to_the_new_branch(self, project_with_stack):
        """Auto-advancing is what makes stacks form a genuine chain."""
        project_path, parent, _, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        self._new_branch(project_path, "feat/new")

        assert store.read_stack_tip() == "feat/new"

    def test_an_explicit_base_overrides_the_tip_for_one_worktree(
        self, project_with_stack
    ):
        project_path, parent, _, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        self._new_branch(project_path, "feat/new", base="main")

        assert store.read("feat/new") == BaseRef()
        # The tip still advances: the next worktree stacks on this one.
        assert store.read_stack_tip() == "feat/new"

    def test_a_main_tip_leaves_new_work_unstacked(self, project_with_stack):
        """The default project has a main tip, so nothing changes for anyone."""
        project_path, _, _, _ = project_with_stack
        store = GitConfigBaseStore(project_path)

        self._new_branch(project_path, "feat/new")

        assert store.read("feat/new") == BaseRef()

    def test_reusing_an_existing_worktree_does_not_move_the_tip(
        self, project_with_stack
    ):
        """Reuse is a no-op path; it must not silently re-point where new work lands."""
        project_path, _, _, _ = project_with_stack
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        self._new_branch(project_path, "feat/child")

        assert store.read_stack_tip() == "feat/parent"

    def test_the_base_survives_recycling_a_worktree(self, project_with_stack):
        """Worktrees are recycled; the base belongs to the branch, so it persists."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent", tip="abc123"))

        recycle_worktree(child, "feat/child")

        assert store.read("feat/child") == BaseRef(branch="feat/parent", tip="abc123")


class TestExistingBranchKeepsItsBase:
    """A branch that already exists is checked out at its own tip, not the tip's.

    ``create_worktree`` and ``recycle_worktree`` both ignore ``base`` for a branch
    that already exists locally or on origin. Recording the stack tip as its base
    anyway would claim a relationship its history does not have, and the next sync
    would rebase it onto an unrelated branch.
    """

    def _reopen(self, project_path, branch):
        return setup_worktree_for_branch(
            project_path, project_path.name, branch, run_install=False
        )

    def test_reopening_a_branch_does_not_overwrite_its_stored_base(
        self, project_with_stack
    ):
        """The `mael close --force` → reopen-branch path must not clobber the base."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent", tip="abc123"))
        store.write_stack_tip("feat/unrelated")

        # Close the child's worktree, leaving the branch behind.
        run_git(child, "checkout", "--detach", "origin/main")
        self._reopen(project_path, "feat/child")

        # The base branch survives. The tip legitimately moves: reopening syncs,
        # and every successful rebase re-records where the base now is.
        assert store.read("feat/child").branch == "feat/parent"

    def test_an_existing_branch_with_no_base_stays_unstacked(self, project_with_stack):
        """An existing branch never had the tip's history, so it is not stacked on it."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write_stack_tip("feat/parent")

        run_git(child, "checkout", "--detach", "origin/main")
        self._reopen(project_path, "feat/child")

        assert store.read("feat/child") == BaseRef()

    def test_base_main_clears_a_previously_stored_base(self, project_with_stack):
        """`--base main` must unstack the branch, not leave a stale base behind."""
        project_path, parent, child, _ = project_with_stack
        create_commit(parent, "parent.txt", "parent\n", "Parent commit")
        _push(parent, "feat/parent")
        store = GitConfigBaseStore(project_path)
        store.write("feat/child", BaseRef(branch="feat/parent", tip="abc123"))

        run_git(child, "checkout", "--detach", "origin/main")
        setup_worktree_for_branch(
            project_path,
            project_path.name,
            "feat/child",
            run_install=False,
            base="main",
        )

        assert store.read("feat/child") == BaseRef()


class TestBaseMustExist:
    """A base that names no real branch is a typo, and must be refused.

    Without this check the branch is silently "collapsed" onto main at the next
    sync and the stored base is cleared, so the user is told the base was set and
    it is gone moments later with nothing reporting why.
    """

    def test_a_nonexistent_base_is_rejected(self, project_with_stack):
        project_path, _, _, _ = project_with_stack
        store = GitConfigBaseStore(project_path)

        with pytest.raises(ValueError, match="feat/typo"):
            setup_worktree_for_branch(
                project_path,
                project_path.name,
                "feat/new",
                run_install=False,
                base="feat/typo",
            )
        assert store.all() == {}

    def test_an_existing_base_is_accepted(self, project_with_stack):
        project_path, _, _, _ = project_with_stack

        setup_worktree_for_branch(
            project_path,
            project_path.name,
            "feat/new",
            run_install=False,
            base="feat/parent",
        )

        assert GitConfigBaseStore(project_path).read("feat/new").branch == "feat/parent"

    def test_main_is_always_an_acceptable_base(self, project_with_stack):
        """`main` opts out; it is never checked against the remote."""
        project_path, _, _, _ = project_with_stack

        setup_worktree_for_branch(
            project_path,
            project_path.name,
            "feat/new",
            run_install=False,
            base="main",
        )

        assert GitConfigBaseStore(project_path).read("feat/new") == BaseRef()
