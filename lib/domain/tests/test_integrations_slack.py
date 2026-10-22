"""Tests for the Slack webhook integration: channel lookup and posting.

The ``mael slack post`` command is tested in ``cli/tests/test_slack.py``.
"""

import json
from unittest.mock import patch

import pytest

from mael_domain.context import GlobalConfig
from mael_domain.integrations.errors import IntegrationError
from mael_domain.integrations.slack import resolve_webhook


def _config(**webhooks: str) -> GlobalConfig:
    cfg = GlobalConfig.default()
    cfg.slack_webhooks = dict(webhooks)
    return cfg


class TestResolveWebhook:
    @patch("mael_domain.integrations.slack.load_global_config")
    def test_named_channel(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        assert resolve_webhook("alerts") == ("https://b", "alerts")

    @patch("mael_domain.integrations.slack.load_global_config")
    def test_default_is_first_defined(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        assert resolve_webhook(None) == ("https://a", "weekly")

    @patch("mael_domain.integrations.slack.load_global_config")
    def test_unknown_channel_lists_available(self, mock_cfg):
        mock_cfg.return_value = _config(weekly="https://a", alerts="https://b")
        with pytest.raises(IntegrationError) as exc:
            resolve_webhook("nope")
        assert "weekly" in str(exc.value)
        assert "alerts" in str(exc.value)

    @patch("mael_domain.integrations.slack.load_global_config")
    def test_empty_config_raises(self, mock_cfg):
        mock_cfg.return_value = _config()
        with pytest.raises(IntegrationError, match="No Slack webhooks configured"):
            resolve_webhook(None)


class TestPostMessageHttp:
    @patch("mael_domain.integrations._http.urllib.request.urlopen")
    def test_post_message_sends_section_block(self, mock_urlopen):
        from mael_domain.integrations.slack import post_message

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

    @patch("mael_domain.integrations._http.urllib.request.urlopen")
    def test_post_message_converts_markdown_to_mrkdwn(self, mock_urlopen):
        from mael_domain.integrations.slack import post_message

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
        assert (
            payload["text"] == "**bold**, *italic*, and a [link](https://example.com)"
        )

    @patch("mael_domain.integrations._http.urllib.request.urlopen")
    def test_post_message_splits_long_text_into_multiple_blocks(self, mock_urlopen):
        from mael_domain.integrations.slack import SECTION_TEXT_LIMIT, post_message

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
        from mael_domain.integrations.slack import _chunk_mrkdwn

        assert _chunk_mrkdwn("hello world") == ["hello world"]

    def test_empty_text_yields_one_empty_chunk(self):
        # Callers always emit a block; cmd_post rejects truly empty messages.
        from mael_domain.integrations.slack import _chunk_mrkdwn

        assert _chunk_mrkdwn("") == [""]

    def test_splits_on_newline_boundaries_within_limit(self):
        from mael_domain.integrations.slack import _chunk_mrkdwn

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
        from mael_domain.integrations.slack import _chunk_mrkdwn

        # One line longer than the limit can't sit on a boundary — hard-split it.
        chunks = _chunk_mrkdwn("a" * 250, limit=100)

        assert chunks == ["a" * 100, "a" * 100, "a" * 50]

    def test_every_chunk_within_limit(self):
        from mael_domain.integrations.slack import _chunk_mrkdwn

        text = "\n".join("word " * 10 for _ in range(500))
        chunks = _chunk_mrkdwn(text, limit=3000)

        assert len(chunks) > 1
        assert all(len(c) <= 3000 for c in chunks)
        assert "\n".join(chunks) == text
