"""The document tag an agent writes in its own message, and the file it names.

Two forms::

    <doc-content kind="other" title="Changelog draft">
    ## 1.4.0
    - the markdown body, inline
    </doc-content>

    <doc-file kind="tasks" filename=".drafts/iter1.md" title="Iteration 1">

``filename`` may name several files, comma-separated. A task set is one
document holding the whole chain, so one tag names every draft in it.

See ``docs/dev/orchestrator-server.md``, "A tagged document", for the design.
"""

import re
from dataclasses import dataclass
from pathlib import Path

#: The document kinds the protocol declares. Anything else reads as ``other``,
#: so a typo shows a document rather than dropping it.
KINDS = ("plan", "tasks", "pr", "review", "other")
DEFAULT_KIND = "other"

_ATTRIBUTE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
#: What sits between a tag's name and its closing ``>``: quoted values, and
#: anything that is neither a quote nor a ``>``. A tag ends at the ``>`` that
#: closes it, so a value may hold one — a title reading ``A > B``, or a
#: placeholder an agent copied out of a skill.
_ATTRIBUTES = r'((?:"[^"]*"|[^>"])*)'
_CONTENT_TAG = re.compile(
    rf"<doc-content\b{_ATTRIBUTES}>\n?(.*?)\n?</doc-content>", re.DOTALL
)
_FILE_TAG = re.compile(rf"<doc-file\b{_ATTRIBUTES}>")


@dataclass(frozen=True)
class DocumentTag:
    """One tag: what to call the document, and where its body comes from.

    ``filenames`` is empty for a ``<doc-content>`` tag, whose body is
    ``markdown``. For a ``<doc-file>`` tag ``markdown`` is empty and the
    filenames name the files to read, in the order the tag listed them — which
    for a task set is the order the chain runs in.
    """

    kind: str
    title: str
    filenames: tuple[str, ...]
    markdown: str
    review: bool


@dataclass(frozen=True)
class TaggedMessage:
    """A message split into the text the transcript shows and the tags it carried."""

    text: str
    tags: tuple[DocumentTag, ...]


def read_tags(text: str) -> TaggedMessage:
    """Split ``text`` into what the user reads and the documents it asks for.

    The tags come out in the order they were written, and the text keeps the
    prose around them.
    """
    tags: list[tuple[int, DocumentTag]] = []
    spans: list[tuple[int, int]] = []

    for match in _CONTENT_TAG.finditer(text):
        attributes = _attributes(match.group(1))
        tags.append(
            (
                match.start(),
                DocumentTag(
                    kind=_kind_of(attributes),
                    title=attributes.get("title", "") or _kind_of(attributes),
                    filenames=(),
                    markdown=match.group(2),
                    review=_review_of(attributes),
                ),
            )
        )
        spans.append(match.span())

    for match in _FILE_TAG.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            # A `<doc-file>` inside a `<doc-content>` body is that body's text.
            continue
        attributes = _attributes(match.group(1))
        filenames = _filenames_of(attributes)
        tags.append(
            (
                match.start(),
                DocumentTag(
                    kind=_kind_of(attributes),
                    # The first name, not the whole list: a set is titled by
                    # its head when the agent gave it no name of its own.
                    title=attributes.get("title", "")
                    or (filenames[0] if filenames else "")
                    or _kind_of(attributes),
                    filenames=filenames,
                    markdown="",
                    review=_review_of(attributes),
                ),
            )
        )
        spans.append(match.span())

    if not tags:
        return TaggedMessage(text=text, tags=())
    tags.sort(key=lambda pair: pair[0])
    return TaggedMessage(text=_without(text, spans), tags=tuple(tag for _, tag in tags))


def _without(text: str, spans: list[tuple[int, int]]) -> str:
    """``text`` with ``spans`` cut out, and the blank lines they left tidied."""
    kept = []
    end = 0
    for start, stop in sorted(spans):
        kept.append(text[end:start])
        end = stop
    kept.append(text[end:])
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


def _attributes(raw: str) -> dict[str, str]:
    return {name: value for name, value in _ATTRIBUTE.findall(raw)}


def _filenames_of(attributes: dict[str, str]) -> tuple[str, ...]:
    """The files a ``<doc-file>`` tag names, comma-separated, in order.

    One name is the ordinary case and reads as a one-file set. Blanks are
    dropped, so a trailing comma names no extra file.
    """
    raw = attributes.get("filename", "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _kind_of(attributes: dict[str, str]) -> str:
    kind = attributes.get("kind", "")
    return kind if kind in KINDS else DEFAULT_KIND


def _review_of(attributes: dict[str, str]) -> bool:
    return attributes.get("review", "").lower() == "true"


def stays_within(cwd: str, filename: str) -> bool:
    """Whether ``filename`` names a file inside ``cwd``.

    A document tag must not read arbitrary files, so the filename resolves
    against the agent's worktree and nothing else. An absolute path and a
    ``..`` that climbs out both fail here, before anything is read.
    """
    if not cwd or not filename:
        return False
    root = Path(cwd)
    try:
        # No `strict`: the file may not exist, and that is the reader's answer,
        # not an escape. `..` still collapses, so a climb out is caught.
        (root / filename).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def read_worktree_file(cwd: str, filename: str) -> str | None:
    """``filename`` read from ``cwd``, or ``None`` when it cannot be read.

    A path that escapes ``cwd`` is refused before anything is read. A
    directory, a missing file and an unreadable one all read as ``None``, and
    the caller mints a document that says so.
    """
    if not stays_within(cwd, filename):
        return None
    try:
        return (Path(cwd) / filename).read_text()
    except OSError:
        return None
