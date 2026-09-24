"""Tests for `mael close --force`.

`--force` is the escape hatch for *incomplete* work: it closes the worktree (freeing
ports + cmux workspace) even with unmerged commits or a dirty tree, aborting an
in-progress sync rather than leaving a half-finished rebase. Nothing is discarded —
uncommitted changes are committed as ``wip: uncommitted changes`` first — and the
branch + PR are always preserved so the work can be reopened later.

Worktree-level tests use the real-git ``project_with_worktree`` fixture from ``domain_fixtures``
and helpers from ``tests/test_sync_flags.py``; CLI tests drive ``cmd_close`` through
``CliRunner`` with ``close_worktree`` + ``add_task`` mocked.
"""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from git_helpers import create_commit, run_git

from mael_domain.ports import get_port_allocation, record_port_allocation
from mael_domain.worktree import (
    CloseResult,
    close_worktree,
    setup_worktree_for_branch,
    sync_worktree,
)
from maelstrom.cli import cli

# Reuse the real-git fixture + helpers from the sync-flags suite.
from tests.test_sync_flags import (
    _current_head,
    _current_head_of_ref,
    _is_detached,
    _rebase_in_progress,
)


def _branch_exists(repo_path: Path, branch: str) -> bool:
    return (
        run_git(repo_path, "rev-parse", "--verify", branch, check=False).returncode == 0
    )


def _tip_subject(repo_path: Path) -> str:
    return run_git(repo_path, "log", "-1", "--format=%s").stdout.strip()


def _make_conflict(project_path: Path, worktree_path: Path, remote_path: Path) -> None:
    """Diverge README on origin/main and on the branch so a rebase conflicts."""
    with TemporaryDirectory() as tmpdir:
        clone = Path(tmpdir) / "pusher"
        subprocess.run(
            ["git", "clone", str(remote_path), str(clone)],
            check=True,
            capture_output=True,
        )
        run_git(clone, "config", "user.email", "test@test.com")
        run_git(clone, "config", "user.name", "Test")
        (clone / "README.md").write_text("# Upstream version\n")
        run_git(clone, "add", "README.md")
        run_git(clone, "commit", "-m", "Upstream README")
        run_git(clone, "push", "origin", "HEAD:main")
    run_git(project_path, "fetch", "origin")

    (worktree_path / "README.md").write_text("# Feature version\n")
    run_git(worktree_path, "add", "README.md")
    run_git(worktree_path, "commit", "-m", "Feature README")


# ---------------------------------------------------------------------------
# close_worktree(force=True) — worktree level, real git
# ---------------------------------------------------------------------------


