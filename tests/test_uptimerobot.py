"""Tests for UptimeRobot integration."""

from unittest.mock import patch

import click
import pytest

from click.testing import CliRunner

from maelstrom.uptimerobot import (
    api_request,
    format_duration,
    format_log_type,
    format_status,
    uptimerobot,
)


class TestFormatStatus:
    def test_known_codes(self):
        assert format_status(0) == "paused"
        assert format_status(1) == "not-checked"
        assert format_status(2) == "up"
        assert format_status(8) == "seems-down"
        assert format_status(9) == "down"

    def test_unknown_code(self):
        assert format_status(42) == "unknown(42)"


class TestFormatLogType:
    def test_known_codes(self):
        assert format_log_type(1) == "down"
        assert format_log_type(2) == "up"
        assert format_log_type(98) == "started"
        assert format_log_type(99) == "paused"

    def test_unknown_code(self):
        assert format_log_type(7) == "unknown(7)"


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(0) == "0s"
        assert format_duration(45) == "45s"

    def test_minutes(self):
        assert format_duration(60) == "1m"
        assert format_duration(125) == "2m 5s"

    def test_hours(self):
        assert format_duration(3600) == "1h"
        assert format_duration(3600 + 15 * 60) == "1h 15m"
        assert format_duration(2 * 3600 + 15 * 60) == "2h 15m"

    def test_days(self):
        assert format_duration(86400) == "1d"
        assert format_duration(86400 + 3600) == "1d 1h"

    def test_negative_treated_as_zero(self):
        assert format_duration(-5) == "0s"


class TestApiRequest:
    @patch("maelstrom.uptimerobot.get_uptimerobot_api_key", return_value="u1-test")
    @patch("maelstrom.uptimerobot.urllib.request.urlopen")
    def test_success_returns_payload(self, mock_urlopen, _mock_key):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"stat":"ok","monitors":[]}'
        )

        result = api_request("/getMonitors")

        assert result == {"stat": "ok", "monitors": []}
        mock_urlopen.assert_called_once()

    @patch("maelstrom.uptimerobot.get_uptimerobot_api_key", return_value="u1-test")
    @patch("maelstrom.uptimerobot.urllib.request.urlopen")
    def test_fail_raises_click_exception(self, mock_urlopen, _mock_key):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            b'{"stat":"fail","error":{"type":"invalid_parameter",'
            b'"message":"api_key is invalid"}}'
        )

        with pytest.raises(click.ClickException, match="api_key is invalid"):
            api_request("/getMonitors")


class TestStatusCommand:
    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=["111", "222"])
    @patch("maelstrom.uptimerobot.api_request")
    def test_status_uses_configured_monitors(self, mock_api, _mock_monitors):
        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {
                    "id": 111,
                    "friendly_name": "API",
                    "status": 2,
                    "last_event_datetime": 1700000000,
                    "custom_uptime_ratio": "100.000-99.991-99.823",
                },
                {
                    "id": 222,
                    "friendly_name": "Web",
                    "status": 9,
                    "last_event_datetime": 1700000000,
                    "custom_uptime_ratio": "98.500-99.100-99.700",
                },
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["status"])

        assert result.exit_code == 0, result.output
        assert "configured" in result.output
        assert "API" in result.output
        assert "Web" in result.output
        assert "up" in result.output
        assert "down" in result.output

        sent_body = mock_api.call_args[0][1]
        assert sent_body["monitors"] == "111-222"
        assert sent_body["custom_uptime_ratios"] == "1-7-30"
        # Uptime columns rendered to 3dp with '%'
        assert "99.991%" in result.output
        assert "99.700%" in result.output

    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=[111, 222])
    @patch("maelstrom.uptimerobot.api_request")
    def test_status_accepts_int_monitor_ids(self, mock_api, _mock_monitors):
        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {"id": 111, "friendly_name": "API", "status": 2, "last_event_datetime": 1700000000},
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["status"])

        assert result.exit_code == 0, result.output
        sent_body = mock_api.call_args[0][1]
        assert sent_body["monitors"] == "111-222"

    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=None)
    @patch("maelstrom.uptimerobot.api_request")
    def test_status_falls_back_to_all_account(self, mock_api, _mock_monitors):
        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {
                    "id": 1,
                    "friendly_name": "Only",
                    "status": 2,
                    "last_event_datetime": 1700000000,
                },
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["status"])

        assert result.exit_code == 0, result.output
        assert "all account" in result.output

        sent_body = mock_api.call_args[0][1]
        assert "monitors" not in sent_body

    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=None)
    @patch("maelstrom.uptimerobot.api_request")
    def test_status_handles_api_fail(self, mock_api, _mock_monitors):
        mock_api.side_effect = click.ClickException("UptimeRobot API error: bad key")

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["status"])

        assert result.exit_code != 0
        assert "bad key" in result.output


class TestOutagesCommand:
    @patch("maelstrom.uptimerobot.time.time", return_value=2_000_000_000)
    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=["111"])
    @patch("maelstrom.uptimerobot.api_request")
    def test_outages_filters_to_down_within_window(self, mock_api, _mock_monitors, _mock_time):
        recent_ts = 2_000_000_000 - 3600  # 1h ago
        old_ts = 2_000_000_000 - 30 * 86400  # 30d ago

        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {
                    "id": 111,
                    "friendly_name": "API",
                    "status": 2,
                    "logs": [
                        {
                            "type": 1,
                            "datetime": recent_ts,
                            "duration": 125,
                            "reason": {"code": "500", "detail": "internal error"},
                        },
                        {
                            "type": 2,
                            "datetime": recent_ts + 10,
                            "duration": 0,
                            "reason": {},
                        },
                        {
                            "type": 1,
                            "datetime": old_ts,
                            "duration": 60,
                            "reason": {"detail": "ancient"},
                        },
                    ],
                },
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["outages", "--since", "24h"])

        assert result.exit_code == 0, result.output
        assert "internal error" in result.output
        assert "2m 5s" in result.output
        assert "ancient" not in result.output

    @patch("maelstrom.uptimerobot.get_uptimerobot_monitors", return_value=None)
    @patch("maelstrom.uptimerobot.api_request")
    def test_outages_empty_window(self, mock_api, _mock_monitors):
        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {"id": 1, "friendly_name": "Web", "status": 2, "logs": []},
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["outages", "--since", "1h"])

        assert result.exit_code == 0
        assert "No outages" in result.output

    def test_outages_rejects_bad_since(self):
        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["outages", "--since", "bogus"])
        assert result.exit_code != 0
        assert "Invalid --since" in result.output


class TestMonitorsCommand:
    @patch("maelstrom.uptimerobot.api_request")
    def test_monitors_lists_all(self, mock_api):
        mock_api.return_value = {
            "stat": "ok",
            "monitors": [
                {"id": 1, "friendly_name": "API", "status": 2, "url": "https://api.example.com"},
                {"id": 2, "friendly_name": "Web", "status": 9, "url": "https://example.com"},
            ],
        }

        runner = CliRunner()
        result = runner.invoke(uptimerobot, ["monitors"])

        assert result.exit_code == 0, result.output
        assert "API" in result.output
        assert "Web" in result.output
        assert "https://api.example.com" in result.output

        # Should not pass `monitors` filter
        sent_body = mock_api.call_args[0][1]
        assert "monitors" not in sent_body
