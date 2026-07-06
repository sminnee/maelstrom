"""Tests for the task metadata index (storage layer + model integration).

The backend tests below exercise the SQLite index store in isolation
(upsert/find/list, HEAD stamp, buffered transaction). The model-integration tests
live further down: they prove the index stays in lock-step with the ``.md`` tree
through the model's write ops, that a rollback discards both, and that the fast
reads come from the index.
"""

import pytest

from maelstrom import task as model
from maelstrom.task_index import SqliteTaskIndex, TaskMeta
from maelstrom.task_store import GitFileStore, InMemoryStore

NOW = "2026-06-08T12:00:00+00:00"
TODAY = "2026-06-08"


def _meta(project="p", id="1", status="todo", **kw) -> TaskMeta:
    return TaskMeta(project=project, id=id, status=status, **kw)


# --- backend contract ---


@pytest.fixture()
def index():
    """Yield a fresh in-memory SQLite index for the contract tests."""
    return SqliteTaskIndex(":memory:")


class TestIndexBackend:
    def test_upsert_then_find(self, index):
        index.upsert(_meta(id="a", title="hi", follows=("x", "y")))
        got = index.find("p", "a")
        assert got is not None
        assert got.id == "a"
        assert got.title == "hi"
        assert got.follows == ("x", "y")

    def test_find_absent_is_none(self, index):
        assert index.find("p", "missing") is None

    def test_find_by_session_id_roundtrips(self, index):
        index.upsert(_meta(id="a", session_id="sid-a"))
        index.upsert(_meta(id="b", session_id="sid-b"))
        got = index.find_by_session_id("sid-b")
        assert got is not None and got.id == "b"

    def test_find_by_session_id_unknown_is_none(self, index):
        index.upsert(_meta(id="a", session_id="sid-a"))
        assert index.find_by_session_id("nope") is None

    def test_find_by_session_id_blank_never_resolves(self, index):
        # "" is the default for never-launched rows; a blank query must not
        # match one of them.
        index.upsert(_meta(id="a", session_id=""))
        assert index.find_by_session_id("") is None

    def test_to_from_row_preserves_session_id(self, index):
        index.upsert(_meta(id="a", session_id="sid-a"))
        got = index.find("p", "a")
        assert got is not None and got.session_id == "sid-a"

    def test_upsert_replaces(self, index):
        index.upsert(_meta(id="a", status="todo"))
        index.upsert(_meta(id="a", status="done"))
        got = index.find("p", "a")
        assert got is not None and got.status == "done"

    def test_remove(self, index):
        index.upsert(_meta(id="a"))
        index.remove("p", "a")
        assert index.find("p", "a") is None

    def test_remove_absent_is_noop(self, index):
        index.remove("p", "nope")  # must not raise

    def test_list_all_sorted_by_id(self, index):
        index.upsert(_meta(id="b"))
        index.upsert(_meta(id="a"))
        assert [m.id for m in index.list("p")] == ["a", "b"]

    def test_list_filters_by_status(self, index):
        index.upsert(_meta(id="a", status="todo"))
        index.upsert(_meta(id="b", status="done"))
        assert [m.id for m in index.list("p", status="todo")] == ["a"]

    def test_list_filters_by_parent(self, index):
        index.upsert(_meta(id="a", parent="root"))
        index.upsert(_meta(id="b", parent="other"))
        assert [m.id for m in index.list("p", parent="root")] == ["a"]

    def test_list_scoped_to_project(self, index):
        index.upsert(_meta(project="p", id="a"))
        index.upsert(_meta(project="q", id="a"))
        assert [m.project for m in index.list("p")] == ["p"]

    def test_head_roundtrip(self, index):
        assert index.head() is None
        index.set_head("deadbeef")
        assert index.head() == "deadbeef"
        index.set_head(None)
        assert index.head() is None

    def test_clear_drops_rows_keeps_head(self, index):
        index.upsert(_meta(id="a"))
        index.set_head("sha")
        index.clear()
        assert index.list("p") == []
        assert index.head() == "sha"

    def test_transaction_commits_on_clean_exit(self, index):
        with index.transaction():
            index.upsert(_meta(id="a"))
            index.upsert(_meta(id="b"))
            # Buffered: not yet visible mid-transaction.
            assert index.find("p", "a") is None
        assert [m.id for m in index.list("p")] == ["a", "b"]

    def test_transaction_discards_on_exception(self, index):
        index.upsert(_meta(id="existing"))
        with pytest.raises(RuntimeError):
            with index.transaction():
                index.upsert(_meta(id="new"))
                raise RuntimeError("boom")
        assert index.find("p", "new") is None
        assert index.find("p", "existing") is not None


