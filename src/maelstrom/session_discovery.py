"""Answer "is there a **live** Claude session for this task / branch / worktree?"

Claude Code's own uniqueness rule is file-based: it stores each session's
transcript at ``~/.claude/projects/<sanitised-cwd>/<session-id>.jsonl`` and
refuses to start ``claude --session-id <id>`` when that file already exists for
the cwd — regardless of whether the owning process is still alive. ``mael task
run`` derives a deterministic ``session_id`` per task (see
:func:`maelstrom.task.session_id_for`) and passes it straight to
``--session-id``, so a leftover transcript makes a relaunch die at ``claude``
start.

This module mirrors Claude's decision the way Claude makes it, in three steps:

1. **find the transcript file** — glob ``<claude-root>/projects/*/<id>.jsonl``.
   A session id is globally unique, so we match by id and never depend on
   reconstructing the exact cwd-slug algorithm
   (:func:`maelstrom.worktree_model.sanitise_path_for_claude`).
2. **identify the owning pid** — ask the OS who holds the transcript open
   (``lsof``), using the ``~/.maelstrom`` registry as a cheap hint first.
3. **check the pid is live** — :func:`maelstrom.process.is_process_running`.

Only a session that clears all three (``ActiveSession.is_live``) should block a
relaunch. A transcript whose owner has exited is a *finished* task: its
transcript persists but the task is safe to re-run, so callers must not treat
it as live.

It sits above :func:`maelstrom.task.session_id_for` and beside
:mod:`maelstrom.session_store` (reused only as a pid/cwd hint), with no import
cycle: ``session_store`` never imports this module.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import session_store
from . import task as model
from .process import is_process_running
from .worktree import list_worktrees
from .worktree_model import sanitise_path_for_claude


@dataclass
class ActiveSession:
    """A Claude transcript found for a task/worktree and its liveness.

    ``is_live`` is the only field callers should gate a relaunch on: it is true
    exactly when a process still holds the transcript open. ``pid`` is ``None``
    when the transcript exists but nothing live owns it (a finished session) —
    ``is_live`` is then false and the task is safe to re-run. ``cwd`` is the
    real worktree path when the registry supplies it, else ``None`` (we do not
    surface the slugified Claude project dir, which is not a real path).
    """

    session_id: str
    transcript: Path
    pid: int | None
    cwd: Path | None
    is_live: bool


def claude_root() -> Path:
    """The Claude Code config root (``~/.claude`` unless overridden).

    Respects ``CLAUDE_CONFIG_DIR`` (the same env var Claude Code itself reads)
    so tests can point discovery at a ``tmp_path`` without touching the real
    home directory.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def transcript_for_session_id(session_id: str) -> Path | None:
    """The transcript file for ``session_id``, or ``None`` if none exists.

    Globs ``<claude-root>/projects/*/<session_id>.jsonl``. A session id is
    globally unique across projects, so at most one file matches; we return it
    without needing to know which cwd-slug directory it lives under (see the
    module docstring on why we glob rather than reconstruct the slug).
    """
    projects = claude_root() / "projects"
    if not projects.is_dir():
        return None
    matches = sorted(projects.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def _registry_hint(transcript: Path) -> dict | None:
    """The ``~/.maelstrom`` registry entry matching ``transcript``, or ``None``.

    The registry records ``pid`` + real ``cwd`` per live session. When an
    entry's ``cwd`` slugifies to the transcript's project directory it is the
    session that owns this transcript, so its ``pid`` spares an ``lsof`` spawn
    on the common path and its ``cwd`` gives the *real* worktree path (the
    transcript's parent is only the slugified form). A stale/absent entry
    yields ``None`` and the caller falls back to ``lsof``.
    """
    project_dir = transcript.parent.name
    for entry in session_store.live_sessions():
        cwd = entry.get("cwd")
        if not cwd:
            continue
        if sanitise_path_for_claude(Path(cwd)) == project_dir:
            return entry
    return None


def _lsof_pid(path: Path) -> int | None:
    """The pid holding ``path`` open per ``lsof``, or ``None``.

    ``lsof -t`` prints just the pids of processes with the file open. Missing
    ``lsof`` or no holder (non-zero exit / empty output) both mean "nobody owns
    it" → ``None``; we never let the probe raise.
    """
    try:
        result = subprocess.run(
            ["lsof", "-t", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    for line in result.stdout.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def pid_holding(path: Path) -> int | None:
    """Which live process holds ``path`` open, or ``None``.

    Tries the registry hint first (no subprocess) and falls back to ``lsof``.
    The returned pid is not re-checked for liveness here — callers pass it
    through :func:`maelstrom.process.is_process_running`.
    """
    hint = _registry_hint(path)
    if hint is not None and isinstance(hint.get("pid"), int):
        return hint["pid"]
    return _lsof_pid(path)


def _active_session_from_transcript(
    session_id: str, transcript: Path
) -> ActiveSession:
    """Build an :class:`ActiveSession` for an existing transcript file.

    Resolves the registry hint once (reused for both the owning pid and the
    ``cwd``), falls back to ``lsof`` for the pid, and checks it is alive.
    ``cwd`` is the *real* worktree path only when the registry supplies it —
    left ``None`` otherwise, since the transcript's parent is the slugified
    Claude project dir, not a real path (and the caller's error message treats
    ``None`` as "location unknown").
    """
    hint = _registry_hint(transcript)
    if hint is not None and isinstance(hint.get("pid"), int):
        pid: int | None = hint["pid"]
    else:
        pid = _lsof_pid(transcript)
    live = pid is not None and is_process_running(pid)
    hint_cwd = hint.get("cwd") if hint else None
    cwd = Path(hint_cwd) if hint_cwd else None
    return ActiveSession(
        session_id=session_id,
        transcript=transcript,
        pid=pid if live else None,
        cwd=cwd,
        is_live=live,
    )


def active_session_for_task(project: str, task_id: str) -> ActiveSession | None:
    """The Claude session for ``(project, task_id)``, or ``None``.

    Computes the deterministic id ``claude`` will collide on, finds its
    transcript, and reports whether a live process still owns it. ``None`` means
    no transcript exists at all (never launched, or transcript removed). The
    primary path for ``mael task run``'s duplicate-launch guard.
    """
    session_id = model.session_id_for(project, task_id)
    transcript = transcript_for_session_id(session_id)
    if transcript is None:
        return None
    return _active_session_from_transcript(session_id, transcript)


def active_session_for_worktree(worktree_path: Path) -> ActiveSession | None:
    """The live Claude session running in ``worktree_path``, or ``None``.

    Enumerates transcripts under the worktree's Claude project directory (the
    ``sanitise_path_for_claude`` slug is a *hint* for which directory to look
    in) and returns the first live one. A directory with only finished
    transcripts, or none at all, yields ``None`` — we care about *live*
    sessions here, not history.
    """
    project_dir = (
        claude_root() / "projects" / sanitise_path_for_claude(worktree_path)
    )
    if not project_dir.is_dir():
        return None
    for transcript in sorted(project_dir.glob("*.jsonl")):
        session = _active_session_from_transcript(
            transcript.stem, transcript
        )
        if session.is_live:
            return session
    return None


def active_session_for_branch(
    project_path: Path, branch: str
) -> ActiveSession | None:
    """The live Claude session for ``branch`` in ``project_path``, or ``None``.

    Resolves the branch to its worktree via the git worktree list, then
    delegates to :func:`active_session_for_worktree`. ``None`` when the branch
    has no worktree checked out or that worktree has no live session.
    """
    for wt in list_worktrees(project_path):
        if wt.branch == branch:
            return active_session_for_worktree(wt.path)
    return None
