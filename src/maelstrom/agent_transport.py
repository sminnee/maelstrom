"""Transport to the agent daemon, mirroring the trio in ``cmux/client.py``.

- :class:`DaemonClient` — a Protocol with a single ``request`` method.
- :class:`SocketDaemonClient` — the real client, one NDJSON round-trip over the
  Unix domain socket.
- :class:`RecordingDaemonClient` — the in-memory fake, so CLI commands are
  testable without a daemon.

:class:`AsyncDaemonClient` is the same pair for a caller that already owns an
event loop: the sync clients wrap ``asyncio.run``, which cannot nest. It also
streams an attach, which the sync Protocol has no shape for.

Every path a daemon uses hangs off one directory, its *daemon root*: see
:class:`DaemonPaths`. The root comes from ``MAEL_AGENT_ROOT`` and falls back to
``~/.maelstrom``, so the default paths are where they have always been.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .context import get_maelstrom_dir
from .shell import mael_path

#: Names the daemon root. The one variable that moves every daemon path at once.
ROOT_ENV = "MAEL_AGENT_ROOT"
#: Set to ``1`` to keep a command from starting a daemon of its own.
NO_AUTOSTART_ENV = "MAEL_AGENT_NO_AUTOSTART"


@dataclass(frozen=True)
class DaemonPaths:
    """Everything one daemon owns, under one directory.

    Socket, lock, pid file, log and spawn records used to be three independent
    variables, so a daemon could be pointed at one directory's records over
    another directory's socket, and two daemons could share the records that
    make them spawn. One root, and one daemon per root (the lock enforces it),
    is what makes "a session belongs to one daemon" true.
    """

    root: Path

    @property
    def socket(self) -> Path:
        """Where the daemon listens for commands."""
        return self.root / "agent-daemon.sock"

    @property
    def lock(self) -> Path:
        """The exclusive lock held for the daemon's life."""
        return self.root / "agent-daemon.lock"

    @property
    def pid_file(self) -> Path:
        """The serving daemon's pid, written after it takes the lock."""
        return self.root / "agent-daemon.pid"

    @property
    def log(self) -> Path:
        """Where a detached daemon writes its output."""
        return self.root / "agent-daemon.log"

    @property
    def spec_dir(self) -> Path:
        """One spawn record per agent this daemon holds."""
        return self.root / "agents"

    @classmethod
    def for_socket(cls, socket_path: str) -> "DaemonPaths":
        """The root a socket path sits in.

        The socket is always ``<root>/agent-daemon.sock``, so a client that
        was handed only a socket path can still start the daemon that belongs
        on it.
        """
        return cls(Path(socket_path).parent)


def resolve_root() -> Path:
    """The daemon root: :data:`ROOT_ENV`, else ``~/.maelstrom``."""
    override = os.environ.get(ROOT_ENV)
    return Path(override) if override else get_maelstrom_dir()


def daemon_paths(root: str | Path | None = None) -> DaemonPaths:
    """The paths under ``root``, or under the resolved default."""
    return DaemonPaths(Path(root) if root is not None else resolve_root())


def all_roots(base: Path | None = None) -> list[DaemonPaths]:
    """Every daemon root on this machine: the default, plus each per-environment one.

    A per-environment daemon lives under ``<base>/daemons/<project>-<worktree>``,
    which is where maelstrom's own ``.maelstrom.yaml`` puts it. A root that
    someone set by hand elsewhere is not found; ``--root`` names that one.
    """
    base = base if base is not None else get_maelstrom_dir()
    roots = [DaemonPaths(base)]
    daemons = base / "daemons"
    if daemons.is_dir():
        roots += [
            DaemonPaths(path) for path in sorted(daemons.iterdir()) if path.is_dir()
        ]
    return roots


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
    """The default daemon's socket, as a string.

    The one-liner the socket clients default to, so a caller that only ever
    wanted the socket need not learn about roots.
    """
    return str(daemon_paths().socket)


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


def spawn_daemon(paths: DaemonPaths) -> tuple[subprocess.Popen, int]:
    """Start a detached daemon on ``paths``' root.

    Returns the child and the log size before it started, so a later failure is
    read back from that offset only — otherwise an older daemon's crash is
    reported as this one's.

    Output goes to a real file, never a ``PIPE``: this process exits, the read
    end closes, and the detached daemon dies of ``SIGPIPE`` on its next write.
    ``start_new_session`` keeps Ctrl-C on the starting command from killing the
    daemon, and the inherited :data:`NO_AUTOSTART_ENV` keeps a daemon from
    spawning a daemon.
    """
    log = paths.log
    log.parent.mkdir(parents=True, exist_ok=True)
    offset = log.stat().st_size if log.exists() else 0
    handle = log.open("ab")
    try:
        child = subprocess.Popen(
            [mael_path(), "agent", "daemon", "serve", "--root", str(paths.root)],
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, NO_AUTOSTART_ENV: "1"},
        )
    finally:
        handle.close()
    return child, offset


def _log_tail(paths: DaemonPaths, offset: int) -> str:
    """What the daemon wrote since ``offset``, for a failure message."""
    log = paths.log
    if not log.exists():
        return ""
    with log.open("rb") as handle:
        handle.seek(offset)
        return handle.read(LOG_TAIL_BYTES).decode(errors="replace").strip()


#: How long to wait for a stopping daemon to let go, in seconds.
GONE_TIMEOUT = 10.0


