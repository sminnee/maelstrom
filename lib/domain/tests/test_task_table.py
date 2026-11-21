"""Tests for the task table: the contract, and the SQLite backend's own rules.

The task notebook's source of truth is a table in the state database. A task is
one row, its prose included, so a write is one transaction over the row and the
cache alike and a rollback leaves nothing behind.

Mirrors ``test_desk_store.py``: a contract every backend answers, then the
SQLite backend's own tests for cuts, notices and the revision counter.
"""

import pytest

from mael_domain.state_db.migrate import open_state_db
from mael_domain.task import LinkedPr, Task, TaskSession
from mael_domain.task_table import InMemoryTaskTable, SqliteTaskTable, row_id


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
        execute_model="sonnet",
        prs=[LinkedPr(118, "https://github.com/o/r/pull/118"), LinkedPr(120)],
        post_merge="Check the deploy.",
        comms=["c3", "c7"],
        content="The prose the row carries.",
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

    async def test_task_repository_I2_an_unknown_task_loads_as_none(self, table):
        assert await table.load("maelstrom", "nope") is None

    async def test_task_repository_I1_each_field_reads_back(self, table):
        """The row carries the prose, so a body field must survive the trip."""
        task = a_task()
        await table.save(task)
        assert await table.load("maelstrom", task.id) == task

    async def test_task_repository_I3_a_save_replaces_the_task(self, table):
        await table.save(a_task(title="First"))
        await table.save(a_task(title="Second"))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None
        assert loaded.title == "Second"

    async def test_task_repository_I3_the_same_id_in_another_project_stays(self, table):
        """Two projects may hold the same id, and neither may shadow the other."""
        await table.save(a_task(project="alpha", title="Alpha's"))
        await table.save(a_task(project="bravo", title="Bravo's"))
        alpha = await table.load("alpha", "2026-06-11.1")
        bravo = await table.load("bravo", "2026-06-11.1")
        assert alpha is not None and alpha.title == "Alpha's"
        assert bravo is not None and bravo.title == "Bravo's"

    async def test_task_repository_I4_a_delete_removes_the_task(self, table):
        await table.save(a_task())
        await table.delete("maelstrom", "2026-06-11.1")
        assert await table.load("maelstrom", "2026-06-11.1") is None

    async def test_task_repository_I4_an_unknown_delete_changes_nothing(self, table):
        await table.save(a_task())
        await table.delete("maelstrom", "never")
        assert await table.load("maelstrom", "2026-06-11.1") is not None

    async def test_task_repository_I5_a_list_is_oldest_first(self, table):
        # The ids sort against the creation order, so the id cannot give a pass.
        await table.save(a_task(id="aaaa", created="2026-06-13T00:00:00+00:00"))
        await table.save(a_task(id="zzzz", created="2026-06-11T00:00:00+00:00"))
        await table.save(a_task(id="mmmm", created="2026-06-12T00:00:00+00:00"))
        listed = await table.list("maelstrom")
        assert [t.id for t in listed] == ["zzzz", "mmmm", "aaaa"]

    async def test_task_repository_I5_an_empty_created_time_is_first(self, table):
        await table.save(a_task(id="aaaa", created="2026-06-11T00:00:00+00:00"))
        await table.save(a_task(id="zzzz", created=""))
        listed = await table.list("maelstrom")
        assert [t.id for t in listed] == ["zzzz", "aaaa"]

    async def test_task_repository_I5_the_id_breaks_a_tie(self, table):
        for id in ("cccc", "aaaa", "bbbb"):
            await table.save(a_task(id=id, created="2026-06-11T00:00:00+00:00"))
        listed = await table.list("maelstrom")
        assert [t.id for t in listed] == ["aaaa", "bbbb", "cccc"]

    async def test_task_repository_I6_a_list_holds_one_project(self, table):
        await table.save(a_task(id="1", project="alpha"))
        await table.save(a_task(id="2", project="bravo"))
        assert [t.id for t in await table.list("alpha")] == ["1"]

    async def test_task_repository_I6_a_list_filters_by_status(self, table):
        await table.save(a_task(id="1", status="todo"))
        await table.save(a_task(id="2", status="merged"))
        assert [t.id for t in await table.list("maelstrom", status="merged")] == ["2"]

    async def test_task_repository_I6_a_list_filters_by_parent(self, table):
        await table.save(a_task(id="1", parent="chain-a"))
        await table.save(a_task(id="2", parent="chain-b"))
        assert [t.id for t in await table.list("maelstrom", parent="chain-b")] == ["2"]

    async def test_task_repository_I6_a_list_filters_by_status_and_parent(self, table):
        await table.save(a_task(id="1", status="todo", parent="chain-a"))
        await table.save(a_task(id="2", status="merged", parent="chain-a"))
        await table.save(a_task(id="3", status="merged", parent="chain-b"))
        listed = await table.list("maelstrom", status="merged", parent="chain-a")
        assert [t.id for t in listed] == ["2"]

    async def test_task_repository_I1_sessions_keep_their_harness(self, table):
        sessions = [
            TaskSession(id="c-1", harness="claude"),
            TaskSession(id="thread-1", harness="codex"),
        ]
        await table.save(a_task(sessions=sessions))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None and loaded.sessions == sessions

    async def test_task_repository_I7_each_session_id_finds_the_task(self, table):
        await table.save(a_task(id="1", sessions=[TaskSession("c-1", "claude")]))
        await table.save(
            a_task(
                id="2",
                sessions=[
                    TaskSession("c-2", "claude"),
                    TaskSession("thread-2", "codex"),
                ],
            )
        )
        for wanted in ("c-2", "thread-2"):
            found = await table.find_by_session_id(wanted)
            assert found is not None and found.id == "2"

    async def test_task_repository_I7_a_blank_session_id_finds_nothing(self, table):
        """A never-launched row has no sessions, so a blank must not match it."""
        await table.save(a_task())
        assert await table.find_by_session_id("") is None

    async def test_task_repository_I7_an_unknown_session_id_finds_nothing(self, table):
        await table.save(a_task(sessions=[TaskSession("c-1", "claude")]))
        assert await table.find_by_session_id("no-such-session") is None

    async def test_task_repository_I1_follows_reads_back_in_order(self, table):
        await table.save(a_task(follows=["a", "b", "c"]))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None and loaded.follows == ["a", "b", "c"]

    async def test_task_repository_I1_an_empty_follows_reads_back_empty(self, table):
        await table.save(a_task(follows=[]))
        loaded = await table.load("maelstrom", "2026-06-11.1")
        assert loaded is not None and loaded.follows == []

    async def test_task_repository_I8_followers_come_from_each_project(self, table):
        await table.save(a_task(id="f1", follows=["a"], created="2026-06-11T09:00:02"))
        await table.save(
            a_task(id="f2", follows=["x", "b"], created="2026-06-11T09:00:01")
        )
        await table.save(
            a_task(
                id="f3",
                project="other",
                follows=["maelstrom/a"],
                created="2026-06-11T09:00:03",
            )
        )
        await table.save(a_task(id="no", follows=["c"]))
        # Another project's bare ``a`` is its own task, not maelstrom's.
        await table.save(a_task(id="away", project="other", follows=["a"]))
        await table.save(a_task(id="wrong", project="other", follows=["third/a"]))

        found = await table.followers("maelstrom", {"a", "b"})

        assert [(t.project, t.id) for t in found] == [
            ("maelstrom", "f2"),
            ("maelstrom", "f1"),
            ("other", "f3"),
        ]
        assert await table.followers("maelstrom", set()) == []

    async def test_task_repository_I9_a_transaction_commits_each_write(self, table):
        async with table.transact():
            await table.save(a_task(id="1"))
            await table.save(a_task(id="2"))
        assert [t.id for t in await table.list("maelstrom")] == ["1", "2"]

    async def test_task_repository_I9_a_failed_transaction_writes_nothing(self, table):
        """The bug class this move deletes: no row outlives a rollback."""
        await table.save(a_task(id="kept"))
        with pytest.raises(RuntimeError):
            async with table.transact():
                await table.save(a_task(id="rolled-back"))
                raise RuntimeError("the write failed halfway")
        assert [t.id for t in await table.list("maelstrom")] == ["kept"]

    async def test_task_repository_I10_an_idle_read_names_nothing(self, table):
        """An idle poll reads nothing: the whole point of asking by revision."""
        await table.save(a_task(id="1"))
        changed = await table.changed_since(await table.revision())
        assert (changed.tasks, changed.removed) == ([], [])

    async def test_task_repository_I10_only_the_saved_task_is_named(self, table):
        """One row moves, one row comes back — not the whole table."""
        await table.save(a_task(id="1"))
        await table.save(a_task(id="2"))
        cursor = await table.revision()

        await table.save(a_task(id="2", title="Moved"))
        changed = await table.changed_since(cursor)

        assert [t.id for t in changed.tasks] == ["2"]
        assert changed.tasks[0].title == "Moved"

    async def test_task_repository_I10_a_deleted_task_is_named_as_removed(self, table):
        """Absence cannot mean deletion, so a removal is reported in its own right."""
        await table.save(a_task(id="1"))
        await table.save(a_task(id="2"))
        cursor = await table.revision()

        await table.delete("maelstrom", "1")
        changed = await table.changed_since(cursor)

        assert changed.removed == [row_id("maelstrom", "1")]
        assert [t.id for t in changed.tasks] == []

    async def test_task_repository_I10_the_next_read_names_no_change_twice(self, table):
        """A poller stores this and hands it back, so no change is read twice."""
        await table.save(a_task(id="1"))
        cursor = await table.revision()
        await table.save(a_task(id="2"))

        changed = await table.changed_since(cursor)
        assert changed.revision == await table.revision()
        assert changed.revision > cursor

        again = await table.changed_since(changed.revision)
        assert (again.tasks, again.removed) == ([], [])

    async def test_task_repository_I9_a_rollback_leaves_no_change_to_read(self, table):
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

    async def test_task_repository_I10_a_change_of_each_project_is_named(self, table):
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
        await table.save(a_task(status="merged"))
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

    async def test_a_load_is_a_pure_read(self, db):
        cursor = await db.revision()
        assert await SqliteTaskTable(db).load("maelstrom", "nope") is None
        assert await db.revision() == cursor
