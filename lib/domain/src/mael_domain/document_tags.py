"""The document tag an agent writes in its own message, and the file it names.

Two forms::

    <doc-content kind="other" title="Changelog draft">
    ## 1.4.0
    - the markdown body, inline
    </doc-content>

    <doc-file kind="tasks" filename=".drafts/iter1.md" title="Iteration 1">

``filename`` may name several files, comma-separated. A task set is one
document holding the whole chain, so one tag names every draft in it.

An agent shows a picture with a third tag, which mints no document at all::

    <image src="docs/shot.png" alt="The failing dialog">

A fourth says what the agent is doing now, and is a field rather than a
document::

    <note>Rebasing onto main, then re-running the failing port test</note>

A fifth marks a stage of the work as reached, so a reader can say where the
token spend went::

    <milestone>built</milestone>

See ``docs/dev/orchestrator-server.md``, "A tagged document", for the design.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: Turns one image tag into the markdown that replaces it, or ``None`` when the
#: file may not be shown. The registry is what decides, so the decision is
#: injected rather than made here.
ShowImage = Callable[["ImageTag"], str | None]

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
_IMAGE_TAG = re.compile(rf"<image\b{_ATTRIBUTES}>")
#: What the agent is doing now. No attributes are read; the body is the note.
#: ``agent_model`` holds its own copy of this pattern, because the daemon reads
#: the same tag without depending on the orchestrator. The two are kept in step
#: by ``test_both_readers_agree_on_the_note_tag``.
_NOTE_TAG = re.compile(rf"<note\b{_ATTRIBUTES}>\n?(.*?)\n?</note>", re.DOTALL)
#: Which stage of the work the agent has just reached. No attributes are read;
#: the body is the name. The daemon holds no copy of this pattern: it cuts every
#: marker by shape, so only this module knows what a milestone is called.
_MILESTONE_TAG = re.compile(
    rf"<milestone\b{_ATTRIBUTES}>\n?(.*?)\n?</milestone>", re.DOTALL
)

#: The stages the task-completion flow passes through, in order. A name outside
#: this set is recorded as written and flagged, so a typo is visible rather than
#: silently costing a snapshot.
#:
#: Not the same list as the one ``shared/agent-prompt.md`` teaches: ``planned``
#: is Maelstrom's, written when the user approves a plan. This set is the names
#: the flow declares, where the prompt's is the names an agent may write.
MILESTONES = ("planned", "built", "reviewed", "presented")

#: The row that closes an agent's ledger, holding what it spent after its last
#: stage. Not in :data:`MILESTONES`: it is Maelstrom's own, and the brackets are
#: syntax no marker can carry, so an agent cannot write one.
FINAL_STAGE = "<final>"


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
class ImageTag:
    """One picture an agent asked to show, and where in the text it sits.

    ``start`` and ``end`` are the span the tag occupied. An image is shown
    where it was written, so the caller replaces that span rather than cutting
    it out the way a document tag is cut out.
    """

    src: str
    alt: str
    start: int
    end: int


@dataclass(frozen=True)
class TaggedMessage:
    """A message split into the text the transcript shows and the tags it carried.

    An image leaves no entry: ``show_image`` has already put it in the text,
    which is the only place an image goes.

    ``note`` is what the agent said it is doing, and is empty when the message
    carried none. A note replaces rather than accumulates, so this is the
    latest one the message held. ``milestone`` follows the same rule, and names
    the stage of the work the agent has just reached.
    """

    text: str
    tags: tuple[DocumentTag, ...]
    note: str = ""
    milestone: str = ""


def read_tags(text: str, show_image: ShowImage) -> TaggedMessage:
    """Split ``text`` into what the user reads and the documents it asks for.

    The tags come out in the order they were written, and the text keeps the
    prose around them.

    ``show_image`` turns one :class:`ImageTag` into the markdown that replaces
    it, and returns ``None`` for an image that may not be shown. It runs in the
    same pass that cuts the document tags out, because an offset taken before
    that cut would not survive it. It is required: a default would quietly
    rewrite every image to "could not be shown".
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

    note = ""
    for match in _NOTE_TAG.finditer(text):
        # A `<note>` inside a `<doc-content>` body is that body's text.
        if any(start <= match.start() < end for start, end in spans):
            continue
        # The last one wins: a note replaces rather than accumulates, and
        # collapsing here gives that rule one home rather than one per sink.
        note = match.group(2)
        spans.append(match.span())

    milestone = ""
    for match in _MILESTONE_TAG.finditer(text):
        # A `<milestone>` inside a `<doc-content>` body is that body's text.
        if any(start <= match.start() < end for start, end in spans):
            continue
        # The last one wins, as a note's does: a message that crosses two
        # stages has reached the later one.
        milestone = match.group(2).strip()
        spans.append(match.span())

    replacements: list[tuple[int, int, str]] = []
    for match in _IMAGE_TAG.finditer(text):
        # An `<image>` inside a `<doc-content>` body is that body's text.
        if any(start <= match.start() < end for start, end in spans):
            continue
        attributes = _attributes(match.group(1))
        src = attributes.get("src", "")
        image = ImageTag(
            src=src,
            alt=attributes.get("alt", "") or src,
            start=match.start(),
            end=match.end(),
        )
        shown = show_image(image)
        replacements.append((match.start(), match.end(), shown or _not_shown(src)))

    tags.sort(key=lambda pair: pair[0])
    return TaggedMessage(
        text=_rewritten(text, spans, replacements),
        tags=tuple(tag for _, tag in tags),
        note=note,
        milestone=milestone,
    )


def _not_shown(src: str) -> str:
    """What stands in for an image the user is not going to see.

    A broken picture would leave the agent believing it showed something, so
    the message says which file and that it was refused.
    """
    return f"_`{src}` could not be shown._"


def _rewritten(
    text: str,
    spans: list[tuple[int, int]],
    replacements: list[tuple[int, int, str]],
) -> str:
    """``text`` with document spans cut and image spans replaced.

    One ordered pass over both, because a cut moves every offset after it and
    an image's span is an offset into the original text.
    """
    edits = sorted(
        [(start, stop, "") for start, stop in spans] + replacements,
        key=lambda edit: edit[0],
    )
    kept = []
    end = 0
    for start, stop, insert in edits:
        kept.append(text[end:start])
        kept.append(insert)
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
    named = (name.strip() for name in raw.split(",") if name.strip())
    # De-duped: one file named twice is one draft, and promoting it twice
    # would make two tasks from one plan.
    return tuple(dict.fromkeys(named))


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
