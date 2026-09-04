"""Tests for maelstrom.desk_store backends."""

import json

import pytest

from maelstrom.desk_store import InMemoryDeskStore, JsonDeskStore

TABLE = {
    "task:askastro/2026-06-11.1": {
        "id": "task:askastro/2026-06-11.1",
        "addedAt": "2026-09-04T09:00:00Z",
    }
}


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    """Run each contract test against both backends."""
    if request.param == "memory":
        return InMemoryDeskStore()
    return JsonDeskStore(path=tmp_path / "desk.json")


class TestDeskStoreContract:
    """Shared contract both backends must satisfy."""

    def test_load_of_nothing_is_empty(self, store):
        assert store.load() == {}

    def test_save_then_load_round_trips(self, store):
        store.save(TABLE)
        assert store.load() == TABLE

    def test_save_replaces_the_whole_table(self, store):
        store.save(TABLE)
        store.save({})
        assert store.load() == {}

    def test_load_does_not_share_a_mutable_reference(self, store):
        store.save(TABLE)
        loaded = store.load()
        loaded["task:askastro/2026-06-11.1"]["addedAt"] = "changed"
        assert store.load() == TABLE

    def test_save_does_not_keep_the_caller_s_reference(self, store):
        table = {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}
        store.save(table)
        table["task:c/d"] = {"id": "task:c/d", "addedAt": "t"}
        assert store.load() == {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}


class TestJsonDeskStore:
    """JsonDeskStore-specific behaviour: layout, atomicity, corruption."""

    def test_on_disk_layout(self, tmp_path):
        path = tmp_path / "desk.json"
        JsonDeskStore(path=path).save(TABLE)
        assert json.loads(path.read_text()) == TABLE

    def test_corrupt_json_loads_empty(self, tmp_path):
        path = tmp_path / "desk.json"
        path.write_text("not valid json{{{")
        assert JsonDeskStore(path=path).load() == {}

    def test_save_is_atomic_no_temp_left_behind(self, tmp_path):
        JsonDeskStore(path=tmp_path / "desk.json").save(TABLE)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["desk.json"]

    def test_a_malformed_entry_is_dropped(self, tmp_path):
        """The file is user-editable, so a bad entry must not reach the wire."""
        path = tmp_path / "desk.json"
        path.write_text(
            json.dumps(
                {
                    "task:a/1": {"id": "task:a/1", "addedAt": "t"},
                    "task:a/2": "not an entry",
                    "task:a/3": {"id": "task:a/3"},
                }
            )
        )
        assert JsonDeskStore(path=path).load() == {
            "task:a/1": {"id": "task:a/1", "addedAt": "t"}
        }

    def test_an_unprefixed_entry_is_migrated_to_a_task_id(self, tmp_path):
        """A desk written before desk ids carried a kind held bare task ids."""
        path = tmp_path / "desk.json"
        path.write_text(json.dumps({"a/1": {"id": "a/1", "addedAt": "t"}}))
        assert JsonDeskStore(path=path).load() == {
            "task:a/1": {"id": "task:a/1", "addedAt": "t"}
        }

    def test_path_defaults_to_the_maelstrom_dir(self, tmp_path, monkeypatch):
        """A path-less store resolves its path lazily via get_maelstrom_dir."""
        monkeypatch.setattr("maelstrom.desk_store.get_maelstrom_dir", lambda: tmp_path)
        store = JsonDeskStore()
        assert store.path == tmp_path / "desk.json"
        store.save(TABLE)
        assert (tmp_path / "desk.json").is_file()
