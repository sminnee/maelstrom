"""The Build queue's ladder: Merge requests and their Builds.

Canonical: the orchestrator server alone writes both. See ``CONTEXT.md``,
"Merge request" and "Queue build", and ``docs/dev/build-queue.md``.
"""

from ..types import Migration, Rung

MERGE_REQUESTS: tuple[Rung, ...] = (
    Migration(
        (
            # One row per Merge request, keyed `mr<n>`. `task` is
            # `<project>/<task id>`; `build` is the linked Build's id.
            "CREATE TABLE merge_requests ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "task TEXT NOT NULL DEFAULT '', "
            "project TEXT NOT NULL DEFAULT '', "
            "task_id TEXT NOT NULL DEFAULT '', "
            "branch TEXT NOT NULL DEFAULT '', "
            "sha TEXT NOT NULL DEFAULT '', "
            "base TEXT NOT NULL DEFAULT '', "
            "state TEXT NOT NULL DEFAULT '', "
            "build TEXT NOT NULL DEFAULT '', "
            "opened TEXT NOT NULL DEFAULT '', "
            "updated TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX merge_requests_revision ON merge_requests (revision)",
            "CREATE INDEX merge_requests_task ON merge_requests (task)",
            # One row per Build, keyed `b<n>`: one gate run of one SHA.
            "CREATE TABLE builds ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "merge_request TEXT NOT NULL DEFAULT '', "
            "project TEXT NOT NULL DEFAULT '', "
            "sha TEXT NOT NULL DEFAULT '', "
            "base TEXT NOT NULL DEFAULT '', "
            "state TEXT NOT NULL DEFAULT '', "
            "queued_at TEXT NOT NULL DEFAULT '', "
            "started TEXT NOT NULL DEFAULT '', "
            "ended TEXT NOT NULL DEFAULT '', "
            "slot TEXT NOT NULL DEFAULT '', "
            "job INTEGER NOT NULL DEFAULT 0, "
            "detail TEXT NOT NULL DEFAULT '', "
            "told TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX builds_revision ON builds (revision)",
            "CREATE INDEX builds_merge_request ON builds (merge_request)",
        )
    ),
    # The Merge: the submitted description, when it merged, and whether the
    # chaperone was told.
    Migration(
        (
            "ALTER TABLE merge_requests ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE merge_requests ADD COLUMN merged TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE merge_requests ADD COLUMN told TEXT NOT NULL DEFAULT ''",
        )
    ),
)
