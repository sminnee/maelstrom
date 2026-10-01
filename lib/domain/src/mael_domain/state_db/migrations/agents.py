"""The canonical Agents Maelstrom has started, and what each one spent."""

import json
import sqlite3

from ..types import Migration, PythonMigration, Rung


def _link_records_to_tasks(conn: sqlite3.Connection) -> None:
    """Give each Agent record the task row id and the session id, as columns.

    The released record held ``task_session_id`` and, from the router alone, a
    bare ``task_id``. Neither names the project. ``tasks.session_id`` does: it
    is the same derived id, on a row whose id is ``<project>/<task_id>``. So the
    join gives the row id, for an adopted record too.

    The body is rewritten as well as the columns, because a record is read
    whole from its body and the two must agree.

    This rung must run before the tasks ladder drops ``tasks.session_id`` — see
    :data:`~mael_domain.state_db.migrate.LADDERS`. A database with no such
    column has no released records to link, and the join is skipped.
    """
    conn.execute("ALTER TABLE agents ADD COLUMN task TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE agents ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX agents_task ON agents (task)")
    conn.execute("CREATE INDEX agents_session_id ON agents (session_id)")
    task_columns = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM pragma_table_info('tasks')"
        ).fetchall()
    }
    by_session: dict[str, str] = {}
    if "session_id" in task_columns:
        by_session = {
            row[1]: row[0]
            for row in conn.execute(
                "SELECT id, session_id FROM tasks WHERE session_id != ''"
            ).fetchall()
        }
    for agent_id, body in conn.execute("SELECT id, body FROM agents").fetchall():
        try:
            agent = json.loads(body)
        except json.JSONDecodeError:
            continue
        if not isinstance(agent, dict):
            continue
        session_id = str(agent.pop("task_session_id", "") or "")
        agent.pop("task_id", None)
        agent["session_id"] = session_id
        agent["task"] = by_session.get(session_id, "")
        # `revision` is left alone, as every migration leaves it: the record
        # did not move, only its shape.
        conn.execute(
            "UPDATE agents SET body = ?, task = ?, session_id = ? WHERE id = ?",
            (json.dumps(agent, sort_keys=True), agent["task"], session_id, agent_id),
        )


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
    # The Agent record is the link between a task and its sessions, so both
    # ends are columns a query can use.
    PythonMigration(
        run=_link_records_to_tasks,
        description="link each Agent record to its task and its session",
    ),
)
