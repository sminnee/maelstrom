"""Tests for maelstrom.desk_store backends."""

import json

import pytest

from maelstrom.desk_store import DeskStore, InMemoryDeskStore, SqliteDeskStore
from maelstrom.state_db.migrate import open_state_db

TABLE = {
    "task:askastro/2026-06-11.1": {
        "id": "task:askastro/2026-06-11.1",
        "addedAt": "2026-09-04T09:00:00Z",
    }
}


@pytest.fixture(params=["memory", "sqlite"])
async def store(request):
    """Run each contract test against every backend.

    Both are async and both subclass :class:`DeskStore`, so a contract test
    calls the store directly rather than through a sync-or-async helper.
    """
    if request.param == "memory":
        yield InMemoryDeskStore()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteDeskStore(db)
    db.close()


class TestDeskStoreContract:
    """Shared contract both backends must satisfy."""

    def test_every_backend_subclasses_the_contract(self):
        """Explicit, so a reader sees which classes claim it."""
        assert issubclass(InMemoryDeskStore, DeskStore)
        assert issubclass(SqliteDeskStore, DeskStore)

    async def test_load_of_nothing_is_empty(self, store):
        assert await store.load() == {}

    async def test_save_then_load_round_trips(self, store):
        await store.save(TABLE)
        assert await store.load() == TABLE

    async def test_save_replaces_the_whole_table(self, store):
        await store.save(TABLE)
        await store.save({})
        assert await store.load() == {}

    async def test_load_does_not_share_a_mutable_reference(self, store):
        await store.save(TABLE)
        loaded = await store.load()
        loaded["task:askastro/2026-06-11.1"]["addedAt"] = "changed"
        assert await store.load() == TABLE

    async def test_save_does_not_keep_the_caller_s_reference(self, store):
        table = {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}
        await store.save(table)
        table["task:c/d"] = {"id": "task:c/d", "addedAt": "t"}
        assert await store.load() == {"task:a/b": {"id": "task:a/b", "addedAt": "t"}}

    async def test_add_writes_one_entry_and_leaves_the_rest(self, store):
        await store.save(TABLE)
        entry = {"id": "task:a/2", "addedAt": "t"}
        await store.add("task:a/2", entry)
        assert await store.load() == {**TABLE, "task:a/2": entry}

    async def test_add_replaces_an_entry_already_there(self, store):
        await store.add("task:a/2", {"id": "task:a/2", "addedAt": "first"})
        await store.add("task:a/2", {"id": "task:a/2", "addedAt": "second"})
        assert await store.load() == {
            "task:a/2": {"id": "task:a/2", "addedAt": "second"}
        }

    async def test_remove_takes_one_entry_off(self, store):
        entry = {"id": "task:a/2", "addedAt": "t"}
        await store.save({**TABLE, "task:a/2": entry})
        await store.remove("task:a/2")
        assert await store.load() == TABLE

    async def test_removing_what_is_absent_changes_nothing(self, store):
        await store.save(TABLE)
        await store.remove("task:never/1")
        assert await store.load() == TABLE


@pytest.fixture
async def db():
    """A migrated in-memory database, for the SQLite backend's own tests."""
    state_db = open_state_db(":memory:")
    await state_db.migrate()
    yield state_db
    state_db.close()


class TestSqliteDeskStore:
    """The desk on the state database: cuts, notices, and per-entry writes."""

    async def test_save_writes_one_cut(self, db):
        """The whole table is one batch, so a restart sees no half-saved desk."""
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        rows = await db.read_all("desk")
        assert {row["revision"] for row in rows} == {1}

    async def test_a_save_that_changes_nothing_raises_no_notice(self, db):
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        cursor = await db.revision()
        await store.save(TABLE)
        assert await db.notices_since(cursor) == ({}, cursor)

    async def test_a_removed_entry_is_a_notice(self, db):
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        cursor = await db.revision()
        await store.save({})
        notices, _ = await db.notices_since(cursor)
        assert notices == {"desk": set(TABLE)}

    async def test_add_is_its_own_revision(self, db):
        """A per-entry write costs one cut, not a rewrite of the table."""
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        cursor = await db.revision()
        await store.add("task:a/2", {"id": "task:a/2", "addedAt": "t"})
        notices, revision = await db.notices_since(cursor)
        assert notices == {"desk": {"task:a/2"}}
        assert revision == cursor + 1

    async def test_remove_raises_one_notice(self, db):
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        cursor = await db.revision()
        await store.remove("task:askastro/2026-06-11.1")
        notices, _ = await db.notices_since(cursor)
        assert notices == {"desk": {"task:askastro/2026-06-11.1"}}

    async def test_load_is_a_pure_read(self, db):
        """It imports nothing and writes nothing, so the counter cannot move."""
        cursor = await db.revision()
        assert await SqliteDeskStore(db).load() == {}
        assert await db.revision() == cursor
        assert await db.meta("desk_imported") is None

    async def test_an_unreadable_entry_is_dropped_not_raised(self, db):
        """A desk is a convenience; one bad row must not stop the server."""
        store = SqliteDeskStore(db)
        await store.save(TABLE)
        await db.upsert("desk", "task:a/bad", body="not valid json{{{")
        assert await store.load() == TABLE