class TestCloseForce:
    """`close_worktree(force=True)` against a real remote."""

    def test_force_with_unmerged_commits_preserves_branch(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        record_port_allocation(project_path, "alpha", 350)
        create_commit(worktree_path, "ahead.txt", "ahead\n", "Unmerged commit")

        result = close_worktree(worktree_path, force=True)

        assert result.success is True
        assert result.had_unmerged_work is True
        assert result.branch == "feature/work"
        # Worktree detached at origin/main, ports freed.
        assert _is_detached(worktree_path)
        assert _current_head(worktree_path) == _current_head_of_ref(
            project_path, "origin/main"
        )
        assert get_port_allocation(project_path, "alpha") is None
        # Branch survives — the unmerged work is recoverable.
        assert _branch_exists(project_path, "feature/work")

    def test_force_with_dirty_tree_commits_wip(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        (worktree_path / "dirty.txt").write_text("uncommitted work\n")  # untracked

        result = close_worktree(worktree_path, force=True)

        assert result.success is True
        assert result.had_unmerged_work is True
        assert result.branch == "feature/work"
        assert _is_detached(worktree_path)
        # The dirty change was committed onto the branch, not discarded.
        wip_tip = run_git(
            project_path, "log", "-1", "--format=%s", "feature/work"
        ).stdout.strip()
        assert wip_tip == "wip: uncommitted changes"
        # File content preserved on the branch tip.
        blob = run_git(project_path, "show", "feature/work:dirty.txt", check=False)
        assert blob.returncode == 0
        assert blob.stdout == "uncommitted work\n"

    def test_force_over_rebase_conflict_aborts_and_closes(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)

        result = close_worktree(worktree_path, force=True)

        assert result.success is True
        assert result.had_unmerged_work is True
        # The conflicting rebase was aborted — none left in progress.
        assert not _rebase_in_progress(worktree_path)
        # Worktree closed, branch preserved.
        assert _is_detached(worktree_path)
        assert _branch_exists(project_path, "feature/work")

    def test_force_on_clean_merged_worktree_no_unmerged_flag(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        # Branch is empty (HEAD == origin/main), clean tree.
        record_port_allocation(project_path, "alpha", 350)

        result = close_worktree(worktree_path, force=True)

        assert result.success is True
        assert result.had_unmerged_work is False
        assert _is_detached(worktree_path)
        assert get_port_allocation(project_path, "alpha") is None

    def test_force_false_regression_dirty(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        (worktree_path / "dirty.txt").write_text("uncommitted\n")

        result = close_worktree(worktree_path, force=False)

        assert result.success is False
        assert result.had_dirty_files is True
        assert not _is_detached(worktree_path)

    def test_force_false_regression_unmerged(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        create_commit(worktree_path, "ahead.txt", "ahead\n", "Unmerged commit")

        result = close_worktree(worktree_path, force=False)

        assert result.success is False
        assert result.had_unpushed_commits is True
        assert not _is_detached(worktree_path)


class TestCloseDiscard:
    """`close_worktree(discard=True)` removes files but keeps branch commits."""

    def test_discard_removes_dirty_files_preserves_ignored_files_and_branch(
        self, project_with_worktree
    ):
        project_path, worktree_path, remote_path = project_with_worktree
        record_port_allocation(project_path, "alpha", 350)
        create_commit(worktree_path, "branch.txt", "branch work\n", "Branch commit")
        branch_tip = _current_head(worktree_path)
        exclude = Path(
            run_git(
                worktree_path, "rev-parse", "--git-path", "info/exclude"
            ).stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = worktree_path / exclude
        exclude.write_text(".env\npreserved.ignore\n")
        (worktree_path / "README.md").write_text("tracked change\n")
        (worktree_path / "staged.txt").write_text("staged change\n")
        run_git(worktree_path, "add", "staged.txt")
        (worktree_path / "untracked.txt").write_text("untracked change\n")
        nested_repository = worktree_path / "nested-repository"
        subprocess.run(
            ["git", "init", str(nested_repository)], check=True, capture_output=True
        )
        (worktree_path / ".env").write_text("PORT_BASE=350\n")
        (worktree_path / "preserved.ignore").write_text("keep me\n")

        result = close_worktree(worktree_path, discard=True)

        assert result.success is True
        assert _is_detached(worktree_path)
        assert _current_head(worktree_path) == _current_head_of_ref(
            project_path, "origin/main"
        )
        assert get_port_allocation(project_path, "alpha") is None
        assert not (worktree_path / "staged.txt").exists()
        assert not (worktree_path / "untracked.txt").exists()
        assert not nested_repository.exists()
        assert (worktree_path / ".env").read_text() == "PORT_BASE=350\n"
        assert (worktree_path / "preserved.ignore").read_text() == "keep me\n"
        assert run_git(worktree_path, "status", "--porcelain").stdout == ""
        assert _current_head_of_ref(project_path, "feature/work") == branch_tip
        assert (
            run_git(project_path, "show", "feature/work:branch.txt").stdout
            == "branch work\n"
        )

    def test_discard_aborts_a_rebase_without_syncing(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        _make_conflict(project_path, worktree_path, remote_path)
        sync_worktree(worktree_path)
        assert _rebase_in_progress(worktree_path)

        with patch("mael_domain.worktree.sync_worktree") as sync:
            result = close_worktree(worktree_path, discard=True)

        sync.assert_not_called()
        assert result.success is True
        assert not _rebase_in_progress(worktree_path)
        assert _is_detached(worktree_path)


# ---------------------------------------------------------------------------
# Reopen round-trip — force-close, then setup_worktree_for_branch restores it
# ---------------------------------------------------------------------------


class TestReopenRoundTrip:
    """Force-close a branch with committed + dirty work, then reopen it."""

    def test_reopen_restores_commits_and_wip(self, project_with_worktree):
        project_path, worktree_path, remote_path = project_with_worktree
        # Both committed unmerged work AND a dirty file.
        create_commit(worktree_path, "feature.txt", "feature work\n", "Feature commit")
        (worktree_path / "dirty.txt").write_text("dirty work\n")

        result = close_worktree(worktree_path, force=True)
        assert result.success is True
        assert result.branch == "feature/work"
        # Worktree is now detached and recyclable.
        assert _is_detached(worktree_path)

        setup = setup_worktree_for_branch(
            project_path, project_path.name, "feature/work", run_install=False
        )

        reopened = setup.path
        # Back on the branch at its tip.
        assert (
            run_git(reopened, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            == "feature/work"
        )
        # The committed feature work is present.
        assert (reopened / "feature.txt").exists()
        assert (reopened / "feature.txt").read_text() == "feature work\n"
        # The wip commit (carrying the previously-dirty file) is back as the tip.
        assert _tip_subject(reopened) == "wip: uncommitted changes"
        assert (reopened / "dirty.txt").read_text() == "dirty work\n"


# ---------------------------------------------------------------------------
# CLI: cmd_close --force via CliRunner (close_worktree + add_task mocked)
# ---------------------------------------------------------------------------


class TestCloseForceCli:
    """`mael close --force` task-creation behaviour via CliRunner."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.project_path = None  # skip copy-back-env path
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, close_result):
        runner = CliRunner()
        env_store = MagicMock()
        with (
            patch("maelstrom.cli.resolve_context", return_value=self._ctx()),
            patch(
                "mael_domain.worktree_close.close_worktree", return_value=close_result
            ),
            patch("maelstrom.cli.make_store", return_value=env_store),
            patch("mael_domain.worktree_close.get_env_status", return_value=[]),
            patch("mael_domain.worktree_close.mael_layout") as mock_layout,
            patch("maelstrom.cli.add_task") as mock_add_task,
        ):
            mock_layout.close_workspace.return_value = False
            mock_add_task.return_value = MagicMock(id="reopen-1")
            result = runner.invoke(cli, ["close", "myproject.alpha", *args])
        return result, mock_add_task

    def test_force_with_unmerged_work_creates_reopen_task(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=True,
        )
        result, mock_add_task = self._run(["--force"], close_result)

        assert result.exit_code == 0
        mock_add_task.assert_called_once()
        _, kwargs = mock_add_task.call_args
        assert kwargs["command"] == "reopen-branch"
        assert kwargs["branch"] == "feature/work"
        assert kwargs["run"] is False

    def test_force_without_unmerged_work_no_task(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=False,
        )
        result, mock_add_task = self._run(["--force"], close_result)

        assert result.exit_code == 0
        mock_add_task.assert_not_called()

    def test_force_already_detached_branch_no_task(self):
        """A worktree already closed (branch == 'HEAD') gets no reopen task."""
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="HEAD",
            had_unmerged_work=True,
        )
        result, mock_add_task = self._run(["--force"], close_result)

        assert result.exit_code == 0
        mock_add_task.assert_not_called()

    def test_force_threads_flag_into_close_worktree(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=False,
        )
        runner = CliRunner()
        with (
            patch("maelstrom.cli.resolve_context", return_value=self._ctx()),
            patch(
                "mael_domain.worktree_close.close_worktree", return_value=close_result
            ) as mock_close,
            patch("maelstrom.cli.make_store", return_value=MagicMock()),
            patch("mael_domain.worktree_close.get_env_status", return_value=[]),
            patch("mael_domain.worktree_close.mael_layout") as mock_layout,
            patch("maelstrom.cli.add_task"),
        ):
            mock_layout.close_workspace.return_value = False
            runner.invoke(cli, ["close", "myproject.alpha", "--force"])
        _, kwargs = mock_close.call_args
        assert kwargs["force"] is True


class TestCloseDiscardCli(TestCloseForceCli):
    def test_discard_does_not_create_a_reopen_task(self):
        close_result = CloseResult(
            success=True,
            message="Worktree closed (detached at origin/main)",
            branch="feature/work",
            had_unmerged_work=True,
        )
        result, mock_add_task = self._run(["--discard"], close_result)

        assert result.exit_code == 0
        mock_add_task.assert_not_called()

    def test_discard_cannot_be_combined_with_force(self):
        result, _ = self._run(
            ["--discard", "--force"], CloseResult(success=True, message="Closed")
        )

        assert result.exit_code == 2
        assert "--discard cannot be used with --force" in result.output

    def test_discard_cannot_be_combined_with_wait(self):
        result, _ = self._run(
            ["--discard", "--wait"], CloseResult(success=True, message="Closed")
        )

        assert result.exit_code == 2
        assert "--discard cannot be used with --wait" in result.output
