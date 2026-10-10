"""The operations ladder.

Canonical: the orchestrator server alone runs an operation. See ``CONTEXT.md``,
"Operation". Only the newest are kept; the store drops the rest.
"""

from ..types import Migration, Rung

OPERATIONS: tuple[Rung, ...] = (
    Migration(
        (
            # One row per operation, keyed `op<n>`. `body` is the wire entity
            # as JSON; `log` is its lines by step name, a separate read;
            # `command` is what a retry runs again.
            "CREATE TABLE operations ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '{}', "
            "log TEXT NOT NULL DEFAULT '{}', "
            "command TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX operations_revision ON operations (revision)",
        )
    ),
)