class TestTheDeskJsonImportRung:
    """The ``desk.json`` import is the desk ladder's second rung.

    The ladder version guarantees a rung runs once, so no marker row is needed
    and a desk the user later emptied cannot spring back from the file.
    """

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        """Keep the rung's read inside tmp_path, not the developer's ~/.maelstrom."""
        monkeypatch.setattr(
            "maelstrom.state_db.paths.get_maelstrom_dir", lambda: tmp_path
        )

    async def test_an_existing_desk_json_imports(self, tmp_path):
        path = tmp_path / "desk.json"
        path.write_text(json.dumps(TABLE))
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await SqliteDeskStore(db).load() == TABLE
            assert path.is_file(), "the file is left as a fallback, not deleted"
        finally:
            db.close()

    async def test_the_rows_carry_revision_zero(self, tmp_path):
        """A migration must not bump the counter, so a client reads them as the start."""
        (tmp_path / "desk.json").write_text(json.dumps(TABLE))
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await db.revision() == 0
            assert {row["revision"] for row in await db.read_all("desk")} == {0}
            assert await db.changed_since("desk", 0) == []
        finally:
            db.close()

    async def test_an_emptied_desk_does_not_spring_back(self, tmp_path):
        """The rung ran, so a second open imports nothing over live rows."""
        (tmp_path / "desk.json").write_text(json.dumps(TABLE))
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            await SqliteDeskStore(db).save({})
        finally:
            db.close()

        again = open_state_db(tmp_path / "state.db")
        try:
            await again.migrate()
            assert await SqliteDeskStore(again).load() == {}
        finally:
            again.close()

    async def test_the_import_applies_the_bare_id_fix(self, tmp_path):
        """A desk written before desk ids carried a kind held bare task ids."""
        (tmp_path / "desk.json").write_text(
            json.dumps({"a/1": {"id": "a/1", "addedAt": "t"}})
        )
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await SqliteDeskStore(db).load() == {
                "task:a/1": {"id": "task:a/1", "addedAt": "t"}
            }
        finally:
            db.close()

    async def test_a_malformed_entry_is_dropped(self, tmp_path):
        """The file is user-editable, so a bad entry must not reach the wire."""
        (tmp_path / "desk.json").write_text(
            json.dumps(
                {
                    "task:a/1": {"id": "task:a/1", "addedAt": "t"},
                    "task:a/2": "not an entry",
                    "task:a/3": {"id": "task:a/3"},
                }
            )
        )
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await SqliteDeskStore(db).load() == {
                "task:a/1": {"id": "task:a/1", "addedAt": "t"}
            }
        finally:
            db.close()

    async def test_no_desk_json_is_skipped(self, tmp_path):
        """A fresh install has no desk to carry, which is not a failure."""
        db = open_state_db(tmp_path / "state.db")
        try:
            await db.migrate()
            assert await SqliteDeskStore(db).load() == {}
        finally:
            db.close()

    async def test_a_corrupt_desk_json_fails_the_migration(self, tmp_path):
        """The opposite of what a backend does, and the reason the rung reads strictly.

        Migrating a corrupt desk to an empty one is the silent data loss every
        refusal in the state database exists to prevent.
        """
        (tmp_path / "desk.json").write_text("not valid json{{{")
        db = open_state_db(tmp_path / "state.db")
        try:
            with pytest.raises(json.JSONDecodeError):
                await db.migrate()
            # The whole run rolled back, so the desk is not half-migrated.
            assert await db.schema_version("desk") == 0
        finally:
            db.close()
