"""Tests for the task table: the contract, and the SQLite backend's own rules.

The task notebook's source of truth is a table in the state database. A task is
one row, its prose included, so a write is one transaction over the row and the
cache alike and a rollback leaves nothing behind.

Mirrors ``test_desk_store.py``: a contract every backend answers, then the
SQLite backend's own tests for cuts, notices and the revision counter.
"""

import pytest

from mael_domain.state_db.migrate import open_state_db
from mael_domain.task import Task
from mael_domain.task_table import InMemoryTaskTable, SqliteTaskTable, TaskTable, row_id


def a_task(id: str = "2026-06-11.1", **fields) -> Task:
    """A task with every field a caller might assert on already set.

    Defaults rather than an empty ``Task``, so a round-trip test that forgets a
    field still fails: a table that dropped ``content`` would otherwise pass
    against a task whose ``content`` was empty anyway.
    """
    task = Task(
        id=id,
        title="Move the notebook",
        project="maelstrom",
        command="plan-task",
        mode="plan",
        branch="refactor/data-backend",
        parent="2026-06-11",
        pre_action="linear.in-progress",
        post_action="linear.done",
        follows=["2026-06-10.1"],
        created="2026-06-11T09:00:00+00:00",
        updated="2026-06-11T10:00:00+00:00",
        schedule="0 9 * * 1",
        last_run="2026-06-10T09:00:00+00:00",
        priority="high",
        model="opus",
        base="main",
        execute_model="sonnet",
        content="The prose the row carries.",
        steps="1. write the ladder",
        log="- started",
        status="todo",
    )
    for name, value in fields.items():
        setattr(task, name, value)
    return task


@pytest.fixture(params=["memory", "sqlite"])
async def table(request):
    """Run each contract test against every backend."""
    if request.param == "memory":
        yield InMemoryTaskTable()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteTaskTable(db)
    db.close()


