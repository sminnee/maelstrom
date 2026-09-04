"""Tests for maelstrom.github_cli module."""

import subprocess
from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.github_cli import _format_size, _render_pr_comments
from maelstrom.github_model import (
    GitHubCommandFailed,
    PRComment,
    PRInfo,
    SyncFailed,
)


class TestFormatSize:
    """Tests for the _format_size helper."""

    def test_bytes(self):
        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(int(2.5 * 1024 * 1024)) == "2.5 MB"


class TestRenderPRComments:
    """Tests for the _render_pr_comments helper."""

    def _pr(self, comments, last_push_at=None):
        return PRInfo(
            number=1,
            title="Test PR",
            url="https://github.com/x/y/pull/1",
            state="OPEN",
            merged=False,
            head_ref="feat/x",
            comments=comments,
            last_push_at=last_push_at,
        )

    def test_no_comments_renders_nothing(self, capsys):
        _render_pr_comments(self._pr([]), all_comments=False)
        assert capsys.readouterr().out == ""

    def test_new_comment_shown(self, capsys):
        comments = [
            PRComment(
                author="alice",
                body="looks good",
                created_at="2026-06-24T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False
        )
        out = capsys.readouterr().out
        assert "Top-level (1 new):" in out
        assert "@alice" in out
        assert "looks good" in out

    def test_old_comment_hidden_without_all(self, capsys):
        comments = [
            PRComment(
                author="bob",
                body="old note",
                created_at="2026-06-20T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False
        )
        out = capsys.readouterr().out
        assert "old note" not in out
        assert "1 older comment hidden" in out

    def test_old_comment_shown_with_all(self, capsys):
        comments = [
            PRComment(
                author="bob",
                body="old note",
                created_at="2026-06-20T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=True
        )
        out = capsys.readouterr().out
        assert "old note" in out


class TestGhCliRegistration:
    """The gh group is reachable through the top-level cli."""

    def test_help_lists_all_commands(self):
        result = CliRunner().invoke(cli, ["gh", "--help"])
        assert result.exit_code == 0
        for cmd in (
            "create-pr",
            "wait-for-pr",
            "read-pr",
            "download-artifact",
            "check-log",
            "show-code",
        ):
            assert cmd in result.output

    def _run_create_pr(self, args):
        """Invoke `gh create-pr` with create_pr mocked; return its kwargs."""
        with (
            patch("maelstrom.github_cli.resolve_context") as mock_ctx,
            patch(
                "maelstrom.github_cli.create_pr",
                return_value=("https://example/pr", True),
            ) as mock_create,
            patch("maelstrom.github_cli._open_pr_in_cmux"),
        ):
            mock_ctx.return_value.worktree_path = None
            result = CliRunner().invoke(cli, ["gh", "create-pr", *args])
        assert result.exit_code == 0, result.output
        return mock_create.call_args.kwargs

    def test_create_pr_passes_autorepair_through(self):
        assert self._run_create_pr(["--autorepair"])["autorepair"] is True

    def test_create_pr_leaves_autorepair_off_by_default(self):
        """A PR push must not start an agent unasked."""
        assert self._run_create_pr([])["autorepair"] is False

    def test_show_code_smoke(self):
        with (
            patch("maelstrom.github_cli.resolve_context") as mock_ctx,
            patch("maelstrom.github_cli.get_worktree_code") as mock_code,
        ):
            mock_ctx.return_value.worktree_path = None
            mock_code.return_value = ("abc123 commit", "")
            result = CliRunner().invoke(cli, ["gh", "show-code", "--committed"])
        assert result.exit_code == 0
        assert "=== Commits ===" in result.output
        assert "abc123 commit" in result.output


class TestCreatePrErrorHandling:
    """`gh create-pr` turns domain errors into clean messages, not tracebacks."""

    @staticmethod
    def _invoke(error):
        with (
            patch("maelstrom.github_cli.resolve_context") as mock_ctx,
            patch("maelstrom.github_cli.create_pr", side_effect=error),
            patch("maelstrom.github_cli._open_pr_in_cmux"),
        ):
            mock_ctx.return_value.worktree_path = None
            return CliRunner().invoke(cli, ["gh", "create-pr"])

    def test_a_github_error_reads_as_a_clean_message(self):
        result = self._invoke(GitHubCommandFailed("push branch", "rejected"))
        assert result.exit_code == 1
        assert "Failed to push branch: rejected" in result.output

    def test_a_sync_failure_reads_as_a_clean_message(self):
        result = self._invoke(SyncFailed("Sync failed: conflicts"))
        assert result.exit_code == 1
        assert "Sync failed: conflicts" in result.output

    def test_a_programming_error_is_not_dressed_up_as_a_user_message(self):
        """A bug in maelstrom must surface as a bug, not as advice to the user."""
        result = self._invoke(AttributeError("'NoneType' has no attribute 'strip'"))
        assert isinstance(result.exception, AttributeError)

    def test_a_git_failure_inside_create_pr_still_reads_as_a_message(self):
        """`create_pr` calls git through `run_git`, which raises
        CalledProcessError — not a GitHubError. Narrowing the catch must not
        turn that into a traceback at the user."""
        err = subprocess.CalledProcessError(1, ["git"], stderr="detached HEAD")
        result = self._invoke(err)
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
