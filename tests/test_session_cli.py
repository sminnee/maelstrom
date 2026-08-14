"""Tests for maelstrom.session_cli module."""

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom import session_cli
from maelstrom import task as model
from maelstrom.task_index import SqliteTaskIndex, TaskMeta
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
    if "mael_task_id" in overrides:
        data["mael_task_id"] = overrides["mael_task_id"]
    path = sessions_dir / f"{key}.json"
    path.write_text(json.dumps(data))
    return path


def _patch_maelstrom_dir(tmp_path: Path):
    """Patch the ~/.maelstrom dir used by session reads to tmp_path."""
    return patch("maelstrom.session_store.get_maelstrom_dir", return_value=tmp_path)


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
        from maelstrom.integrations import linear

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


def _patch_live(sessions):
    """Patch the live-process sweep `session list` drives off."""
    from maelstrom import session_discovery

    return patch.object(
        session_discovery, "all_live_sessions", return_value=list(sessions)
    )


def _patch_pid_lookup(pid, cwd="/w/alpha"):
    """Patch the single-process lookup the sweep-blind pid fallback uses.

    ``pid=None`` answers "no session for that pid" — a dead pid, or a live one
    that is not a ``claude``. Patched rather than run for real so no test shells
    out to `ps`/`lsof` and depends on what is running on the machine.
    """
    from maelstrom import session_discovery

    found = None if pid is None else _live(pid, cwd)
    return patch.object(session_discovery, "session_for_pid", return_value=found)


def _live(pid, cwd):
    from maelstrom.session_discovery import LiveSession

    return LiveSession(pid=pid, cwd=Path(cwd))


