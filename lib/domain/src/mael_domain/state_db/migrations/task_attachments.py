"""The documents attached past the agent, the worktree and the server that made them."""

from ..types import Migration, Rung

TASK_ATTACHMENTS: tuple[Rung, ...] = (
    Migration(
        (
            # See `docs/dev/orchestrator-server.md`, "An attached document".
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
)
