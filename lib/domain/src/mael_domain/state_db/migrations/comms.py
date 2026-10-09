"""The comms ladder.

Canonical: maelstrom alone authors a comm. See ``CONTEXT.md``, "Comm". The
link to tasks is not here: it is the task's own ``comms`` column.
"""

from ..types import Migration, Rung

COMMS: tuple[Rung, ...] = (
    Migration(
        (
            # One row per comm, keyed `c<n>`. `recipients` is a JSON list of
            # free strings: a Slack channel, an email address, or a note.
            "CREATE TABLE comms ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "title TEXT NOT NULL DEFAULT '', "
            "content TEXT NOT NULL DEFAULT '', "
            "recipients TEXT NOT NULL DEFAULT '[]', "
            "created_at TEXT NOT NULL DEFAULT '', "
            "closed_at TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX comms_revision ON comms (revision)",
        )
    ),
    Migration(
        (
            "ALTER TABLE comms ADD COLUMN category TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE comms ADD COLUMN project TEXT NOT NULL DEFAULT ''",
        )
    ),
)
