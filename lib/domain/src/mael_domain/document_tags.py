"""The document tag an agent writes in its own message, and the files it names.

::

    <doc-file kind="tasks" filename=".drafts/iter1.md" title="Iteration 1">

Every document is a file, and its path is its identity. ``filename`` may name
several files, comma-separated; they form one review group. A task set is such
a group, so one tag names every draft in the chain.

An agent shows a picture with another tag, which mints no document at all::

    <image src="docs/shot.png" alt="The failing dialog">

Another says what the agent is doing now, and is a field rather than a
document::

    <note>Rebasing onto main, then re-running the failing port test</note>

A last one marks a stage of the work as reached, so a reader can say where the
token spend went::

    <milestone>built</milestone>

See ``docs/dev/orchestrator-server.md``, "A tagged document", for the design.
"""

import posixpath
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
KINDS = ("plan", "tasks", "pr", "review", "verification", "other")
DEFAULT_KIND = "other"

_ATTRIBUTE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
#: What sits between a tag's name and its closing ``>``: quoted values, and
#: anything that is neither a quote nor a ``>``. A tag ends at the ``>`` that
#: closes it, so a value may hold one — a title reading ``A > B``, or a
#: placeholder an agent copied out of a skill.
_ATTRIBUTES = r'((?:"[^"]*"|[^>"])*)'
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
    """One tag: what to call its review group, and the files it names.

    ``filenames`` are in the order the tag listed them — which for a task set
    is the order the chain runs in. ``title`` names the group.
    """

    kind: str
    title: str
    filenames: tuple[str, ...]
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
    tags: list[DocumentTag] = []
    spans: list[tuple[int, int]] = []

    for match in _FILE_TAG.finditer(text):
        attributes = _attributes(match.group(1))
        filenames = _filenames_of(attributes)
        if not filenames:
            # A tag naming no file presents nothing. Left as text, so the
            # malformed tag is visible rather than silently gone.
            continue
        tags.append(
            DocumentTag(
                kind=_kind_of(attributes),
                # The first name, not the whole list: a set is titled by its
                # head when the agent gave it no name of its own.
                title=attributes.get("title", "") or filenames[0],
                filenames=filenames,
                review=_review_of(attributes),
            )
        )
        spans.append(match.span())

    note = ""
    for match in _NOTE_TAG.finditer(text):
        # A `<note>` inside a tag already cut is that tag's text.
        if any(start <= match.start() < end for start, end in spans):
            continue
        # The last one wins: a note replaces rather than accumulates, and
        # collapsing here gives that rule one home rather than one per sink.
        note = match.group(2)
        spans.append(match.span())

    milestone = ""
    for match in _MILESTONE_TAG.finditer(text):
        # A `<milestone>` inside a tag already cut is that tag's text.
        if any(start <= match.start() < end for start, end in spans):
            continue
        # The last one wins, as a note's does: a message that crosses two
        # stages has reached the later one.
        milestone = match.group(2).strip()
        spans.append(match.span())

    replacements: list[tuple[int, int, str]] = []
    for match in _IMAGE_TAG.finditer(text):
        # An `<image>` inside a tag already cut is that tag's text.
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
        replacements.append((match.start(), match.end(), shown or not_shown(src)))

    return TaggedMessage(
        text=_rewritten(text, spans, replacements),
        tags=tuple(tags),
        note=note,
        milestone=milestone,
    )


#: Every marker an agent writes in a message. The last is read by the
#: renderer and not here, but half of it is as wrong on screen as half of any.
_MARKER_NAMES = ("doc-file", "image", "note", "milestone", "user-attention")
#: The markers whose body is not prose: nothing of one shows until it closes.
_BODY_MARKERS = ("note", "milestone")
_PREFIXES = "|".join(
    sorted({re.escape(name[:n]) for name in _MARKER_NAMES for n in range(1, len(name))})
)
_NAMES = "|".join(re.escape(name) for name in _MARKER_NAMES)
#: A marker the text stops in the middle of: its name half spelled, or its
#: attributes not yet closed. A bare ``<`` at the very end counts, because the
#: next chunk may make it one.
_HALF_TAG = re.compile(
    rf'</?(?:{_PREFIXES})?$|</?(?:{_NAMES})\b(?:"[^"]*"|[^>"])*(?:"[^"]*)?$'
)
_BODY_OPENING = re.compile(rf"<({'|'.join(_BODY_MARKERS)})\b{_ATTRIBUTES}>")
_FENCE_OPENING = re.compile(r"^(```|~~~)", re.MULTILINE)
#: Stands where an image tag was while :func:`read_tags` runs, and is then cut.
_NO_IMAGE = "\x00"


