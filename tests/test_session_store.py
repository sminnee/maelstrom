"""Tests for maelstrom.session_store: registry reads and task↔session lookup."""

import json
import socket
from pathlib import Path
from unittest.mock import patch

from maelstrom import session_store
from maelstrom import task as model


def _write_session(sessions_dir: Path, key: str, **overrides) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "session_key": key,
        "session_id": overrides.get("session_id", key),
        "cwd": overrides.get("cwd", "/tmp/proj"),
        "pid": overrides.get("pid", 12345),
        "mael_task_id": overrides.get("mael_task_id"),
        "state": overrides.get("state", "idle"),
        "channel_port": overrides.get("channel_port", 0),
    }
    path = sessions_dir / f"{key}.json"
    path.write_text(json.dumps(data))
    return path


def _patch_dir(tmp_path: Path):
    return patch("maelstrom.session_store.get_maelstrom_dir", return_value=tmp_path)


def _listener():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


class TestLiveSessions:
    def test_no_dir_is_empty(self, tmp_path):
        with _patch_dir(tmp_path):
            assert session_store.live_sessions() == []

    def test_dead_port_skipped(self, tmp_path):
        _write_session(tmp_path / "sessions", "dead", channel_port=1)
        with _patch_dir(tmp_path):
            assert session_store.live_sessions() == []

    def test_live_port_listed(self, tmp_path):
        srv, port = _listener()
        try:
            _write_session(tmp_path / "sessions", "live", channel_port=port)
            with _patch_dir(tmp_path):
                live = session_store.live_sessions()
            assert len(live) == 1
            assert live[0]["session_key"] == "live"
        finally:
            srv.close()

    def test_corrupt_file_skipped_not_deleted(self, tmp_path):
        sdir = tmp_path / "sessions"
        sdir.mkdir(parents=True)
        bad = sdir / "bad.json"
        bad.write_text("not-json")
        with _patch_dir(tmp_path):
            assert session_store.live_sessions() == []
        assert bad.exists()  # GC stays with `session list`, not the reader


class TestLivenessCheck:
    def test_zero_and_falsy_are_dead(self):
        assert session_store.liveness_check(0) is False
        assert session_store.liveness_check("") is False
        assert session_store.liveness_check(None) is False

    def test_non_numeric_is_dead_not_raised(self):
        # channel_port comes from external JSON — garbage must not raise.
        assert session_store.liveness_check("not-a-port") is False
        assert session_store.liveness_check({"x": 1}) is False

    def test_out_of_range_is_dead(self):
        assert session_store.liveness_check(70000) is False
        assert session_store.liveness_check(-1) is False

    def test_string_port_coerced(self):
        # An unused port given as a string is parsed and then probed (dead).
        assert session_store.liveness_check("1") is False


class TestSessionMatchesTask:
    def test_matches_by_deterministic_session_id(self):
        sid = model.session_id_for("proj", "t1")
        assert session_store.session_matches_task(
            {"session_id": sid}, "proj", "t1"
        )

    def test_matches_by_session_key(self):
        sid = model.session_id_for("proj", "t1")
        assert session_store.session_matches_task(
            {"session_key": sid}, "proj", "t1"
        )

    def test_matches_by_recorded_task_id(self):
        assert session_store.session_matches_task(
            {"session_id": "unrelated", "mael_task_id": "t1"}, "proj", "t1"
        )

    def test_no_match(self):
        assert not session_store.session_matches_task(
            {"session_id": "x", "mael_task_id": "other"}, "proj", "t1"
        )


class TestFindLiveSessionForTask:
    def test_found_by_session_id(self, tmp_path):
        srv, port = _listener()
        try:
            sid = model.session_id_for("proj", "t1")
            _write_session(
                tmp_path / "sessions", "s", session_id=sid, channel_port=port
            )
            with _patch_dir(tmp_path):
                found = session_store.find_live_session_for_task("proj", "t1")
            assert found is not None
            assert found["session_id"] == sid
        finally:
            srv.close()

    def test_dead_session_not_found(self, tmp_path):
        sid = model.session_id_for("proj", "t1")
        _write_session(
            tmp_path / "sessions", "s", session_id=sid, channel_port=1
        )
        with _patch_dir(tmp_path):
            assert session_store.find_live_session_for_task("proj", "t1") is None

    def test_other_task_not_found(self, tmp_path):
        srv, port = _listener()
        try:
            sid = model.session_id_for("proj", "other")
            _write_session(
                tmp_path / "sessions", "s", session_id=sid, channel_port=port
            )
            with _patch_dir(tmp_path):
                assert (
                    session_store.find_live_session_for_task("proj", "t1") is None
                )
        finally:
            srv.close()
