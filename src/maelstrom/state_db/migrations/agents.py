"""The canonical Agents Maelstrom has started."""

from ..types import Migration, Rung

AGENTS: tuple[Rung, ...] = (
    Migration(
        (
            "CREATE TABLE agents ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX agents_revision ON agents (revision)",
        )
    ),
)