class TestSqlitePersistence:
    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "index.db"
        idx = SqliteTaskIndex(db)
        idx.upsert(_meta(id="a", title="kept"))
        idx.set_head("sha")
        reopened = SqliteTaskIndex(db)
        got = reopened.find("p", "a")
        assert got is not None and got.title == "kept"
        assert reopened.head() == "sha"

    def test_clear_recreates_table_from_current_schema(self, tmp_path):
        """An old-schema on-disk table (no session_id) is dropped+recreated.

        Simulates upgrading an ``index.db`` written before ``session_id`` existed:
        ``clear()`` (as ``task reindex`` calls it) drops the stale table and
        recreates it with the current column set, so a subsequent upsert of a
        row carrying ``session_id`` succeeds. The HEAD stamp in ``meta`` survives.
        """
        import sqlite3

        db = tmp_path / "index.db"
        # Hand-build a pre-session_id ``tasks`` table, mirroring the old schema.
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE tasks ("
            "project TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, "
            "title TEXT, priority TEXT, branch TEXT, parent TEXT, "
            "follows TEXT, command TEXT, mode TEXT, schedule TEXT, "
            "last_run TEXT, created TEXT, updated TEXT, "
            "PRIMARY KEY (project, id))"
        )
        conn.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.commit()
        conn.close()

        idx = SqliteTaskIndex(db)
        idx.set_head("sha")
        idx.clear()  # drop+recreate under the current schema
        idx.upsert(_meta(id="a", session_id="sid-a"))  # would fail on old schema
        got = idx.find_by_session_id("sid-a")
        assert got is not None and got.id == "a"
        assert idx.head() == "sha"


# --- model integration: the index stays in lock-step with the .md tree ---


def _index_snapshot(index, store, project):
    """Return ``{id: status}`` from the index vs. a forced slow-path scan.

    The scan passes ``no_index=True``, so it always reflects the ``.md`` tree — the
    source of truth the index must match.
    """
    from_index = {m.id: m.status for m in index.list(project)}
    scanned = model.list_tasks(store, project=project, no_index=True)
    from_scan = {t.id: t.status for t in scanned}
    return from_index, from_scan


class TestIndexTracksModelWrites:
    """After each model write op, the index mirrors a slow-path scan exactly."""

    @pytest.fixture()
    def index(self):
        return SqliteTaskIndex(":memory:")

    def test_create_move_update_rename_delete(self, index):
        store = InMemoryStore()
        a = model.create(store, project="p", title="a", now=NOW, today=TODAY, index=index)
        b = model.create(
            store, project="p", title="b", follows=[a.id], now=NOW, today=TODAY, index=index
        )
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan == {a.id: "todo", b.id: "todo"}

        # move: single status upsert, id unchanged
        model.move(store, "p", a.id, "done", now=NOW, index=index)
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan and idx[a.id] == "done"

        # update: metadata field change reflected
        model.update(store, "p", b.id, priority="high", now=NOW, index=index)
        assert index.find("p", b.id).priority == "high"

        # rename: old row gone, new row present, dependent follows rewritten
        model.rename(store, "p", a.id, "a-renamed", now=NOW, index=index)
        assert index.find("p", a.id) is None
        assert index.find("p", "a-renamed") is not None
        assert index.find("p", b.id).follows == ("a-renamed",)
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan

        # delete: row removed, dependent stripped
        model.delete(store, "p", "a-renamed", index=index)
        assert index.find("p", "a-renamed") is None
        assert index.find("p", b.id).follows == ()
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan == {b.id: "todo"}

    def test_load_many_indexes_every_task(self, index):
        store = InMemoryStore()
        blocks = [
            {"name": "x", "args": {"title": "X"}, "content": ""},
            {"name": "y", "args": {"title": "Y", "follow": "x"}, "content": ""},
        ]
        created = model.load_many(store, project="p", blocks=blocks, index=index)
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan
        assert set(idx) == {t.id for t in created}


