"""Tests for the tasks ladder's import rung.

The notebook import is the tasks ladder's second rung. The ladder version
guarantees a rung runs once, so no marker row is needed and a notebook the user
later emptied cannot spring back from the files.

Mirrors ``test_desk_store.py::TestTheDeskJsonImportRung``, and diverges on one
rule: a corrupt task is logged and skipped rather than failing the migration.
"""

import pytest

from maelstrom.state_db.migrate import open_state_db
from maelstrom.state_db.migrations.notebook_md import _SESSION_NS
from maelstrom.state_db.paths import get_state_db_path
from maelstrom.task import Task, session_id_for
from maelstrom.task_table import SqliteTaskTable

A_TASK = Task(
    id="2026-06-11.1",
    title="Move the notebook",
    project="maelstrom",
    command="plan-task",
    mode="plan",
    branch="refactor/data-backend",
    parent="2026-06-11",
    pre_action="linear.in-progress",
    post_action="linear.done",
    follows=["2026-06-10.1", "2026-06-10.2"],
    created="2026-06-11T09:00:00+00:00",
    updated="2026-06-11T10:00:00+00:00",
    schedule="0 9 * * 1",
    last_run="2026-06-10T09:00:00+00:00",
    priority="high",
    model="opus",
    base="main",
    content="The prose the row carries.",
    steps="1. write the ladder",
    log="- started",
    status="todo",
)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Keep the rung's read inside tmp_path, not the developer's ~/.maelstrom.

    The suite's own ``_isolate_state_db_paths`` already does this; it is repeated
    here because these tests are *about* the rung's read, so the isolation is
    load-bearing rather than incidental.

    Both resolvers, because the database resolves through ``get_state_root``
    while the notebook the rung imports resolves through ``get_maelstrom_dir``.
    Pinned here rather than through ``MAEL_STATE_ROOT``: the suite pins the
    binding, so the variable would not be read.
    """
    monkeypatch.setattr("maelstrom.state_db.paths.get_maelstrom_dir", lambda: tmp_path)
    monkeypatch.setattr("maelstrom.state_db.paths.get_state_root", lambda: tmp_path)


def write_task(tmp_path, task: Task) -> None:
    """Put ``task`` in the notebook the way the markdown store does."""
    folder = tmp_path / "tasks" / task.project / task.status
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{task.id}.md").write_text(task.to_markdown())


async def migrated(tmp_path):
    """A database with the ladder run against the notebook in ``tmp_path``."""
    db = open_state_db(tmp_path / "state.db")
    await db.migrate()
    return db


class TestAFreshPlaypenStartsEmpty:
    """A playpen imports nothing, however full the real notebook is.

    The rung reads the notebook under ``get_maelstrom_dir``, not under the state
    root, so a worktree's own database starts empty rather than with a stale
    snapshot of the real one. A playpen that imported 800 real tasks would be
    indistinguishable from prod at a glance, which is the failure this guards.
    """

    async def test_an_empty_playpen_imports_no_tasks(self, tmp_path, monkeypatch):
        real = tmp_path / "real"
        write_task(real, A_TASK)
        monkeypatch.setattr(
            "maelstrom.state_db.paths.get_maelstrom_dir", lambda: real / "empty"
        )
        playpen = tmp_path / "playpens" / "bravo"
        playpen.mkdir(parents=True)
        monkeypatch.setattr("maelstrom.state_db.paths.get_state_root", lambda: playpen)

        db = open_state_db(get_state_db_path())
        try:
            await db.migrate()
            assert await db.read_all("tasks") == []
        finally:
            db.close()


class TestTheNotebookImportRung:
    async def test_the_namespace_matches_the_model(self):
        """The rung derives ``session_id`` itself, so the two must not drift.

        A divergence here would break the reverse session lookup for every
        imported task, silently — the id would simply never match.
        """
        from maelstrom.task import _SESSION_NS as model_ns

        assert _SESSION_NS == model_ns

    async def test_a_task_round_trips_every_field(self, tmp_path):
        """The row carries the prose, so the body must survive the import."""
        write_task(tmp_path, A_TASK)
        db = await migrated(tmp_path)
        try:
            loaded = await SqliteTaskTable(db).load("maelstrom", "2026-06-11.1")
            assert loaded == A_TASK
        finally:
            db.close()

    async def test_the_rows_carry_revision_zero(self, tmp_path):
        """A migration must not bump the counter: a client reads them as the start."""
        write_task(tmp_path, A_TASK)
        db = await migrated(tmp_path)
        try:
            assert await db.revision() == 0
            assert {row["revision"] for row in await db.read_all("tasks")} == {0}
            assert await db.changed_since("tasks", 0) == []
        finally:
            db.close()

    async def test_status_comes_from_the_folder(self, tmp_path):
        """The folder was the status, so the column takes it from there."""
        for status in ("todo", "in-progress", "done", "template"):
            write_task(
                tmp_path, Task(id=f"t-{status}", title="T", project="p", status=status)
            )
        db = await migrated(tmp_path)
        try:
            table = SqliteTaskTable(db)
            for status in ("todo", "in-progress", "done", "template"):
                found = await table.list("p", status=status)
                assert [t.id for t in found] == [f"t-{status}"]
        finally:
            db.close()

    async def test_every_project_is_imported(self, tmp_path):
        write_task(tmp_path, Task(id="1", title="A", project="alpha"))
        write_task(tmp_path, Task(id="1", title="B", project="bravo"))
        db = await migrated(tmp_path)
        try:
            table = SqliteTaskTable(db)
            alpha = await table.load("alpha", "1")
            bravo = await table.load("bravo", "1")
            assert alpha is not None and alpha.title == "A"
            assert bravo is not None and bravo.title == "B"
        finally:
            db.close()

    async def test_the_session_id_is_derived_and_findable(self, tmp_path):
        """The reverse lookup must work on an imported row, not only a written one."""
        write_task(tmp_path, A_TASK)
        db = await migrated(tmp_path)
        try:
            wanted = session_id_for("maelstrom", "2026-06-11.1")
            found = await SqliteTaskTable(db).find_by_session_id(wanted)
            assert found is not None and found.id == "2026-06-11.1"
        finally:
            db.close()

    async def test_a_second_run_changes_nothing(self, tmp_path):
        """The ladder version guarantees the rung runs once."""
        write_task(tmp_path, A_TASK)
        db = await migrated(tmp_path)
        try:
            await SqliteTaskTable(db).delete("maelstrom", "2026-06-11.1")
        finally:
            db.close()

        again = open_state_db(tmp_path / "state.db")
        try:
            await again.migrate()
            assert await SqliteTaskTable(again).list("maelstrom") == []
        finally:
            again.close()

    async def test_no_notebook_is_skipped(self, tmp_path):
        """A fresh install has none to carry, which is not a failure."""
        db = await migrated(tmp_path)
        try:
            assert await db.read_all("tasks") == []
        finally:
            db.close()

    async def test_a_corrupt_task_is_skipped_not_fatal(self, tmp_path):
        """The divergence from the desk: one bad file must not block 793 others.

        A desk is one canvas, so a corrupt desk stops the migration. A notebook
        is hundreds of independent files, and refusing them all over one is the
        worse failure — the bad file stays in the export tree to fix by hand.
        """
        write_task(tmp_path, A_TASK)
        bad = tmp_path / "tasks" / "maelstrom" / "todo" / "broken.md"
        bad.write_text("this file has no frontmatter at all")
        db = await migrated(tmp_path)
        try:
            listed = await SqliteTaskTable(db).list("maelstrom")
            assert [t.id for t in listed] == ["2026-06-11.1"]
        finally:
            db.close()

    async def test_the_skipped_count_is_reported(self, tmp_path, caplog):
        """Silence is the failure mode: a skipped task must be findable in the log."""
        write_task(tmp_path, A_TASK)
        (tmp_path / "tasks" / "maelstrom" / "todo" / "broken.md").write_text("no fm")
        with caplog.at_level("INFO"):
            db = await migrated(tmp_path)
        db.close()
        assert "1 skipped" in caplog.text
        assert "broken.md" in caplog.text

    async def test_the_wiki_is_not_imported_as_tasks(self, tmp_path):
        """The wiki shares the tree but is not a task; only status folders count."""
        write_task(tmp_path, A_TASK)
        wiki = tmp_path / "tasks" / "_wiki"
        wiki.mkdir(parents=True, exist_ok=True)
        (wiki / "a-pattern.md").write_text("# A pattern\n")
        db = await migrated(tmp_path)
        try:
            rows = await db.read_all("tasks")
            assert [row["task_id"] for row in rows] == ["2026-06-11.1"]
        finally:
            db.close()

    async def test_an_empty_follows_imports_as_empty(self, tmp_path):
        write_task(tmp_path, Task(id="1", title="T", project="p", follows=[]))
        db = await migrated(tmp_path)
        try:
            loaded = await SqliteTaskTable(db).load("p", "1")
            assert loaded is not None and loaded.follows == []
        finally:
            db.close()

    async def test_a_quoted_scalar_is_unquoted(self, tmp_path):
        """The writer quotes anything YAML would auto-type; the rung must undo it."""
        write_task(tmp_path, Task(id="1", title="12:30 sharp", project="p"))
        db = await migrated(tmp_path)
        try:
            loaded = await SqliteTaskTable(db).load("p", "1")
            assert loaded is not None and loaded.title == "12:30 sharp"
        finally:
            db.close()

    async def test_the_filename_wins_over_a_disagreeing_id(self, tmp_path):
        """The notebook keys by filename, so that is the id the row takes."""
        task = Task(id="claims-to-be-this", title="T", project="p")
        folder = tmp_path / "tasks" / "p" / "todo"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "really-this.md").write_text(task.to_markdown())
        db = await migrated(tmp_path)
        try:
            assert await SqliteTaskTable(db).load("p", "really-this") is not None
            assert await SqliteTaskTable(db).load("p", "claims-to-be-this") is None
        finally:
            db.close()
