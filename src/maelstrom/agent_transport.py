"""Transport to the agent daemon, mirroring the trio in ``cmux/client.py``.

- :class:`DaemonClient` — a Protocol with a single ``request`` method.
- :class:`SocketDaemonClient` — the real client, one NDJSON round-trip over the
  Unix domain socket.
- :class:`RecordingDaemonClient` — the in-memory fake, so CLI commands are
  testable without a daemon.

:class:`AsyncDaemonClient` is the same pair for a caller that already owns an
event loop: the sync clients wrap ``asyncio.run``, which cannot nest. It also
streams an attach, which the sync Protocol has no shape for.

The socket path comes from ``MAEL_AGENT_SOCKET`` and falls back to
:data:`DEFAULT_SOCKET_PATH`.
"""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .shell import mael_path

#: Where the daemon listens for CLI commands. One socket per machine.
DEFAULT_SOCKET_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.sock")
#: Where an auto-started daemon writes its output.
DEFAULT_LOG_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.log")
#: Where the daemon keeps its spawn records, beside the socket and the log.
DEFAULT_SPEC_DIR = str(Path.home() / ".maelstrom" / "agents")
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

#: How long a request waits for the daemon's reply line before giving up.
REPLY_TIMEOUT = 30.0

#: How long a single NDJSON line may be, in bytes.
#:
#: ``asyncio``'s default is 64 KiB, and one reply is one line. A stopped
#: listing carries every resumable session on the machine — hundreds of rows —
#: which overruns that and kills the read with ``LimitOverrunError``. 16 MiB
#: leaves ample headroom and still bounds a runaway line.
STREAM_LIMIT = 16 * 1024 * 1024


def resolve_socket_path() -> str:
    """The daemon socket path from the environment, or the default."""
    return os.environ.get("MAEL_AGENT_SOCKET") or DEFAULT_SOCKET_PATH


def resolve_log_path() -> str:
    """Where an auto-started daemon's output goes."""
    return os.environ.get("MAEL_AGENT_LOG") or DEFAULT_LOG_PATH


def resolve_spec_dir() -> str:
    """Where the daemon keeps its spawn records.

    Overridden by ``MAEL_AGENT_SPEC_DIR``, so a test daemon on its own socket
    also keeps its own records and cannot resume the real one's agents.
    """
    return os.environ.get("MAEL_AGENT_SPEC_DIR") or DEFAULT_SPEC_DIR


def autostart_enabled() -> bool:
    """Whether a command may start a daemon it finds missing."""
    return os.environ.get(NO_AUTOSTART_ENV, "") != "1"


async def open_connection(
    socket_path: str, *, limit: int = STREAM_LIMIT
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """One connection to the daemon, as one seam.

    The sibling of :data:`client_factory`, for the callers that open a socket
    rather than send a command. See "Tests in an agent sandbox" in
    ``CONTRIBUTING.md`` for what a test hands back here.
    """
    return await asyncio.open_unix_connection(socket_path, limit=limit)


async def _probe(socket_path: str) -> bool:
    """Whether a daemon already answers on ``socket_path``."""
    try:
        _, writer = await asyncio.wait_for(
            open_connection(socket_path), timeout=PROBE_TIMEOUT
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
        reader, writer = await open_connection(socket_path)
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


#: The transport every sync caller goes through, as one seam. Tests override
#: this attribute to drive a command through :class:`RecordingDaemonClient`
#: instead of a real socket. One copy, so a test patching it reaches every
#: caller.
client_factory: Callable[[], DaemonClient] = SocketDaemonClient


def client() -> DaemonClient:
    """The transport for one command."""
    return client_factory()


# --- the async pair, for a caller that already owns an event loop -----------


class AsyncDaemonClient(Protocol):
    """A transport for a caller on its own event loop.

    ``request`` is the same single round-trip as :class:`DaemonClient`.
    ``attach`` is what the sync Protocol has no shape for: a long-lived
    connection yielding the agent's raw events until it ends.
    """

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``payload`` and return the daemon's reply."""
        ...

    def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield one agent's raw events until the stream ends.

        With ``from_seq`` and ``epoch``, the daemon replays only what came
        after that cursor in that life of the agent.
        """
        ...


def attach_command(agent_id: str, from_seq: int = 0, epoch: str = "") -> dict[str, Any]:
    """The ``attach`` request, carrying the cursor only when there is one."""
    command: dict[str, Any] = {"cmd": "attach", "id": agent_id}
    if from_seq > 0:
        command["from"] = from_seq
    if epoch:
        command["epoch"] = epoch
    return command


@dataclass
class SocketAsyncDaemonClient:
    """The real async client: same socket, on the caller's loop.

    Errors reach the caller as data, not exceptions, on the same non-fatal
    contract as :class:`SocketDaemonClient`: ``request`` returns a reply whose
    ``error`` explains it, and ``attach`` yields one such dict and stops.
    """

    socket_path: str = field(default_factory=resolve_socket_path)
    autostart: bool = True

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await request_over_socket(
            self.socket_path, payload, autostart=self.autostart
        )

    async def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """Open an attach connection and yield every line the daemon sends.

        A malformed line is skipped rather than ending the stream: the daemon
        forwards the child's stdout, and a child can write a line that is not
        JSON. The cursor travels only when given, so an older daemon that knows
        no cursor still answers.
        """
        try:
            if self.autostart:
                await ensure_daemon(self.socket_path)
            reader, writer = await open_connection(self.socket_path)
        except (OSError, asyncio.TimeoutError) as exc:
            yield {"error": f"agent daemon not reachable at {self.socket_path}: {exc}"}
            return
        try:
            writer.write(
                (json.dumps(attach_command(agent_id, from_seq, epoch)) + "\n").encode()
            )
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        finally:
            writer.close()
