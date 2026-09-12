"""Tests for maelstrom.desk_store backends."""

import inspect
import json

import pytest

from maelstrom.desk_store import InMemoryDeskStore, JsonDeskStore, SqliteDeskStore
from maelstrom.state_db import StateDb

TABLE = {
    "task:askastro/2026-06-11.1": {
        "id": "task:askastro/2026-06-11.1",
        "addedAt": "2026-09-04T09:00:00Z",
    }
}


@pytest.fixture(params=["memory", "json", "sqlite"])
async def store(request, tmp_path):
    """Run each contract test against every backend.

    The three do not agree on sync or async — the Protocol widened rather than
    flipped, so the two older backends stay sync — which is why the contract
    tests go through :func:`load`/:func:`save` below rather than calling the
    store directly.
    """
    if request.param == "memory":
        yield InMemoryDeskStore()
        return
    if request.param == "json":
        yield JsonDeskStore(path=tmp_path / "desk.json")
        return
    db = StateDb(":memory:")
    await db.migrate()
    # A path inside tmp_path, so the one-time import cannot reach the
    # developer's real ~/.maelstrom/desk.json.
    yield SqliteDeskStore(db, json_path=tmp_path / "desk.json")
    db.close()


async def load(store):
    """``store.load()``, awaiting it when the backend is async."""
    result = store.load()
    return await result if inspect.isawaitable(result) else result


async def save(store, table):
    """``store.save(table)``, awaiting it when the backend is async."""
    result = store.save(table)
    if inspect.isawaitable(result):
        await result


class TestDeskStoreContract:
    """Shared contract both backends must satisfy."""

    async def test_load_of_nothing_is_empty(self, store):
        assert await load(store) == {}

    async def test_save_then_load_round_trips(self, store):
        await save(store, TABLE)
        assert await load(store) == TABLE

    async def test_save_replaces_the_whole_table(self, store):
        await save(store, TABLE)
        await save(store, {})
        assert await load(store) == {}

    async def test_load_does_not_share_a_mutable_reference(self, store):
        await save(store, TABLE)
        loaded = await load(store)
        loaded["task:askastro/2026-06-11.1"]["addedAt"] = "changed"
        assert await load(store) == TABLE

    async def test_save_does_not_keep_the_caller_s_reference(self, store):
        table = {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}
        await save(store, table)
        table["task:c/d"] = {"id": "task:c/d", "addedAt": "t"}
        assert await load(store) == {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}


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


@pytest.fixture
async def db():
    """A migrated in-memory database, for the SQLite backend's own tests."""
    state_db = StateDb(":memory:")
    await state_db.migrate()
    yield state_db
    state_db.close()


class TestSqliteDeskStore:
    """Slices 20 and 21: the desk on the state database, and its one-time import."""

    async def test_save_writes_one_cut(self, db, tmp_path):
        """The whole table is one batch, so a restart sees no half-saved desk."""
        store = SqliteDeskStore(db, json_path=tmp_path / "desk.json")
        await store.save(TABLE)
        rows = await db.read_all("desk")
        assert {row["revision"] for row in rows} == {1}

    async def test_a_save_that_changes_nothing_raises_no_notice(self, db, tmp_path):
        store = SqliteDeskStore(db, json_path=tmp_path / "desk.json")
        await store.save(TABLE)
        cursor = await db.revision()
        await store.save(TABLE)
        assert await db.notices_since(cursor) == ({}, cursor)

    async def test_a_removed_entry_is_a_notice(self, db, tmp_path):
        store = SqliteDeskStore(db, json_path=tmp_path / "desk.json")
        await store.save(TABLE)
        cursor = await db.revision()
        await store.save({})
        notices, _ = await db.notices_since(cursor)
        assert notices == {"desk": set(TABLE)}

    async def test_an_existing_desk_json_imports_once(self, db, tmp_path):
        path = tmp_path / "desk.json"
        path.write_text(json.dumps(TABLE))
        store = SqliteDeskStore(db, json_path=path)

        assert await store.load() == TABLE
        assert path.is_file(), "the file is left as a fallback, not deleted"

        # A second open does not re-import: a desk saved as empty must stay
        # empty rather than springing back from the file.
        await store.save({})
        assert await SqliteDeskStore(db, json_path=path).load() == {}

    async def test_the_import_applies_the_bare_id_migration(self, db, tmp_path):
        """The JSON backend's own fixes still run: the import loads through it."""
        path = tmp_path / "desk.json"
        path.write_text(json.dumps({"a/1": {"id": "a/1", "addedAt": "t"}}))
        store = SqliteDeskStore(db, json_path=path)
        assert await store.load() == {"task:a/1": {"id": "task:a/1", "addedAt": "t"}}

    async def test_no_desk_json_imports_nothing(self, db, tmp_path):
        store = SqliteDeskStore(db, json_path=tmp_path / "absent.json")
        assert await store.load() == {}

    async def test_the_import_lands_with_its_marker(self, db, tmp_path):
        """The rows and the marker share a transaction.

        Set afterwards, a crash between the two would re-import on the next
        open — and a desk the user had since emptied would come back, which is
        the one thing the marker exists to stop.
        """
        path = tmp_path / "desk.json"
        path.write_text(json.dumps(TABLE))
        await SqliteDeskStore(db, json_path=path).load()
        assert await db.meta("desk_imported")
        assert len(await db.read_all("desk")) == len(TABLE)

    async def test_no_desk_json_still_marks_the_import_done(self, db, tmp_path):
        """Otherwise a desk.json appearing later would import over live rows."""
        store = SqliteDeskStore(db, json_path=tmp_path / "absent.json")
        assert await store.load() == {}
        assert await db.meta("desk_imported")

    async def test_an_unreadable_entry_is_dropped_not_raised(self, db, tmp_path):
        """A desk is a convenience; one bad row must not stop the server."""
        store = SqliteDeskStore(db, json_path=tmp_path / "desk.json")
        await store.save(TABLE)
        await db.upsert("desk", "task:a/bad", body="not valid json{{{")
        assert await store.load() == TABLE
