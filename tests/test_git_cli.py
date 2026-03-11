"""Tests for maelstrom.git_cli module."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.git_cli import (
    get_worktree_file_status,
    get_diff_stat_summary,
    get_recent_commits,
    format_git_status,
    build_status_dict,
)


class TestGetWorktreeFileStatus:
    """Tests for get_worktree_file_status helper."""

    def test_staged_files(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="A  src/new.py\nM  src/changed.py\n"
            )
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert result["staged"] == ["src/new.py", "src/changed.py"]
        assert result["modified"] == []
        assert result["untracked"] == []

    def test_modified_files(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=" M src/routes.py\n"
            )
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert result["staged"] == []
        assert result["modified"] == ["src/routes.py"]
        assert result["untracked"] == []

    def test_untracked_files(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="?? src/new_file.py\n"
            )
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert result["staged"] == []
        assert result["modified"] == []
        assert result["untracked"] == ["src/new_file.py"]

    def test_mixed_status(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="A  src/new.py\n M src/routes.py\n?? src/todo.txt\nMM src/both.py\n",
            )
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert "src/new.py" in result["staged"]
        assert "src/routes.py" in result["modified"]
        assert "src/todo.txt" in result["untracked"]
        # MM = staged AND modified
        assert "src/both.py" in result["staged"]
        assert "src/both.py" in result["modified"]

    def test_empty_output(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert result == {"staged": [], "modified": [], "untracked": []}

    def test_git_failure(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            result = get_worktree_file_status(Path("/tmp/claude/repo"))

        assert result == {"staged": [], "modified": [], "untracked": []}


class TestGetDiffStatSummary:
    """Tests for get_diff_stat_summary helper."""

    def test_with_changes(self):
        stat_output = (
            " src/auth.py | 10 +++++++---\n"
            " src/login.py | 5 +++++\n"
            " 2 files changed, 12 insertions(+), 3 deletions(-)\n"
        )
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stat_output)
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result == (2, 12, 3)

    def test_insertions_only(self):
        stat_output = " 1 file changed, 5 insertions(+)\n"
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=stat_output)
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result == (1, 5, 0)

    def test_no_changes(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            result = get_diff_stat_summary(Path("/tmp/claude/repo"))

        assert result is None


class TestGetRecentCommits:
    """Tests for get_recent_commits helper."""

    def test_with_commits(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="a1b2c3d feat: add login page\nd4e5f6g fix: handle null user\n",
            )
            result = get_recent_commits(Path("/tmp/claude/repo"))

        assert len(result) == 2
        assert result[0] == {"hash": "a1b2c3d", "message": "feat: add login page"}
        assert result[1] == {"hash": "d4e5f6g", "message": "fix: handle null user"}

    def test_no_commits(self):
        with patch("maelstrom.git_cli.subprocess.run") as mock_run:
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
    """Integration tests for mael git status command."""

    @patch("maelstrom.git_cli.get_recent_commits")
    @patch("maelstrom.git_cli.get_diff_stat_summary")
    @patch("maelstrom.git_cli.get_worktree_file_status")
    @patch("maelstrom.git_cli.get_local_only_commits")
    @patch("maelstrom.git_cli.get_commits_ahead")
    @patch("maelstrom.git_cli.get_current_branch")
    @patch("maelstrom.git_cli.resolve_context")
    def test_plain_output(
        self,
        mock_ctx,
        mock_branch,
        mock_ahead,
        mock_unpushed,
        mock_files,
        mock_diff,
        mock_commits,
    ):
        mock_ctx.return_value = MagicMock(worktree_path=Path("/tmp/claude/repo"))
        mock_branch.return_value = "feat/test"
        mock_ahead.return_value = 2
        mock_unpushed.return_value = 1
        mock_files.return_value = {
            "staged": ["a.py"],
            "modified": [],
            "untracked": [],
        }
        mock_diff.return_value = (1, 5, 0)
        mock_commits.return_value = [{"hash": "abc", "message": "test"}]

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "status"])
        assert result.exit_code == 0
        assert "feat/test" in result.output
        assert "Staged:" in result.output

    @patch("maelstrom.git_cli.get_recent_commits")
    @patch("maelstrom.git_cli.get_diff_stat_summary")
    @patch("maelstrom.git_cli.get_worktree_file_status")
    @patch("maelstrom.git_cli.get_local_only_commits")
    @patch("maelstrom.git_cli.get_commits_ahead")
    @patch("maelstrom.git_cli.get_current_branch")
    @patch("maelstrom.git_cli.resolve_context")
    def test_json_output(
        self,
        mock_ctx,
        mock_branch,
        mock_ahead,
        mock_unpushed,
        mock_files,
        mock_diff,
        mock_commits,
    ):
        mock_ctx.return_value = MagicMock(worktree_path=Path("/tmp/claude/repo"))
        mock_branch.return_value = "main"
        mock_ahead.return_value = 0
        mock_unpushed.return_value = 0
        mock_files.return_value = {"staged": [], "modified": [], "untracked": []}
        mock_diff.return_value = None
        mock_commits.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "git", "status"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["branch"] == "main"
        assert data["commits_ahead"] == 0
        assert data["diff_stat"] is None

    @patch("maelstrom.git_cli.get_recent_commits")
    @patch("maelstrom.git_cli.get_diff_stat_summary")
    @patch("maelstrom.git_cli.get_worktree_file_status")
    @patch("maelstrom.git_cli.get_local_only_commits")
    @patch("maelstrom.git_cli.get_commits_ahead")
    @patch("maelstrom.git_cli.get_current_branch")
    @patch("maelstrom.git_cli.resolve_context")
    def test_clean_worktree_output(
        self,
        mock_ctx,
        mock_branch,
        mock_ahead,
        mock_unpushed,
        mock_files,
        mock_diff,
        mock_commits,
    ):
        mock_ctx.return_value = MagicMock(worktree_path=None)
        mock_branch.return_value = "main"
        mock_ahead.return_value = 0
        mock_unpushed.return_value = 0
        mock_files.return_value = {"staged": [], "modified": [], "untracked": []}
        mock_diff.return_value = None
        mock_commits.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["git", "status"])
        assert result.exit_code == 0
        assert "Clean working tree, no commits ahead of main." in result.output
