"""Tests for Sentry integration."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mael_domain.integrations._format import parse_since
from mael_domain.integrations.errors import IntegrationError, IntegrationHTTPError
from maelstrom.integrations.sentry_cli import sentry_group as sentry


class TestParseSince:
    def test_valid_forms(self):
        assert parse_since("30m") == 30 * 60
        assert parse_since("24h") == 24 * 3600
        assert parse_since("7d") == 7 * 86400
        assert parse_since("45s") == 45

    def test_bad_input_raises(self):
        with pytest.raises(IntegrationError, match="Invalid --since"):
            parse_since("bogus")


class TestListIssuesCommand:
    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    def test_bad_since_is_a_cli_error(self, _mock_config):
        result = CliRunner().invoke(sentry, ["list-issues", "--since", "bogus"])

        assert result.exit_code == 1
        assert "Error: Invalid --since value 'bogus'" in result.output

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch(
        "mael_domain.integrations.sentry.api_request",
        side_effect=IntegrationHTTPError(403, "forbidden"),
    )
    def test_an_http_error_is_a_cli_error(self, _mock_api, _mock_config):
        result = CliRunner().invoke(sentry, ["list-issues"])

        assert result.exit_code == 1
        assert "Error: HTTP Error 403: forbidden" in result.output

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request", return_value=[])
    def test_no_since_query_unchanged(self, mock_api, _mock_config):
        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues"])

        assert result.exit_code == 0, result.output
        params = mock_api.call_args[0][1]
        assert params["query"] == "is:unresolved environment:prod"
        assert "lastSeen" not in params["query"]

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request", return_value=[])
    def test_since_adds_lastseen_token(self, mock_api, _mock_config):
        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues", "--since", "7d"])

        assert result.exit_code == 0, result.output
        query = mock_api.call_args[0][1]["query"]
        assert "lastSeen:-7d" in query
        assert "is:unresolved" in query
        assert "environment:prod" in query

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request", return_value=[])
    def test_since_whitespace_normalized_in_query(self, mock_api, _mock_config):
        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues", "--since", " 7d "])

        assert result.exit_code == 0, result.output
        query = mock_api.call_args[0][1]["query"]
        # The interpolated token must be whitespace-free, not the raw input.
        assert "lastSeen:-7d" in query
        assert "lastSeen:- 7d" not in query

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request", return_value=[])
    def test_since_rejects_bad_value(self, _mock_api, _mock_config):
        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues", "--since", "bogus"])

        assert result.exit_code != 0
        assert "Invalid --since" in result.output

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request")
    def test_renders_table_with_window_heading(self, mock_api, _mock_config):
        mock_api.return_value = [
            {
                "shortId": "PROJ-1",
                "title": "Boom happened",
                "lastSeen": "2026-07-01T00:00:00Z",
                "count": "42",
                "stats": {},
            },
        ]

        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues", "--since", "7d"])

        assert result.exit_code == 0, result.output
        assert "PROJ-1" in result.output
        assert "Boom happened" in result.output
        assert "last 7d" in result.output

    @patch(
        "mael_domain.integrations.sentry.get_sentry_config",
        return_value=("org", "proj"),
    )
    @patch("mael_domain.integrations.sentry.api_request", return_value=[])
    def test_empty_result_reflects_window(self, _mock_api, _mock_config):
        runner = CliRunner()
        result = runner.invoke(sentry, ["list-issues", "--since", "7d"])

        assert result.exit_code == 0, result.output
        assert "in the last 7d" in result.output
