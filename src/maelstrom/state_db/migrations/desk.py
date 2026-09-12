"""The desk ladder: the first subsystem on the state database.

Canonical, so no ``fetched_at``: nobody else authors a desk.
"""

import sqlite3

from ..paths import get_desk_json_path
from ..types import Migration, PythonMigration, Rung
from .desk_json import import_desk_json


def _import_desk_json(conn: sqlite3.Connection) -> None:
    """Bring an existing ``desk.json`` in, as the ladder's second rung.

    The path is resolved here rather than captured, so a test that redirects
    ``get_maelstrom_dir`` is honoured.

    A ladder version guarantees a rung runs once, which is why no marker row is
    needed: a desk the user later emptied cannot spring back from the file.
    """
    import_desk_json(conn, get_desk_json_path())


#: The desk's ladder. Append-only, so a rung's version is its index plus one.
DESK: tuple[Rung, ...] = (
    Migration(
        (
            # The entry's own fields, `addedAt` among them, live inside
            # `body`. A column beside it would be a second copy free to
            # disagree, and nothing queries the desk by date.
            "CREATE TABLE desk ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX desk_revision ON desk (revision)",
        )
    ),
    PythonMigration(
        run=_import_desk_json,
        description="import ~/.maelstrom/desk.json, if there is one",
    ),
)
