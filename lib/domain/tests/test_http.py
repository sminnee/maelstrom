"""Tests for the shared urllib request wrapper."""

import urllib.error
from email.parser import BytesParser
from email.policy import default
from unittest.mock import patch

import pytest

from mael_domain.integrations._http import multipart_body, request_bytes
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


class TestMultipartBody:
    def test_a_standard_parser_reads_back_each_field_and_file(self):
        body, content_type = multipart_body(
            {"model": "gpt-4o-mini-transcribe"},
            {"file": ("clip.webm", b"\x1aE\xdf\xa3\r\n--bytes", "audio/webm")},
        )

        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body
        )
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in message.iter_parts()
        }
        assert parts["model"].get_content() == "gpt-4o-mini-transcribe"
        assert parts["file"].get_filename() == "clip.webm"
        assert parts["file"].get_content_type() == "audio/webm"
        assert parts["file"].get_content() == b"\x1aE\xdf\xa3\r\n--bytes"

    @pytest.mark.parametrize("name", ['a"b', "a\r\nb"], ids=["quote", "crlf"])
    def test_a_name_that_breaks_the_header_is_refused(self, name):
        with pytest.raises(ValueError):
            multipart_body({}, {"file": (name, b"x", "audio/webm")})