class TestSessionList:
    @pytest.fixture(autouse=True)
    def _fresh_index(self, monkeypatch):
        # Default every session-list test to an empty in-memory index so none
        # touches the real on-disk notebook. Tests that assert an index hit
        # override this with their own populated index.
        monkeypatch.setattr(
            session_cli, "_task_index", lambda: SqliteTaskIndex(":memory:")
        )

    def test_empty_when_no_live_processes(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live([]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0
        assert "No active Claude Code sessions." in result.output

    def test_live_process_listed_without_registry(self, tmp_path):
        # A running claude with no registry entry still lists (PID + CWD),
        # STATE/AGE simply blank.
        with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output
        assert "/w/alpha" in result.output

    def test_state_enriched_from_matching_registry_entry(self, tmp_path):
        # Live channel port so the registry entry counts as live enrichment.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            sessions = tmp_path / "sessions"
            now = datetime.now(timezone.utc).isoformat()
            _write_session(
                sessions, "live",
                cwd="/w/alpha", pid=4242,
                channel_port=port, state="processing", updated_at=now,
            )
            with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0, result.output
            assert "processing" in result.output
        finally:
            srv.close()

    def test_stale_processing_shown_as_idle(self, tmp_path):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            sessions = tmp_path / "sessions"
            _write_session(
                sessions, "stale",
                cwd="/w/alpha", pid=4242,
                channel_port=port, state="processing",
                updated_at="2020-01-01T00:00:00+00:00",
            )
            with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0, result.output
            assert "idle" in result.output
            assert "processing" not in result.output
        finally:
            srv.close()

    def test_gc_removes_dead_port(self, tmp_path):
        sessions = tmp_path / "sessions"
        # Use a port that is almost certainly not listening.
        path = _write_session(sessions, "dead", channel_port=1)

        with _patch_maelstrom_dir(tmp_path), _patch_live([]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        # GC is a side pass: the dead-port file is cleaned even though listing
        # is driven by live processes.
        assert not path.exists()

    def test_corrupt_file_removed(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        bad = sessions / "bad.json"
        bad.write_text("not-json")

        with _patch_maelstrom_dir(tmp_path), _patch_live([]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])

        assert result.exit_code == 0
        assert not bad.exists()

    def test_task_column_from_session_id_index_lookup(self, tmp_path, monkeypatch):
        # A live session whose --session-id resolves via the task index shows TASK.
        sid = model.session_id_for("askastro", "daily.maintenance.2026-07-03.2")
        sess = _live(4242, "/w/delta")
        sess.session_id = sid
        index = SqliteTaskIndex(":memory:")
        index.upsert(
            TaskMeta(
                project="askastro",
                id="daily.maintenance.2026-07-03.2",
                status="in-progress",
                session_id=sid,
            )
        )
        monkeypatch.setattr(session_cli, "_task_index", lambda: index)
        with _patch_maelstrom_dir(tmp_path), _patch_live([sess]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "TASK" in result.output
        assert "daily.maintenance.2026-07-03.2" in result.output

    def test_task_column_cold_index_falls_back_to_registry(self, tmp_path, monkeypatch):
        # The index doesn't resolve the session (cold/stale), but the registry
        # recorded mael_task_id — TASK falls back to it.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            sid = model.session_id_for("askastro", "2026-07-03.7")
            sess = _live(4242, "/w/alpha")
            sess.session_id = sid
            sessions = tmp_path / "sessions"
            now = datetime.now(timezone.utc).isoformat()
            _write_session(
                sessions, "live",
                cwd="/w/alpha", pid=4242, session_id=sid,
                channel_port=port, state="idle", updated_at=now,
                mael_task_id="2026-07-03.7",
            )
            # Cold index: nothing to resolve the session-id.
            monkeypatch.setattr(
                session_cli, "_task_index", lambda: SqliteTaskIndex(":memory:")
            )
            with _patch_maelstrom_dir(tmp_path), _patch_live([sess]):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0, result.output
            assert "2026-07-03.7" in result.output
        finally:
            srv.close()

    def test_task_column_falls_back_to_registry_task_id(self, tmp_path):
        # No forward match (no notebook), but the registry recorded mael_task_id.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            sessions = tmp_path / "sessions"
            now = datetime.now(timezone.utc).isoformat()
            _write_session(
                sessions, "live",
                cwd="/w/alpha", pid=4242,
                channel_port=port, state="idle", updated_at=now,
                mael_task_id="2026-07-03.7",
            )
            with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
                runner = CliRunner()
                result = runner.invoke(cli, ["session", "list"])
            assert result.exit_code == 0, result.output
            assert "2026-07-03.7" in result.output
        finally:
            srv.close()

    def test_task_column_blank_for_non_mael_session(self, tmp_path):
        # A bare claude (no --session-id, no registry) shows a blank TASK.
        with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_id_column_shows_the_session_id_prefix(self, tmp_path):
        # The ID column is what makes a listed session addressable: it prints the
        # first 8 characters, which is what `session info <prefix>` takes.
        sess = _live(4242, "/w/alpha")
        sess.session_id = "97894d02-f335-5ea3-9d9f-050330a4902b"
        with _patch_maelstrom_dir(tmp_path), _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "ID" in result.output
        assert "97894d02" in result.output

    def test_id_column_blank_for_a_bare_claude(self, tmp_path):
        # A session started outside mael carries no --session-id. The ID cell
        # must be empty rather than "None" — the pid is how you name it.
        with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "None" not in result.output
        row = next(line for line in result.output.splitlines() if "4242" in line)
        header = result.output.splitlines()[0]
        start = header.index("ID")
        assert row[start:start + len("ID")].strip() == ""


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


class TestSessionInfo:
    """``mael session info [ID]`` — the fields for one session.

    The handle defaults from the environment, so the four cases are the same
    ones ``TestGetStatus`` pins for ``mael task get-status``: explicit argument,
    environment fallback, neither, and an unknown id.
    """

    _SID = "97894d02-f335-5ea3-9d9f-050330a4902b"

    @pytest.fixture(autouse=True)
    def _fresh_index(self, monkeypatch):
        monkeypatch.setattr(
            session_cli, "_task_index", lambda: SqliteTaskIndex(":memory:")
        )
        # A session command must never read the ambient session env of the
        # process running the tests.
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_PID", raising=False)

    def _sess(self, pid=4242, cwd="/w/alpha", session_id=None):
        s = _live(pid, cwd)
        s.session_id = session_id
        return s

    def test_explicit_id_shows_the_session(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live(
            [self._sess(session_id=self._SID)]
        ):
            result = CliRunner().invoke(cli, ["session", "info", self._SID])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output
        assert "4242" in result.output
        assert "/w/alpha" in result.output

    def test_resolves_an_id_prefix(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live(
            [self._sess(session_id=self._SID)]
        ):
            result = CliRunner().invoke(cli, ["session", "info", "97894d02"])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output

    def test_resolves_a_pid(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "4242"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_defaults_to_the_live_session_id_from_the_environment(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", self._SID)
        with _patch_maelstrom_dir(tmp_path), _patch_live(
            [self._sess(session_id=self._SID)]
        ):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output

    def test_falls_back_to_claude_pid_when_the_live_id_does_not_match(
        self, tmp_path, monkeypatch
    ):
        # After a /clear the live id is not the one in argv, so the pid is the
        # only handle that still resolves. Both are tried, in that order.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cleared-and-unknown-id")
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with _patch_maelstrom_dir(tmp_path), _patch_live(
            [self._sess(session_id=self._SID)]
        ):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_an_explicit_id_never_falls_back_to_the_environment(
        self, tmp_path, monkeypatch
    ):
        # A named session that does not exist is an error, even when the
        # environment could have resolved some other session. Falling back would
        # silently show the wrong session.
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with _patch_maelstrom_dir(tmp_path), _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_claude_pid_resolves_when_the_sweep_cannot_see_this_session(
        self, tmp_path, monkeypatch
    ):
        # The real case this defends: `pgrep` does not list the claude running
        # `mael`, so the sweep is blind to the current session. CLAUDE_PID is
        # authoritative for "which process am I", so `session info` still works —
        # with the process-only fields, since there is no swept session to enrich.
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with _patch_maelstrom_dir(tmp_path), _patch_live([]), _patch_pid_lookup(4242):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_errors_when_no_id_and_no_environment(self, tmp_path):
        # Both variables that would have resolved it get named, so a user
        # debugging a hook environment does not chase only one of them.
        with _patch_maelstrom_dir(tmp_path), _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code != 0
        assert "CLAUDE_CODE_SESSION_ID" in result.output
        assert "CLAUDE_PID" in result.output

    def test_errors_on_an_unknown_id(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_errors_on_an_ambiguous_prefix(self, tmp_path):
        sessions = [
            self._sess(pid=1, cwd="/w/a", session_id="abcd1111-0000-0000-0000-000000000000"),
            self._sess(pid=2, cwd="/w/b", session_id="abcd2222-0000-0000-0000-000000000000"),
        ]
        with _patch_maelstrom_dir(tmp_path), _patch_live(sessions):
            result = CliRunner().invoke(cli, ["session", "info", "abcd"])
        assert result.exit_code != 0
        assert "ambiguous" in result.output

    def test_json_output_via_the_global_flag(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live(
            [self._sess(session_id=self._SID)]
        ):
            result = CliRunner().invoke(
                cli, ["--json", "session", "info", self._SID]
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == self._SID
        assert data["pid"] == 4242
        assert data["cwd"] == "/w/alpha"

    def test_shows_the_task_when_the_index_resolves_it(self, tmp_path, monkeypatch):
        sid = model.session_id_for("askastro", "2026-07-03.7")
        index = SqliteTaskIndex(":memory:")
        index.upsert(
            TaskMeta(
                project="askastro", id="2026-07-03.7",
                status="in-progress", session_id=sid,
            )
        )
        monkeypatch.setattr(session_cli, "_task_index", lambda: index)
        with _patch_maelstrom_dir(tmp_path), _patch_live([self._sess(session_id=sid)]):
            result = CliRunner().invoke(cli, ["session", "info", sid])
        assert result.exit_code == 0, result.output
        assert "2026-07-03.7" in result.output


class TestSessionEnd:
    """``mael session end [ID]`` — stop one session without closing its worktree."""

    _SID = "97894d02-f335-5ea3-9d9f-050330a4902b"

    @pytest.fixture(autouse=True)
    def _no_ambient_env(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_PID", raising=False)

    def test_stops_the_named_session(self, tmp_path, monkeypatch):
        sess = _live(4242, "/w/alpha")
        sess.session_id = self._SID
        stopped = []

        def fake_stop(sessions, **kwargs):
            stopped.extend(sessions)
            return [f"claude session (pid {s.pid}): stopped" for s in sessions]

        monkeypatch.setattr(session_cli, "stop_sessions", fake_stop)
        with _patch_maelstrom_dir(tmp_path), _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "end", "97894d02"])

        assert result.exit_code == 0, result.output
        assert [s.pid for s in stopped] == [4242]
        assert "stopped" in result.output

    def test_ending_our_own_session_says_so_and_stops_nothing(
        self, tmp_path, monkeypatch
    ):
        # A handle that names the `mael` process must not signal it, and must say
        # why nothing happened — silence reads as a crash.
        import os

        sess = _live(os.getpid(), "/w/alpha")
        stopped = []
        monkeypatch.setattr(
            session_cli, "stop_sessions", lambda s, **kw: stopped.extend(s) or []
        )
        monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
        with _patch_maelstrom_dir(tmp_path), _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "end"])

        assert result.exit_code == 0, result.output
        assert stopped == []  # never even handed to the stopper
        assert "this session" in result.output

    def test_errors_on_an_unknown_id(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "end", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_stops_a_live_pid_the_sweep_cannot_see(self, tmp_path, monkeypatch):
        # `pgrep` does not report every live claude — the session running `mael`
        # is itself missing from the sweep on some setups. A pid read straight
        # from the process resolves even with an empty sweep.
        stopped = []
        monkeypatch.setattr(
            session_cli, "stop_sessions",
            lambda s, **kw: stopped.extend(s) or ["stopped"],
        )
        with _patch_maelstrom_dir(tmp_path), _patch_live([]), _patch_pid_lookup(4242):
            result = CliRunner().invoke(cli, ["session", "end", "4242"])

        assert result.exit_code == 0, result.output
        assert [s.pid for s in stopped] == [4242]

    def test_a_pid_that_is_no_session_is_an_error(self, tmp_path, monkeypatch):
        # A dead pid, or a live one that is not a claude. `session end` signals
        # what it resolves, so neither may resolve — a typo must not reach an
        # unrelated process.
        stopped = []
        monkeypatch.setattr(
            session_cli, "stop_sessions",
            lambda s, **kw: stopped.extend(s) or ["stopped"],
        )
        with _patch_maelstrom_dir(tmp_path), _patch_live([]), _patch_pid_lookup(None):
            result = CliRunner().invoke(cli, ["session", "end", "4242"])
        assert result.exit_code != 0
        assert "4242" in result.output
        assert stopped == []

    def test_errors_when_no_id_and_no_environment(self, tmp_path):
        with _patch_maelstrom_dir(tmp_path), _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "end"])
        assert result.exit_code != 0
        assert "CLAUDE_CODE_SESSION_ID" in result.output
