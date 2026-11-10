"""The tasks ladder: the task notebook's source of truth.

Canonical, so no ``fetched_at``: nobody else authors a task.

The row carries the prose: ``content`` and ``log`` are columns beside the
frontmatter fields — see ``docs/dev/data-architecture.md``, "Canonical".
"""

import json
import sqlite3
from pathlib import Path

from ..paths import notebook_path
from ..types import Migration, PythonMigration, Rung
from .notebook_md import import_notebook


def _import_notebook(conn: sqlite3.Connection, root: Path | None) -> None:
    """Bring an existing markdown notebook in, as the ladder's second rung.

    An in-memory database has no ``root``, so nothing beside it to import.

    A ladder version guarantees a rung runs once, which is why no marker row is
    needed: a notebook the user later emptied cannot spring back from the files.
    """
    if root is not None:
        import_notebook(conn, notebook_path(root))


def _store_sessions(conn: sqlite3.Connection, root: Path | None) -> None:
    """Move each task's derived session id into its new ``sessions`` list.

    Each task with a ``session_id`` gets it as a ``claude`` entry. A Codex
    Agent record labelled with that id adds its own id, the thread id, as a
    ``codex`` entry, oldest first, and its ``task_session_id`` becomes the
    thread id. So resume and the agent link see no change at upgrade.

    The ``agents`` table is read only when it exists: on a fresh database this
    ladder can run before the agents ladder. A rewritten record keeps its
    revision, because a rung must not bump the counter. See
    :class:`~mael_domain.state_db.types.PythonMigration`.
    """
    del root
    conn.execute("ALTER TABLE tasks ADD COLUMN sessions TEXT NOT NULL DEFAULT '[]'")
    sessions: dict[str, list[dict[str, str]]] = {
        row[0]: [{"id": row[1], "harness": "claude"}]
        for row in conn.execute(
            "SELECT id, session_id FROM tasks WHERE session_id != ''"
        )
    }
    owner = {entries[0]["id"]: key for key, entries in sessions.items()}
    has_agents = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agents'"
    ).fetchone()
    if has_agents is not None:
        records = []
        for agent_id, body in conn.execute("SELECT id, body FROM agents"):
            try:
                record = json.loads(body)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("harness") != "codex":
                continue
            if (key := owner.get(record.get("task_session_id") or "")) is not None:
                records.append(
                    (str(record.get("started_at") or ""), agent_id, key, record)
                )
        for _, agent_id, key, record in sorted(records, key=lambda r: (r[0], r[1])):
            sessions[key].append({"id": agent_id, "harness": "codex"})
            record["task_session_id"] = agent_id
            conn.execute(
                "UPDATE agents SET body = ? WHERE id = ?",
                (json.dumps(record, sort_keys=True), agent_id),
            )
    for key, entries in sessions.items():
        conn.execute(
            "UPDATE tasks SET sessions = ? WHERE id = ?", (json.dumps(entries), key)
        )
    conn.execute("DROP INDEX IF EXISTS tasks_session_id")
    conn.execute("ALTER TABLE tasks DROP COLUMN session_id")