def wait_for_daemon_gone(paths: DaemonPaths, timeout: float = GONE_TIMEOUT) -> None:
    """Wait until a stopping daemon has let go of its root.

    Waiting on the socket file alone is not enough. Shutdown unlinks the socket
    *before* it releases the lock, so a restart that watched only the file would
    spawn while the old daemon still held the lock — and the new daemon would
    lose the bind and report "a daemon is already serving".

    Taking the lock is the test: it succeeds only once the old daemon has gone.
    The lock is released again straight away, so the daemon spawned next can
    take it.

    Returns when the daemon has gone, or when ``timeout`` passes — a caller that
    spawns anyway gets the clearer "already serving" error from the child.
    """
    from .agent_server import _release_lock, _take_lock

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fd = _take_lock(paths.lock)
        if fd is not None:
            _release_lock(fd)
            return
        time.sleep(0.05)


def local_source_tree() -> str:
    """The tree this code was imported from.

    ``src/maelstrom/agent_transport.py`` sits two directories below the tree
    root, the same derivation ``build_daemon_identity`` uses for the daemon's
    own side of the comparison.
    """
    return str(Path(__file__).parents[2])


async def warn_on_skew(socket_path: str) -> None:
    """Say so when the daemon answering runs different code than this caller.

    A daemon lives for days holding the modules it imported at start, and a
    command from a newer tree is served by it silently. That has produced a
    bug that looked like the feature under development.

    A warning, never a refusal: the daemon works, and stopping it is the
    caller's decision.
    """
    reply = await request_over_socket(socket_path, {"cmd": "ping"}, autostart=False)
    identity = reply.get("daemon")
    if not isinstance(identity, dict):
        # A daemon that cannot answer `ping` predates the command, so it is
        # older than this code by construction. Staying quiet here would mute
        # the warning in precisely the case it exists for.
        #
        # Match on there being an error at all, not on its words: a pre-`ping`
        # daemon falls through to the agent lookup and answers "no such agent",
        # never "unknown command". Only an unreachable daemon is exempt — the
        # caller's own request reports that better than a warning would.
        error = str(reply.get("error", ""))
        if error and "not reachable" not in error:
            _warn(
                f"the daemon on {socket_path} is older than this code: it does "
                "not answer `ping`."
            )
        return
    theirs = str(identity.get("source_tree", ""))
    ours = local_source_tree()
    if not theirs or theirs == ours:
        return
    _warn(f"the daemon on {socket_path} runs code from {theirs}, not {ours}.")


def _warn(what: str) -> None:
    """One warning line, plus the command that fixes it."""
    print(
        f"Warning: {what}\n"
        "         Run `mael agent daemon restart` to serve from this tree.",
        file=sys.stderr,
    )


async def ensure_daemon(paths: DaemonPaths) -> None:
    """Make sure a daemon answers on ``paths``' socket, starting one if not.

    A no-op when one already runs, or when auto-start is disabled — a caller
    that finds no daemon then gets its own connection error, which says the same
    thing.

    The readiness wait races three outcomes: the socket answers, the child dies,
    or the deadline passes. So a daemon that fails in 40 ms is reported in 40 ms
    rather than after the full timeout.

    Raises:
        OSError: If the daemon could not be started, quoting what it wrote.
    """
    if not autostart_enabled():
        return
    socket_path = str(paths.socket)
    if await _probe(socket_path):
        # It answers, so nothing needs starting — but it may be another tree's.
        await warn_on_skew(socket_path)
        return

    child, offset = spawn_daemon(paths)
    deadline = asyncio.get_running_loop().time() + READY_TIMEOUT
    while True:
        # Probe before checking the child: two commands can race, the loser
        # exits 1 having lost the socket, and the winner is already listening.
        # Reversing the order turns a won race into a spurious failure.
        if await _probe(socket_path):
            return
        if child.poll() is not None:
            raise OSError(
                f"the agent daemon exited at once ({child.returncode})"
                f"{_reason(paths, offset)}"
            )
        if asyncio.get_running_loop().time() >= deadline:
            # SIGTERM, not SIGKILL: a daemon this slow has already restored
            # its agents, and `serve`'s `finally` is what stops them again.
            # A kill would leave every child running on a dead pipe.
            child.terminate()
            raise OSError(
                f"the agent daemon did not start within {READY_TIMEOUT:g}s"
                f"{_reason(paths, offset)}"
            )
        await asyncio.sleep(POLL_INTERVAL)


def _reason(paths: DaemonPaths, offset: int) -> str:
    """The daemon's own words, when it left any."""
    tail = _log_tail(paths, offset)
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
            await ensure_daemon(DaemonPaths.for_socket(socket_path))
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
    #: Same shape as the real client, so ``client()`` is one plain call and a
    #: test can assert which socket a command asked for, and whether it would
    #: have started a daemon.
    socket_path: str = ""
    autostart: bool = True

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
client_factory: Callable[..., DaemonClient] = SocketDaemonClient


def client(*, autostart: bool = True, socket_path: str | None = None) -> DaemonClient:
    """The transport for one command.

    ``autostart=False`` is for the commands that must not conjure a daemon:
    stopping one that is already gone, or asking whether one is there.
    ``socket_path`` addresses a daemon other than the resolved default, which
    is how a per-environment daemon is reached without setting an env var.
    """
    kwargs: dict[str, Any] = {"autostart": autostart}
    if socket_path is not None:
        kwargs["socket_path"] = socket_path
    return client_factory(**kwargs)


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
                await ensure_daemon(DaemonPaths.for_socket(self.socket_path))
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
