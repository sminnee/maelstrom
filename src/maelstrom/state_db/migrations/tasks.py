"""The tasks ladder: the task notebook's source of truth.

Canonical, so no ``fetched_at``: nobody else authors a task.

The row carries the prose. ``content``, ``steps`` and ``log`` are columns beside
the frontmatter fields, because splitting a task across two stores is what makes
a rollback partial — see ``docs/dev/data-architecture.md``, "Canonical".
"""

import sqlite3

from ..paths import get_notebook_path
from ..types import Migration, PythonMigration, Rung
from .notebook_md import import_notebook


def _import_notebook(conn: sqlite3.Connection) -> None:
    """Bring an existing markdown notebook in, as the ladder's second rung.

    The path is resolved here rather than captured, so a test that redirects
    ``get_maelstrom_dir`` is honoured — and it is resolved through
    :mod:`maelstrom.state_db.paths` rather than through
    :func:`maelstrom.task_store.tasks_root`, which is the binding the suite's
    isolation fixture patches.

    A ladder version guarantees a rung runs once, which is why no marker row is
    needed: a notebook the user later emptied cannot spring back from the files.
    """
    import_notebook(conn, get_notebook_path())


#: Every column the ``tasks`` table carries, in the order the DDL declares them.
#: The 17 frontmatter fields, plus ``project`` and ``status`` — which the key and
#: the folder used to carry — plus ``session_id`` and the three body columns.
#:
#: ``id`` is ``<project>/<id>``: :class:`~maelstrom.state_db.db.StateDb` keys
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
    # The deterministic uuid5 link a session is found by; see
    # :func:`maelstrom.task.session_id_for`.
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
)
