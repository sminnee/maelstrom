"""Telling a running orchestrator that the world moved.

A command that changes something the canvas draws posts here, so the change
reaches the UI at once rather than at the next poll. The orchestrator is one
service per worktree, reached on the ``ORCHESTRATOR_PORT`` its ``.env`` names —
the same per-environment discovery ``MAEL_AGENT_ROOT`` gives the agent daemon.

Every call is best-effort. No orchestrator running is the ordinary case for a
plain CLI user, so a command whose own work has already succeeded must never
fail, or print, because nothing was listening.
"""

import http.client
import logging
import urllib.request
from pathlib import Path

from .worktree import read_env_file

log = logging.getLogger(__name__)

#: The ``.env`` key naming the port this worktree's orchestrator serves on.
PORT_VAR = "ORCHESTRATOR_PORT"

#: How long to wait for the server to answer. Short: the caller has finished
#: its work and is holding the user's terminal open only for this.
TIMEOUT_SECS = 2.0


def orchestrator_url(worktree_path: Path, path: str) -> str | None:
    """The URL for ``path`` on this worktree's orchestrator, if it has one.

    ``None`` when the worktree's ``.env`` names no port — a worktree made
    before the service existed, or a directory that is not a worktree at all.
    """
    port = read_env_file(worktree_path).get(PORT_VAR, "").strip()
    if not port.isdigit():
        return None
    return f"http://127.0.0.1:{port}{path}"


def tell_orchestrator(worktree_path: Path, path: str) -> None:
    """POST to ``path`` on this worktree's orchestrator, and ignore the answer.

    Raises nothing, so a caller needs no guard of its own: every command that
    reaches here has already done its real work, and a server that is not
    listening must not turn that into a failure. ``http.client`` is named
    beside ``OSError`` because a dropped connection raises from there and is
    not an ``OSError``. Reading the worktree's ``.env`` is inside the guard for
    the same reason: an unreadable file is not worth a failed push.

    The failure is logged at debug, because "no orchestrator here" is the
    ordinary case for a plain CLI user and is not worth a line on their
    terminal.
    """
    url = None
    try:
        url = orchestrator_url(worktree_path, path)
        if url is None:
            return
        request = urllib.request.Request(
            url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECS) as response:
            response.read()
    except (OSError, http.client.HTTPException, ValueError) as exc:
        log.debug("no orchestrator answered at %s: %s", url, exc)
