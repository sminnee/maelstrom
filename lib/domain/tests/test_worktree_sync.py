"""Real-git tests for syncing a worktree: rebase, push, close, autorepair.

Covers ``rebase_worktree`` and ``sync_worktree`` with their ``abort_on_conflict``
and ``close_if_empty`` flags, the autorepair wrappers, and the sync
``setup_worktree_for_branch`` runs when it opens a worktree. The repo follows the
source → bare-remote → working-clone pattern of ``project_with_worktree``. The
``mael sync`` flag handling is tested in ``cli/tests/test_sync_flags.py``.
"""

import math
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from git_helpers import (
    branch_exists,
    create_commit,
    is_detached,
    make_conflict,
    push_branch,
    push_to_origin_main,
    rev_parse,
    run_git,
)

from mael_domain.ports import get_port_allocation, record_port_allocation
from mael_domain.rebase_repair import _REPAIR_TIMEOUT, run_resolve_rebase_session
from mael_domain.task_launch import LaunchBlocked, check_synced
from mael_domain.worktree import (
    get_current_branch,
    rebase_in_progress,
    rebase_worktree,
    rebase_worktree_with_autorepair,
    setup_worktree_for_branch,
    sync_worktree,
    sync_worktree_with_autorepair,
)

# ---------------------------------------------------------------------------
# rebase_worktree(abort_on_conflict=…)
# ---------------------------------------------------------------------------


class TestSquashAbort:
    """`rebase_worktree(abort_on_conflict=…)`."""

    def test_conflict_with_abort_restores_worktree(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        head_before = rev_parse(worktree_path)

        result = rebase_worktree(
            worktree_path, max_age=math.inf, abort_on_conflict=True
        )

        assert result.success is False
        assert result.had_conflicts is True
        assert result.aborted is True
        assert not rebase_in_progress(worktree_path)
        assert rev_parse(worktree_path) == head_before

    def test_conflict_default_leavesrebase_in_progress(self, project_with_worktree):
        """Regression guard: without the flag, the rebase is left in progress."""
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)

        result = rebase_worktree(worktree_path, max_age=math.inf)

        assert result.success is False
        assert result.had_conflicts is True
        assert result.aborted is False
        assert rebase_in_progress(worktree_path)

        # Clean up the in-progress rebase so the fixture teardown is happy.
        run_git(worktree_path, "rebase", "--abort", check=False)

    def test_clean_rebase_with_abort_still_succeeds(self, project_with_worktree):
        """abort_on_conflict is a no-op when there is no conflict."""
        project_path, worktree_path, remote_path = project_with_worktree
        # A non-conflicting feature commit + an unrelated upstream commit.
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        push_to_origin_main(project_path, remote_path)

        result = rebase_worktree(
            worktree_path, max_age=math.inf, abort_on_conflict=True
        )

        assert result.success is True
        assert result.aborted is False
        assert not rebase_in_progress(worktree_path)


# ---------------------------------------------------------------------------
# sync_worktree(close_if_empty=…)
# ---------------------------------------------------------------------------


