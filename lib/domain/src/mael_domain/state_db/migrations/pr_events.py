"""The PR watch's ledger: what each Registered PR's agents were last told.

The orchestrator server's own bookkeeping. See ``CONTEXT.md``, "External
event", and ``docs/dev/orchestrator-server.md``, "External events".
"""

from ..types import Migration, Rung

PR_EVENTS: tuple[Rung, ...] = (
    Migration(
        (
            # One row per PR, keyed by its URL. `state` and `conflicting` are
            # the last read; the `told_*` oids are the heads whose failure and
            # pass the agents were told.
            "CREATE TABLE pr_events ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "state TEXT NOT NULL DEFAULT '', "
            "conflicting INTEGER NOT NULL DEFAULT 0, "
            "told_failed_oid TEXT NOT NULL DEFAULT '', "
            "told_green_oid TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX pr_events_revision ON pr_events (revision)",
        )
    ),
)
