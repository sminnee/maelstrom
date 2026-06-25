"""Tests for the leaf utilities in maelstrom.util."""

import json
import os
import re
import stat

import pytest

from maelstrom.util import atomic_write_json, harden_path, locked_file, now_iso


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestNowIso:
    def test_returns_utc_iso_with_offset(self):
        result = now_iso()
        # datetime.now(timezone.utc).isoformat() always ends in +00:00
        assert result.endswith("+00:00")
        # Parseable ISO 8601 with date + time components
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result)


class TestAtomicWriteJson:
    def test_writes_content_and_roundtrips(self, tmp_path):
        path = tmp_path / "state.json"
        data = {"b": 2, "a": 1}
        atomic_write_json(path, data)
        assert json.loads(path.read_text()) == data

    def test_defaults_indent_and_sort_keys(self, tmp_path):
        path = tmp_path / "state.json"
        atomic_write_json(path, {"b": 2, "a": 1})
        # sort_keys=True -> a before b; indent=2 -> newlines present
        text = path.read_text()
        assert text == json.dumps({"a": 1, "b": 2}, indent=2, sort_keys=True)

    def test_no_temp_file_left_behind(self, tmp_path):
        path = tmp_path / "state.json"
        atomic_write_json(path, {"x": 1})
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "state.json"
        atomic_write_json(path, {"ok": True})
        assert json.loads(path.read_text()) == {"ok": True}


class TestHardenPath:
    def test_tightens_loose_file_and_returns_true(self, tmp_path):
        path = tmp_path / "secret"
        path.write_text("x")
        os.chmod(path, 0o644)
        assert harden_path(path, 0o600) is True
        assert _mode(path) == 0o600

    def test_noop_when_already_tight_returns_false(self, tmp_path):
        path = tmp_path / "secret"
        path.write_text("x")
        os.chmod(path, 0o600)
        assert harden_path(path, 0o600) is False
        assert _mode(path) == 0o600

    def test_never_widens_a_tighter_file(self, tmp_path):
        path = tmp_path / "secret"
        path.write_text("x")
        os.chmod(path, 0o400)  # tighter than 0o600
        assert harden_path(path, 0o600) is False
        assert _mode(path) == 0o400

    def test_tightens_loose_dir(self, tmp_path):
        d = tmp_path / "d"
        d.mkdir()
        os.chmod(d, 0o755)
        assert harden_path(d, 0o700) is True
        assert _mode(d) == 0o700


class TestLockedFile:
    """Tests for the locked_file transaction context manager."""

    def test_writes_buffered_text_on_clean_exit(self, tmp_path):
        path = tmp_path / "f.env"
        path.write_text("A=1\n")
        with locked_file(path) as txn:
            assert txn.text == "A=1\n"
            txn.text = "A=1\nB=2\n"
        assert path.read_text() == "A=1\nB=2\n"

    def test_no_write_when_unchanged(self, tmp_path):
        path = tmp_path / "f.env"
        path.write_text("A=1\n")
        with locked_file(path) as txn:
            _ = txn.text  # read but do not modify
        assert path.read_text() == "A=1\n"

    def test_no_write_on_exception(self, tmp_path):
        path = tmp_path / "f.env"
        path.write_text("A=1\n")

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            with locked_file(path) as txn:
                txn.text = "A=1\nB=2\n"
                raise Boom()
        # Buffered change not flushed; lock released so a re-acquire succeeds.
        assert path.read_text() == "A=1\n"
        with locked_file(path) as txn:
            assert txn.text == "A=1\n"

    def test_creates_missing_file(self, tmp_path):
        path = tmp_path / "new.env"
        with locked_file(path) as txn:
            assert txn.text == ""
            txn.text = "X=1\n"
        assert path.read_text() == "X=1\n"

    def test_missing_file_without_create_raises(self, tmp_path):
        path = tmp_path / "absent.env"
        with pytest.raises(FileNotFoundError):
            with locked_file(path, create=False):
                pass

    def test_second_acquisition_times_out_while_held(self, tmp_path):
        path = tmp_path / "f.env"
        path.write_text("A=1\n")
        # Hold the lock via a raw fd, then assert locked_file gives up.
        import fcntl as _fcntl

        held = open(path, "a+")
        _fcntl.flock(held, _fcntl.LOCK_EX)
        try:
            with pytest.raises(TimeoutError):
                with locked_file(path, timeout=0.3):
                    pass
        finally:
            _fcntl.flock(held, _fcntl.LOCK_UN)
            held.close()

    # --- permission guarantees ---

    def test_new_file_created_at_0o600(self, tmp_path):
        path = tmp_path / "new.env"
        with locked_file(path) as txn:
            txn.text = "SECRET=1\n"
        assert _mode(path) == 0o600

    def test_parent_dir_tightened_to_0o700_on_create(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir(mode=0o755)
        os.chmod(d, 0o755)
        path = d / "new.env"
        with locked_file(path) as txn:
            txn.text = "SECRET=1\n"
        assert _mode(d) == 0o700

    def test_existing_loose_file_tightened_on_noop_exit(self, tmp_path):
        path = tmp_path / "f.env"
        path.write_text("A=1\n")
        os.chmod(path, 0o644)
        with locked_file(path) as txn:
            _ = txn.text  # no modification
        assert _mode(path) == 0o600
        # Content preserved.
        assert path.read_text() == "A=1\n"

    def test_custom_mode_respected(self, tmp_path):
        path = tmp_path / "f.env"
        with locked_file(path, mode=0o640) as txn:
            txn.text = "A=1\n"
        assert _mode(path) == 0o640