class TestSyncClose:
    """`sync_worktree(close_if_empty=…)` against a real remote."""

    def test_empty_branch_with_remote_is_deleted_and_closed(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        # Branch is empty (HEAD == origin/main) and exists on the remote.
        push_branch(worktree_path, "feature/work")
        record_port_allocation(project_path, "alpha", 350)

        result = sync_worktree(worktree_path, max_age=math.inf, close_if_empty=True)

        assert result.success is True
        assert result.closed is True
        assert result.deleted_remote is True
        # Local + remote branch gone.
        assert not branch_exists(project_path, "feature/work")
        assert not branch_exists(project_path, "origin/feature/work")
        # HEAD detached at origin/main, ports freed.
        assert is_detached(worktree_path)
        assert rev_parse(worktree_path) == rev_parse(project_path, "origin/main")
        assert get_port_allocation(project_path, "alpha") is None

    def test_empty_local_only_branch_is_never_pushed(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Branch empty, NOT on the remote (only main was ever pushed).
        record_port_allocation(project_path, "alpha", 350)

        result = sync_worktree(worktree_path, max_age=math.inf, close_if_empty=True)

        assert result.success is True
        assert result.closed is True
        assert result.deleted_remote is False
        # Local branch deleted; remote only ever had main.
        assert not branch_exists(project_path, "feature/work")
        assert not branch_exists(project_path, "origin/feature/work")
        assert get_port_allocation(project_path, "alpha") is None

    def test_non_empty_branch_is_pushed_not_closed(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Push the branch, then add a real commit so it's ahead of origin/main.
        push_branch(worktree_path, "feature/work")
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")

        result = sync_worktree(worktree_path, max_age=math.inf, close_if_empty=True)

        assert result.success is True
        assert result.closed is False
        assert result.pushed is True
        # Branch + HEAD intact (still on feature/work, not detached).
        assert not is_detached(worktree_path)
        assert (
            run_git(worktree_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            == "feature/work"
        )
        # The commit reached the remote.
        run_git(project_path, "fetch", "origin")
        assert branch_exists(project_path, "origin/feature/work")

    def test_failed_local_delete_is_reported(self, project_with_worktree):
        """A failed `git branch -D` is surfaced, not silently claimed as deleted."""
        project_path, worktree_path, remote_path = project_with_worktree

        with patch("mael_domain.worktree.delete_branch", return_value=(False, False)):
            result = sync_worktree(worktree_path, max_age=math.inf, close_if_empty=True)

        assert result.success is False
        # Worktree still closed (detach already happened before the delete).
        assert result.closed is True
        assert "deleting the local branch failed" in result.message
        assert is_detached(worktree_path)

    def test_failed_remote_delete_is_reported(self, project_with_worktree):
        """A failed remote delete (when a remote branch existed) is surfaced."""
        project_path, worktree_path, remote_path = project_with_worktree
        push_branch(worktree_path, "feature/work")  # so delete_remote is attempted

        with patch("mael_domain.worktree.delete_branch", return_value=(True, False)):
            result = sync_worktree(worktree_path, max_age=math.inf, close_if_empty=True)

        assert result.success is False
        assert result.closed is True
        assert result.deleted_remote is False
        assert "origin/feature/work" in result.message
        assert "failed" in result.message

    def test_empty_branch_without_flag_is_preserved(self, project_with_worktree):
        """Default (close_if_empty=False) preserves current behaviour."""
        project_path, worktree_path, remote_path = project_with_worktree
        push_branch(worktree_path, "feature/work")

        result = sync_worktree(worktree_path, max_age=math.inf)

        assert result.closed is False
        # Branch still present, not detached.
        assert not is_detached(worktree_path)
        assert branch_exists(project_path, "feature/work")


# ---------------------------------------------------------------------------
# sync_worktree_with_autorepair
# ---------------------------------------------------------------------------


def _repairing_runner(worktree_path: Path):
    """A stub repair session that actually resolves the README conflict."""
    (worktree_path / "README.md").write_text("# Merged version\n")
    run_git(worktree_path, "add", "README.md")
    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=worktree_path,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    return subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout="", stderr=""
    )


def _idle_runner(worktree_path: Path):
    """A stub repair session that exits cleanly without touching the rebase."""
    return subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout="", stderr=""
    )


class TestRunResolveRebaseSession:
    """The real repair runner. Every other test substitutes its own."""

    def test_runs_the_resolve_command_headlessly_in_the_worktree(self, tmp_path):
        with patch("mael_domain.rebase_repair.run_cmd") as run:
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
        with patch("mael_domain.rebase_repair.run_cmd") as run:
            run_resolve_rebase_session(tmp_path)

        assert run.call_args.kwargs["stream"] is True


class TestSyncAutorepair:
    """`sync_worktree_with_autorepair` against real git, repair session stubbed."""

    def test_successful_repair_completes_the_rebase_and_pushes(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        push_branch(worktree_path, "feature/work")
        make_conflict(project_path, worktree_path, remote_path)

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=_repairing_runner,
        )

        assert result.success is True, result.message
        assert result.repaired is True
        assert not rebase_in_progress(worktree_path)
        assert get_current_branch(worktree_path) == "feature/work"
        # The branch was pushed: origin now matches the rebased local tip.
        assert result.pushed is True
        run_git(worktree_path, "fetch", "origin")
        assert rev_parse(worktree_path, "origin/feature/work") == rev_parse(
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
        make_conflict(project_path, worktree_path, remote_path)

        seen = []

        def announcing_runner(path):
            # Drain on the way in, so what is captured is what the console had
            # before the session started — not a summary printed afterwards.
            seen.append(capsys.readouterr().out)
            return _repairing_runner(path)

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=announcing_runner,
        )

        assert result.success is True, result.message
        assert "Starting autorepair" in seen[0]

    def test_the_caller_can_supply_its_own_announce(self, project_with_worktree):
        """The model layer stays click-free.

        The CLI passes ``click.echo`` so the line goes out the same way as every
        other command message, without ``worktree.py`` importing click.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        lines = []

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=_repairing_runner,
            announce=lines.append,
        )

        assert result.success is True, result.message
        assert any("Starting autorepair" in line for line in lines)

    def test_repair_that_does_nothing_aborts_and_restores(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        head_before = rev_parse(worktree_path)

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=_idle_runner,
        )

        assert result.success is False
        assert result.aborted is True
        assert result.repaired is False
        assert not rebase_in_progress(worktree_path)
        assert rev_parse(worktree_path) == head_before

    def test_repair_session_failure_aborts(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        head_before = rev_parse(worktree_path)

        def failing(path):
            return subprocess.CompletedProcess(
                args=["claude"], returncode=1, stdout="", stderr="boom"
            )

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=failing,
        )

        assert result.success is False
        assert result.aborted is True
        assert not rebase_in_progress(worktree_path)
        assert rev_parse(worktree_path) == head_before

    def test_repair_that_raises_aborts_the_rebase(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        head_before = rev_parse(worktree_path)

        def exploding(path):
            raise OSError("claude: not found")

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=exploding,
        )

        assert result.success is False
        assert result.aborted is True
        assert "claude: not found" in result.message
        assert not rebase_in_progress(worktree_path)
        assert rev_parse(worktree_path) == head_before

    def test_repair_leaving_the_wrong_branch_fails_naming_the_branch(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)

        def wanders(path):
            _repairing_runner(path)
            run_git(path, "checkout", "-b", "some/other-branch")
            return subprocess.CompletedProcess(
                args=["claude"], returncode=0, stdout="", stderr=""
            )

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=wanders,
        )

        assert result.success is False
        # Both branches are named: the one expected, and the one it is on now —
        # without the latter the user cannot tell what to check back out.
        assert "feature/work" in result.message
        assert "some/other-branch" in result.message

    def test_clean_rebase_never_calls_the_repair_runner(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        push_to_origin_main(project_path, remote_path)
        runner = MagicMock()

        result = sync_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=runner,
        )

        assert result.success is True
        assert result.repaired is False
        runner.assert_not_called()

    def test_rebase_failing_without_a_conflict_never_calls_the_repair_runner(
        self, project_with_worktree
    ):
        """A rebase can fail with no conflict, leaving no rebase in progress.

        ``rebase_worktree`` reports ``had_conflicts`` for any non-zero rebase
        exit, so the conflict flag alone must not trigger a repair session:
        there would be nothing for it to resolve.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        push_to_origin_main(project_path, remote_path)
        runner = MagicMock()

        # A stale index.lock makes git refuse to rebase at all: the rebase exits
        # non-zero, and no rebase is left in progress.
        lock = Path(
            run_git(
                worktree_path, "rev-parse", "--git-path", "index.lock"
            ).stdout.strip()
        )
        if not lock.is_absolute():
            lock = worktree_path / lock
        lock.write_text("")
        try:
            result = sync_worktree_with_autorepair(
                worktree_path,
                max_age=math.inf,
                repair_runner=runner,
            )
        finally:
            lock.unlink(missing_ok=True)

        assert result.success is False
        runner.assert_not_called()
        assert not rebase_in_progress(worktree_path)

    def test_fetch_failure_blocks_before_any_repair(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        run_git(
            worktree_path,
            "remote",
            "set-url",
            "origin",
            str(project_path / "nonexistent.git"),
        )
        runner = MagicMock()

        result = sync_worktree_with_autorepair(worktree_path, repair_runner=runner)

        assert result.success is False
        assert result.had_conflicts is False
        assert "fetch" in result.message.lower()
        runner.assert_not_called()


class TestSquashAutorepair:
    """`rebase_worktree_with_autorepair`: repair a rebase, and never push."""

    def test_successful_repair_completes_the_rebase_without_pushing(
        self, project_with_worktree
    ):
        """The squash variant rebases only.

        `mael sync --no-push` publishes nothing, so a repaired rebase must leave
        origin where it was.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        push_branch(worktree_path, "feature/work")
        origin_before = rev_parse(worktree_path, "origin/feature/work")
        make_conflict(project_path, worktree_path, remote_path)

        result = rebase_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=_repairing_runner,
        )

        assert result.success is True, result.message
        assert result.repaired is True
        assert not rebase_in_progress(worktree_path)
        assert get_current_branch(worktree_path) == "feature/work"
        # Nothing was published: origin still points where it did.
        assert result.pushed is False
        run_git(worktree_path, "fetch", "origin")
        assert rev_parse(worktree_path, "origin/feature/work") == origin_before
        # A successful result never reports conflicts: the repair resolved them,
        # and a caller reading the flag would see a conflict that is gone.
        assert result.had_conflicts is False

    def test_repair_that_does_nothing_aborts_and_restores(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        make_conflict(project_path, worktree_path, remote_path)
        head_before = rev_parse(worktree_path)

        result = rebase_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=_idle_runner,
        )

        assert result.success is False
        assert result.aborted is True
        assert result.repaired is False
        assert not rebase_in_progress(worktree_path)
        assert rev_parse(worktree_path) == head_before

    def test_clean_rebase_never_calls_the_repair_runner(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "feature.txt", "feature\n", "Feature commit")
        push_to_origin_main(project_path, remote_path)
        runner = MagicMock()

        result = rebase_worktree_with_autorepair(
            worktree_path,
            max_age=math.inf,
            repair_runner=runner,
        )

        assert result.success is True
        assert result.repaired is False
        runner.assert_not_called()


# ---------------------------------------------------------------------------
# setup_worktree_for_branch: sync when the worktree is opened
# ---------------------------------------------------------------------------


class TestSetupWorktreeSyncOnOpen:
    """`setup_worktree_for_branch` syncs when it opens a worktree, not when it reuses one."""

    def _stale_branch(
        self, project_path, worktree_path, remote_path, branch="feature/stale"
    ):
        """Push ``branch`` from the alpha worktree, then advance origin/main past it.

        Leaves the branch checked out nowhere: the alpha worktree goes back to a
        detached HEAD so ``setup_worktree_for_branch`` is free to claim the branch.
        """
        run_git(worktree_path, "checkout", "-b", branch)
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        push_branch(worktree_path, branch)
        run_git(worktree_path, "checkout", "--detach", "origin/main")
        run_git(worktree_path, "branch", "-D", branch)
        run_git(worktree_path, "fetch", "origin")
        push_to_origin_main(project_path, remote_path)
        return branch

    def test_created_worktree_is_rebased_and_pushed(
        self, project_with_worktree, quiet_finalize
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._stale_branch(project_path, worktree_path, remote_path)

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            branch,
            no_recycle=True,
            run_install=False,
        )

        assert result.action == "created"
        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        # origin/main is now an ancestor of the branch tip: it was rebased.
        merged = run_git(
            result.path,
            "merge-base",
            "--is-ancestor",
            "origin/main",
            "HEAD",
            check=False,
        )
        assert merged.returncode == 0
        assert result.sync.pushed is True

    def test_recycled_worktree_is_rebased(self, project_with_worktree, quiet_finalize):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._stale_branch(project_path, worktree_path, remote_path)
        # The alpha worktree is detached, clean and at origin/main → recyclable.

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            branch,
            run_install=False,
        )

        assert result.action == "recycled"
        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        merged = run_git(
            result.path,
            "merge-base",
            "--is-ancestor",
            "origin/main",
            "HEAD",
            check=False,
        )
        assert merged.returncode == 0

    def test_reused_worktree_is_rebased_without_pushing(
        self, project_with_worktree, quiet_finalize
    ):
        """Reopening a worktree puts its branch on current code, and stops there.

        The remote is left alone: an agent may be live in the worktree, and a
        force-push would rewrite history under it.
        """
        project_path, worktree_path, remote_path = project_with_worktree
        # The fixture's alpha worktree already has feature/work checked out.
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        push_branch(worktree_path, "feature/work")
        remote_before = run_git(
            worktree_path, "rev-parse", "origin/feature/work"
        ).stdout.strip()
        push_to_origin_main(project_path, remote_path)

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            "feature/work",
            run_install=False,
        )

        assert result.action == "reused"
        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        assert result.sync.pushed is False
        merged = run_git(
            result.path,
            "merge-base",
            "--is-ancestor",
            "origin/main",
            "HEAD",
            check=False,
        )
        assert merged.returncode == 0
        # The remote branch is untouched.
        assert (
            run_git(worktree_path, "rev-parse", "origin/feature/work").stdout.strip()
            == remote_before
        )

    def test_a_live_session_skips_the_reuse_rebase(
        self, project_with_worktree, quiet_finalize
    ):
        """A rebase under a running agent rewrites the commits it is building on."""
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        push_to_origin_main(project_path, remote_path)
        head_before = rev_parse(worktree_path)
        live = MagicMock()
        live.all_for.return_value = [MagicMock()]
        lines: list[str] = []

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            "feature/work",
            run_install=False,
            live=live,
            announce=lines.append,
        )

        assert result.action == "reused"
        assert result.sync is None
        assert rev_parse(worktree_path) == head_before
        assert any("live session" in line for line in lines), lines

    def test_reuse_does_not_autosquash_fixup_commits(
        self, project_with_worktree, quiet_finalize
    ):
        """A reopen is not a squash: a pending fixup! survives as its own commit."""
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        create_commit(worktree_path, "more.txt", "more\n", "fixup! Branch work")
        push_to_origin_main(project_path, remote_path)

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            "feature/work",
            run_install=False,
        )

        assert result.sync is not None and result.sync.success is True
        subjects = run_git(
            result.path, "log", "--format=%s", "origin/main..HEAD"
        ).stdout.split("\n")
        assert "fixup! Branch work" in subjects

    def test_reuse_keeps_uncommitted_work(self, project_with_worktree, quiet_finalize):
        """The rebase autostashes, so a dirty worktree is rebased, not refused."""
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        push_to_origin_main(project_path, remote_path)
        scratch = worktree_path / "scratch.txt"
        scratch.write_text("work in progress\n")

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            "feature/work",
            run_install=False,
        )

        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        assert scratch.read_text() == "work in progress\n"
        merged = run_git(
            result.path,
            "merge-base",
            "--is-ancestor",
            "origin/main",
            "HEAD",
            check=False,
        )
        assert merged.returncode == 0

    def test_brand_new_branch_is_synced_but_never_closed(
        self, project_with_worktree, quiet_finalize
    ):
        """A branch with no commits is 'empty' — close_if_empty must stay off."""
        project_path, worktree_path, remote_path = project_with_worktree

        result = setup_worktree_for_branch(
            project_path,
            "test-repo",
            "feature/brand-new",
            no_recycle=True,
            run_install=False,
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
            "mael_domain.worktree.run_resolve_rebase_session",
            side_effect=_idle_runner,
        ):
            result = setup_worktree_for_branch(
                project_path,
                "test-repo",
                branch,
                no_recycle=True,
                run_install=False,
            )

        assert result.sync is not None
        assert result.sync.success is False
        assert result.sync.aborted is True
        assert not rebase_in_progress(result.path)
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

        with (
            patch("mael_domain.worktree.setup_claude_memory_symlink"),
            patch("mael_domain.worktree.run_install_cmd"),
            patch(
                "mael_domain.worktree.update_claude_local_md",
                return_value=False,
            ) as local_md,
            patch(
                "mael_domain.worktree.run_resolve_rebase_session",
                side_effect=_idle_runner,
            ),
        ):
            result = setup_worktree_for_branch(
                project_path,
                "test-repo",
                branch,
                no_recycle=True,
                run_install=False,
            )

        assert result.sync is not None and result.sync.success is False
        local_md.assert_called_once()

    def test_conflicting_branch_with_successful_repair_reports_repaired(
        self, project_with_worktree, quiet_finalize
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        branch = self._conflicting_branch(project_path, worktree_path, remote_path)

        with patch(
            "mael_domain.worktree.run_resolve_rebase_session",
            side_effect=_repairing_runner,
        ):
            result = setup_worktree_for_branch(
                project_path,
                "test-repo",
                branch,
                no_recycle=True,
                run_install=False,
            )

        assert result.sync is not None
        assert result.sync.success is True, result.sync.message
        assert result.sync.repaired is True

    def test_a_failed_reuse_rebase_blocks_the_launch(
        self, project_with_worktree, quiet_finalize
    ):
        """Reuse does not make stale code safe: an unrepaired conflict still blocks."""
        project_path, worktree_path, remote_path = project_with_worktree
        # Conflict the alpha worktree's own branch against origin/main.
        make_conflict(project_path, worktree_path, remote_path)

        with patch(
            "mael_domain.worktree.run_resolve_rebase_session",
            side_effect=_idle_runner,
        ):
            result = setup_worktree_for_branch(
                project_path,
                "test-repo",
                "feature/work",
                run_install=False,
            )

        assert result.action == "reused"
        assert result.sync is not None and result.sync.success is False
        with pytest.raises(LaunchBlocked):
            check_synced("t1", "feature/work", result)

    def _conflicting_branch(self, project_path, worktree_path, remote_path):
        """A pushed branch whose README edit conflicts with a later origin/main edit."""
        branch = "feature/conflicting"
        run_git(worktree_path, "checkout", "-b", branch)
        create_commit(
            worktree_path, "README.md", "# Feature version\n", "Feature README"
        )
        push_branch(worktree_path, branch)
        run_git(worktree_path, "checkout", "--detach", "origin/main")
        run_git(worktree_path, "branch", "-D", branch)
        # Upstream edits the same line.
        push_to_origin_main(
            project_path, remote_path, "README.md", "# Upstream version\n"
        )
        return branch


class TestSyncNoPushAgainstRealGit:
    """`mael sync --no-push` against a real remote: rebases, publishes nothing."""

    def test_no_push_rebases_and_leaves_origin_where_it_was(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "work.txt", "work\n", "Branch work")
        push_branch(worktree_path, "feature/work")
        remote_before = run_git(
            worktree_path, "rev-parse", "origin/feature/work"
        ).stdout.strip()
        push_to_origin_main(project_path, remote_path)

        result = rebase_worktree(worktree_path, squash=False)

        assert result.success is True, result.message
        assert result.pushed is False
        merged = run_git(
            worktree_path,
            "merge-base",
            "--is-ancestor",
            "origin/main",
            "HEAD",
            check=False,
        )
        assert merged.returncode == 0
        assert (
            run_git(worktree_path, "rev-parse", "origin/feature/work").stdout.strip()
            == remote_before
        )
