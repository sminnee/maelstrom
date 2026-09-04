"""Storage layer for Claude's own session transcripts.

A session that has stopped leaves no maelstrom record of its own beyond its
spawn record, and a session a person started by hand leaves none at all. What
does survive is Claude's transcript at
``~/.claude/projects/<slug>/<session-id>.jsonl``. That file is also what makes
``claude --resume`` work, so it is the right thing to enumerate when asking
which sessions can be brought back.

Follows the storage-layer shape in ``docs/dev/architecture-patterns.md``: a
Protocol, an in-memory backend for tests, and a real backend whose root is
injected.

Two things keep the read affordable. :func:`read_head` reads only the head of
each file, and a ``cwds`` filter computes each slug forward with
:func:`~maelstrom.worktree_model.sanitise_path_for_claude` rather than trying
to reverse a slug back into a path — the slug replaces both ``/`` and ``.``
with ``-``, so that direction is lossy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .agent_model import DRIVEN_ENTRYPOINT, KIND_CLI, KIND_MAEL, TranscriptMeta
from .worktree_model import sanitise_path_for_claude

#: How far into a transcript the head read goes before it gives up.
#:
#: A ``cwd`` arrives within a line or two, but an ``ai-title`` is written after
#: the first turn, behind the attachments and the opening prompt. Forty lines
#: clears that on every transcript measured, and reading all ~800 of them
#: head-only takes about a third of a second.
HEAD_LINES = 40


def get_transcript_root() -> Path:
    """Where Claude Code keeps its per-project session transcripts."""
    return Path.home() / ".claude" / "projects"


class TranscriptStore(Protocol):
    """The Claude session transcripts on this machine."""

    def list(self, cwds: list[Path] | None) -> list[TranscriptMeta]:
        """One :class:`TranscriptMeta` per transcript.

        ``cwds`` restricts the read to the sessions of those working
        directories; ``None`` reads every one.
        """
        ...


class InMemoryTranscriptStore:
    """A ``dict``-backed :class:`TranscriptStore` with no filesystem."""

    def __init__(self) -> None:
        self._data: dict[str, TranscriptMeta] = {}

    def add(self, session_id: str, entries: list[dict[str, Any]], **kw: Any) -> None:
        """Store the meta ``entries`` describe, as the real backend would read it."""
        self.add_meta(build_meta(session_id, entries, **kw))

    def add_meta(self, meta: TranscriptMeta) -> None:
        """Store ``meta`` as it stands, for a caller that already has one."""
        self._data[meta.session_id] = meta

    def list(self, cwds: list[Path] | None) -> list[TranscriptMeta]:
        wanted = None if cwds is None else {path.resolve() for path in cwds}
        metas = [
            meta
            for meta in self._data.values()
            if wanted is None or meta.cwd.resolve() in wanted
        ]
        return sorted(metas, key=lambda meta: meta.session_id)


class ClaudeTranscriptStore:
    """A :class:`TranscriptStore` over ``<root>/<slug>/<session-id>.jsonl``.

    The root defaults to :func:`get_transcript_root`, and a test injects its own
    rather than patching ``Path.home``.

    A file that will not parse is skipped rather than raised, exactly as
    ``JsonAgentSpecStore._load`` skips a truncated record: one bad transcript
    must not hide every other resumable session.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else get_transcript_root()

    def list(self, cwds: list[Path] | None) -> list[TranscriptMeta]:
        metas = [self._read(path) for path in self._paths(cwds)]
        found = [meta for meta in metas if meta is not None]
        return sorted(found, key=lambda meta: meta.session_id)

    def _paths(self, cwds: list[Path] | None) -> list[Path]:
        """Every transcript file to read, one directory per cwd when filtered."""
        if cwds is None:
            return sorted(self.root.glob("*/*.jsonl"))
        paths: list[Path] = []
        for cwd in cwds:
            paths += sorted((self.root / sanitise_path_for_claude(cwd)).glob("*.jsonl"))
        return paths

    @staticmethod
    def _read(path: Path) -> TranscriptMeta | None:
        entries, lines_read = read_head(path)
        if entries is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        meta = build_meta(
            path.stem,
            entries,
            modified_at=stat.st_mtime,
            size=stat.st_size,
            lines_read=lines_read,
        )
        # No cwd means nothing knows where to resume the session, so the file is
        # of no use to a listing whose whole point is bringing one back.
        return meta if str(meta.cwd) != "." else None


def read_head(path: Path) -> tuple[list[dict[str, Any]] | None, int]:
    """The first entries of ``path``, and how many lines were read.

    Stops at :data:`HEAD_LINES`, or sooner once the file has yielded everything
    :func:`build_meta` looks for. Returns ``(None, 0)`` for a file that cannot
    be opened at all; an individual line that will not parse is skipped, since
    the fields wanted may well have arrived already.
    """
    entries: list[dict[str, Any]] = []
    read = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                read += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
                if read >= HEAD_LINES or _has_every_field(entries):
                    break
    except OSError:
        return None, 0
    return entries, read


def _has_every_field(entries: list[dict[str, Any]]) -> bool:
    """True once nothing later in the file could change the meta."""
    return bool(_first_cwd(entries)) and bool(_ai_title(entries))


def build_meta(
    session_id: str,
    entries: list[dict[str, Any]],
    *,
    modified_at: float = 0.0,
    size: int = 0,
    lines_read: int = 0,
) -> TranscriptMeta:
    """The meta ``entries`` describe. Pure, so it is testable on plain dicts."""
    placed = _first_placed(entries)
    entrypoint = placed.get("entrypoint", "")
    return TranscriptMeta(
        session_id=session_id,
        cwd=Path(_first_cwd(entries)),
        branch=placed.get("gitBranch", "") or "",
        kind=KIND_MAEL if entrypoint == DRIVEN_ENTRYPOINT else KIND_CLI,
        label=_ai_title(entries) or _first_prompt(entries),
        modified_at=modified_at,
        size=size,
        lines_read=lines_read,
    )


def _first_placed(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The first entry carrying a ``cwd``, which is where the rest of it is.

    Claude writes ``cwd``, ``gitBranch`` and ``entrypoint`` together on the same
    entries, and the bookkeeping lines that open a transcript carry none of
    them.
    """
    return next((entry for entry in entries if entry.get("cwd")), {})


def _first_cwd(entries: list[dict[str, Any]]) -> str:
    return _first_placed(entries).get("cwd", "") or ""


def _ai_title(entries: list[dict[str, Any]]) -> str:
    """The title Claude wrote for the session, or ``""``.

    Only an interactive session gets one; a daemon-driven agent never does.
    """
    for entry in entries:
        if entry.get("type") == "ai-title" and entry.get("aiTitle"):
            return str(entry["aiTitle"])
    return ""


def _first_prompt(entries: list[dict[str, Any]]) -> str:
    """The first thing a person actually asked, as one line.

    Skips a ``isMeta`` entry: a transcript commonly opens with a local-command
    caveat or the echo of a slash command, and neither says what the session
    was for.
    """
    for entry in entries:
        if entry.get("type") != "user" or entry.get("isMeta"):
            continue
        text = _entry_text(entry)
        if text:
            return " ".join(text.split())
    return ""


def _entry_text(entry: dict[str, Any]) -> str:
    """The text of one ``user`` entry, whichever shape its content takes.

    A typed prompt is a plain string; a replayed one is a list of blocks.
    """
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
    return ""


def write_transcript(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write ``entries`` as a transcript at ``path``. For tests and fixtures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
