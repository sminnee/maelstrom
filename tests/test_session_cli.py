"""Tests for maelstrom.session_cli module."""

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom import session_cli
from maelstrom import task as model
from maelstrom.task_store import GitFileStore


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

    def test_permission_prompt(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "permission-prompt"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "awaiting-permission"

    def test_elicitation_prompt(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "elicitation-prompt"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "awaiting-permission"

    def test_idle_prompt_sets_idle(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc", state="processing")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "idle-prompt"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "idle"

    def test_ask_user_pre_post(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc", state="processing")

        runner = CliRunner()
        with _patch_maelstrom_dir(tmp_path):
            result = runner.invoke(
                cli,
                ["session", "record", "ask-user-pre"],
                input=json.dumps({"session_id": "abc"}),
            )
            assert result.exit_code == 0
            assert json.loads(path.read_text())["state"] == "awaiting-user-input"

            result = runner.invoke(
                cli,
                ["session", "record", "ask-user-post"],
                input=json.dumps({"session_id": "abc"}),
            )
            assert result.exit_code == 0
            assert json.loads(path.read_text())["state"] == "processing"

    def test_stop_failure_sets_idle(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc", state="processing")

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "stop-failure"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert json.loads(path.read_text())["state"] == "idle"

    def test_heartbeat_bumps_updated_at_without_changing_state(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(
            sessions, "s1", session_id="abc",
            state="processing",
            updated_at="2020-01-01T00:00:00+00:00",
        )

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "heartbeat"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert data["state"] == "processing"
        assert data["updated_at"] != "2020-01-01T00:00:00+00:00"

    def test_session_end_deletes_file(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")
        assert path.exists()

        with _patch_maelstrom_dir(tmp_path):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert not path.exists()

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


class TestSessionEndAutoClose:
    """`session record session-end` closes the task its session launched.

    The launching `mael task run` exports MAEL_TASK_ID into the Claude process,
    which the hook subprocess inherits; ending the session is the completion
    signal that moves the still-in-progress task to done.
    """

    def _setup(self, tmp_path, monkeypatch, *, status):
        """Create a task in ``status`` and wire the auto-close collaborators.

        Returns the GitFileStore so callers can assert the task's final status.
        """
        store = GitFileStore(root=tmp_path / "tasks")
        task = model.create(store, project="proj", title="throwaway")
        if status != model.STATUS_TODO:
            model.move(store, "proj", task.id, status)

        monkeypatch.setenv("MAEL_TASK_ID", task.id)
        monkeypatch.setattr(
            session_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="proj", worktree=None),
        )
        monkeypatch.setattr(
            "maelstrom.task_store.get_maelstrom_dir", lambda: tmp_path
        )
        return store, task.id

    def test_in_progress_task_moves_to_done(self, tmp_path, monkeypatch):
        store, task_id = self._setup(
            tmp_path, monkeypatch, status=model.STATUS_IN_PROGRESS
        )
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert not path.exists()  # session file still unlinked
        assert "closed task" in result.output
        key = model.find_key(store, "proj", task_id)
        assert key is not None
        assert model.status_from_key(key) == model.STATUS_DONE

    def test_post_action_fires_on_session_end(self, tmp_path, monkeypatch):
        # A task carrying post-action: linear.done under a linear.<ID> parent
        # flips Linear when the session ends and the task moves to done.
        store = GitFileStore(root=tmp_path / "tasks")
        task = model.create(
            store,
            project="proj",
            title="exec",
            parent="linear.NORT-12",
            post_action="linear.done",
        )
        model.move(store, "proj", task.id, model.STATUS_IN_PROGRESS)
        monkeypatch.setenv("MAEL_TASK_ID", task.id)
        monkeypatch.setattr(
            session_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="proj", worktree=None),
        )
        monkeypatch.setattr(
            "maelstrom.task_store.get_maelstrom_dir", lambda: tmp_path
        )

        calls = []
        from maelstrom import linear

        monkeypatch.setattr(
            linear, "set_issue_status", lambda i, s: calls.append((i, s))
        )

        sessions = tmp_path / "sessions"
        _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert calls == [("NORT-12", "done")]
        key = model.find_key(store, "proj", task.id)
        assert key is not None
        assert model.status_from_key(key) == model.STATUS_DONE

    def test_no_task_id_is_noop(self, tmp_path, monkeypatch):
        store, task_id = self._setup(
            tmp_path, monkeypatch, status=model.STATUS_IN_PROGRESS
        )
        monkeypatch.delenv("MAEL_TASK_ID", raising=False)
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert not path.exists()  # session file still unlinked
        # Task untouched: still in-progress.
        key = model.find_key(store, "proj", task_id)
        assert key is not None
        assert model.status_from_key(key) == model.STATUS_IN_PROGRESS

    def test_already_terminal_task_left_untouched(self, tmp_path, monkeypatch):
        store, task_id = self._setup(
            tmp_path, monkeypatch, status=model.STATUS_DONE
        )
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert not path.exists()
        key = model.find_key(store, "proj", task_id)
        assert key is not None
        assert model.status_from_key(key) == model.STATUS_DONE

    def test_cancelled_task_not_reopened(self, tmp_path, monkeypatch):
        store, task_id = self._setup(
            tmp_path, monkeypatch, status=model.STATUS_CANCELLED
        )
        sessions = tmp_path / "sessions"
        _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        key = model.find_key(store, "proj", task_id)
        assert key is not None
        assert model.status_from_key(key) == model.STATUS_CANCELLED

    def test_missing_task_does_not_crash(self, tmp_path, monkeypatch):
        # MAEL_TASK_ID points at a task that doesn't exist in the store.
        GitFileStore(root=tmp_path / "tasks")  # empty store
        monkeypatch.setenv("MAEL_TASK_ID", "2026-01-01.1")
        monkeypatch.setattr(
            session_cli,
            "resolve_context",
            lambda *a, **k: SimpleNamespace(project="proj", worktree=None),
        )
        monkeypatch.setattr(
            "maelstrom.task_store.get_maelstrom_dir", lambda: tmp_path
        )
        sessions = tmp_path / "sessions"
        path = _write_session(sessions, "s1", session_id="abc")

        with _patch_maelstrom_dir(tmp_path):
            result = CliRunner().invoke(
                cli,
                ["session", "record", "session-end"],
                input=json.dumps({"session_id": "abc"}),
            )

        assert result.exit_code == 0, result.output
        assert not path.exists()  # session file still unlinked


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
            now = datetime.now(timezone.utc).isoformat()
            _write_session(
                sessions, "live",
                channel_port=port,
                state="processing",
                updated_at=now,
            )

            with _patch_maelstrom_dir(tmp_path):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])

            assert result.exit_code == 0, result.output
            assert "processing" in result.output
            assert "STATE" in result.output  # header rendered
        finally:
            srv.close()

    def test_stale_processing_rewritten_to_idle(self, tmp_path):
        # Live channel port but stale updated_at — should be downgraded.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        try:
            sessions = tmp_path / "sessions"
            path = _write_session(
                sessions, "stale",
                channel_port=port,
                state="processing",
                updated_at="2020-01-01T00:00:00+00:00",
            )

            with _patch_maelstrom_dir(tmp_path):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])

            assert result.exit_code == 0, result.output
            assert "idle" in result.output
            assert "processing" not in result.output
            # File rewritten too.
            assert json.loads(path.read_text())["state"] == "idle"
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
