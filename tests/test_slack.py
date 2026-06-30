"""Tests for the Slack webhook posting integration."""

import json

from unittest.mock import Mock, patch

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

    @patch("maelstrom.integrations.slack.post_message")
    @patch("maelstrom.integrations.slack.load_global_config")
    def test_arg_with_tty_stdin_does_not_read(self, mock_cfg, mock_post):
        # An interactive TTY has no pending input; reading it would block
        # forever. With an arg given, the command must skip the stdin read
        # entirely rather than hang or trip the "both" guard.
        mock_cfg.return_value = _config(weekly="https://a")

        tty_stream = Mock()
        tty_stream.isatty.return_value = True

        with patch(
            "maelstrom.integrations.slack.click.get_text_stream",
            return_value=tty_stream,
        ):
            result = CliRunner().invoke(slack, ["post", "hello"])

        assert result.exit_code == 0, result.output
        tty_stream.read.assert_not_called()
        mock_post.assert_called_once_with("https://a", "hello")

    @patch("maelstrom.integrations.slack.load_global_config")
    def test_unknown_channel_exits_nonzero(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a")

        result = CliRunner().invoke(slack, ["post", "--channel", "nope", "hi"])

        assert result.exit_code != 0
        assert "Unknown Slack channel" in result.output


class TestPostMessageHttp:
    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_post_message_sends_section_block(self, mock_urlopen):
        from maelstrom.integrations.slack import post_message

        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"ok"

        # Slack returns the literal "ok" (not JSON) — request_text must not choke.
        post_message("https://hooks.slack.com/services/XXX", "hi there")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        # Parse rather than pin the serialized bytes (key order / whitespace).
        payload = json.loads(req.data)
        # Raw text is the notification fallback; the rendered body rides in a
        # section block. Plain text needs no conversion, so mrkdwn == text here.
        assert payload["text"] == "hi there"
        assert payload["blocks"] == [
            {"type": "section", "text": {"type": "mrkdwn", "text": "hi there"}}
        ]
        assert req.method == "POST"

    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_post_message_converts_markdown_to_mrkdwn(self, mock_urlopen):
        from maelstrom.integrations.slack import post_message

        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"ok"

        # Standard Markdown the user types -> Slack mrkdwn in the section block.
        post_message(
            "https://hooks.slack.com/services/XXX",
            "**bold**, *italic*, and a [link](https://example.com)",
        )

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        # **bold** -> *bold*, *italic* -> _italic_, [t](u) -> <u|t>.
        assert payload["blocks"] == [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*bold*, _italic_, and a <https://example.com|link>",
                },
            }
        ]
        # The unconverted Markdown is preserved as the notification fallback.
        assert payload["text"] == "**bold**, *italic*, and a [link](https://example.com)"

    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_post_message_splits_long_text_into_multiple_blocks(self, mock_urlopen):
        from maelstrom.integrations.slack import SECTION_TEXT_LIMIT, post_message

        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"ok"

        # A message past the 3000-char section limit must be split across blocks,
        # else Slack rejects the oversized block with HTTP 400 invalid_blocks.
        long_text = "\n".join(f"line {i} with some words" for i in range(400))
        post_message("https://hooks.slack.com/services/XXX", long_text)

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data)
        blocks = payload["blocks"]
        assert len(blocks) > 1
        for block in blocks:
            assert block["type"] == "section"
            assert block["text"]["type"] == "mrkdwn"
            assert len(block["text"]["text"]) <= SECTION_TEXT_LIMIT
            assert block["text"]["text"].strip()  # no empty/whitespace block
        # Fallback keeps the whole message.
        assert payload["text"] == long_text


class TestChunkMrkdwn:
    def test_short_text_is_single_chunk(self):
        from maelstrom.integrations.slack import _chunk_mrkdwn

        assert _chunk_mrkdwn("hello world") == ["hello world"]

    def test_empty_text_yields_one_empty_chunk(self):
        # Callers always emit a block; cmd_post rejects truly empty messages.
        from maelstrom.integrations.slack import _chunk_mrkdwn

        assert _chunk_mrkdwn("") == [""]

    def test_splits_on_newline_boundaries_within_limit(self):
        from maelstrom.integrations.slack import _chunk_mrkdwn

        # Three 40-char lines, limit 100: two fit per chunk, third spills over.
        lines = ["x" * 40, "y" * 40, "z" * 40]
        chunks = _chunk_mrkdwn("\n".join(lines), limit=100)

        assert all(len(c) <= 100 for c in chunks)
        # Reassembles losslessly and never splits mid-line.
        assert "\n".join(chunks) == "\n".join(lines)
        for chunk in chunks:
            for line in chunk.split("\n"):
                assert line in lines

    def test_hard_splits_a_single_oversized_line(self):
        from maelstrom.integrations.slack import _chunk_mrkdwn

        # One line longer than the limit can't sit on a boundary — hard-split it.
        chunks = _chunk_mrkdwn("a" * 250, limit=100)

        assert chunks == ["a" * 100, "a" * 100, "a" * 50]

    def test_every_chunk_within_limit(self):
        from maelstrom.integrations.slack import _chunk_mrkdwn

        text = "\n".join("word " * 10 for _ in range(500))
        chunks = _chunk_mrkdwn(text, limit=3000)

        assert len(chunks) > 1
        assert all(len(c) <= 3000 for c in chunks)
        assert "\n".join(chunks) == text
