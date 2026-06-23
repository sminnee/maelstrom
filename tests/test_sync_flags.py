"""Tests for `mael sync --abort` and `mael sync --close`.

Covers the two independent, composable flags added to `mael sync`:

- ``--abort``  → on a rebase conflict, abort and restore the worktree.
- ``--close``  → after a successful rebase, if the branch is empty (HEAD ==
  origin/main), delete it (local + remote) and close the worktree instead of
  pushing.

Worktree-level tests use real git via the source → bare-remote → working-clone
pattern (mirroring ``tests/test_tidy_branches.py``); CLI tests drive ``cmd_sync``
through ``CliRunner`` with ``sync_worktree`` mocked.
"""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.ports import (
    get_port_allocation,
    record_port_allocation,
)
from maelstrom.worktree import (
    CloseResult,
    SyncResult,
    _detach_and_free_ports,
    close_worktree,
    squash_worktree,
    sync_worktree,
)

from tests.git_helpers import create_commit, run_git, setup_git_repo


# ---------------------------------------------------------------------------
# Real-git fixtures
# ---------------------------------------------------------------------------


def _rebase_in_progress(worktree_path: Path) -> bool:
    """True if a rebase is currently in progress in the worktree."""
    result = run_git(worktree_path, "rev-parse", "--git-path", "rebase-merge", check=False)
    rebase_merge = Path(result.stdout.strip())
    if not rebase_merge.is_absolute():
        rebase_merge = worktree_path / rebase_merge
    result2 = run_git(worktree_path, "rev-parse", "--git-path", "rebase-apply", check=False)
    rebase_apply = Path(result2.stdout.strip())
    if not rebase_apply.is_absolute():
        rebase_apply = worktree_path / rebase_apply
    return rebase_merge.exists() or rebase_apply.exists()


def _current_head(path: Path) -> str:
    return run_git(path, "rev-parse", "HEAD").stdout.strip()


def _is_detached(path: Path) -> bool:
    result = run_git(path, "symbolic-ref", "-q", "HEAD", check=False)
    return result.returncode != 0


