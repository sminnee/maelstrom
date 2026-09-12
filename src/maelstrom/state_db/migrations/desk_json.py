"""Reading an old ``desk.json`` into the desk table.

A migration tool, not a backend. It runs once, from the desk ladder's second
rung, so a user who built a desk before the state database keeps their canvas.

Stdlib only, because a rung sits at the bottom layer and this file is reached
from one.

**A corrupt file fails the migration.** That is the opposite of what a desk
backend does, and the difference is the duty: a backend loads an unreadable
desk as empty, because a desk is a convenience and refusing to start would help
nobody. A migration that turned a corrupt desk into an empty one would destroy
the user's canvas silently, which is what every refusal in this package exists
to prevent.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The kinds a desk id can name, duplicated to keep this layer below the model.
_KIND_PREFIXES = ("task:", "agent:")


def import_desk_json(conn: sqlite3.Connection, path: Path) -> None:
    """Copy ``path``'s entries into the ``desk`` table at revision 0.

    A missing file is skipped and logged: a fresh install has no desk to carry.

    The rows name ``revision = 0`` rather than taking a cut, because a migration
    must not bump the revision counter. A client polling ``changed_since`` then
    reads them as the state it started from, which is what a fresh install and a
    restart both want.

    Raises:
        json.JSONDecodeError: If ``path`` holds text that is not JSON.
        ValueError: If ``path`` holds JSON that is not a table of entries.
        OSError: If ``path`` exists but cannot be read.
    """
    if not path.is_file():
        log.info("no desk at %s to import", path)
        return
    with open(path) as f:
        table = json.load(f)
    if not isinstance(table, dict):
        raise ValueError(f"the desk at {path} is not a table of entries")
    entries = {
        k: v for k, v in (_migrated(k, v) for k, v in table.items()) if is_entry(v)
    }
    for id, entry in entries.items():
        conn.execute(
            "INSERT OR REPLACE INTO desk (id, revision, body) VALUES (?, 0, ?)",
            (id, json.dumps(entry, sort_keys=True)),
        )
    log.info("imported %d desk entries from %s", len(entries), path)


def _migrated(key: str, value: Any) -> tuple[str, Any]:
    """``key`` and its entry, with a desk written before ids carried a kind fixed.

    A desk from that time held bare task ids. Left alone they would match no
    task and the user would lose their canvas, so the key and the entry's own
    id both gain the ``task:`` prefix. An id that already carries a kind is
    left as it is, so the two shapes need no version field to tell apart.
    """
    if not isinstance(key, str) or key.startswith(_KIND_PREFIXES):
        return key, value
    migrated = f"task:{key}"
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return migrated, {**value, "id": migrated}
    return migrated, value


def is_entry(value: Any) -> bool:
    """Whether ``value`` is a desk entry the wire can carry.

    One definition, because two would be free to disagree: this decides what
    the import admits, and :mod:`maelstrom.desk_store` decides what a read
    publishes. A malformed entry is not corruption — the file parsed, and one
    bad entry costs one node.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("addedAt"), str)
    )
