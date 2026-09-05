"""What ``mael session`` shows about one live session.

The model layer between :mod:`maelstrom.session_discovery` (which finds live
sessions) and :mod:`maelstrom.session_cli` (which prints them). A live session
knows its pid, cwd and session-id and nothing else, so a display row has to
combine three sources: the process itself, the ``~/.maelstrom`` registry, and the
task metadata index.

Everything is injected — the registry entries, the index, and the resolver that
maps a cwd to a project and worktree. Nothing here reads the filesystem, the
environment, or a process table, and nothing prints. That keeps the row builder
exercisable with plain dicts, and it is convention 2 in
``docs/dev/architecture-patterns.md``.

The resolver is a parameter rather than a direct call because resolving a cwd to
a project reads config and walks the filesystem. Taking it as an argument keeps
that I/O in the caller and this module pure.
"""

from datetime import datetime, timezone
from typing import Callable, Protocol

from .session_discovery import LiveSession
from .task_index import TaskMeta

# How long a session may sit in ``processing`` before the state is treated as
# stale. Claude Code fires no hook on ESC / user-interrupt, so a session stuck in
# ``processing`` would never resolve on its own. The heartbeat hooks bump
# ``updated_at`` on every tool call, so a session genuinely working keeps
# ticking; 5 minutes clears the slowest single tool call (a long Bash run or a
# Task sub-agent).
STALE_PROCESSING_SECS = 300

#: Resolve a cwd to its ``(project, worktree)``, either of which may be ``None``.
ProjectResolver = Callable[[str], tuple[str | None, str | None]]


class TaskLookup(Protocol):
    """The one question this module asks the task index."""

    def find_by_session_id(self, session_id: str) -> TaskMeta | None: ...


def age_since(started_at: str) -> str:
    """``started_at`` as an age like ``45s`` / ``12m`` / ``3h`` / ``2d``.

    Takes a timestamp, and shows seconds. :func:`~maelstrom.agent_model.age_of`
    takes a duration and shows "now" under a minute — a session listing wants
    the seconds while it starts, and a stopped listing never does.

    Empty for a timestamp that will not parse, and ``0s`` for one in the future —
    a clock skew must not print a negative age.
    """
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    total = int((datetime.now(timezone.utc) - start).total_seconds())
    if total < 0:
        return "0s"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def is_stale_processing(state: str, updated_at: str) -> bool:
    """Whether a ``processing`` state is old enough to be reported as idle."""
    if state != "processing" or not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() > STALE_PROCESSING_SECS


def registry_enrichment(pid: int, cwd: str, registry: list[dict]) -> dict | None:
    """The entry in ``registry`` matching ``(pid, cwd)``, or ``None``.

    Matches a live process to its recorded session, so a listing can show the
    Claude hook ``state`` and ``started_at`` age the process itself cannot report.
    Prefers an exact pid+cwd match and falls back to cwd alone, which covers a
    session recorded before its pid was known.

    The cwd-only fallback can misattribute when two live sessions share one cwd:
    the first process may show the other's state and age. That is acceptable
    because state and age are best-effort display fields, while pid and cwd —
    which come from the process — are always correct.
    """
    by_cwd: dict | None = None
    for entry in registry:
        if entry.get("cwd") != cwd:
            continue
        if entry.get("pid") == pid:
            return entry
        if by_cwd is None:
            by_cwd = entry
    return by_cwd


def build_session_row(
    sess: LiveSession,
    registry: list[dict],
    index: TaskLookup,
    resolve_project: ProjectResolver,
) -> dict:
    """Everything ``mael session`` knows about one live session, as a flat dict.

    Both ``session list`` and ``session info`` render from the result, and
    ``mael --json session info`` emits it as-is.

    ``pid`` and ``cwd`` come from the process itself and are always right.
    ``state``, ``age`` and ``model`` are registry-only, so they are blank when no
    registry entry matches. ``task`` prefers an indexed reverse lookup on the
    session-id and falls back to the registry's ``mael_task_id`` when the index is
    cold or stale. Every key is always present; a field with nothing to report is
    an empty string.
    """
    cwd = str(sess.cwd)
    project, worktree = resolve_project(cwd)

    state = ""
    age = ""
    model = ""
    entry = registry_enrichment(sess.pid, cwd, registry)
    if entry is not None:
        state = entry.get("state", "")
        if is_stale_processing(state, entry.get("updated_at", "")):
            state = "idle"  # display-only; ESC/interrupt leaves it stuck
        age = age_since(entry.get("started_at", ""))
        model = entry.get("model", "") or ""

    task_id = ""
    if sess.session_id:
        meta = index.find_by_session_id(sess.session_id)
        if meta is not None:
            task_id = meta.id
    if not task_id and entry is not None:
        task_id = entry.get("mael_task_id", "") or ""

    return {
        "id": sess.session_id or "",
        "pid": sess.pid,
        "state": state,
        "project": project or "",
        "worktree": worktree or "",
        "task": task_id,
        "cwd": cwd,
        "age": age,
        "model": model,
    }