@pytest.fixture
def project_with_worktree():
    """A bare-clone project ``test-repo`` with a worktree ``test-repo-alpha``.

    Mirrors maelstrom's real layout so port-allocation name extraction works:
    the worktree folder is ``<project>-<nato>``. ``get_maelstrom_dir`` is patched
    to a temp directory so port allocations don't touch the real home dir.

    Yields ``(project_path, worktree_path, remote_path)``.
    """
    with TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Source repo with an initial commit on main.
        source_path = tmp / "source"
        source_path.mkdir()
        setup_git_repo(source_path)
        create_commit(source_path, "README.md", "# Test\n", "Initial commit")
        run_git(source_path, "branch", "-M", "main")

        # Bare "remote".
        remote_path = tmp / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True, capture_output=True,
        )

        # Project root: bare clone in .git (maelstrom layout).
        project_path = tmp / "test-repo"
        project_path.mkdir()
        git_dir = project_path / ".git"
        subprocess.run(
            ["git", "clone", "--bare", str(remote_path), str(git_dir)],
            check=True, capture_output=True,
        )
        run_git(project_path, "config", "core.bare", "true")
        run_git(project_path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        run_git(project_path, "config", "user.email", "test@test.com")
        run_git(project_path, "config", "user.name", "Test")
        run_git(project_path, "fetch", "origin")

        # Detach project-root HEAD so main isn't checked out there.
        head_sha = _current_head(project_path)
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

        # Worktree on a feature branch, folder named <project>-alpha.
        worktree_path = project_path / "test-repo-alpha"
        subprocess.run(
            ["git", "worktree", "add", "-b", "feature/work", str(worktree_path), "origin/main"],
            cwd=project_path, check=True, capture_output=True,
        )
        run_git(worktree_path, "config", "user.email", "test@test.com")
        run_git(worktree_path, "config", "user.name", "Test")

        maelstrom_dir = tmp / "maelstrom-home"
        maelstrom_dir.mkdir()
        with patch("maelstrom.context.get_maelstrom_dir", return_value=maelstrom_dir):
            yield project_path, worktree_path, remote_path


def _push_branch(worktree_path: Path, branch: str) -> None:
    """Push the worktree's branch to origin and refresh remote-tracking refs."""
    run_git(worktree_path, "push", "origin", f"{branch}:{branch}")
    run_git(worktree_path, "fetch", "origin")


def _advance_origin_main(project_path: Path, remote_path: Path) -> None:
    """Add a commit to origin/main (via a throwaway clone) and fetch it."""
    with TemporaryDirectory() as tmpdir:
        clone = Path(tmpdir) / "pusher"
        subprocess.run(
            ["git", "clone", str(remote_path), str(clone)],
            check=True, capture_output=True,
        )
        run_git(clone, "config", "user.email", "test@test.com")
        run_git(clone, "config", "user.name", "Test")
        create_commit(clone, "upstream.txt", "upstream change\n", "Upstream commit")
        run_git(clone, "push", "origin", "HEAD:main")
    run_git(project_path, "fetch", "origin")


# ---------------------------------------------------------------------------
# squash_worktree(abort_on_conflict=…)
# ---------------------------------------------------------------------------


class TestSquashAbort:
    """`squash_worktree(abort_on_conflict=…)`."""

    def _make_conflict(self, project_path, worktree_path, remote_path):
        """Create a divergent edit to README so a rebase onto origin/main conflicts."""
        # Upstream changes README on main.
        with TemporaryDirectory() as tmpdir:
            clone = Path(tmpdir) / "pusher"
            subprocess.run(
                ["git", "clone", str(remote_path), str(clone)],
                check=True, capture_output=True,
            )
            run_git(clone, "config", "user.email", "test@test.com")
            run_git(clone, "config", "user.name", "Test")
            (clone / "README.md").write_text("# Upstream version\n")
            run_git(clone, "add", "README.md")
            run_git(clone, "commit", "-m", "Upstream README")
            run_git(clone, "push", "origin", "HEAD:main")
        run_git(project_path, "fetch", "origin")

        # The worktree branch edits the same line differently.
        (worktree_path / "README.md").write_text("# Feature version\n")
        run_git(worktree_path, "add", "README.md")
        run_git(worktree_path, "commit", "-m", "Feature README")

    def test_conflict_with_abort_restores_worktree(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        self._make_conflict(project_path, worktree_path, remote_path)
        head_before = _current_head(worktree_path)

        result = squash_worktree(worktree_path, skip_fetch=True, abort_on_conflict=True)

        assert result.success is False
        assert result.had_conflicts is True
        assert result.aborted is True
        assert not _rebase_in_progress(worktree_path)
        assert _current_head(worktree_path) == head_before

    def test_conflict_default_leaves_rebase_in_progress(self, project_with_worktree):
        """Regression guard: without the flag, the rebase is left in progress."""
        project_path, worktree_path, remote_path = project_with_worktree
        self._make_conflict(project_path, worktree_path, remote_path)

        result = squash_worktree(worktree_path, skip_fetch=True)

        assert result.success is False
        assert result.had_conflicts is True
        assert result.aborted is False
        assert _rebase_in_progress(worktree_path)

        # Clean up the in-progress rebase so the fixture teardown is happy.
        run_git(worktree_path, "rebase", "--abort", check=False)

    def test_clean_rebase_with_abort_still_succeeds(self, project_with_worktree):
        """abort_on_conflict is a no-op when there is no conflict."""
        project_path, worktree_path, remote_path = project_with_worktree
        # A non-conflicting feature commit + an unrelated upstream commit.
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        _advance_origin_main(project_path, remote_path)

        result = squash_worktree(worktree_path, skip_fetch=True, abort_on_conflict=True)

        assert result.success is True
        assert result.aborted is False
        assert not _rebase_in_progress(worktree_path)


# ---------------------------------------------------------------------------
# sync_worktree(close_if_empty=…)
# ---------------------------------------------------------------------------


class TestSyncClose:
    """`sync_worktree(close_if_empty=…)` against a real remote."""

    def test_empty_branch_with_remote_is_deleted_and_closed(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Branch is empty (HEAD == origin/main) and exists on the remote.
        _push_branch(worktree_path, "feature/work")
        record_port_allocation(project_path, "alpha", 350)

        result = sync_worktree(worktree_path, skip_fetch=True, close_if_empty=True)

        assert result.success is True
        assert result.closed is True
        assert result.deleted_remote is True
        # Local + remote branch gone.
        local = run_git(project_path, "rev-parse", "--verify", "feature/work", check=False)
        assert local.returncode != 0
        remote = run_git(project_path, "rev-parse", "--verify", "origin/feature/work", check=False)
        assert remote.returncode != 0
        # HEAD detached at origin/main, ports freed.
        assert _is_detached(worktree_path)
        assert _current_head(worktree_path) == _current_head_of_ref(project_path, "origin/main")
        assert get_port_allocation(project_path, "alpha") is None

    def test_empty_local_only_branch_is_never_pushed(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Branch empty, NOT on the remote (only main was ever pushed).
        record_port_allocation(project_path, "alpha", 350)

        result = sync_worktree(worktree_path, skip_fetch=True, close_if_empty=True)

        assert result.success is True
        assert result.closed is True
        assert result.deleted_remote is False
        # Local branch deleted; remote only ever had main.
        local = run_git(project_path, "rev-parse", "--verify", "feature/work", check=False)
        assert local.returncode != 0
        remote = run_git(project_path, "rev-parse", "--verify", "origin/feature/work", check=False)
        assert remote.returncode != 0
        assert get_port_allocation(project_path, "alpha") is None

    def test_non_empty_branch_is_pushed_not_closed(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Push the branch, then add a real commit so it's ahead of origin/main.
        _push_branch(worktree_path, "feature/work")
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")

        result = sync_worktree(worktree_path, skip_fetch=True, close_if_empty=True)

        assert result.success is True
        assert result.closed is False
        assert result.pushed is True
        # Branch + HEAD intact (still on feature/work, not detached).
        assert not _is_detached(worktree_path)
        assert run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/work"
        # The commit reached the remote.
        run_git(project_path, "fetch", "origin")
        remote = run_git(project_path, "rev-parse", "--verify", "origin/feature/work", check=False)
        assert remote.returncode == 0

    def test_failed_local_delete_is_reported(self, project_with_worktree):
        """A failed `git branch -D` is surfaced, not silently claimed as deleted."""
        project_path, worktree_path, remote_path = project_with_worktree

        with patch("maelstrom.worktree.delete_branch", return_value=(False, False)):
            result = sync_worktree(worktree_path, skip_fetch=True, close_if_empty=True)

        assert result.success is False
        # Worktree still closed (detach already happened before the delete).
        assert result.closed is True
        assert "deleting the local branch failed" in result.message
        assert _is_detached(worktree_path)

    def test_failed_remote_delete_is_reported(self, project_with_worktree):
        """A failed remote delete (when a remote branch existed) is surfaced."""
        project_path, worktree_path, remote_path = project_with_worktree
        _push_branch(worktree_path, "feature/work")  # so delete_remote is attempted

        with patch("maelstrom.worktree.delete_branch", return_value=(True, False)):
            result = sync_worktree(worktree_path, skip_fetch=True, close_if_empty=True)

        assert result.success is False
        assert result.closed is True
        assert result.deleted_remote is False
        assert "origin/feature/work" in result.message
        assert "failed" in result.message

    def test_empty_branch_without_flag_is_preserved(self, project_with_worktree):
        """Default (close_if_empty=False) preserves current behaviour."""
        project_path, worktree_path, remote_path = project_with_worktree
        _push_branch(worktree_path, "feature/work")

        result = sync_worktree(worktree_path, skip_fetch=True)

        assert result.closed is False
        # Branch still present, not detached.
        assert not _is_detached(worktree_path)
        local = run_git(project_path, "rev-parse", "--verify", "feature/work", check=False)
        assert local.returncode == 0


def _current_head_of_ref(path: Path, ref: str) -> str:
    return run_git(path, "rev-parse", ref).stdout.strip()


# ---------------------------------------------------------------------------
# Refactor regression: _detach_and_free_ports + close_worktree
# ---------------------------------------------------------------------------


class TestDetachAndFreePorts:
    """`_detach_and_free_ports` and the refactored `close_worktree` tail."""

    def test_detach_and_free_ports_direct(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        record_port_allocation(project_path, "alpha", 350)

        result = _detach_and_free_ports(worktree_path)

        assert isinstance(result, CloseResult)
        assert result.success is True
        assert _is_detached(worktree_path)
        assert _current_head(worktree_path) == _current_head_of_ref(project_path, "origin/main")
        assert get_port_allocation(project_path, "alpha") is None

    def test_close_worktree_happy_path(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        record_port_allocation(project_path, "alpha", 350)

        result = close_worktree(worktree_path)

        assert result.success is True
        assert _is_detached(worktree_path)
        assert get_port_allocation(project_path, "alpha") is None

    def test_close_worktree_dirty_files(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        (worktree_path / "dirty.txt").write_text("uncommitted\n")

        result = close_worktree(worktree_path)

        assert result.success is False
        assert result.had_dirty_files is True
        assert not _is_detached(worktree_path)

    def test_close_worktree_commits_ahead(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "ahead.txt", "ahead\n", "Unmerged commit")

        result = close_worktree(worktree_path)

        assert result.success is False
        assert result.had_unpushed_commits is True
        assert not _is_detached(worktree_path)


# ---------------------------------------------------------------------------
# CLI: cmd_sync via CliRunner (sync_worktree mocked)
# ---------------------------------------------------------------------------


class TestSyncCli:
    """`mael sync` flag handling via CliRunner."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, sync_result):
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.sync_worktree", return_value=sync_result) as mock_sync:
            result = runner.invoke(cli, ["sync", "myproject.alpha", *args])
        return result, mock_sync

    def test_abort_on_conflict_short_message(self):
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="Rebase of feature/work onto origin/main hit conflicts; "
                    "aborted and restored worktree to its previous state.",
            had_conflicts=True,
            aborted=True,
        )
        result, mock_sync = self._run(["--abort"], sync_result)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        assert "git rebase --continue" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["abort_on_conflict"] is True

    def test_conflict_without_abort_shows_help(self):
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="CONFLICT",
            had_conflicts=True,
            aborted=False,
            merge_base="abc1234",
            upstream_head="def5678",
        )
        result, _ = self._run([], sync_result)

        assert result.exit_code == 1
        # The multi-line resolution help mentions rebase --continue.
        assert "git rebase --continue" in result.output

    def test_close_empty_branch_no_push_line(self):
        sync_result = SyncResult(
            success=True,
            branch="feature/work",
            message="feature/work is empty (merged into origin/main); "
                    "deleted branch (local + remote) and closed worktree.",
            closed=True,
            deleted_remote=True,
        )
        result, mock_sync = self._run(["--close"], sync_result)

        assert result.exit_code == 0
        assert "closed worktree" in result.output
        assert "Pushed" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["close_if_empty"] is True

    def test_close_non_empty_branch_normal_push(self):
        sync_result = SyncResult(
            success=True,
            branch="feature/work",
            message="Successfully rebased feature/work onto origin/main",
            closed=False,
            pushed=True,
            push_message="Pushed feature/work to origin",
        )
        result, _ = self._run(["--close"], sync_result)

        assert result.exit_code == 0
        assert "Pushed feature/work to origin" in result.output

    def test_abort_and_close_on_conflict_aborts_cleanly(self):
        """--abort --close: a conflict aborts; close/delete is never attempted."""
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="Rebase of feature/work onto origin/main hit conflicts; "
                    "aborted and restored worktree to its previous state.",
            had_conflicts=True,
            aborted=True,
        )
        result, mock_sync = self._run(["--abort", "--close"], sync_result)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        # closed/deleted never reported; both flags forwarded.
        assert "closed worktree" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["abort_on_conflict"] is True
        assert kwargs["close_if_empty"] is True
