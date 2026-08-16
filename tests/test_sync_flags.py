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

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from maelstrom.base_store import InMemoryBaseStore
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
    get_current_branch,
    setup_worktree_for_branch,
    squash_worktree,
    squash_worktree_with_autorepair,
    sync_worktree,
    sync_worktree_with_autorepair,
)
from maelstrom.worktree_model import BaseRef, StackTip
from maelstrom.worktree import rebase_in_progress as _rebase_in_progress
from maelstrom.rebase_repair import _REPAIR_TIMEOUT, run_resolve_rebase_session

from tests.git_helpers import create_commit, run_git, setup_git_repo


# ---------------------------------------------------------------------------
# Real-git fixtures
# ---------------------------------------------------------------------------


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


def _make_conflict(project_path: Path, worktree_path: Path, remote_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# squash_worktree(abort_on_conflict=…)
# ---------------------------------------------------------------------------


class TestSquashAbort:
    """`squash_worktree(abort_on_conflict=…)`."""

    def _make_conflict(self, project_path, worktree_path, remote_path):
        _make_conflict(project_path, worktree_path, remote_path)

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
# sync_worktree_with_autorepair
# ---------------------------------------------------------------------------


def _repairing_runner(worktree_path: Path):
    """A stub repair session that actually resolves the README conflict."""
    (worktree_path / "README.md").write_text("# Merged version\n")
    run_git(worktree_path, "add", "README.md")
    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=worktree_path, check=True, capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    return subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")


def _idle_runner(worktree_path: Path):
    """A stub repair session that exits cleanly without touching the rebase."""
    return subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")


class TestRunResolveRebaseSession:
    """The real repair runner. Every other test substitutes its own."""

    def test_runs_the_resolve_command_headlessly_in_the_worktree(self, tmp_path):
        with patch("maelstrom.rebase_repair.run_cmd") as run:
            run_resolve_rebase_session(tmp_path)

        argv, kwargs = run.call_args.args[0], run.call_args.kwargs
        assert argv[0] == "claude"
        assert "-p" in argv
        assert "/resolve-rebase-conflicts" in argv
        # Unattended: the session must not stop to ask for permission, and must
        # not load project MCP servers.
        assert argv[argv.index("--permission-mode") + 1] == "auto"
        assert "--strict-mcp-config" in argv
        # It runs in the worktree, so it sees the mid-rebase tree.
        assert kwargs["cwd"] == tmp_path
        # A non-zero exit is a result to inspect, not an exception to raise.
        assert kwargs["check"] is False
        assert kwargs["timeout"] == _REPAIR_TIMEOUT

    def test_streams_session_output_to_the_console(self, tmp_path):
        """The repair can run for minutes: the user watches it work.

        Capturing the output would hold it until the session exits, so the
        console stays silent for the whole repair.
        """
        with patch("maelstrom.rebase_repair.run_cmd") as run:
            run_resolve_rebase_session(tmp_path)

        assert run.call_args.kwargs["stream"] is True


class TestSyncAutorepair:
    """`sync_worktree_with_autorepair` against real git, repair session stubbed."""

    def test_successful_repair_completes_the_rebase_and_pushes(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _push_branch(worktree_path, "feature/work")
        _make_conflict(project_path, worktree_path, remote_path)

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=_repairing_runner,
        )

        assert result.success is True, result.message
        assert result.repaired is True
        assert not _rebase_in_progress(worktree_path)
        assert get_current_branch(worktree_path) == "feature/work"
        # The branch was pushed: origin now matches the rebased local tip.
        assert result.pushed is True
        run_git(worktree_path, "fetch", "origin")
        assert _current_head_of_ref(worktree_path, "origin/feature/work") == _current_head(
            worktree_path
        )

    def test_announces_the_repair_before_the_session_starts(
        self, project_with_worktree, capsys
    ):
        """The session streams its own output, so say what started it.

        Without this the console jumps from a sync line to raw Claude output
        with nothing to explain why an agent is running.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)

        seen = []

        def announcing_runner(path):
            # Drain on the way in, so what is captured is what the console had
            # before the session started — not a summary printed afterwards.
            seen.append(capsys.readouterr().out)
            return _repairing_runner(path)

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=announcing_runner,
        )

        assert result.success is True, result.message
        assert "Starting autorepair" in seen[0]

    def test_the_caller_can_supply_its_own_announce(self, project_with_worktree):
        """The model layer stays click-free.

        The CLI passes ``click.echo`` so the line goes out the same way as every
        other command message, without ``worktree.py`` importing click.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        lines = []

        result = sync_worktree_with_autorepair(
            worktree_path,
            skip_fetch=True,
            repair_runner=_repairing_runner,
            announce=lines.append,
        )

        assert result.success is True, result.message
        assert any("Starting autorepair" in line for line in lines)

    def test_repair_that_does_nothing_aborts_and_restores(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        head_before = _current_head(worktree_path)

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=_idle_runner,
        )

        assert result.success is False
        assert result.aborted is True
        assert result.repaired is False
        assert not _rebase_in_progress(worktree_path)
        assert _current_head(worktree_path) == head_before

    def test_repair_session_failure_aborts(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        head_before = _current_head(worktree_path)

        def failing(path):
            return subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="boom")

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=failing,
        )

        assert result.success is False
        assert result.aborted is True
        assert not _rebase_in_progress(worktree_path)
        assert _current_head(worktree_path) == head_before

    def test_repair_that_raises_aborts_the_rebase(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        head_before = _current_head(worktree_path)

        def exploding(path):
            raise OSError("claude: not found")

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=exploding,
        )

        assert result.success is False
        assert result.aborted is True
        assert "claude: not found" in result.message
        assert not _rebase_in_progress(worktree_path)
        assert _current_head(worktree_path) == head_before

    def test_repair_leaving_the_wrong_branch_fails_naming_the_branch(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)

        def wanders(path):
            _repairing_runner(path)
            run_git(path, "checkout", "-b", "some/other-branch")
            return subprocess.CompletedProcess(args=["claude"], returncode=0, stdout="", stderr="")

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=wanders,
        )

        assert result.success is False
        # Both branches are named: the one expected, and the one it is on now —
        # without the latter the user cannot tell what to check back out.
        assert "feature/work" in result.message
        assert "some/other-branch" in result.message

    def test_clean_rebase_never_calls_the_repair_runner(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        _advance_origin_main(project_path, remote_path)
        runner = MagicMock()

        result = sync_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=runner,
        )

        assert result.success is True
        assert result.repaired is False
        runner.assert_not_called()

    def test_rebase_failing_without_a_conflict_never_calls_the_repair_runner(
        self, project_with_worktree
    ):
        """A rebase can fail with no conflict, leaving no rebase in progress.

        ``squash_worktree`` reports ``had_conflicts`` for any non-zero rebase
        exit, so the conflict flag alone must not trigger a repair session:
        there would be nothing for it to resolve.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        _advance_origin_main(project_path, remote_path)
        runner = MagicMock()

        # A stale index.lock makes git refuse to rebase at all: the rebase exits
        # non-zero, and no rebase is left in progress.
        lock = Path(
            run_git(worktree_path, "rev-parse", "--git-path", "index.lock").stdout.strip()
        )
        if not lock.is_absolute():
            lock = worktree_path / lock
        lock.write_text("")
        try:
            result = sync_worktree_with_autorepair(
                worktree_path, skip_fetch=True, repair_runner=runner,
            )
        finally:
            lock.unlink(missing_ok=True)

        assert result.success is False
        runner.assert_not_called()
        assert not _rebase_in_progress(worktree_path)

    def test_fetch_failure_blocks_before_any_repair(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        run_git(worktree_path, "remote", "set-url", "origin", str(project_path / "nonexistent.git"))
        runner = MagicMock()

        result = sync_worktree_with_autorepair(worktree_path, repair_runner=runner)

        assert result.success is False
        assert result.had_conflicts is False
        assert "fetch" in result.message.lower()
        runner.assert_not_called()


class TestSquashAutorepair:
    """`squash_worktree_with_autorepair`: repair a rebase, and never push."""

    def test_successful_repair_completes_the_rebase_without_pushing(
        self, project_with_worktree
    ):
        """The squash variant rebases only.

        `mael git squash` publishes nothing, so a repaired rebase must leave
        origin where it was.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        _push_branch(worktree_path, "feature/work")
        origin_before = _current_head_of_ref(worktree_path, "origin/feature/work")
        _make_conflict(project_path, worktree_path, remote_path)

        result = squash_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=_repairing_runner,
        )

        assert result.success is True, result.message
        assert result.repaired is True
        assert not _rebase_in_progress(worktree_path)
        assert get_current_branch(worktree_path) == "feature/work"
        # Nothing was published: origin still points where it did.
        assert result.pushed is False
        run_git(worktree_path, "fetch", "origin")
        assert _current_head_of_ref(worktree_path, "origin/feature/work") == origin_before
        # A successful result never reports conflicts: the repair resolved them,
        # and a caller reading the flag would see a conflict that is gone.
        assert result.had_conflicts is False

    def test_repair_that_does_nothing_aborts_and_restores(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        head_before = _current_head(worktree_path)

        result = squash_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=_idle_runner,
        )

        assert result.success is False
        assert result.aborted is True
        assert result.repaired is False
        assert not _rebase_in_progress(worktree_path)
        assert _current_head(worktree_path) == head_before

    def test_clean_rebase_never_calls_the_repair_runner(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        _advance_origin_main(project_path, remote_path)
        runner = MagicMock()

        result = squash_worktree_with_autorepair(
            worktree_path, skip_fetch=True, repair_runner=runner,
        )

        assert result.success is True
        assert result.repaired is False
        runner.assert_not_called()


# ---------------------------------------------------------------------------
# setup_worktree_for_branch: sync when the worktree is opened
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_finalize():
    """Stub the finalize side-effects so worktrees stay clean for real-git assertions."""
    with patch("maelstrom.worktree.update_claude_local_md", return_value=False), \
            patch("maelstrom.worktree.run_install_cmd"), \
            patch("maelstrom.worktree.setup_claude_memory_symlink"):
        yield


class TestSetupWorktreeSyncOnOpen:
    """`setup_worktree_for_branch` syncs when it opens a worktree, not when it reuses one."""

    def _stale_branch(self, project_path, worktree_path, remote_path, branch="feature/stale"):
        """Push ``branch`` from the alpha worktree, then advance origin/main past it.

        Leaves the branch checked out nowhere: the alpha worktree goes back to a
        detached HEAD so ``setup_worktree_for_branch`` is free to claim the branch.
        """
        run_git(worktree_path, "checkout", "-b", branch)
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        _push_branch(worktree_path, branch)
        run_git(worktree_path, "checkout", "--detach", "origin/main")
        run_git(worktree_path, "branch", "-D", branch)
        run_git(worktree_path, "fetch", "origin")
        _advance_origin_main(project_path, remote_path)
        return branch

    def test_created_worktree_is_rebased_and_pushed(
        self, project_with_worktree, quiet_finalize
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._stale_branch(project_path, worktree_path, remote_path)

        result = setup_worktree_for_branch(
            project_path, "test-repo", branch, no_recycle=True, run_install=False,
        )

        assert result.action == "created"
        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        # origin/main is now an ancestor of the branch tip: it was rebased.
        merged = run_git(
            result.path, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False,
        )
        assert merged.returncode == 0
        assert result.sync.pushed is True

    def test_recycled_worktree_is_rebased(self, project_with_worktree, quiet_finalize):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._stale_branch(project_path, worktree_path, remote_path)
        # The alpha worktree is detached, clean and at origin/main → recyclable.

        result = setup_worktree_for_branch(
            project_path, "test-repo", branch, run_install=False,
        )

        assert result.action == "recycled"
        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        merged = run_git(
            result.path, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False,
        )
        assert merged.returncode == 0

    def test_reused_worktree_is_never_synced(self, project_with_worktree, quiet_finalize):
        project_path, worktree_path, remote_path = project_with_worktree
        # The fixture's alpha worktree already has feature/work checked out.
        with patch("maelstrom.worktree.sync_worktree_with_autorepair") as sync:
            result = setup_worktree_for_branch(
                project_path, "test-repo", "feature/work", run_install=False,
            )

        assert result.action == "reused"
        assert result.sync is None
        sync.assert_not_called()

    def test_brand_new_branch_is_synced_but_never_closed(
        self, project_with_worktree, quiet_finalize
    ):
        """A branch with no commits is 'empty' — close_if_empty must stay off."""
        project_path, worktree_path, remote_path = project_with_worktree

        result = setup_worktree_for_branch(
            project_path, "test-repo", "feature/brand-new",
            no_recycle=True, run_install=False,
        )

        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        assert result.sync.closed is False
        assert result.sync.pushed is False  # no origin/<branch> to push to
        assert get_current_branch(result.path) == "feature/brand-new"

    def test_conflicting_branch_with_failed_repair_blocks_and_aborts(
        self, project_with_worktree, quiet_finalize
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._conflicting_branch(project_path, worktree_path, remote_path)

        with patch(
            "maelstrom.worktree.run_resolve_rebase_session", side_effect=_idle_runner,
        ):
            result = setup_worktree_for_branch(
                project_path, "test-repo", branch, no_recycle=True, run_install=False,
            )

        assert result.sync is not None
        assert result.sync.success is False
        assert result.sync.aborted is True
        assert not _rebase_in_progress(result.path)
        # No conflict residue: tracked files are clean (the generated .env is
        # untracked maelstrom state, not rebase leftovers).
        tracked = run_git(result.path, "status", "--porcelain", "--untracked-files=no")
        assert tracked.stdout.strip() == ""

    def test_a_blocked_worktree_is_still_finalized(self, project_with_worktree):
        """A failed sync must not leave the worktree without CLAUDE.local.md.

        The user's documented recovery is `mael sync --autorepair` in the
        worktree, which rebases but never finalizes. If finalize were skipped
        here, nothing short of another `mael add` would ever write it.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._conflicting_branch(project_path, worktree_path, remote_path)

        with patch("maelstrom.worktree.setup_claude_memory_symlink"), \
                patch("maelstrom.worktree.run_install_cmd"), \
                patch(
                    "maelstrom.worktree.update_claude_local_md", return_value=False,
                ) as local_md, \
                patch(
                    "maelstrom.worktree.run_resolve_rebase_session",
                    side_effect=_idle_runner,
                ):
            result = setup_worktree_for_branch(
                project_path, "test-repo", branch, no_recycle=True, run_install=False,
            )

        assert result.sync is not None and result.sync.success is False
        local_md.assert_called_once()

    def test_conflicting_branch_with_successful_repair_reports_repaired(
        self, project_with_worktree, quiet_finalize
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._conflicting_branch(project_path, worktree_path, remote_path)

        with patch(
            "maelstrom.worktree.run_resolve_rebase_session", side_effect=_repairing_runner,
        ):
            result = setup_worktree_for_branch(
                project_path, "test-repo", branch, no_recycle=True, run_install=False,
            )

        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        assert result.sync.repaired is True

    def _conflicting_branch(self, project_path, worktree_path, remote_path):
        """A pushed branch whose README edit conflicts with a later origin/main edit."""
        branch = "feature/conflicting"
        run_git(worktree_path, "checkout", "-b", branch)
        (worktree_path / "README.md").write_text("# Feature version\n")
        run_git(worktree_path, "add", "README.md")
        run_git(worktree_path, "commit", "-m", "Feature README")
        _push_branch(worktree_path, branch)
        run_git(worktree_path, "checkout", "--detach", "origin/main")
        run_git(worktree_path, "branch", "-D", branch)

        # Upstream edits the same line.
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
        run_git(worktree_path, "fetch", "origin")
        return branch


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


class TestSyncBaseCli:
    """`mael sync --base` — set, change, and clear a branch's base."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, *, bases=None, branch="feature/work"):
        """Invoke `mael sync` with an in-memory base store."""
        store = InMemoryBaseStore()
        for child, parent in (bases or {}).items():
            store.write(child, BaseRef(branch=parent))
        sync_result = SyncResult(
            success=True,
            branch=branch,
            message=f"Successfully rebased {branch} onto origin/main",
        )
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value=branch), \
             patch("maelstrom.cli.check_base_exists"), \
             patch("maelstrom.cli.sync_worktree", return_value=sync_result):
            result = runner.invoke(cli, ["sync", "myproject.alpha", *args])
        return result, store

    def test_base_sets_the_base_before_syncing(self):
        result, store = self._run(["--base", "feat/parent"])

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/parent")

    def test_changing_the_base_clears_the_stale_tip(self):
        """The old tip points into the old base's history; keeping it would misreplay."""
        store = InMemoryBaseStore()
        store.write("feature/work", BaseRef(branch="feat/one", tip="abc123"))
        sync_result = SyncResult(success=True, branch="feature/work", message="ok")
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value="feature/work"), \
             patch("maelstrom.cli.check_base_exists"), \
             patch("maelstrom.cli.sync_worktree", return_value=sync_result):
            result = runner.invoke(cli, ["sync", "--base", "feat/two"])

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/two", tip=None)

    def test_base_main_clears_the_base(self):
        result, store = self._run(["--base", "main"], bases={"feature/work": "feat/parent"})

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef()
        assert store.all() == {}

    def test_self_base_exits_one_with_the_validation_message(self):
        result, store = self._run(["--base", "feature/work"])

        assert result.exit_code == 1
        assert "cannot be based on itself" in result.output
        assert store.all() == {}

    def test_a_cyclic_base_exits_one(self):
        result, store = self._run(["--base", "feat/a"], bases={"feat/a": "feature/work"})

        assert result.exit_code == 1
        assert "Cycle" in result.output
        # The rejected base is not written, so the store is left as it was.
        assert store.all() == {"feat/a": "feature/work"}

    def test_a_base_that_does_not_exist_exits_one(self):
        """A typo must be refused, not accepted and silently collapsed later."""
        store = InMemoryBaseStore()
        sync_result = SyncResult(success=True, branch="feature/work", message="ok")
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value="feature/work"), \
             patch("maelstrom.cli.check_base_exists",
                   side_effect=ValueError("No such branch to stack on: feat/typo.")), \
             patch("maelstrom.cli.sync_worktree", return_value=sync_result):
            result = runner.invoke(cli, ["sync", "--base", "feat/typo"])

        assert result.exit_code == 1
        assert "No such branch to stack on" in result.output
        assert store.all() == {}

    def test_no_base_flag_leaves_the_store_untouched(self):
        """A plain `mael sync` must not write anything."""
        result, store = self._run([], bases={"feature/work": "feat/parent"})

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/parent")


