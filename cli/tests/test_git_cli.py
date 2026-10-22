"""Tests for mael_cli.git_cli module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from git_helpers import create_commit, run_git, setup_git_repo

from mael_cli.cli import cli
from mael_cli.git_cli import (
    build_status_dict,
    format_git_status,
    get_diff_stat_summary,
    get_recent_commits,
)


class TestGetDiffStatSummary:
    """Tests for get_diff_stat_summary helper."""

    def test_with_changes(self):
        stat_output = (
            " src/auth.py | 10 +++++++---\n"
            " src/login.py | 5 +++++\n"
            " 2 files changed, 12 insertions(+), 3 deletions(-)\n"
        )
        with patch("mael_cli.git_cli.run_cmd") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stat_output)
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result == (2, 12, 3)

    def test_insertions_only(self):
        stat_output = " 1 file changed, 5 insertions(+)\n"
        with patch("mael_cli.git_cli.run_cmd") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stat_output)
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result == (1, 5, 0)

    def test_no_changes(self):
        with patch("mael_cli.git_cli.run_cmd") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result is None


class TestGetRecentCommits:
    """Tests for get_recent_commits helper."""

    def test_with_commits(self):
        with patch("mael_cli.git_cli.run_cmd") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="a1b2c3d feat: add login page\nd4e5f6g fix: handle null user\n",
            )
            result = get_recent_commits(Path("/tmp/claude/repo"))

        assert len(result) == 2
        assert result[0] == {"hash": "a1b2c3d", "message": "feat: add login page"}
        assert result[1] == {"hash": "d4e5f6g", "message": "fix: handle null user"}

    def test_no_commits(self):
        with patch("mael_cli.git_cli.run_cmd") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = get_recent_commits(Path("/tmp/claude/repo"))

        assert result == []


class TestFormatGitStatus:
    """Tests for format_git_status."""

    def test_clean_worktree(self):
        output = format_git_status(
            branch="main",
            commits_ahead=0,
            unpushed=0,
            file_status={"staged": [], "modified": [], "untracked": []},
            diff_stat=None,
            recent_commits=[],
        )
        assert "## Branch" in output
        assert "main" in output
        assert "Clean working tree, no commits ahead of main." in output

    def test_dirty_worktree(self):
        output = format_git_status(
            branch="feat/login",
            commits_ahead=3,
            unpushed=1,
            file_status={
                "staged": ["src/auth.py"],
                "modified": ["src/routes.py"],
                "untracked": ["src/new.py"],
            },
            diff_stat=(3, 42, 10),
            recent_commits=[{"hash": "abc1234", "message": "feat: add login"}],
        )
        assert "feat/login (3 ahead of main, 1 unpushed)" in output
        assert "Staged:" in output
        assert "  src/auth.py" in output
        assert "Modified:" in output
        assert "  src/routes.py" in output
        assert "Untracked:" in output
        assert "  src/new.py" in output
        assert "Diff: 3 files, +42 -10" in output
        assert "abc1234 feat: add login" in output

    def test_ahead_but_clean_tree(self):
        output = format_git_status(
            branch="feat/work",
            commits_ahead=2,
            unpushed=0,
            file_status={"staged": [], "modified": [], "untracked": []},
            diff_stat=None,
            recent_commits=[{"hash": "abc1234", "message": "feat: work"}],
        )
        assert "2 ahead of main" in output
        assert "Clean working tree." in output
        assert "abc1234 feat: work" in output


class TestBuildStatusDict:
    """Tests for build_status_dict."""

    def test_full_status(self):
        result = build_status_dict(
            branch="feat/test",
            commits_ahead=2,
            unpushed=1,
            file_status={
                "staged": ["a.py"],
                "modified": ["b.py"],
                "untracked": ["c.py"],
            },
            diff_stat=(3, 10, 5),
            recent_commits=[{"hash": "abc", "message": "test"}],
        )
        assert result["branch"] == "feat/test"
        assert result["commits_ahead"] == 2
        assert result["unpushed"] == 1
        assert result["staged"] == ["a.py"]
        assert result["diff_stat"] == {"files": 3, "insertions": 10, "deletions": 5}

    def test_no_diff_stat(self):
        result = build_status_dict(
            branch="main",
            commits_ahead=0,
            unpushed=0,
            file_status={"staged": [], "modified": [], "untracked": []},
            diff_stat=None,
            recent_commits=[],
        )
        assert result["diff_stat"] is None


class TestGitStatusCommand:
    """`mael git status` on a real repository."""

    def test_prints_the_branch_and_the_split_working_tree(self, tmp_path):
        setup_git_repo(tmp_path)
        run_git(tmp_path, "checkout", "-b", "feat/test")
        create_commit(tmp_path, "a.py", "a\n", "chore: start")
        (tmp_path / "a.py").write_text("a2\n")
        (tmp_path / "new.py").write_text("n\n")
        run_git(tmp_path, "add", "new.py")
        (tmp_path / "todo.txt").write_text("t\n")

        with patch(
            "mael_cli.git_cli.resolve_context",
            return_value=MagicMock(worktree_path=tmp_path),
        ):
            result = CliRunner().invoke(cli, ["git", "status"])

        assert result.exit_code == 0
        assert "feat/test" in result.output
        assert "Staged:\n  new.py\nModified:\n  a.py\nUntracked:\n  todo.txt" in (
            result.output
        )


class TestGitMergeCommand:
    """Integration tests for mael git merge command."""

    @pytest.fixture(autouse=True)
    def told(self):
        """The relay to the orchestrator, so no test reaches a live server."""
        with patch("mael_cli.orchestrator_client.tell_orchestrator") as told:
            self.told = told
            yield told

    def _mock_worktree_path(self):
        wt = MagicMock()
        wt.exists.return_value = True
        return wt

    @patch("mael_cli.git_cli.merge_to_main")
    @patch("mael_cli.git_cli.get_current_branch")
    @patch("mael_cli.git_cli.resolve_context")
    def test_success_echoes_merge_and_push_messages(
        self, mock_ctx, mock_branch, mock_merge
    ):
        from mael_domain.worktree import SyncResult

        mock_ctx.return_value = MagicMock(
            worktree_path=self._mock_worktree_path(), project="myproject"
        )
        mock_branch.return_value = "feat/test"
        mock_merge.return_value = SyncResult(
            success=True,
            branch="feat/test",
            message="Merged feat/test into main",
            pushed=True,
            push_message="Pushed main to origin",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "merge"])
        assert result.exit_code == 0
        assert "Merged feat/test into main" in result.output
        assert "Pushed main to origin" in result.output
        # The push merged the PR, so the server reads it again.
        self.told.assert_called_once_with(
            "/api/worktrees/refresh", {"project": "myproject", "branch": "feat/test"}
        )
        # Defaults: squash on, close off.
        _, kwargs = mock_merge.call_args
        assert kwargs.get("squash") is True
        assert kwargs.get("close") is False

    @patch("mael_cli.git_cli.merge_to_main")
    @patch("mael_cli.git_cli.get_current_branch")
    @patch("mael_cli.git_cli.resolve_context")
    def test_close_flag_passes_close_true(self, mock_ctx, mock_branch, mock_merge):
        from mael_domain.worktree import SyncResult

        mock_ctx.return_value = MagicMock(worktree_path=self._mock_worktree_path())
        mock_branch.return_value = "feat/test"
        mock_merge.return_value = SyncResult(
            success=True,
            branch="feat/test",
            message="Merged feat/test into main and closed worktree",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "merge", "--close"])
        assert result.exit_code == 0
        _, kwargs = mock_merge.call_args
        assert kwargs.get("close") is True

    @patch("mael_cli.git_cli.merge_to_main")
    @patch("mael_cli.git_cli.get_current_branch")
    @patch("mael_cli.git_cli.resolve_context")
    def test_no_squash_flag_passes_squash_false(
        self, mock_ctx, mock_branch, mock_merge
    ):
        from mael_domain.worktree import SyncResult

        mock_ctx.return_value = MagicMock(worktree_path=self._mock_worktree_path())
        mock_branch.return_value = "feat/test"
        mock_merge.return_value = SyncResult(
            success=True,
            branch="feat/test",
            message="Merged feat/test into main",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "merge", "--no-squash"])
        assert result.exit_code == 0
        _, kwargs = mock_merge.call_args
        assert kwargs.get("squash") is False

    @patch("mael_cli.git_cli.merge_to_main")
    @patch("mael_cli.git_cli.get_current_branch")
    @patch("mael_cli.git_cli.resolve_context")
    def test_conflicts_print_guidance_and_exit_1(
        self, mock_ctx, mock_branch, mock_merge
    ):
        from mael_domain.worktree import SyncResult

        mock_ctx.return_value = MagicMock(worktree_path=self._mock_worktree_path())
        mock_branch.return_value = "feat/test"
        mock_merge.return_value = SyncResult(
            success=False,
            branch="feat/test",
            message="CONFLICT (content): Merge conflict in feature.txt",
            had_conflicts=True,
            merge_base="abc1234",
            upstream_head="def5678",
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "merge"])
        assert result.exit_code == 1
        self.told.assert_not_called()
        assert "Rebase encountered conflicts." in result.output
        assert "git log abc1234..def5678 --oneline" in result.output
        assert "git rebase --continue" in result.output

    @patch("mael_cli.git_cli.merge_to_main")
    @patch("mael_cli.git_cli.get_current_branch")
    @patch("mael_cli.git_cli.resolve_context")
    def test_on_main_refuses_without_merging(self, mock_ctx, mock_branch, mock_merge):
        mock_ctx.return_value = MagicMock(worktree_path=self._mock_worktree_path())
        mock_branch.return_value = "main"

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "merge"])
        assert result.exit_code != 0
        assert "nothing to merge" in result.output
        mock_merge.assert_not_called()
