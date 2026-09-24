"""The spine: the one table set every subsystem depends on.

Versioned through ``PRAGMA user_version``, because it is the one table set a
global version legitimately describes.
"""

from ..types import Migration

#: The spine's ladder. Append-only, like every other.
SPINE: tuple[Migration, ...] = (
    Migration(
        (
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "INSERT INTO meta (key, value) VALUES ('revision', '0')",
            "CREATE TABLE schema_version ("
            "name TEXT PRIMARY KEY, version INTEGER NOT NULL)",
            "CREATE TABLE removals ("
            "table_name TEXT NOT NULL, id TEXT NOT NULL, "
            "revision INTEGER NOT NULL, PRIMARY KEY (table_name, id))",
            "CREATE INDEX removals_revision ON removals (revision)",
            "CREATE TABLE refresher_health ("
            "name TEXT PRIMARY KEY, "
            "reachable INTEGER NOT NULL DEFAULT 1, "
            "since TEXT NOT NULL DEFAULT '', "
            "last_attempt TEXT NOT NULL DEFAULT '', "
            "last_success TEXT NOT NULL DEFAULT '', "
            "stand_off_until TEXT NOT NULL DEFAULT '', "
            "detail TEXT NOT NULL DEFAULT '')",
        )
    ),
)
