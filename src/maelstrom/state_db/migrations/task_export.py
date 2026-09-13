"""The task-export ladder: the queue table the markdown export drains.

See :mod:`maelstrom.task_export` for why the queue lives in this database.
"""

from ..types import Migration, Rung

#: The task-export ladder. Append-only, so a rung's version is its index plus one.
TASK_EXPORT: tuple[Rung, ...] = (
    Migration(
        (
            # ``id`` is the task's own row id, ``<project>/<id>``: one row per
            # task, upserted, so a task written ten times drains once.
            #
            # ``path`` is the export key as it stood at enqueue time. A write
            # re-derives it from the live row instead, because a task that
            # moved again before the drain must land at its *current* path —
            # but a delete leaves no row to re-derive from, so the path it had
            # is recorded here or it is lost.
            #
            # ``deleted`` says which of those two a row is.
            "CREATE TABLE task_export ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "path TEXT NOT NULL DEFAULT '', "
            "deleted INTEGER NOT NULL DEFAULT 0, "
            "queued_at TEXT NOT NULL DEFAULT '')",
            # Every table carries a revision index; that is what `changed_since`
            # range-scans. The drain reads the whole queue rather than a range,
            # but the spine's notice path reads this column for every table.
            "CREATE INDEX task_export_revision ON task_export (revision)",
        )
    ),
)
