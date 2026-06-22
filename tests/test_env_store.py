"""Tests for maelstrom.env_store backends."""

import json

import pytest

from maelstrom.env_store import InMemoryEnvStore, JsonEnvStore


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    """Run each contract test against both backends."""
    if request.param == "memory":
        return InMemoryEnvStore()
    return JsonEnvStore(root=tmp_path)


class TestEnvStoreContract:
    """Shared contract both backends must satisfy."""

    def test_write_read_round_trip(self, store):
        store.write("proj/bravo.json", {"a": 1, "b": ["x", "y"]})
        assert store.read("proj/bravo.json") == {"a": 1, "b": ["x", "y"]}

    def test_read_missing_returns_none(self, store):
        assert store.read("proj/missing.json") is None

    def test_exists(self, store):
        assert store.exists("proj/bravo.json") is False
        store.write("proj/bravo.json", {"v": 1})
        assert store.exists("proj/bravo.json") is True

    def test_delete(self, store):
        store.write("proj/bravo.json", {"v": 1})
        store.delete("proj/bravo.json")
        assert store.read("proj/bravo.json") is None
        assert store.exists("proj/bravo.json") is False

    def test_delete_missing_is_noop(self, store):
        store.delete("proj/missing.json")  # should not raise

    def test_overwrite(self, store):
        store.write("proj/bravo.json", {"v": 1})
        store.write("proj/bravo.json", {"v": 2})
        assert store.read("proj/bravo.json") == {"v": 2}

    def test_list_dir_by_prefix(self, store):
        store.write("proj/alpha.json", {})
        store.write("proj/bravo.json", {})
        store.write("proj/_shared.json", {})
        store.write("other/charlie.json", {})

        keys = set(store.list_dir("proj/"))
        assert keys == {"proj/alpha.json", "proj/bravo.json", "proj/_shared.json"}

    def test_list_dir_empty(self, store):
        assert store.list_dir("nothing/") == []

    def test_read_does_not_share_mutable_reference(self, store):
        """Mutating a returned value must not corrupt the stored copy."""
        store.write("proj/bravo.json", {"services": ["web"]})
        loaded = store.read("proj/bravo.json")
        assert loaded is not None
        loaded["services"].append("worker")
        again = store.read("proj/bravo.json")
        assert again == {"services": ["web"]}


class TestJsonEnvStore:
    """JsonEnvStore-specific behaviour: layout, atomicity, corruption."""

    def test_on_disk_layout(self, tmp_path):
        store = JsonEnvStore(root=tmp_path)
        store.write("proj/bravo.json", {"b": 2, "a": 1})

        path = tmp_path / "proj" / "bravo.json"
        assert path.is_file()
        # indent=2, sort_keys=True so files round-trip byte-identically.
        assert path.read_text() == json.dumps({"b": 2, "a": 1}, indent=2, sort_keys=True)

    def test_creates_parent_dirs(self, tmp_path):
        store = JsonEnvStore(root=tmp_path)
        store.write("proj/bravo.json", {"v": 1})
        assert (tmp_path / "proj" / "bravo.json").exists()

    def test_corrupt_json_reads_none(self, tmp_path):
        store = JsonEnvStore(root=tmp_path)
        (tmp_path / "proj").mkdir()
        (tmp_path / "proj" / "bravo.json").write_text("not valid json{{{")
        assert store.read("proj/bravo.json") is None

    def test_write_is_atomic_no_temp_left_behind(self, tmp_path):
        """After a write, only the final file remains — no .tmp residue."""
        store = JsonEnvStore(root=tmp_path)
        store.write("proj/bravo.json", {"v": 1})

        proj_dir = tmp_path / "proj"
        files = sorted(p.name for p in proj_dir.iterdir())
        assert files == ["bravo.json"]
        assert not any(p.suffix == ".tmp" for p in proj_dir.iterdir())

    def test_write_replaces_completely(self, tmp_path):
        """A second write fully replaces the file (no leftover bytes)."""
        store = JsonEnvStore(root=tmp_path)
        store.write("proj/bravo.json", {"long": "x" * 100})
        store.write("proj/bravo.json", {"v": 1})
        # The whole file is the new value — os.replace swapped it wholesale.
        assert store.read("proj/bravo.json") == {"v": 1}

    def test_list_dir_returns_relative_keys(self, tmp_path):
        store = JsonEnvStore(root=tmp_path)
        store.write("proj/bravo.json", {})
        store.write("proj/_shared.json", {})
        assert sorted(store.list_dir("proj/")) == [
            "proj/_shared.json",
            "proj/bravo.json",
        ]

    def test_root_defaults_to_state_dir(self, tmp_path, monkeypatch):
        """A root-less store resolves its root lazily via get_maelstrom_dir."""
        monkeypatch.setattr(
            "maelstrom.env_store.get_maelstrom_dir", lambda: tmp_path
        )
        store = JsonEnvStore()
        assert store.root == tmp_path / "envs"
        store.write("proj/bravo.json", {"v": 1})
        assert (tmp_path / "envs" / "proj" / "bravo.json").exists()