#: The columns rung 1's ``CREATE TABLE`` declares, in its order. Frozen: a rung
#: runs against whatever version a database is stamped at, and every database —
#: a fresh one included — starts at 0 and climbs the whole ladder. So a column
#: added here as well as in a later ``ALTER TABLE`` rung would be declared
#: twice on a fresh install and fail. A new field goes in a rung, and only in a
#: rung. ``execute_model`` is the worked example below.
#: The 17 original frontmatter fields, plus ``project`` and ``status`` — which the key and
#: the folder used to carry — plus ``session_id`` and the three body columns.
#:
#: ``id`` is ``<project>/<id>``: :class:`~mael_domain.state_db.db.StateDb` keys
#: every table by a single ``id`` column, so the project is folded into it and
#: kept in its own column besides, for the ``list`` filter to read.
_COLUMNS = (
    "project TEXT NOT NULL DEFAULT ''",
    "task_id TEXT NOT NULL DEFAULT ''",
    "title TEXT NOT NULL DEFAULT ''",
    "status TEXT NOT NULL DEFAULT ''",
    "command TEXT NOT NULL DEFAULT ''",
    "mode TEXT NOT NULL DEFAULT ''",
    "branch TEXT NOT NULL DEFAULT ''",
    "parent TEXT NOT NULL DEFAULT ''",
    "pre_action TEXT NOT NULL DEFAULT ''",
    "post_action TEXT NOT NULL DEFAULT ''",
    # A JSON list, as the index stored it: nothing queries a single follows edge.
    "follows TEXT NOT NULL DEFAULT '[]'",
    "created TEXT NOT NULL DEFAULT ''",
    "updated TEXT NOT NULL DEFAULT ''",
    "schedule TEXT NOT NULL DEFAULT ''",
    "last_run TEXT NOT NULL DEFAULT ''",
    "priority TEXT NOT NULL DEFAULT ''",
    "model TEXT NOT NULL DEFAULT ''",
    "base TEXT NOT NULL DEFAULT ''",
    # The uuid5 link a session was found by. The ``sessions`` rung moves it
    # into the ``sessions`` list and drops it.
    "session_id TEXT NOT NULL DEFAULT ''",
    # The prose. What makes the row whole, and the rollback total.
    "content TEXT NOT NULL DEFAULT ''",
    "steps TEXT NOT NULL DEFAULT ''",
    "log TEXT NOT NULL DEFAULT ''",
)


#: The tasks ladder. Append-only, so a rung's version is its index plus one.
TASKS: tuple[Rung, ...] = (
    Migration(
        (
            "CREATE TABLE tasks ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            + ", ".join(_COLUMNS)
            + ")",
            # Every table carries a revision index; that is what `changed_since`
            # range-scans.
            "CREATE INDEX tasks_revision ON tasks (revision)",
            # Carried over from the index this table replaces: the common
            # status-filtered list, and the reverse session lookup.
            "CREATE INDEX tasks_project_status ON tasks (project, status)",
            "CREATE INDEX tasks_session_id ON tasks (session_id)",
        )
    ),
    PythonMigration(
        run=_import_notebook,
        description="import the markdown task notebook, if there is one",
    ),
    # The model a session switches to when its plan is approved. A rung, not a
    # ``_COLUMNS`` entry — see the note on ``_COLUMNS``.
    Migration(("ALTER TABLE tasks ADD COLUMN execute_model TEXT NOT NULL DEFAULT ''",)),
    # Drops ``steps``, which rung 1 still declares — see the note on
    # ``_COLUMNS``. Steps text moves into content first, in the shape
    # ``from_markdown`` now keeps an old ``## Steps`` heading, so none is lost.
    Migration(
        (
            "UPDATE tasks SET content = "
            "CASE WHEN content = '' THEN '' ELSE content || char(10, 10) END "
            "|| '## Steps' || char(10, 10) || steps WHERE steps != ''",
            "ALTER TABLE tasks DROP COLUMN steps",
        )
    ),
    # The Registered PR. See CONTEXT.md, "Registered PR".
    Migration(
        (
            "ALTER TABLE tasks ADD COLUMN pr_number INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN pr_url TEXT NOT NULL DEFAULT ''",
        )
    ),
    # What fires a template. See CONTEXT.md, "Trigger".
    Migration(("ALTER TABLE tasks ADD COLUMN trigger TEXT NOT NULL DEFAULT ''",)),
    # The comms a task feeds: a JSON list, like ``follows``. See CONTEXT.md, "Comm".
    Migration(("ALTER TABLE tasks ADD COLUMN comms TEXT NOT NULL DEFAULT '[]'",)),
    # The Registered PR is withdrawn. See CONTEXT.md, "Withdrawn PR".
    Migration(
        ("ALTER TABLE tasks ADD COLUMN pr_withdrawn INTEGER NOT NULL DEFAULT 0",)
    ),
    # Each task stores its sessions. See CONTEXT.md, "Task session id".
    PythonMigration(
        run=_store_sessions,
        description="store each task's sessions on the task",
    ),
    # Branch stacking is gone, and the task base with it. See ADR 0004.
    Migration(("ALTER TABLE tasks DROP COLUMN base",)),
)
