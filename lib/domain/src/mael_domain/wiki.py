"""Model for the cross-project development-pattern wiki.

See ``docs/guide/concepts.md`` for what the wiki is for and how it differs from
per-project docs and Claude's memory.

Each page is one row in the state database's ``wiki_pages`` table, keyed by
its page id. A page id is a free-form relative path such as
``dev-patterns/python/pypi-publication``; the taxonomy is a convention, and only
path safety is enforced here. Each page carries a one-line ``description:`` in
frontmatter, which is what :func:`list_pages` prints.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from mael_common.util import now_iso

from .state_db.db import StateDb

TABLE = "wiki_pages"

# The legal character set for one path segment. Note this deliberately allows the
# dot, so it matches ``.`` and ``..`` too — rejecting those is the explicit check
# in :func:`normalise_page`, not this pattern.
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class WikiPage:
    """One page in the table of contents.

    ``path`` is the page id. ``description`` is the one-line ``description:``
    frontmatter value, or an empty string when the page has no frontmatter.
    """

    path: str
    description: str


def normalise_page(page: str) -> str:
    """Return the canonical page id for ``page``.

    Strips a leading ``/`` and a trailing ``.md``, then validates every segment.
    Raises ``ValueError`` for an empty path, any segment outside
    ``[A-Za-z0-9._-]``, and the bare ``.``/``..`` segments. The character set
    alone does not stop traversal — it allows the dot — so the explicit
    ``.``/``..`` rejection below is what keeps a page id a clean relative path.

    :func:`mael_domain.state_db.migrations.wiki.is_page_id` duplicates this rule
    for the import rung. A test pins the two together.
    """
    cleaned = page.strip().lstrip("/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[: -len(".md")]
    if not cleaned:
        raise ValueError(f"Invalid wiki page: {page!r}")
    segments = cleaned.split("/")
    for segment in segments:
        if not _SEGMENT.fullmatch(segment) or segment in (".", ".."):
            raise ValueError(f"Invalid wiki page: {page!r}")
    return "/".join(segments)


class WikiTable(ABC):
    """Where wiki pages are stored, keyed by page id."""

    @abstractmethod
    async def read(self, page: str) -> str | None:
        """The body of ``page``, or ``None`` when there is no such page."""

    @abstractmethod
    async def write(self, page: str, body: str) -> None:
        """Store ``body`` as the whole of ``page``."""

    @abstractmethod
    async def list(self) -> dict[str, str]:
        """Every page id with its body."""


class InMemoryWikiTable(WikiTable):
    """Wiki pages for a test."""

    def __init__(self) -> None:
        self._pages: dict[str, str] = {}

    async def read(self, page: str) -> str | None:
        return self._pages.get(page)

    async def write(self, page: str, body: str) -> None:
        self._pages[page] = body

    async def list(self) -> dict[str, str]:
        return dict(self._pages)


class SqliteWikiTable(WikiTable):
    """Wiki pages in the state database."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, page: str) -> str | None:
        row = await self._db.read(TABLE, page)
        return row["body"] if row is not None else None

    async def write(self, page: str, body: str) -> None:
        await self._db.upsert(TABLE, page, body=body, updated=now_iso())

    async def list(self) -> dict[str, str]:
        return {row["id"]: row["body"] for row in await self._db.read_all(TABLE)}


def parse_description(text: str) -> str:
    """Return the one-line ``description:`` from ``text``'s frontmatter.

    Returns an empty string when the page has no frontmatter or no
    ``description`` key. A quoted value has its quotes removed, so both
    ``description: How to publish`` and ``description: "How to publish"`` yield
    the same string.

    This is a deliberately small parser rather than a YAML load: the wiki reads
    exactly one scalar key, and parsing it by hand keeps :func:`list_pages` from
    paying a YAML parse per page. A page whose frontmatter is malformed still
    lists, with an empty description.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            return ""
        key, sep, value = line.partition(":")
        if sep and key.strip() == "description":
            return value.strip().strip("\"'")
    return ""


async def list_pages(table: WikiTable) -> list[WikiPage]:
    """Return every wiki page with its description, sorted by path."""
    pages = [
        WikiPage(path=path, description=parse_description(body))
        for path, body in (await table.list()).items()
    ]
    pages.sort(key=lambda p: p.path)
    return pages


async def read_page(table: WikiTable, page: str) -> str:
    """Return the raw text of ``page``.

    Accepts the page id with or without the ``.md`` suffix. Raises ``KeyError``
    when the page does not exist and ``ValueError`` when the path is unsafe.
    """
    text = await table.read(normalise_page(page))
    if text is None:
        raise KeyError(page)
    return text


async def write_page(table: WikiTable, page: str, text: str) -> str:
    """Write ``text`` as the whole body of ``page``, and return the page id.

    The write is a whole-body replace, not a merge — the caller supplies the
    complete page.

    Raises ``ValueError`` when the path is unsafe.
    """
    path = normalise_page(page)
    if text and not text.endswith("\n"):
        text += "\n"
    await table.write(path, text)
    return path