class TestTaskTableContract:
    """Shared contract both backends must satisfy."""

    def test_every_backend_subclasses_the_contract(self):
        """Explicit, so a reader sees which classes claim it."""
        assert issubclass(InMemoryTaskTable, TaskTable)
        assert issubclass(SqliteTaskTable, TaskTable)

    async def test_loading_what_is_absent_is_none(self, table):
        assert await table.load("maelstrom", "nope") is None

    async def test_save_then_load_round_trips_every_field(self, table):
        """The row carries the prose, so a body field must survive the trip."""
        task = a_task()
        await table.save(task)
        assert await table.load("maelstrom", task.id) == task

    async def test_save_replaces_the_row(self, table):
        await table.save(a_task(title="First"))
        await table.save(a_task(title="Second"))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None
        assert loaded.title == "Second"

    async def test_a_task_is_keyed_by_project_and_id(self, table):
        """Two projects may hold the same id, and neither may shadow the other."""
        await table.save(a_task(project="alpha", title="Alpha's"))
        await table.save(a_task(project="bravo", title="Bravo's"))
        alpha = await table.load("alpha", "2026-06-11.1")
        bravo = await table.load("bravo", "2026-06-11.1")
        assert alpha is not None and alpha.title == "Alpha's"
        assert bravo is not None and bravo.title == "Bravo's"

    async def test_delete_removes_the_row(self, table):
        await table.save(a_task())
        await table.delete("maelstrom", "2026-06-11.1")
        assert await table.load("maelstrom", "2026-06-11.1") is None

    async def test_deleting_what_is_absent_changes_nothing(self, table):
        await table.save(a_task())
        await table.delete("maelstrom", "never")
        assert await table.load("maelstrom", "2026-06-11.1") is not None

    async def test_list_is_id_sorted(self, table):
        for id in ("2026-06-11.3", "2026-06-11.1", "2026-06-11.2"):
            await table.save(a_task(id=id))
        listed = await table.list("maelstrom")
        assert [t.id for t in listed] == [
            "2026-06-11.1",
            "2026-06-11.2",
            "2026-06-11.3",
        ]

    async def test_list_is_scoped_to_one_project(self, table):
        await table.save(a_task(id="1", project="alpha"))
        await table.save(a_task(id="2", project="bravo"))
        assert [t.id for t in await table.list("alpha")] == ["1"]

    async def test_list_filters_by_status(self, table):
        await table.save(a_task(id="1", status="todo"))
        await table.save(a_task(id="2", status="done"))
        assert [t.id for t in await table.list("maelstrom", status="done")] == ["2"]

    async def test_list_filters_by_parent(self, table):
        await table.save(a_task(id="1", parent="chain-a"))
        await table.save(a_task(id="2", parent="chain-b"))
        assert [t.id for t in await table.list("maelstrom", parent="chain-b")] == ["2"]

    async def test_list_filters_by_status_and_parent_together(self, table):
        await table.save(a_task(id="1", status="todo", parent="chain-a"))
        await table.save(a_task(id="2", status="done", parent="chain-a"))
        await table.save(a_task(id="3", status="done", parent="chain-b"))
        listed = await table.list("maelstrom", status="done", parent="chain-a")
        assert [t.id for t in listed] == ["2"]

    async def test_status_is_a_column_not_a_move(self, table):
        """The change this whole move buys: one column, no write-new + delete-old."""
        await table.save(a_task(status="todo"))
        moved = a_task(status="done")
        await table.save(moved)
        assert [t.id for t in await table.list("maelstrom", status="todo")] == []
        assert [t.id for t in await table.list("maelstrom", status="done")] == [
            "2026-06-11.1"
        ]

    async def test_find_by_session_id_answers_the_reverse_lookup(self, table):
        await table.save(a_task(id="1"))
        await table.save(a_task(id="2"))
        from mael_domain.task import session_id_for

        wanted = session_id_for("maelstrom", "2")
        found = await table.find_by_session_id(wanted)
        assert found is not None and found.id == "2"

    async def test_a_blank_session_id_never_resolves(self, table):
        """``""`` is the default for a never-launched row, so it must not match one."""
        await table.save(a_task())
        assert await table.find_by_session_id("") is None

    async def test_find_by_session_id_of_nothing_is_none(self, table):
        assert await table.find_by_session_id("no-such-session") is None

    async def test_follows_survives_the_round_trip_as_a_list(self, table):
        await table.save(a_task(follows=["a", "b", "c"]))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None and loaded.follows == ["a", "b", "c"]

    async def test_an_empty_follows_round_trips_as_empty(self, table):
        await table.save(a_task(follows=[]))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None and loaded.follows == []

    async def test_a_transaction_commits_every_write(self, table):
        async with table.transact():
            await table.save(a_task(id="1"))
            await table.save(a_task(id="2"))
        assert [t.id for t in await table.list("maelstrom")] == ["1", "2"]

    async def test_a_failed_transaction_leaves_no_partial_row(self, table):
        """The bug class this move deletes: no row outlives a rollback."""
        await table.save(a_task(id="kept"))
        with pytest.raises(RuntimeError):
            async with table.transact():
                await table.save(a_task(id="rolled-back"))
                raise RuntimeError("the write failed halfway")
        assert [t.id for t in await table.list("maelstrom")] == ["kept"]

    async def test_nothing_changed_since_the_current_revision(self, table):
        """An idle poll reads nothing: the whole point of asking by revision."""
        await table.save(a_task(id="1"))
        changed = await table.changed_since(await table.revision())
        assert (changed.tasks, changed.removed) == ([], [])

    async def test_only_the_saved_row_has_changed(self, table):
        """One row moves, one row comes back — not the whole table."""
        await table.save(a_task(id="1"))
        await table.save(a_task(id="2"))
        cursor = await table.revision()

        await table.save(a_task(id="2", title="Moved"))
        changed = await table.changed_since(cursor)

        assert [t.id for t in changed.tasks] == ["2"]
        assert changed.tasks[0].title == "Moved"

    async def test_a_changed_row_comes_back_whole(self, table):
        """The row carries the prose, so a partial read is not a partial task."""
        await table.save(a_task(id="1"))
        cursor = await table.revision()

        await table.save(a_task(id="1", title="Moved", content="The plan."))
        changed = await table.changed_since(cursor)

        assert (changed.tasks[0].title, changed.tasks[0].content) == (
            "Moved",
            "The plan.",
        )

    async def test_a_deleted_row_is_named_as_removed(self, table):
        """Absence cannot mean deletion, so a removal is reported in its own right."""
        await table.save(a_task(id="1"))
        await table.save(a_task(id="2"))
        cursor = await table.revision()

        await table.delete("maelstrom", "1")
        changed = await table.changed_since(cursor)

        assert changed.removed == [row_id("maelstrom", "1")]
        assert [t.id for t in changed.tasks] == []

    async def test_the_reading_carries_the_revision_to_ask_from_next(self, table):
        """A poller stores this and hands it back, so no change is read twice."""
        await table.save(a_task(id="1"))
        cursor = await table.revision()
        await table.save(a_task(id="2"))

        changed = await table.changed_since(cursor)
        assert changed.revision == await table.revision()
        assert changed.revision > cursor

        again = await table.changed_since(changed.revision)
        assert (again.tasks, again.removed) == ([], [])

    async def test_a_rolled_back_write_is_invisible_to_a_later_read(self, table):
        """A rollback leaves no row *and* no change to read.

        The in-memory twin is the one that can drift here: SQLite rolls its
        counter back with the transaction, so only a hand-written backend can
        leave the revision advanced over a write that never landed.
        """
        await table.save(a_task(id="kept"))
        cursor = await table.revision()

        with pytest.raises(RuntimeError):
            async with table.transact():
                await table.save(a_task(id="rolled-back"))
                raise RuntimeError("the write failed halfway")

        changed = await table.changed_since(cursor)
        assert (changed.tasks, changed.removed) == ([], [])
        assert changed.revision == cursor

    async def test_every_project_is_read_not_just_one(self, table):
        """The poller asks the database once, then scopes what it got itself."""
        await table.save(a_task(id="1", project="maelstrom"))
        cursor = await table.revision()

        await table.save(a_task(id="1", project="askastro"))
        changed = await table.changed_since(cursor)

        assert [t.project for t in changed.tasks] == ["askastro"]


