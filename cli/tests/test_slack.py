"""Tests for ``mael slack post``.

The webhook lookup and the HTTP post are tested in
``lib/domain/tests/test_integrations_slack.py``.
"""

from unittest.mock import Mock, patch

from click.testing import CliRunner

from mael_cli.integrations.slack_cli import slack_group as slack
from mael_domain.context import GlobalConfig


def _config(**webhooks: str) -> GlobalConfig:
    cfg = GlobalConfig.default()
    cfg.slack_webhooks = dict(webhooks)
    return cfg


class TestPostCommand:
    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_posts_to_default(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")

        result = CliRunner().invoke(slack, ["post", "hello"])

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "hello")
        assert "Posted to #weekly." in result.output

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_posts_to_named_channel(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")

        result = CliRunner().invoke(slack, ["post", "--channel", "alerts", "alert!"])

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://b", "alert!")
        assert "Posted to #alerts." in result.output

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_reads_from_stdin(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post"], input="from stdin\n")

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "from stdin")

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_empty_message_errors(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post"], input="")

        assert result.exit_code != 0
        assert "No message provided" in result.output
        mock_post.assert_not_called()

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_arg_and_stdin_both_provided_errors(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "hello"], input="from stdin\n")

        assert result.exit_code != 0
        assert "not both" in result.output
        mock_post.assert_not_called()

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_arg_with_empty_stdin_is_fine(self, mock_cfg, mock_post):
        # A non-tty-but-empty stdin (cron/CI) must not trip the "both" guard.
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "hello"], input="")

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "hello")

    @patch("mael_domain.integrations.slack.post_message")
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_arg_with_tty_stdin_does_not_read(self, mock_cfg, mock_post):
        # An interactive TTY has no pending input; reading it would block
        # forever. With an arg given, the command must skip the stdin read
        # entirely rather than hang or trip the "both" guard.
        mock_cfg.return_value = _config(weekly="https://a")

        tty_stream = Mock()
        tty_stream.isatty.return_value = True

        with patch(
            "mael_cli.integrations.slack_cli.click.get_text_stream",
            return_value=tty_stream,
        ):
            result = CliRunner().invoke(slack, ["post", "hello"])

        assert result.exit_code == 0, result.output
        tty_stream.read.assert_not_called()
        mock_post.assert_called_once_with("https://a", "hello")

    @patch("mael_domain.integrations.slack.load_global_config")
    def test_unknown_channel_exits_nonzero(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "--channel", "nope", "hi"])

        assert result.exit_code == 1
        assert "Error: Unknown Slack channel 'nope'" in result.output
