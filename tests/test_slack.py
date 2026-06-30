"""Tests for the Slack webhook posting integration."""

from unittest.mock import patch

import click
import pytest

from click.testing import CliRunner

from maelstrom.context import GlobalConfig
from maelstrom.integrations.slack import resolve_webhook, slack


def _config(**webhooks: str) -> GlobalConfig:
    cfg = GlobalConfig.default()
    cfg.slack_webhooks = dict(webhooks)
    return cfg


class TestGlobalConfigParsing:
    def test_parses_webhooks_map(self):
        cfg = GlobalConfig.from_dict(
            {"slack": {"webhooks": {"weekly": "https://a", "alerts": "https://b"}}}
        )
        assert cfg.slack_webhooks == {"weekly": "https://a", "alerts": "https://b"}
        # Insertion order preserved (relied on for "first = default").
        assert next(iter(cfg.slack_webhooks)) == "weekly"

    def test_missing_slack_block(self):
        assert GlobalConfig.from_dict({}).slack_webhooks == {}

    def test_non_dict_slack_block(self):
        assert GlobalConfig.from_dict({"slack": "nope"}).slack_webhooks == {}

    def test_non_dict_webhooks(self):
        assert GlobalConfig.from_dict({"slack": {"webhooks": ["a", "b"]}}).slack_webhooks == {}

    def test_coerces_values_to_str(self):
        cfg = GlobalConfig.from_dict({"slack": {"webhooks": {"a": 123}}})
        assert cfg.slack_webhooks == {"a": "123"}

    def test_default_is_empty(self):
        assert GlobalConfig.default().slack_webhooks == {}


class TestResolveWebhook:
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_named_channel(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        assert resolve_webhook("alerts") == ("https://b", "alerts")

    @patch("maelstrom.integrations.slack.load_global_config")
    def test_default_is_first_defined(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        assert resolve_webhook(None) == ("https://a", "weekly")

    @patch("maelstrom.integrations.slack.load_global_config")
    def test_unknown_channel_lists_available(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        with pytest.raises(click.ClickException) as exc:
            resolve_webhook("nope")
        assert "weekly" in str(exc.value)
        assert "alerts" in str(exc.value)

    @patch("maelstrom.integrations.slack.load_global_config")
    def test_empty_config_raises(self, mock_cfg):
        mock_cfg.return_value = _config()
        with pytest.raises(click.ClickException, match="No Slack webhooks configured"):
            resolve_webhook(None)


class TestPostCommand:
    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_posts_to_default(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")

        result = CliRunner().invoke(slack, ["post", "hello"])

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "hello")
        assert "Posted to #weekly." in result.output

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_posts_to_named_channel(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")

        result = CliRunner().invoke(slack, ["post", "--channel", "alerts", "alert!"])

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://b", "alert!")
        assert "Posted to #alerts." in result.output

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_reads_from_stdin(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post"], input="from stdin\n")

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "from stdin")

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_empty_message_errors(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post"], input="")

        assert result.exit_code != 0
        assert "No message provided" in result.output
        mock_post.assert_not_called()

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_arg_and_stdin_both_provided_errors(self, mock_cfg, mock_post):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "hello"], input="from stdin\n")

        assert result.exit_code != 0
        assert "not both" in result.output
        mock_post.assert_not_called()

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_arg_with_empty_stdin_is_fine(self, mock_cfg, mock_post):
        # A non-tty-but-empty stdin (cron/CI) must not trip the "both" guard.
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "hello"], input="")

        assert result.exit_code == 0, result.output
        mock_post.assert_called_once_with("https://a", "hello")

    @patch("maelstrom.integrations.slack.load_global_config")
    def test_unknown_channel_exits_nonzero(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "--channel", "nope", "hi"])

        assert result.exit_code != 0
        assert "Unknown Slack channel" in result.output


class TestPostMessageHttp:
    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_post_message_sends_text_body(self, mock_urlopen):
        from maelstrom.integrations.slack import post_message

        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"ok"

        # Slack returns the literal "ok" (not JSON) — request_text must not choke.
        post_message("https://hooks.slack.com/services/XXX", "hi there")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.data == b'{"text": "hi there"}'
        assert req.method == "POST"
