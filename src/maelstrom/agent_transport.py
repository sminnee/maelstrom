"""Transport to the agent daemon, mirroring the trio in ``cmux/client.py``.

- :class:`DaemonClient` — a Protocol with a single ``request`` method.
- :class:`SocketDaemonClient` — the real client, one NDJSON round-trip over the
  Unix domain socket.
- :class:`RecordingDaemonClient` — the in-memory fake, so CLI commands are
  testable without a daemon.

The socket path comes from ``MAEL_AGENT_SOCKET`` and falls back to
:data:`DEFAULT_SOCKET_PATH`.
"""

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .shell import mael_path

#: Where the daemon listens for CLI commands. One socket per machine.
DEFAULT_SOCKET_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.sock")
#: Where an auto-started daemon writes its output.
DEFAULT_LOG_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.log")
#: Set to ``1`` to keep a command from starting a daemon of its own.
NO_AUTOSTART_ENV = "MAEL_AGENT_NO_AUTOSTART"

#: How long one probe of the socket may take. Much shorter than
#: :data:`READY_TIMEOUT`, so a single hung connect cannot eat the whole budget.
PROBE_TIMEOUT = 0.5
#: How long to wait for a freshly spawned daemon to bind.
READY_TIMEOUT = 5.0
#: How often to re-probe while waiting. The daemon binds in tens of ms.
POLL_INTERVAL = 0.025
#: How much of the log to quote when a spawned daemon dies.
LOG_TAIL_BYTES = 8192


def resolve_socket_path() -> str:
    """The daemon socket path from the environment, or the default."""
    return os.environ.get("MAEL_AGENT_SOCKET") or DEFAULT_SOCKET_PATH


def resolve_log_path() -> str:
    """Where an auto-started daemon's output goes."""
    return os.environ.get("MAEL_AGENT_LOG") or DEFAULT_LOG_PATH


def autostart_enabled() -> bool:
    """Whether a command may start a daemon it finds missing."""
    return os.environ.get(NO_AUTOSTART_ENV, "") != "1"


async def _probe(socket_path: str) -> bool:
    """Whether a daemon already answers on ``socket_path``."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(socket_path), timeout=PROBE_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


def spawn_daemon(socket_path: str) -> tuple[subprocess.Popen, int]:
    """Start a detached daemon on ``socket_path``.

    Returns the child and the log size before it started, so a later failure is
    read back from that offset only — otherwise an older daemon's crash is
    reported as this one's.

    Output goes to a real file, never a ``PIPE``: this process exits, the read
    end closes, and the detached daemon dies of ``SIGPIPE`` on its next write.
    ``start_new_session`` keeps Ctrl-C on the starting command from killing the
    daemon, and the inherited :data:`NO_AUTOSTART_ENV` keeps a daemon from
    spawning a daemon.
    """
    log = Path(resolve_log_path())
    log.parent.mkdir(parents=True, exist_ok=True)
    offset = log.stat().st_size if log.exists() else 0
    handle = log.open("ab")
    try:
        child = subprocess.Popen(
            [mael_path(), "agent", "daemon", "--socket", socket_path],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, NO_AUTOSTART_ENV: "1"},
        )
    finally:
        handle.close()
    return child, offset


def _log_tail(offset: int) -> str:
    """What the daemon wrote since ``offset``, for a failure message."""
    log = Path(resolve_log_path())
    if not log.exists():
        return ""
    with log.open("rb") as handle:
        handle.seek(offset)
        return handle.read(LOG_TAIL_BYTES).decode(errors="replace").strip()


async def ensure_daemon(socket_path: str) -> None:
    """Make sure a daemon answers on ``socket_path``, starting one if not.

    A no-op when one already runs, or when auto-start is disabled — a caller
    that finds no daemon then gets its own connection error, which says the same
    thing.

    The readiness wait races three outcomes: the socket answers, the child dies,
    or the deadline passes. So a daemon that fails in 40 ms is reported in 40 ms
    rather than after the full timeout.

    Raises:
        OSError: If the daemon could not be started, quoting what it wrote.
    """
    if not autostart_enabled() or await _probe(socket_path):
        return

    child, offset = spawn_daemon(socket_path)
    deadline = asyncio.get_running_loop().time() + READY_TIMEOUT
    while True:
        # Probe before checking the child: two commands can race, the loser
        # exits 1 having lost the socket, and the winner is already listening.
        # Reversing the order turns a won race into a spurious failure.
        if await _probe(socket_path):
            return
        if child.poll() is not None:
            raise OSError(
                f"the agent daemon exited at once ({child.returncode}){_reason(offset)}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            child.kill()
            raise OSError(
                f"the agent daemon did not start within {READY_TIMEOUT:g}s"
                f"{_reason(offset)}"
            )
        await asyncio.sleep(POLL_INTERVAL)


def _reason(offset: int) -> str:
    """The daemon's own words, when it left any."""
    tail = _log_tail(offset)
    return f": {tail}" if tail else ""


#: How long a request waits for the daemon's reply line before giving up.
REPLY_TIMEOUT = 30.0


async def request_over_socket(
    socket_path: str, payload: dict[str, Any], *, autostart: bool = True
) -> dict[str, Any]:
    """One NDJSON round-trip over the daemon's Unix domain socket.

    The body both socket clients share. A connection failure, a reply that
    never comes within :data:`REPLY_TIMEOUT`, a closed connection and a
    malformed line all come back as a reply whose ``error`` explains them,
    never an exception — the same non-fatal contract as ``CmuxResult``.

    ``autostart`` starts a daemon first when none answers on ``socket_path``.
    """
    try:
        if autostart:
            await ensure_daemon(socket_path)
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except (OSError, asyncio.TimeoutError) as exc:
        return {"error": f"agent daemon not reachable at {socket_path}: {exc}"}
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        try:
            line = await asyncio.wait_for(reader.readline(), REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            return {"error": f"agent daemon did not reply within {REPLY_TIMEOUT:g}s"}
    finally:
        writer.close()
    if not line:
        return {"error": "agent daemon closed the connection without replying"}
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        return {"error": f"agent daemon sent a malformed reply: {exc}"}


class DaemonClient(Protocol):
    """A transport that sends one command to the daemon and returns its reply."""

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``payload`` and return the daemon's reply."""
        ...


@dataclass
class RecordingDaemonClient:
    """In-memory fake: records every command and returns scripted replies.

    The agent analogue of ``RecordingCmuxClient``. Replies are consumed in
    order; once they run out it returns ``{"ok": True}``, so a test only has to
    script the calls it actually asserts on.
    """

    replies: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.replies:
            return self.replies.pop(0)
        return {"ok": True}


@dataclass
class SocketDaemonClient:
    """The real client: one NDJSON round-trip over the Unix domain socket.

    A connection failure surfaces as a reply whose ``error`` explains it, never
    an exception — same non-fatal contract as ``CmuxResult``, so the CLI can
    print a useful line instead of a traceback when the daemon is down.
    """

    socket_path: str = field(default_factory=resolve_socket_path)
    #: Set false to keep this client from starting a daemon it finds missing.
    #: ``MAEL_AGENT_NO_AUTOSTART`` does the same from outside.
    autostart: bool = True

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(
            request_over_socket(self.socket_path, payload, autostart=self.autostart)
        )
