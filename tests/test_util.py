"""Tests for the leaf utilities in maelstrom.util."""

import json
import re

from maelstrom.util import atomic_write_json, now_iso


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
