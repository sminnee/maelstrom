"""Tests for the shared urllib request wrapper."""

import urllib.error
from unittest.mock import patch

import click
import pytest

from maelstrom.integrations._http import request_bytes


class TestRequestBytes:
    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_returns_raw_bytes(self, mock_urlopen):
        raw = b"\x89PNG\r\n\x1a\n\x00\x01\x02"
        mock_urlopen.return_value.__enter__.return_value.read.return_value = raw

        result = request_bytes(
            "https://uploads.linear.app/abc", headers={"Authorization": "lin_x"}
        )

        assert result == raw
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "lin_x"

    @patch("maelstrom.integrations._http.urllib.request.urlopen")
    def test_http_error_raises_click_exception(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://uploads.linear.app/abc",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

        with pytest.raises(click.ClickException) as exc:
            request_bytes("https://uploads.linear.app/abc")

        assert "HTTP Error 401" in str(exc.value)
