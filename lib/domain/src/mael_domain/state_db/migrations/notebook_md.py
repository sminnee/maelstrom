"""Reading the markdown task notebook into the tasks table.

A migration tool, not a backend. It runs once, from the tasks ladder's second
rung, so a user who built a notebook before the state database keeps their tasks.

Stdlib only, because a rung sits at the bottom layer and this file is reached
from one. That rules out importing :mod:`mael_domain.task`, so the frontmatter
parsing here is its own — deliberately small, and deliberately tolerant.

**A corrupt task is logged and skipped, not fatal.** This is the one place this
package diverges from :mod:`mael_domain.state_db.migrations.desk_json`, and the
difference is scale: one desk is one canvas, so a desk that will not parse must
stop the migration rather than be silently emptied. A notebook is hundreds of
independent files, and letting one unreadable file block every other task's
migration is the worse failure. The skipped file stays in the export tree, where
it can be recovered by hand.
"""

import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

#: The statuses a task may sit in — the folder names the importer walks.
#: Duplicated from :mod:`mael_domain.task` to keep this layer below the model.
_STATUSES = ("todo", "in-progress", "blocked", "done", "cancelled", "template")

#: Fixed namespace for deriving a task's session id. Duplicated from
#: :func:`mael_domain.task.session_id_for` for the same reason, and pinned by a
#: test that asserts the two agree — changing it would orphan every session.
_SESSION_NS = uuid.UUID("5b970d0a-51ab-49ae-ba93-0f7b0f615908")

#: Frontmatter key to column, for the keys whose names differ.
_KEBAB = {
    "pre-action": "pre_action",
    "post-action": "post_action",
    "last-run": "last_run",
}

#: Every scalar column an imported row may carry, with the notebook's defaults.
#:
#: Frozen at the shape the ``tasks`` table had when this rung was written. The
#: rung runs second, before any later ``ALTER TABLE`` rung, so a column added
#: after it does not exist yet and an INSERT naming one would fail. A field
#: added later arrives at its own rung with its default, which is right: the
#: markdown notebook this reads predates every such field.
_SCALARS = {
    "title": "",
    "command": "",
    "mode": "plan",
    "branch": "",
    "parent": "",
    "pre_action": "",
    "post_action": "",
    "created": "",
    "updated": "",
    "schedule": "",
    "last_run": "",
    "priority": "medium",
    "model": "",
    "base": "",
}


class NotebookImportError(Exception):
    """One task file that could not be read. Caught per file, never raised out."""


def import_notebook(conn: sqlite3.Connection, root: Path) -> None:
    """Copy every task under ``root`` into the ``tasks`` table at revision 0.

    A missing notebook is skipped and logged: a fresh install has none to carry.

    The rows name ``revision = 0`` rather than taking a cut, because a migration
    must not bump the revision counter. A client polling ``changed_since`` then
    reads them as the state it started from, which is what a fresh install and a
    restart both want.

    An unparseable file is logged and skipped, and the count of skipped files is
    reported — one bad task must not block the other 793.
    """
    if not root.is_dir():
        log.info("no task notebook at %s to import", root)
        return
    imported = 0
    skipped = 0
    for path in sorted(_task_files(root)):
        project = path.parent.parent.name
        task_id = path.stem
        try:
            row = _row_from_file(path, project, task_id)
        except (OSError, NotebookImportError) as exc:
            # Logged with the path, because recovering it is a hand edit and the
            # user needs to know which file to open.
            log.warning("skipping unreadable task at %s: %s", path, exc)
            skipped += 1
            continue
        names = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        conn.execute(
            f"INSERT OR REPLACE INTO tasks ({names}) VALUES ({placeholders})",  # noqa: S608 — names are this module's own
            row,
        )
        imported += 1
    log.info(
        "imported %d tasks from %s (%d skipped as unreadable)", imported, root, skipped
    )


def _task_files(root: Path) -> list[Path]:
    """Every ``<project>/<status>/<id>.md`` under ``root``.

    Only a file sitting directly in a status folder is a task, so the wiki under
    ``_wiki/`` and anything else beside the notebook is passed over rather than
    imported as a malformed task.
    """
    found: list[Path] = []
    for status in _STATUSES:
        found.extend(root.glob(f"*/{status}/*.md"))
    return found


def _row_from_file(path: Path, project: str, task_id: str) -> dict:
    """One task file as a row, or raise :class:`NotebookImportError`.

    Raises:
        NotebookImportError: If the file holds no readable frontmatter.
        OSError: If the file cannot be read.
    """
    text = path.read_text()
    frontmatter = _parse_frontmatter(text)
    sections = _split_sections(text)
    row: dict[str, object] = {
        "id": f"{project}/{task_id}",
        "revision": 0,
        "project": project,
        # The id in the file is advisory: the filename is what the notebook keys
        # by, so a file whose frontmatter disagrees imports under its real name.
        "task_id": task_id,
        "status": path.parent.name,
        "session_id": str(uuid.uuid5(_SESSION_NS, f"{project}\x00{task_id}")),
        "follows": json.dumps(_coerce_follows(frontmatter.get("follows"))),
        "content": sections.get("content", ""),
        "steps": sections.get("steps", ""),
        "log": sections.get("log", ""),
    }
    for column, default in _SCALARS.items():
        value = frontmatter.get(column)
        row[column] = default if value in (None, "") else str(value)
    return row


def _parse_frontmatter(text: str) -> dict:
    """The YAML frontmatter as a table, without importing a YAML parser.

    A rung is stdlib-only, and the notebook's own writer quotes anything YAML
    would auto-type, so the shapes reaching here are ``key: value`` scalars and
    one inline ``[a, b]`` list. Anything else is left as its raw string, which
    the column then carries verbatim rather than losing.

    Raises:
        NotebookImportError: If there is no frontmatter block at all.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise NotebookImportError("no frontmatter block")
    out: dict[str, object] = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return out
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        out[_KEBAB.get(key, key)] = _unquote(value.strip())
    raise NotebookImportError("unterminated frontmatter block")


def _unquote(value: str) -> str:
    """A frontmatter scalar with its quoting and escaping undone."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _coerce_follows(value: object) -> list[str]:
    """A ``follows`` value as a list of ids.

    Accepts the inline ``[a, b]`` list the notebook writes, a bare scalar, and
    an empty value.
    """
    if value is None or value == "":
        return []
    text = str(value).strip()
    if text in ("[]", ""):
        return []
    if text.startswith("[") and text.endswith("]"):
        parts = [p.strip() for p in text[1:-1].split(",")]
        return [_unquote(p) for p in parts if p]
    return [_unquote(text)]


_SECTIONS = ("content", "steps", "log")
_HEADING = re.compile(r"^##\s+(\w+)\s*$")


def _split_sections(text: str) -> dict[str, str]:
    """The ``## Content`` / ``## Steps`` / ``## Log`` sections of a task body.

    Splits only on those three headings, so a ``##`` inside a section's prose is
    kept verbatim as part of it — the same rule the model's own splitter follows.
    """
    _, _, body = text.partition("---")
    _, _, body = body.partition("\n---")
    out: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.split("\n"):
        match = _HEADING.match(line.strip())
        name = match.group(1).lower() if match else None
        if name in _SECTIONS:
            if current is not None:
                out[current] = "\n".join(buf).strip()
            current = name
            buf = []
            continue
        buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf).strip()
    return out
