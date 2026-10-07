"""A task's landing: the pull requests it reads, and the steps it has reached.

See ``CONTEXT.md``, "Landing", and ``docs/dev/data-architecture.md``.
"""

from ..types import Migration, Rung

LANDINGS: tuple[Rung, ...] = (
    Migration(
        (
            # GitHub's answer about one pull request, keyed `<project>/<number>`.
            # `envs` is JSON: `{step: {state, deploy_sha, first_seen_at}}`.
            "CREATE TABLE pull_requests ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, fetched_at TEXT, "
            "project TEXT NOT NULL DEFAULT '', "
            "number INTEGER NOT NULL DEFAULT 0, "
            "url TEXT NOT NULL DEFAULT '', "
            "title TEXT NOT NULL DEFAULT '', "
            "merged_at TEXT NOT NULL DEFAULT '', "
            "merge_sha TEXT NOT NULL DEFAULT '', "
            "state TEXT NOT NULL DEFAULT '', "
            "envs TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX pull_requests_revision ON pull_requests (revision)",
            # One row per step a task reached, keyed `<project>/<task_id>/<step>`.
            "CREATE TABLE task_steps ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "project TEXT NOT NULL DEFAULT '', "
            "task_id TEXT NOT NULL DEFAULT '', "
            "step TEXT NOT NULL DEFAULT '', "
            "pr_number INTEGER NOT NULL DEFAULT 0, "
            "at TEXT NOT NULL DEFAULT '', "
            "recorded_at TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX task_steps_revision ON task_steps (revision)",
            "CREATE INDEX task_steps_task_id ON task_steps (task_id)",
        )
    ),
)
