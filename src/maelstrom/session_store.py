"""Read-side helpers over the Claude session registry (``~/.maelstrom/sessions``).

The session channel (``tools/mael-session-channel/index.ts``) writes one JSON
file per live Claude session — ``cwd``, ``pid``, ``state``, ``channel_port``,
the deterministic ``session_id``, and the launching ``mael_task_id``. This
module is the shared reader for that registry: liveness probing and the
task↔session lookups that both ``session list`` and ``task run`` / ``task
reconcile`` need.

It sits in the storage layer (it owns *where* session state lives and how to
read it), so both ``session_cli`` and ``task_cli`` can import it without a
cycle. It depends on :mod:`maelstrom.task` only for the pure
:func:`~maelstrom.task.session_id_for` derivation.
"""

import json
import socket
from pathlib import Path

from . import task as model
from .context import get_maelstrom_dir

SESSIONS_SUBDIR = "sessions"


def sessions_dir() -> Path:
    """The directory holding one JSON file per live session."""
    return get_maelstrom_dir() / SESSIONS_SUBDIR


def read_session_file(path: Path) -> dict | None:
    """Parse one session file, or ``None`` if it is missing/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def liveness_check(port: object) -> bool:
    """Return True if something is listening on 127.0.0.1:port.

    The session channel runs a tiny HTTP listener purely so callers can probe
    whether the session is still alive; a dead port means a crashed/exited
    session whose registry file is stale. ``port`` comes straight from an
    external JSON file, so a missing/garbage value (non-numeric, out of range)
    is treated as "not listening" rather than allowed to raise.
    """
    try:
        port_num = int(port)  # type: ignore[arg-type]  # coerces str/float
    except (TypeError, ValueError):
        return False
    if port_num <= 0 or port_num > 65535:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port_num), timeout=0.1):
            return True
    except OSError:
        return False


def live_sessions() -> list[dict]:
    """Every session file whose ``channel_port`` is still listening.

    Stale files (dead port, unparseable) are skipped but not deleted — GC of
    the registry stays the responsibility of ``mael session list``; readers
    here just filter to the live set.
    """
    sdir = sessions_dir()
    if not sdir.is_dir():
        return []
    live: list[dict] = []
    for f in sorted(sdir.glob("*.json")):
        data = read_session_file(f)
        if data is None:
            continue
        if not liveness_check(data.get("channel_port", 0)):
            continue
        live.append(data)
    return live


def session_matches_task(session: dict, project: str, task_id: str) -> bool:
    """True if ``session`` is the live session launched for ``task_id``.

    Primary key is the deterministic ``session_id`` derived from
    ``(project, task_id)``; the recorded ``mael_task_id`` is a secondary match
    so a session launched before the env var existed (or by an older channel)
    still correlates.
    """
    expected = model.session_id_for(project, task_id)
    if session.get("session_id") == expected:
        return True
    if session.get("session_key") == expected:
        return True
    return session.get("mael_task_id") == task_id


def find_live_session_for_task(project: str, task_id: str) -> dict | None:
    """The live session for ``task_id``, or ``None`` if there isn't one."""
    for session in live_sessions():
        if session_matches_task(session, project, task_id):
            return session
    return None


def live_sessions_by_task_id(
    project: str, task_ids: list[str]
) -> dict[str, dict]:
    """Map each id in ``task_ids`` to its live session, if any.

    Reads the registry once and indexes it by the keys a task can match on —
    ``session_id``, ``session_key`` and recorded ``mael_task_id`` — so each
    task is resolved with O(1) lookups rather than rescanning every session
    per task. Ids with no live session are omitted from the result.
    """
    sessions = live_sessions()
    if not sessions:
        return {}
    by_session_id: dict[str, dict] = {}
    by_task_id: dict[str, dict] = {}
    for s in sessions:
        sid = s.get("session_id")
        if sid:
            by_session_id.setdefault(sid, s)
        skey = s.get("session_key")
        if skey:
            by_session_id.setdefault(skey, s)
        mtid = s.get("mael_task_id")
        if mtid:
            by_task_id.setdefault(mtid, s)
    mapping: dict[str, dict] = {}
    for task_id in task_ids:
        match = by_session_id.get(model.session_id_for(project, task_id))
        if match is None:
            match = by_task_id.get(task_id)
        if match is not None:
            mapping[task_id] = match
    return mapping
