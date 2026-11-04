"""Task documents: each one, its versions, and the tasks that link it.

The table keeps its old name; see ``docs/adr/0001-task-documents.md``, "Storage".
"""

import hashlib
import sqlite3
from pathlib import Path

from ..types import Migration, PythonMigration, Rung


def _id_of(key: str, name: str) -> str:
    """A document's id: as ``task_attachments._id_of`` makes it, frozen here."""
    return hashlib.sha256(f"{key}\0{name}".encode()).hexdigest()[:16]


def _to_versions(conn: sqlite3.Connection, _root: Path | None) -> None:
    """Make each row's text its document's version 1, and fold plans into one.

    Every approved plan of a task was its own row, keyed by its plan file. Now
    they are the versions of one document named ``plan``, in the order they were
    attached.
    """
    rows = conn.execute(
        "SELECT * FROM task_attachments ORDER BY task_key, attached_at, id"
    ).fetchall()
    conn.execute("DELETE FROM task_attachments")
    versions: dict[str, int] = {}
    for row in rows:
        is_plan = row["kind"] == "plan"
        name = "plan" if is_plan else row["name"]
        doc_id = _id_of(row["task_key"], name)
        version = versions[doc_id] = versions.get(doc_id, 0) + 1
        if version == 1:
            conn.execute(
                "INSERT INTO task_attachments (id, revision, task_key, kind, name, "
                "title, version, body, digest, attached_at) "
                "VALUES (?, 0, ?, 'markdown', ?, '', 1, '', '', ?)",
                (doc_id, row["task_key"], name, row["attached_at"]),
            )
        conn.execute(
            "INSERT INTO task_attachment_versions (id, revision, attachment_id, "
            "version, title, body, digest, created_at, review_status) "
            "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"{doc_id}@v{version}",
                doc_id,
                version,
                row["title"],
                row["body"],
                row["digest"],
                row["attached_at"],
                "approved" if is_plan else "draft",
            ),
        )


TASK_ATTACHMENTS: tuple[Rung, ...] = (
    Migration(
        (
            # See `docs/dev/orchestrator-server.md`, "Storing a Task document".
            "CREATE TABLE task_attachments ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "task_key TEXT NOT NULL DEFAULT '', "
            "kind TEXT NOT NULL DEFAULT '', "
            "path TEXT NOT NULL DEFAULT '', "
            "title TEXT NOT NULL DEFAULT '', "
            "version INTEGER NOT NULL DEFAULT 1, "
            "body TEXT NOT NULL DEFAULT '', "
            "digest TEXT NOT NULL DEFAULT '', "
            "attached_at TEXT NOT NULL DEFAULT '', "
            "UNIQUE (task_key, path))",
            "CREATE INDEX task_attachments_revision ON task_attachments (revision)",
        )
    ),
    Migration(
        (
            "CREATE TABLE task_attachment_versions ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "attachment_id TEXT NOT NULL DEFAULT '', "
            "version INTEGER NOT NULL DEFAULT 1, "
            "title TEXT NOT NULL DEFAULT '', "
            "body TEXT NOT NULL DEFAULT '', "
            "digest TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL DEFAULT '', "
            "review_status TEXT NOT NULL DEFAULT 'draft', "
            "UNIQUE (attachment_id, version))",
            "CREATE INDEX task_attachment_versions_revision "
            "ON task_attachment_versions (revision)",
            "CREATE TABLE task_attachment_links ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "attachment_id TEXT NOT NULL DEFAULT '', "
            "project TEXT NOT NULL DEFAULT '', "
            "task_id TEXT NOT NULL DEFAULT '', "
            "fragment TEXT NOT NULL DEFAULT '', "
            "UNIQUE (attachment_id, project, task_id, fragment))",
            "CREATE INDEX task_attachment_links_revision "
            "ON task_attachment_links (revision)",
            "CREATE INDEX task_attachment_links_task "
            "ON task_attachment_links (task_id, project)",
        )
    ),
    Migration(
        (
            "ALTER TABLE task_attachments RENAME COLUMN path TO name",
            "ALTER TABLE task_attachments "
            "ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        )
    ),
    PythonMigration(_to_versions, "each attached row becomes its document's version 1"),
    Migration(
        tuple(
            f"ALTER TABLE task_attachments DROP COLUMN {column}"
            for column in ("title", "version", "body", "digest")
        )
    ),
)