class TestBaseCli:
    """`mael base` — show the current branch's base."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, bases, branch="feature/work"):
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value=branch):
            return runner.invoke(cli, ["base"]), store

    def test_shows_the_stacked_base(self):
        result, _ = self._run({"feature/work": "feat/parent"})

        assert result.exit_code == 0
        assert "feat/parent" in result.output

    def test_an_unstacked_branch_shows_main(self):
        result, _ = self._run({})

        assert result.exit_code == 0
        assert "main" in result.output


class TestStackTipCli:
    """`mael stack-tip` — show or move where new work stacks."""

    def _ctx(self, project_path=None):
        mock_ctx = MagicMock()
        mock_ctx.project = "myproject"
        mock_ctx.project_path = project_path or MagicMock()
        mock_ctx.project_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, *, tip="main", bases=None):
        store = InMemoryBaseStore()
        store.write_stack_tip(tip)
        for child, parent in (bases or {}).items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.check_base_exists"), \
             patch("maelstrom.cli.current_stack_tip", return_value=StackTip(tip)):
            result = runner.invoke(cli, ["stack-tip", *args])
        return result, store

    def test_with_no_argument_it_shows_the_tip(self):
        result, store = self._run([], tip="feat/parent")

        assert result.exit_code == 0
        assert "feat/parent" in result.output
        assert store.read_stack_tip() == "feat/parent"

    def test_it_moves_the_tip_to_a_named_branch(self):
        result, store = self._run(["feat/other"], tip="feat/parent")

        assert result.exit_code == 0
        assert store.read_stack_tip() == "feat/other"

    def test_a_tip_that_does_not_exist_exits_one(self):
        """A tip naming no branch would be healed back to main at the next add."""
        store = InMemoryBaseStore()
        store.write_stack_tip("main")
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.check_base_exists",
                   side_effect=ValueError("No such branch to stack on: feat/typo.")):
            result = runner.invoke(cli, ["stack-tip", "feat/typo"])

        assert result.exit_code == 1
        assert store.read_stack_tip() == "main"

    def test_main_resets_the_tip_to_the_bottom(self):
        """The one-command escape from a deepening stack."""
        result, store = self._run(["main"], tip="feat/parent")

        assert result.exit_code == 0
        assert store.read_stack_tip() == "main"


class TestPromoteAndEjectCli:
    """`mael promote` and `mael eject` — the urgent-PR escape hatches."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.project = "myproject"
        mock_ctx.worktree = "alpha"
        mock_ctx.project_path = MagicMock()
        mock_ctx.project_path.exists.return_value = True
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, command, args, bases, branch="feat/urgent"):
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value=branch):
            result = runner.invoke(cli, [command, *args])
        return result, store

    def test_promote_moves_the_branch_to_the_bottom(self):
        """Merge order is enforced bottom-up, so an urgent PR must be able to jump."""
        result, store = self._run(
            "promote", [], {"feat/urgent": "feat/parent", "feat/parent": "main"}
        )

        assert result.exit_code == 0
        assert store.read("feat/urgent") == BaseRef()

    def test_promote_repoints_the_branches_that_were_based_on_it(self):
        """Otherwise a child would be stacked on a branch that left from under it."""
        result, store = self._run(
            "promote",
            [],
            {"feat/urgent": "feat/parent", "feat/child": "feat/urgent"},
        )

        assert result.exit_code == 0
        assert store.read("feat/child").branch == "feat/parent"

    def test_promote_clears_a_repointed_childs_stale_tip(self):
        store = InMemoryBaseStore()
        store.write("feat/urgent", BaseRef(branch="feat/parent"))
        store.write("feat/child", BaseRef(branch="feat/urgent", tip="abc123"))
        runner = CliRunner()
        with patch("maelstrom.cli.resolve_context", return_value=self._ctx()), \
             patch("maelstrom.cli.GitConfigBaseStore", return_value=store), \
             patch("maelstrom.cli.get_current_branch", return_value="feat/urgent"):
            result = runner.invoke(cli, ["promote"])

        assert result.exit_code == 0
        assert store.read("feat/child").tip is None

    def test_promote_on_an_unstacked_branch_is_a_no_op(self):
        result, store = self._run("promote", [], {})

        assert result.exit_code == 0
        assert store.all() == {}

    def test_eject_unstacks_the_branch_and_leaves_the_rest_alone(self):
        """Eject is promote without the re-point: pull one branch out, touch nothing else."""
        result, store = self._run(
            "eject", [], {"feat/urgent": "feat/parent", "feat/child": "feat/urgent"}
        )

        assert result.exit_code == 0
        assert store.read("feat/urgent") == BaseRef()
        assert store.read("feat/child").branch == "feat/urgent"
