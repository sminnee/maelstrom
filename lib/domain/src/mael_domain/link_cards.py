"""The ``mael:`` URI a **Link card** names, and the target the transcript holds.

An agent writes the marker with one URI::

    <link uri="mael:task/maelstrom/j8zh">
    <link uri="mael:doc/maelstrom/j8zh/.drafts/pr.md?v=2">The PR draft</link>

The normaliser replaces it with a markdown link, and the UI's ``Markdown``
renders each ``mael:`` link as a card. A ``doc`` or ``plan`` URI becomes the
document id, which only the server can compute. A URI that is not a known form
becomes ``mael:missing``, so the card says the target cannot be found rather
than the raw tag staying as text. Every other form passes through, re-encoded.

See ``docs/dev/orchestrator-server.md``, "A link card".
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, unquote

from .attachments import markdown_link
from .document_tags import CardLink
from .task_attachments import task_key
from .task_documents import PLAN_NAME

SCHEME = "mael:"

#: The forms whose path is a fixed count of segments, and that count.
_ARITY = {
    "task": 2,
    "session": 1,
    "plan": 2,
    "comm": 1,
    "worktree": 2,
    "pr": 2,
}
#: A document's name is the rest of the path, so it may hold a ``/``.
_DOC_MIN_SEGMENTS = 3
_VERSION = re.compile(r"v=(\d+)")
#: The characters a segment keeps as written: an agent id holds a ``:``.
_SAFE = ":@-._~"

#: A card's markdown link, as :func:`card_markdown` writes it.
_CARD_MARKDOWN = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(mael:[^)\s]*\)")
_ESCAPE = re.compile(r"\\(.)")

#: The world id of the task document ``name`` of the task ``key``.
DocumentId = Callable[[str, str], str]


@dataclass(frozen=True)
class LinkTarget:
    """One parsed ``mael:`` URI.

    ``kind`` is one of the forms, or ``missing`` for a URI that is not one.
    ``parts`` are the decoded path segments. A document's name is one part,
    whatever ``/`` it holds. ``version`` is a document's ``?v=``.
    """

    kind: str
    parts: tuple[str, ...]
    uri: str
    version: int | None = None


def parse_link_uri(uri: str) -> LinkTarget:
    """``uri`` as a :class:`LinkTarget`, or a ``missing`` one when it is not a form."""
    missing = LinkTarget("missing", (), uri)
    if not uri.startswith(SCHEME) or any(char.isspace() for char in uri):
        return missing
    path, has_query, query = uri[len(SCHEME) :].partition("?")
    kind, _, tail = path.partition("/")
    parts = tuple(unquote(part) for part in tail.split("/")) if tail else ()
    if not parts or not all(parts):
        return missing
    version: int | None = None
    if has_query:
        found = _VERSION.fullmatch(query)
        if kind != "doc" or found is None:
            return missing
        version = int(found.group(1))
    if kind == "doc":
        if len(parts) < _DOC_MIN_SEGMENTS:
            return missing
        parts = (parts[0], parts[1], "/".join(parts[2:]))
    elif _ARITY.get(kind) != len(parts):
        return missing
    if kind == "pr" and not parts[1].isdigit():
        return missing
    return LinkTarget(kind, parts, uri, version)


def card_href(link: CardLink, document_id: DocumentId) -> str:
    """The URI the transcript holds for ``link``.

    A ``doc`` or ``plan`` URI becomes ``mael:doc/<docId>[?v=<n>]``, with the id
    ``document_id`` gives. Any other form is the URI, re-encoded. A URI that is
    not a form becomes ``mael:missing?uri=<encoded>``.
    """
    target = parse_link_uri(link.uri)
    if target.kind == "missing":
        return f"{SCHEME}missing?uri={quote(link.uri, safe='')}"
    if target.kind in ("doc", "plan"):
        project, task_id = target.parts[0], target.parts[1]
        name = target.parts[2] if target.kind == "doc" else PLAN_NAME
        href = f"{SCHEME}doc/{quote(document_id(task_key(project, task_id), name), safe=_SAFE)}"
        return href if target.version is None else f"{href}?v={target.version}"
    path = "/".join(quote(part, safe=_SAFE) for part in target.parts)
    return f"{SCHEME}{target.kind}/{path}"


def card_markdown(link: CardLink, document_id: DocumentId) -> str:
    """The markdown link that replaces ``link``: its label, to :func:`card_href`."""
    return markdown_link(link.label, card_href(link, document_id))


def card_labels(markdown: str) -> str:
    """``markdown`` with each card's link replaced by its label.

    A one-line summary, such as an agent's ``lastMessage``, is shown as text,
    where a card's link would read as a hash id.
    """
    return _CARD_MARKDOWN.sub(
        lambda match: _ESCAPE.sub(r"\1", match.group(1)), markdown
    )