@pytest.fixture
async def db():
    """A migrated in-memory database, for the SQLite backend's own tests."""
    state_db = open_state_db(":memory:")
    await state_db.migrate()
    yield state_db
    state_db.close()


class TestSqliteTaskTable:
    """The task table on the state database: cuts, notices and the counter."""

    async def test_a_save_is_one_revision(self, db):
        table = SqliteTaskTable(db)
        await table.save(a_task())
        rows = await db.read_all("tasks")
        assert {row["revision"] for row in rows} == {1}

    async def test_the_row_id_carries_the_project(self, db):
        """``StateDb`` keys every table by one ``id`` column."""
        table = SqliteTaskTable(db)
        await table.save(a_task(id="2026-06-11.1", project="maelstrom"))
        rows = await db.read_all("tasks")
        assert [row["id"] for row in rows] == ["maelstrom/2026-06-11.1"]

    async def test_a_save_that_changes_nothing_raises_no_notice(self, db):
        """An idle write must not bump the counter, or the number stops meaning
        anything."""
        table = SqliteTaskTable(db)
        await table.save(a_task())
        cursor = await db.revision()
        await table.save(a_task())
        assert await db.notices_since(cursor) == ({}, cursor)

    async def test_a_status_change_is_one_notice(self, db):
        table = SqliteTaskTable(db)
        await table.save(a_task(status="todo"))
        cursor = await db.revision()
        await table.save(a_task(status="done"))
        notices, revision = await db.notices_since(cursor)
        assert notices == {"tasks": {"maelstrom/2026-06-11.1"}}
        assert revision == cursor + 1

    async def test_a_delete_raises_a_removal_notice(self, db):
        table = SqliteTaskTable(db)
        await table.save(a_task())
        cursor = await db.revision()
        await table.delete("maelstrom", "2026-06-11.1")
        notices, _ = await db.notices_since(cursor)
        assert notices == {"tasks": {"maelstrom/2026-06-11.1"}}

    async def test_one_transaction_is_one_cut(self, db):
        """Several tasks written together share one revision."""
        table = SqliteTaskTable(db)
        async with table.transact():
            await table.save(a_task(id="1"))
            await table.save(a_task(id="2"))
        rows = await db.read_all("tasks")
        assert len({row["revision"] for row in rows}) == 1

    async def test_a_rolled_back_transaction_consumes_no_revision(self, db):
        table = SqliteTaskTable(db)
        cursor = await db.revision()
        with pytest.raises(RuntimeError):
            async with table.transact():
                await table.save(a_task())
                raise RuntimeError("failed")
        assert await db.revision() == cursor
        assert await db.read_all("tasks") == []

    def test_the_table_is_canonical_so_it_carries_no_fetched_at(self):
        """Nobody else authors a task, so there is nothing to be fresh against."""
        from mael_domain.state_db.migrate import TABLES

        assert TABLES["tasks"].cached is False

    async def test_a_load_is_a_pure_read(self, db):
        cursor = await db.revision()
        assert await SqliteTaskTable(db).load("maelstrom", "nope") is None
        assert await db.revision() == cursor
