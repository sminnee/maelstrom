"""The markdown export: the queue, the enqueue, and the drain.

The notebook's source of truth is the tasks table. The markdown tree survives as
an export nothing reads back, written by the orchestrator rather than by the
process that changed the task.

Three seams, tested apart:

- the queue, over both backends — what is owed, and what clearing it does;
- the enqueue, on the SQLite task table — that a task write queues its export
  *in the same transaction*, which is the guarantee the whole design rests on;
- the drain, over a real ``GitFileStore`` — that the file lands where
  ``task_key`` says, and that a rollback leaves neither row nor queue entry.

The rollback test is the one that matters. A queue in a file or another database
could be written for a task write that rolled back, or missed for one that
committed. Being a table in the same database is what makes that impossible, so
the test asserts both halves of one cut.
"""

import pytest

from mael_domain import task as model
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_export import (
    TABLE,
    ExportQueue,
    InMemoryExportQueue,
    Queued,
    SqliteExportQueue,
    TaskExporter,
    split_row_id,
)
from mael_domain.task_store import GitFileStore
from mael_domain.task_table import SqliteTaskTable

PROJECT = "northwind"


@pytest.fixture(params=["memory", "sqlite"])
async def queue(request):
    """Run each contract test against every backend."""
    if request.param == "memory":
        yield InMemoryExportQueue()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteExportQueue(db)
    db.close()


@pytest.fixture
async def db(state_db):
    """The shared migrated database, for the tests that need both tables."""
    return state_db


class TestExportQueueContract:
    """Shared contract both backends must satisfy."""

    def test_every_backend_subclasses_the_contract(self):
        """Explicit, so a reader sees which classes claim it."""
        assert issubclass(InMemoryExportQueue, ExportQueue)
        assert issubclass(SqliteExportQueue, ExportQueue)

    async def test_a_caught_up_export_owes_nothing(self, queue):
        assert await queue.pending() == []
        assert await queue.depth() == 0

    async def test_clearing_what_is_absent_changes_nothing(self, queue):
        await queue.clear("northwind/never")
        assert await queue.depth() == 0


class TestTheQueueIsOneRowPerTask:
    """Ten writes between two drains export once, not ten times.

    The export carries the notebook's current state rather than its history, so
    the queue is keyed by the task's own row id and an enqueue is an upsert.
    """

    async def test_two_writes_to_one_task_queue_one_export(self, db):
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await model.update(table, PROJECT, "NORT-7", title="Ship it now")

        assert await SqliteExportQueue(db).depth() == 1

    async def test_two_tasks_queue_two_exports(self, db):
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="One", id="NORT-7")
        await model.create(table, project=PROJECT, title="Two", id="NORT-8")

        queued = await SqliteExportQueue(db).pending()
        assert [entry.id for entry in queued] == [
            f"{PROJECT}/NORT-7",
            f"{PROJECT}/NORT-8",
        ]


class TestTheEnqueueJoinsTheTaskWrite:
    """The whole reason the queue is a table in this same database."""

    async def test_a_saved_task_queues_its_export(self, db):
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")

        queued = await SqliteExportQueue(db).pending()
        assert [entry.id for entry in queued] == [f"{PROJECT}/NORT-7"]
        assert queued[0].deleted is False
        assert queued[0].path == f"{PROJECT}/todo/NORT-7.md"

    async def test_a_deleted_task_queues_the_path_it_had(self, db):
        """A delete leaves no row to render, so the path is recorded now or lost."""
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await SqliteExportQueue(db).clear(f"{PROJECT}/NORT-7")

        await model.delete(table, PROJECT, "NORT-7")

        queued = await SqliteExportQueue(db).pending()
        assert [entry.id for entry in queued] == [f"{PROJECT}/NORT-7"]
        assert queued[0].deleted is True
        assert queued[0].path == f"{PROJECT}/todo/NORT-7.md"

    async def test_a_rolled_back_write_leaves_no_row_and_no_queued_export(self, db):
        """The bug class this whole move deletes.

        A failed multi-task write must leave neither a partial row nor an export
        that would write one. The two cannot disagree, because the enqueue is
        part of the transaction that wrote the task rather than a second act
        hoping to match it.
        """
        table = SqliteTaskTable(db)
        queue = SqliteExportQueue(db)
        cursor = await db.revision()

        with pytest.raises(RuntimeError):
            async with table.transact():
                await model.create(table, project=PROJECT, title="One", id="NORT-7")
                await model.create(table, project=PROJECT, title="Two", id="NORT-8")
                raise RuntimeError("the second task failed")

        assert await table.list(PROJECT) == [], "a partial row survived the rollback"
        assert await queue.pending() == [], "a rolled-back write queued an export"
        assert await db.revision() == cursor

    async def test_the_enqueue_shares_the_task_write_s_revision(self, db):
        """One cut, so one revision stamps the row and its queue entry alike."""
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")

        task_row = await db.read("tasks", f"{PROJECT}/NORT-7")
        queue_row = await db.read(TABLE, f"{PROJECT}/NORT-7")
        assert task_row is not None and queue_row is not None
        assert task_row["revision"] == queue_row["revision"]