def partial_text(text: str) -> str:
    """What a partial message shows: ``text`` so far, with no marker in it.

    A complete marker is cut, as :func:`read_tags` cuts it, and what it asked
    for is discarded. An image is cut too, because showing one registers a file.

    A marker the text stops inside is held back with everything after it: a
    half-written tag, or an unclosed ``<note>`` or ``<milestone>``. A marker in
    a code span or a fence is not held back.
    """
    code = _code_spans(text)

    def in_code(position: int) -> bool:
        return any(start <= position < end for start, end in code)

    cut = len(text)
    for match in _BODY_OPENING.finditer(text):
        if in_code(match.start()):
            continue
        if f"</{match.group(1)}>" not in text[match.end() :]:
            cut = match.start()
            break
    # Each `<` in turn, not one search: the leftmost match can start in code
    # and run over a real half tag after it.
    for opening in re.finditer("<", text[:cut]):
        if not in_code(opening.start()) and _HALF_TAG.match(
            text[:cut], opening.start()
        ):
            cut = opening.start()
            break
    shown = read_tags(text[:cut], lambda _image: _NO_IMAGE).text
    return shown.replace(_NO_IMAGE, "")


def _code_spans(text: str) -> list[tuple[int, int]]:
    """The spans of ``text`` a markdown reader shows as written.

    Each complete fence and code span, and a fence the text has not closed yet,
    which runs to the end.
    """
    spans = [match.span() for match in _CODE.finditer(text)]
    for match in _FENCE_OPENING.finditer(text):
        if not any(start <= match.start() < end for start, end in spans):
            spans.append((match.start(), len(text)))
            break
    return spans


def not_shown(src: str) -> str:
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
    # Normalised, because the path is the document's identity: `./a.md` and
    # `a.md` are one file, and must not become two documents.
    named = (
        posixpath.normpath(name.strip()) for name in raw.split(",") if name.strip()
    )
    # De-duped: one file named twice is one draft, and promoting it twice
    # would make two tasks from one plan.
    return tuple(dict.fromkeys(named))


def _kind_of(attributes: dict[str, str]) -> str:
    kind = attributes.get("kind", "")
    return kind if kind in KINDS else DEFAULT_KIND


def _review_of(attributes: dict[str, str]) -> bool:
    return attributes.get("review", "").lower() == "true"


@dataclass(frozen=True)
class MediaRef:
    """One image or video a document body names, and where in the body it sits.

    ``start`` and ``end`` span the whole ref, so a caller replaces it where it
    was written.
    """

    alt: str
    target: str
    start: int
    end: int


#: A markdown image ref. Video takes the same syntax: the reader picks the
#: element from the target's extension.
_MEDIA_REF = re.compile(r'!\[((?:\\.|[^\]\\])*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
#: Text a markdown reader shows as written: a fenced block, then a code span.
_CODE = re.compile(r"^(```|~~~).*?^\1[^\n]*$|`[^`\n]+`", re.DOTALL | re.MULTILINE)
#: A target that is not a file in the worktree: a URL with a scheme, a
#: protocol-relative URL, an absolute or home path, an anchor, or a token the
#: notebook already expanded.
_NOT_A_WORKTREE_PATH = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:|/|~|#|\{\{")


def media_refs(markdown: str) -> list[MediaRef]:
    """The media refs in ``markdown`` whose target is a worktree-relative path.

    A ref inside a code fence or a code span is text the reader shows, so it is
    not one. A URL and an absolute path are not either: the first is served
    elsewhere, and the second is outside the worktree by construction.

    Two regular expressions, not a markdown parser. See
    ``docs/dev/orchestrator-server.md``, "Media in a document", for what they
    do not know.
    """
    code = [match.span() for match in _CODE.finditer(markdown)]
    refs: list[MediaRef] = []
    for match in _MEDIA_REF.finditer(markdown):
        if any(start <= match.start() < end for start, end in code):
            continue
        target = match.group(2)
        if _NOT_A_WORKTREE_PATH.match(target):
            continue
        refs.append(
            MediaRef(
                alt=re.sub(r"\\(.)", r"\1", match.group(1)),
                target=target,
                start=match.start(),
                end=match.end(),
            )
        )
    return refs


def replace_media(markdown: str, replace: Callable[[MediaRef], str]) -> str:
    """``markdown`` with each media ref replaced by what ``replace`` returns."""
    kept = []
    end = 0
    for ref in media_refs(markdown):
        kept.append(markdown[end : ref.start])
        kept.append(replace(ref))
        end = ref.end
    kept.append(markdown[end:])
    return "".join(kept)


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
