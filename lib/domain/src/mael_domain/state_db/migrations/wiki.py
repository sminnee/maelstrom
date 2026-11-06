"""The wiki ladder: the cross-project pattern pages, one row each.

Canonical, so no ``fetched_at``. See :mod:`mael_domain.wiki` for the model.

Stdlib only, because a rung sits at the bottom layer. That rules out importing
:mod:`mael_domain.wiki`, so the page-id rule the import applies is its own,
pinned by a test that asserts it agrees with
:func:`mael_domain.wiki.normalise_page`.
"""

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..paths import notebook_path
from ..types import Migration, PythonMigration, Rung

log = logging.getLogger(__name__)

#: Where the wiki pages sat in the old git notebook.
_WIKI_DIR = "_wiki"

#: One legal page-id segment. Duplicated from :mod:`mael_domain.wiki`.
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


def is_page_id(page: str) -> bool:
    """Whether ``page`` is already a canonical page id."""
    if not page:
        return False
    return all(
        _SEGMENT.fullmatch(segment) and segment not in (".", "..")
        for segment in page.split("/")
    )


def _import_wiki(conn: sqlite3.Connection, root: Path | None) -> None:
    """Copy each page under ``<root>/tasks/_wiki`` into ``wiki_pages``.

    A file whose path is not a legal page id is skipped: it could never be read
    back. A file that is not UTF-8 is skipped too, so one bad file cannot fail
    the whole migration. The rows name ``revision = 0``, as every import rung does. An
    in-memory database has no ``root``, so nothing to import.
    """
    if root is None:
        return
    wiki_dir = notebook_path(root) / _WIKI_DIR
    if not wiki_dir.is_dir():
        return
    for file in sorted(wiki_dir.rglob("*.md")):
        page = file.relative_to(wiki_dir).as_posix()[: -len(".md")]
        if not file.is_file() or not is_page_id(page):
            log.warning("wiki file %s is not a legal page id: skipped", file)
            continue
        try:
            body = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log.warning("wiki file %s is not UTF-8: skipped", file)
            continue
        updated = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
        conn.execute(
            "INSERT OR IGNORE INTO wiki_pages (id, revision, body, updated) "
            "VALUES (?, 0, ?, ?)",
            (page, body, updated.isoformat()),
        )


#: The wiki ladder. Append-only, so a rung's version is its index plus one.
WIKI: tuple[Rung, ...] = (
    Migration(
        (
            # ``id`` is the page id, such as ``dev-patterns/python/pypi``.
            "CREATE TABLE wiki_pages ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '', "
            "updated TEXT NOT NULL DEFAULT '')",
            "CREATE INDEX wiki_pages_revision ON wiki_pages (revision)",
        )
    ),
    PythonMigration(
        run=_import_wiki,
        description="import the wiki pages from the old git notebook, if any",
    ),
)
