"""Where Claude Code keeps its per-project data on disk.

A leaf: pure path arithmetic plus one ``exists`` check. The daemon, the
transcript store and the worktree commands all derive Claude's directory
names here, so they agree on the slug.
"""

from pathlib import Path


def get_transcript_root(home: Path | None = None) -> Path:
    """Where Claude Code keeps its per-project session transcripts.

    ``home`` defaults to ``Path.home()``; a test passes a fake one.
    """
    return (home if home is not None else Path.home()) / ".claude" / "projects"


def sanitise_path_for_claude(path: Path) -> str:
    """Convert a filesystem path to Claude Code's sanitised project directory name.

    Claude Code stores per-project data in ~/.claude/projects/<sanitised>/
    where the sanitised name is the absolute path with both '/' and '.'
    replaced by '-'. The '.' substitution matters for real temp paths like
    ``/private/tmp/claude.501`` (→ ``-private-tmp-claude-501``).

    Args:
        path: Absolute path to sanitise.

    Returns:
        Sanitised path string (e.g., '-Users-sminnee-Projects-foo').
    """
    return str(path.resolve()).replace("/", "-").replace(".", "-")


def claude_transcript_path(
    worktree_path: Path, session_id: str, *, home: Path | None = None
) -> Path:
    """Where Claude Code writes ``session_id``'s transcript for ``worktree_path``.

    ``~/.claude/projects/<sanitised-worktree-path>/<session-id>.jsonl``, where the
    directory slug comes from :func:`sanitise_path_for_claude`. The ``home`` kwarg
    (defaulting to ``Path.home()``) exists so the derivation is unit-testable
    against a fake home. Pure — it builds a path, it does not touch disk.
    """
    return (
        get_transcript_root(home)
        / sanitise_path_for_claude(worktree_path)
        / f"{session_id}.jsonl"
    )


def has_claude_transcript(
    worktree_path: Path, session_id: str, *, home: Path | None = None
) -> bool:
    """True iff ``session_id`` has an on-disk transcript for ``worktree_path``.

    The single source of truth for "has this task ever been run in this worktree?"
    A stopped session's transcript file persists; a never-run task has none. This
    is strictly "not necessarily live, but a transcript exists": liveness is
    read from the process table.
    """
    return claude_transcript_path(worktree_path, session_id, home=home).exists()