class TestRollbackDiscardsBoth:
    """An exception mid-transaction rolls back both the store tree and the index.

    Uses a real GitFileStore + SqliteTaskIndex on tmp_path so the git rollback is
    genuine, and the nested index transaction discards its buffer.
    """

    def _setup(self, tmp_path):
        store = GitFileStore(root=tmp_path / "tasks")
        index = SqliteTaskIndex(tmp_path / "tasks" / "idx.db")
        return store, index

    def test_delete_rollback(self, tmp_path, monkeypatch):
        store, index = self._setup(tmp_path)
        a = model.create(store, project="p", title="a", index=index)
        b = model.create(store, project="p", title="b", follows=[a.id], index=index)
        # Force the dependent rewrite inside delete's txn to blow up.
        real_write = store.write
        calls = {"n": 0}

        def boom(key, text, *, message=None):
            calls["n"] += 1
            if calls["n"] >= 1 and key.endswith(f"{b.id}.md"):
                raise RuntimeError("boom")
            return real_write(key, text, message=message)

        monkeypatch.setattr(store, "write", boom)
        with pytest.raises(RuntimeError, match="boom"):
            model.delete(store, "p", a.id, index=index)

        # Store tree intact: both tasks still present.
        assert store.exists(f"p/todo/{a.id}.md")
        assert store.exists(f"p/todo/{b.id}.md")
        # Index intact: a still indexed, b's follows unchanged (buffer discarded).
        assert index.find("p", a.id) is not None
        assert index.find("p", b.id).follows == (a.id,)

    def test_move_rollback(self, tmp_path, monkeypatch):
        store, index = self._setup(tmp_path)
        a = model.create(store, project="p", title="a", index=index)

        def boom(key, *, message=None):
            raise RuntimeError("boom")

        monkeypatch.setattr(store, "delete", boom)
        with pytest.raises(RuntimeError, match="boom"):
            model.move(store, "p", a.id, "done", index=index)

        # Rolled back to todo in both store and index.
        assert store.exists(f"p/todo/{a.id}.md")
        assert index.find("p", a.id).status == "todo"


class TestReadsComeFromIndex:
    """The read fast path is served by the index, and falls back when HEAD is stale."""

    def test_list_served_from_index_not_store(self):
        # Seed the index only; leave the store empty. A fresh index (in-memory
        # store head is None, index head is None) must answer from the index.
        store = InMemoryStore()
        index = SqliteTaskIndex(":memory:")
        index.upsert(TaskMeta(project="p", id="ghost", status="todo", title="only in index"))
        tasks = model.list_tasks(store, project="p", index=index, head=None)
        assert [t.id for t in tasks] == ["ghost"]
        # And the store genuinely has nothing (forced scan proves the index answered).
        assert model.list_tasks(store, project="p", no_index=True) == []

    def test_stale_index_falls_back_to_store_scan(self):
        # Index HEAD disagrees with the passed store head -> stale -> scan the store.
        store = InMemoryStore()
        index = SqliteTaskIndex(":memory:")
        model.create(store, project="p", title="real", now=NOW, today=TODAY, index=index)
        index.upsert(TaskMeta(project="p", id="ghost", status="todo"))
        index.set_head("some-old-sha")
        tasks = model.list_tasks(store, project="p", index=index, head="current-sha")
        # Fell back to the store scan: the real task, not the stale index ghost.
        assert [t.id for t in tasks] != ["ghost"]
        assert all(t.id != "ghost" for t in tasks)
        assert len(tasks) == 1

    def test_find_key_served_from_index(self):
        store = InMemoryStore()
        index = SqliteTaskIndex(":memory:")
        index.upsert(TaskMeta(project="p", id="x", status="in-progress"))
        key = model.find_key(store, "p", "x", index=index, head=None)
        assert key == "p/in-progress/x.md"  # status came from the index row
        # The store never had it.
        assert not store.exists("p/in-progress/x.md")

    def test_reindex_rebuilds_from_store(self):
        store = InMemoryStore()
        index = SqliteTaskIndex(":memory:")
        model.create(store, project="p", title="a", now=NOW, today=TODAY, index=index)
        model.create(store, project="p", title="b", now=NOW, today=TODAY, index=index)
        index.upsert(TaskMeta(project="p", id="stale", status="todo"))  # bogus pre-state
        n = model.reindex(store, index, projects=["p"], head=None)
        assert n == 2
        assert index.find("p", "stale") is None  # cleared
        idx, scan = _index_snapshot(index, store, "p")
        assert idx == scan

    def test_reindex_invalidates_head_before_clearing(self, monkeypatch):
        # An interrupted rebuild must never leave an empty index reading fresh: the
        # HEAD stamp is cleared *before* the rows are, so a crash mid-rebuild reads
        # stale. Simulate the crash by raising inside the rebuild scan.
        store = InMemoryStore()
        index = SqliteTaskIndex(":memory:")
        model.create(store, project="p", title="a", now=NOW, today=TODAY, index=index)
        index.set_head("old-sha")  # previously stamped fresh at some head

        real_list = model.list_tasks

        def boom(*a, **kw):
            raise RuntimeError("crash mid-rebuild")

        monkeypatch.setattr(model, "list_tasks", boom)
        with pytest.raises(RuntimeError, match="crash mid-rebuild"):
            model.reindex(store, index, projects=["p"], head="old-sha")

        # Stamp was invalidated before the crash, so the half-built index reads
        # stale against the store head it was previously fresh at.
        assert index.head() is None
        monkeypatch.setattr(model, "list_tasks", real_list)
        assert not model._index_fresh(index, "old-sha")
