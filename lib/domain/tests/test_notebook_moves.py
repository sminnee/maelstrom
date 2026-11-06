"""Tests for the rungs that retire the git task notebook.

The attachments-move ladder takes the media out of ``<root>/tasks/`` and
rewrites the refs to them. Each test runs over a real SQLite file in
``tmp_path``, so the rung reads the notebook beside it.
"""

from mael_domain.state_db.migrate import LADDERS, open_state_db
from mael_domain.state_db.migrations.attachments_move import _TOKEN
from mael_domain.state_db.types import Write
from mael_domain.task import MAEL_TASK_DIR_TOKEN, Task


async def migrated(tmp_path, *, upto: dict[str, int] | None = None):
    """A database at ``tmp_path``, migrated, with ``upto`` ladders held short."""
    db = open_state_db(tmp_path / "state.db")
    for name, count in (upto or {}).items():
        db.ladders[name] = db.ladders[name][:count]
    await db.migrate()
    return db


async def climb(db) -> None:
    """Restore every ladder to this build's, and migrate again."""
    db.ladders.update(LADDERS)
    await db.migrate()


def write(path, text="x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def tree(root) -> dict[str, str]:
    """Every file under ``root``, keyed by its path relative to ``root``."""
    return {
        p.relative_to(root).as_posix(): p.read_text()
        for p in root.rglob("*")
        if p.is_file() and p.name != "state.db" and not p.name.startswith("state.db-")
    }


# --- the attachments move ---


class TestTheAttachmentsMoveRung:
    async def test_buckets_move_per_project(self, tmp_path):
        """Two projects with one bucket name keep apart: buckets are per project."""
        write(tmp_path / "tasks/p/images/NORT-1/a.png", "p")
        write(tmp_path / "tasks/q/images/NORT-1/a.png", "q")

        (await migrated(tmp_path)).close()

        new = tmp_path / "attachments"
        assert (new / "p/images/NORT-1/a.png").read_text() == "p"
        assert (new / "q/images/NORT-1/a.png").read_text() == "q"
        assert not (tmp_path / "tasks/p/images/NORT-1").exists()

    async def test_only_free_project_buckets_move(self, tmp_path):
        """A taken target, a hidden project and a loose file all stay put."""
        write(tmp_path / "tasks/p/images/b/a.png", "old")
        write(tmp_path / "attachments/p/images/b/a.png", "new")
        write(tmp_path / "tasks/p/images/c/a.png", "c")
        write(tmp_path / "tasks/p/images/loose.png", "loose")
        write(tmp_path / "tasks/_x/images/b/a.png", "x")

        (await migrated(tmp_path)).close()

        assert tree(tmp_path) == {
            "attachments/p/images/b/a.png": "new",
            "attachments/p/images/c/a.png": "c",
            "tasks/p/images/b/a.png": "old",
            "tasks/p/images/loose.png": "loose",
            "tasks/_x/images/b/a.png": "x",
        }

    async def test_no_old_notebook_is_a_no_op(self, tmp_path):
        (await migrated(tmp_path)).close()

        assert not (tmp_path / "attachments").exists()

    async def test_old_absolute_refs_are_rewritten(self, tmp_path):
        old = tmp_path / "tasks"
        old.mkdir()
        new = tmp_path / "attachments"
        own = f"![s]({old}/p/images/b/a.png)"
        other = f"![s]({old}/q/images/b/a.png)"
        elsewhere = "![s](/somewhere/else/tasks/p/images/b/a.png)"
        db = await migrated(tmp_path, upto={"attachments_move": 0})
        try:
            await db.write_all(
                [
                    Write("tasks", "p/t1", {"project": "p", "content": own}),
                    Write("tasks", "p/t2", {"project": "p", "content": other}),
                    Write("tasks", "p/t3", {"project": "p", "content": elsewhere}),
                    Write("tasks", "p/t4", {"project": "p", "content": "prose"}),
                    Write("task_attachments", "d1", {"task_key": "p/t1", "body": own}),
                ]
            )
            await climb(db)
            content = {r["id"]: r["content"] for r in await db.read_all("tasks")}
            row = await db.read("task_attachments", "d1")
            assert row is not None
            body = row["body"]
        finally:
            db.close()

        assert content == {
            "p/t1": f"![s]({_TOKEN}/images/b/a.png)",
            "p/t2": f"![s]({new}/q/images/b/a.png)",
            "p/t3": elsewhere,
            "p/t4": "prose",
        }
        assert body == f"![s]({_TOKEN}/images/b/a.png)"

    async def test_a_task_imported_in_the_same_migrate_is_rewritten(self, tmp_path):
        """One migrate imports the old notebook, then rewrites: ladder order holds."""
        ref = f"![s]({tmp_path}/tasks/p/images/b/a.png)"
        task = Task(id="t1", title="t", project="p", status="todo", content=ref)
        write(tmp_path / "tasks/p/todo/t1.md", task.to_markdown())

        db = await migrated(tmp_path)
        try:
            rows = await db.read_all("tasks")
        finally:
            db.close()

        assert [r["content"] for r in rows] == [f"![s]({_TOKEN}/images/b/a.png)"]

    def test_the_token_matches_the_model(self):
        """The rung writes the token itself, so the two must not drift."""
        assert _TOKEN == MAEL_TASK_DIR_TOKEN
