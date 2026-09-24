"""Tests for the shared urllib request wrapper."""

import urllib.error
from unittest.mock import patch

import pytest

from mael_domain.integrations._http import request_bytes
from mael_domain.integrations.errors import IntegrationHTTPError


class TestRequestBytes:
    @patch("mael_domain.integrations._http.urllib.request.urlopen")
    def test_returns_raw_bytes(self, mock_urlopen):
        raw = b"\x89PNG\r\n\x1a\n\x00\x01\x02"
        mock_urlopen.return_value.__enter__.return_value.read.return_value = raw

        result = request_bytes(
            "https://uploads.linear.app/abc", headers={"Authorization": "lin_x"}
        )

        assert result == raw
        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Authorization") == "lin_x"

    @patch("mael_domain.integrations._http.urllib.request.urlopen")
    def test_http_error_raises_integration_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://uploads.linear.app/abc",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

        with pytest.raises(IntegrationHTTPError) as exc:
            request_bytes("https://uploads.linear.app/abc")

        assert exc.value.code == 401
        assert str(exc.value).startswith("HTTP Error 401: ")
