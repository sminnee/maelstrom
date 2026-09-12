"""The desk ladder: the first subsystem on the state database.

Canonical, so no ``fetched_at``: nobody else authors a desk.
"""

from ..types import Migration, Rung

#: The desk's ladder. Append-only, so a rung's version is its index plus one.
DESK: tuple[Rung, ...] = (
    Migration(
        (
            # The entry's own fields, `addedAt` among them, live inside
            # `body`. A column beside it would be a second copy free to
            # disagree, and nothing queries the desk by date.
            "CREATE TABLE desk ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX desk_revision ON desk (revision)",
        )
    ),
)