class TestTheDrain:
    """What the orchestrator does with the queue: render, write, clear."""

    def an_exporter(self, db, tmp_path) -> TaskExporter:
        """A drain over a real git-backed store, so the file and commit are real."""
        return TaskExporter(
            SqliteExportQueue(db), SqliteTaskTable(db), GitFileStore(root=tmp_path)
        )

    async def test_a_drain_writes_the_task_s_markdown(self, db, tmp_path):
        table = SqliteTaskTable(db)
        await model.create(
            table, project=PROJECT, title="Ship it", id="NORT-7", content="The plan."
        )

        assert await self.an_exporter(db, tmp_path).drain() == 1

        written = (tmp_path / PROJECT / "todo" / "NORT-7.md").read_text()
        assert "title: Ship it" in written
        assert "The plan." in written

    async def test_a_drained_task_is_cleared(self, db, tmp_path):
        """The queue is what is *owed*, so a written task leaves it."""
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")

        await self.an_exporter(db, tmp_path).drain()

        assert await SqliteExportQueue(db).depth() == 0

    async def test_an_idle_drain_writes_nothing(self, db, tmp_path):
        assert await self.an_exporter(db, tmp_path).drain() == 0

    async def test_a_deleted_task_s_file_is_unlinked(self, db, tmp_path):
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await self.an_exporter(db, tmp_path).drain()
        assert (tmp_path / PROJECT / "todo" / "NORT-7.md").is_file()

        await model.delete(table, PROJECT, "NORT-7")
        await self.an_exporter(db, tmp_path).drain()

        assert not (tmp_path / PROJECT / "todo" / "NORT-7.md").exists()

    async def test_a_status_move_leaves_no_file_at_the_old_path(self, db, tmp_path):
        """Status is a column now, so one save moves the file between two folders."""
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await self.an_exporter(db, tmp_path).drain()

        await model.move(table, PROJECT, "NORT-7", model.STATUS_DONE)
        await self.an_exporter(db, tmp_path).drain()

        assert (tmp_path / PROJECT / "done" / "NORT-7.md").is_file()
        assert not (tmp_path / PROJECT / "todo" / "NORT-7.md").exists()

    async def test_the_drain_renders_the_row_as_it_stands_now(self, db, tmp_path):
        """Two writes before one drain export the second, not the first.

        The queue stores no markdown, so a task that moved again before the
        drain cannot export a body it has already replaced.
        """
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="First", id="NORT-7")
        await model.update(table, PROJECT, "NORT-7", title="Second")

        await self.an_exporter(db, tmp_path).drain()

        written = (tmp_path / PROJECT / "todo" / "NORT-7.md").read_text()
        assert "title: Second" in written
        assert "title: First" not in written

    async def test_a_drain_commits(self, db, tmp_path):
        """The export is git-committed, which is what makes it an audit trail."""
        import subprocess

        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await self.an_exporter(db, tmp_path).drain()

        log = subprocess.run(
            ["git", "-C", str(tmp_path), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert f"{PROJECT}/NORT-7" in log.stdout


class TestSplitRowId:
    """The inverse of ``task_table.row_id``, which the drain needs to load a row."""

    def test_it_splits_a_row_id_back(self):
        assert split_row_id("northwind/NORT-7") == ("northwind", "NORT-7")

    def test_it_splits_on_the_first_slash(self):
        """A project name is the first segment, and a task id may hold no slash."""
        assert split_row_id("northwind/a/b") == ("northwind", "a/b")


class TestTheQueueStaysOffTheNoticePath:
    """Nothing draws the queue, so a write to it must wake no client."""

    async def test_a_task_write_notices_the_task_and_not_the_queue(self, db):
        table = SqliteTaskTable(db)
        cursor = await db.revision()

        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")

        notices, _ = await db.notices_since(cursor)
        assert notices == {"tasks": {f"{PROJECT}/NORT-7"}}

    async def test_the_queue_row_is_written_all_the_same(self, db):
        """The notice is suppressed, not the write: the export still owes it."""
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")

        assert await SqliteExportQueue(db).depth() == 1


class TestQueueAll:
    """The enqueue with no task write to join: the rebuild, and the test double."""

    async def test_it_queues_what_it_is_given(self, queue):
        entry = Queued(
            id=f"{PROJECT}/NORT-7",
            path=f"{PROJECT}/todo/NORT-7.md",
            deleted=False,
            queued_at="2026-09-13T09:00:00+00:00",
        )
        await queue.queue_all([entry])
        assert await queue.pending() == [entry]

    async def test_queuing_the_same_task_twice_owes_one_export(self, queue):
        """One row per task, so a rebuild over a queued task does not double it."""
        first = Queued(
            id=f"{PROJECT}/NORT-7",
            path=f"{PROJECT}/todo/NORT-7.md",
            deleted=False,
            queued_at="2026-09-13T09:00:00+00:00",
        )
        await queue.queue_all([first])
        await queue.queue_all([Queued(**{**first.__dict__, "queued_at": "later"})])

        assert await queue.depth() == 1

    async def test_an_empty_rebuild_queues_nothing(self, queue):
        await queue.queue_all([])
        assert await queue.pending() == []


class TestTheOldestEntry:
    """What tells a busy notebook from a drain that stopped."""

    async def test_nothing_owed_has_no_oldest(self, queue):
        assert await queue.oldest() is None

    async def test_the_oldest_is_the_longest_waiting(self, db):
        table = SqliteTaskTable(db)
        await model.create(table, project=PROJECT, title="First", id="NORT-7")
        first = (await SqliteExportQueue(db).pending())[0].queued_at
        await model.create(table, project=PROJECT, title="Second", id="NORT-8")

        assert await SqliteExportQueue(db).oldest() == first
