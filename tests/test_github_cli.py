"""Tests for maelstrom.github_cli module."""

from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.github import PRComment, PRInfo
from maelstrom.github_cli import _format_size, _render_pr_comments


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
            PRComment(author="alice", body="looks good", created_at="2026-06-24T00:00:00Z", kind="issue"),
        ]
        _render_pr_comments(self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False)
        out = capsys.readouterr().out
        assert "Top-level (1 new):" in out
        assert "@alice" in out
        assert "looks good" in out

    def test_old_comment_hidden_without_all(self, capsys):
        comments = [
            PRComment(author="bob", body="old note", created_at="2026-06-20T00:00:00Z", kind="issue"),
        ]
        _render_pr_comments(self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False)
        out = capsys.readouterr().out
        assert "old note" not in out
        assert "1 older comment hidden" in out

    def test_old_comment_shown_with_all(self, capsys):
        comments = [
            PRComment(author="bob", body="old note", created_at="2026-06-20T00:00:00Z", kind="issue"),
        ]
        _render_pr_comments(self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=True)
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

    def test_show_code_smoke(self):
        with patch("maelstrom.github_cli.resolve_context") as mock_ctx, patch(
            "maelstrom.github_cli.get_worktree_code"
        ) as mock_code:
            mock_ctx.return_value.worktree_path = None
            mock_code.return_value = ("abc123 commit", "")
            result = CliRunner().invoke(cli, ["gh", "show-code", "--committed"])
        assert result.exit_code == 0
        assert "=== Commits ===" in result.output
        assert "abc123 commit" in result.output
