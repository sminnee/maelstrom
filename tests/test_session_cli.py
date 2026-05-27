"""Tests for maelstrom.session_cli module."""

import json
import socket
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom import session_cli


def _write_session(sessions_dir: Path, key: str, **overrides) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "session_key": key,
        "session_id": overrides.get("session_id", key),
        "cwd": overrides.get("cwd", "/tmp/proj"),
        "pid": overrides.get("pid", 12345),
        "model": overrides.get("model", "claude-opus"),
        "state": overrides.get("state", "idle"),
        "started_at": overrides.get("started_at", "2026-05-27T10:00:00+00:00"),
        "updated_at": overrides.get("updated_at", "2026-05-27T10:00:00+00:00"),
        "channel_port": overrides.get("channel_port", 0),
    }
    path = sessions_dir / f"{key}.json"
    path.write_text(json.dumps(data))
    return path


def _patch_maelstrom_dir(tmp_path: Path):
    """Patch the ~/.maelstrom dir used by session_cli to tmp_path."""
    return patch("maelstrom.session_cli.get_maelstrom_dir", return_value=tmp_path)


class TestSessionRecord:
    def test_user_prompt_submit_sets_processing(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "user-prompt-submit"],
                input=json.dumps({"session_id": "abc", "prompt": "hi"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "processing"
        assert data["updated_at"] != "2026-05-27T10:00:00+00:00"

    def test_stop_sets_idle(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc", state="processing")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "stop"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "idle"

    def test_notification_permission_prompt(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "notification"],
                input=json.dumps({
                    "session_id": "abc",
                    "type": "permission_prompt",
                    "message": "bash command",
                }),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "awaiting-permission"

    def test_notification_idle_prompt(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "notification"],
                input=json.dumps({
                    "session_id": "abc",
                    "type": "idle_prompt",
                    "message": "you there?",
                }),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "waiting-for-input"

    def test_notification_unknown_type_is_ignored(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc", state="processing")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "notification"],
                input=json.dumps({
                    "session_id": "abc",
                    "type": "something_else",
                }),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        # State unchanged
        assert data["state"] == "processing"

    def test_fallback_to_cwd_pid(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(
            sessions, "s1", session_id="abc", cwd="/x/y", pid=999,
        )

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "user-prompt-submit"],
                # No session_id — match by cwd+pid
                input=json.dumps({"cwd": "/x/y", "pid": 999}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "processing"

    def test_no_matching_session_is_silent(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        # No session files at all.

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "user-prompt-submit"],
                input=json.dumps({"session_id": "nope"}),
            )

        assert result.exit_code == 0, result.output

    def test_unknown_event_errors(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "bogus"],
                input="{}",
            )
        assert result.exit_code == 2


class TestSessionList:
    def test_empty(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0
        assert "No active Claude Code sessions." in result.output

    def test_gc_removes_dead_port(self, tmp_path):
        sessions = tmp_path / "sessions"
        # Use a port that is almost certainly not listening.
        path = _write_session(sessions, "dead", channel_port=1)

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        assert "No active Claude Code sessions." in result.output
        assert not path.exists()

    def test_live_session_listed(self, tmp_path):
        # Start a real listener so the liveness check passes.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        try:
            sessions = tmp_path / "sessions"
            _write_session(sessions, "live", channel_port=port, state="processing")

            with _patch_maelstrom_dir(tmp_path):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])

            assert result.exit_code == 0, result.output
            assert "processing" in result.output
            assert "STATE" in result.output  # header rendered
        finally:
            srv.close()

    def test_corrupt_file_removed(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        bad = sessions / "bad.json"
        bad.write_text("not-json")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        assert not bad.exists()


class TestLivenessCheck:
    def test_zero_port_is_dead(self):
        assert session_cli._liveness_check(0) is False

    def test_unused_port_is_dead(self):
        # Pick a random high port that is very unlikely to be in use.
        assert session_cli._liveness_check(1) is False

    def test_listener_is_alive(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert session_cli._liveness_check(port) is True
        finally:
            srv.close()
