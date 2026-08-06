"""Model for the cross-project development-pattern wiki.

See ``docs/guide/concepts.md`` for what the wiki is for and how it differs from
per-project docs and Claude's memory.

Pages are markdown files in the same git-backed store as the task notebook
(``~/.maelstrom/tasks``), under the ``_wiki/`` key prefix. A page id is a
free-form relative path such as ``dev-patterns/python/pypi-publication``; the
taxonomy is a convention, and only path safety is enforced here. Each page
carries a one-line ``description:`` in frontmatter, which is what
:func:`list_pages` prints.

Task keys are ``<project>/<status>/<id>.md``, so a project named ``_wiki`` would
write into this prefix and its tasks would list as pages. The leading underscore
makes that unlikely but not impossible, so the name is reserved:
:data:`maelstrom.context.RESERVED_PROJECT_NAMES` rejects it at project creation
and rename, which is what turns the prefix into a real guarantee.
"""

import re
from dataclasses import dataclass

from .task_store import TaskStore

WIKI_PREFIX = "_wiki/"

# The legal character set for one path segment. Note this deliberately allows the
# dot, so it matches ``.`` and ``..`` too — rejecting those is the explicit check
# in :func:`normalise_page`, not this pattern.
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class WikiPage:
    """One page in the table of contents.

    ``path`` is the page id — the store key with the ``_wiki/`` prefix and the
    ``.md`` suffix removed. ``description`` is the one-line ``description:``
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
    ``.``/``..`` rejection below is what keeps a page inside the wiki prefix.
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


def page_key(page: str) -> str:
    """Return the store key for ``page``. Raises ``ValueError`` on an unsafe path."""
    return f"{WIKI_PREFIX}{normalise_page(page)}.md"


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


def list_pages(store: TaskStore) -> list[WikiPage]:
    """Return every wiki page with its description, sorted by path.

    Non-markdown keys under the prefix are ignored, so a stray file in the wiki
    folder never shows up as a page.
    """
    pages: list[WikiPage] = []
    for key in store.list_dir(WIKI_PREFIX):
        if not key.endswith(".md"):
            continue
        path = key[len(WIKI_PREFIX) : -len(".md")]
        # Only list keys whose derived path rebuilds the same key. The store is a
        # real directory that a human (or another tool) can write into, so a key
        # under the prefix is not necessarily a legal page id — ``_wiki/a b.md``
        # and ``_wiki/.md`` both list otherwise, and neither can be read back.
        try:
            if page_key(path) != key:
                continue
        except ValueError:
            continue
        text = store.read(key)
        if text is None:
            continue
        pages.append(WikiPage(path=path, description=parse_description(text)))
    pages.sort(key=lambda p: p.path)
    return pages


def read_page(store: TaskStore, page: str) -> str:
    """Return the raw text of ``page``.

    Accepts the page id with or without the ``.md`` suffix. Raises ``KeyError``
    when the page does not exist and ``ValueError`` when the path is unsafe.
    """
    key = page_key(page)
    text = store.read(key)
    if text is None:
        raise KeyError(page)
    return text


def write_page(store: TaskStore, page: str, text: str) -> str:
    """Write ``text`` as the whole body of ``page``, and return the page id.

    The write is a whole-body replace, not a merge — the caller supplies the
    complete page. The commit subject records whether the page was created or
    updated, so the store history reads as a changelog.

    Raises ``ValueError`` when the path is unsafe.
    """
    path = normalise_page(page)
    key = page_key(path)
    verb = "update" if store.exists(key) else "create"
    if text and not text.endswith("\n"):
        text += "\n"
    store.write(key, text, message=f"wiki: {verb} {path}")
    return path
