"""The canonical Agents Maelstrom has started, and what each one spent."""

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
    # A new rung, never an edit to the one above: a shipped migration changed
    # in place leaves an existing database silently diverged, because `check()`
    # compares version numbers rather than table shape.
    Migration(
        (
            # One snapshot of what an agent had spent when it reached a stage
            # of the work. `id` is the agent id and an ordinal, so `read_all`'s
            # sort by id is the order the stages were reached in.
            #
            # `own_*` is the agent's own spend, `sub_*` its subagents'; the
            # `_delta` pair is each one's spend since the previous snapshot,
            # stored rather than derived so a report reads one row per stage.
            #
            # Totals, not the four-way split a `usage` block carries: the world
            # holds one summed figure per side, so a split here could only ever
            # store zeros. See `docs/dev/agent-daemon.md`, "A turn".
            "CREATE TABLE agent_milestones ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "agent_id TEXT NOT NULL DEFAULT '', "
            "name TEXT NOT NULL DEFAULT '', "
            "at TEXT NOT NULL DEFAULT '', "
            # 0 when the name is outside the declared vocabulary. Such a row is
            # kept as the agent wrote it, so a typo is visible in the report.
            "recognised INTEGER NOT NULL DEFAULT 1, "
            "own_total INTEGER NOT NULL DEFAULT 0, "
            "sub_total INTEGER NOT NULL DEFAULT 0, "
            "cost_usd REAL NOT NULL DEFAULT 0, "
            "own_delta INTEGER NOT NULL DEFAULT 0, "
            "sub_delta INTEGER NOT NULL DEFAULT 0, "
            "cost_delta REAL NOT NULL DEFAULT 0)",
            "CREATE INDEX agent_milestones_revision ON agent_milestones (revision)",
            "CREATE INDEX agent_milestones_agent ON agent_milestones (agent_id)",
        )
    ),
)
