"""Transport to the agent daemon, mirroring the trio in ``cmux/client.py``.

- :class:`AsyncDaemonClient` — a Protocol with ``request`` and ``attach``.
- :class:`SocketAsyncDaemonClient` — the real client: one NDJSON round-trip
  over the Unix domain socket for a ``request``, a long-lived connection for
  an ``attach``.
- :class:`RecordingDaemonClient` — the in-memory fake, so CLI commands are
  testable without a daemon.

One trio serves every caller, because the CLI runs on one loop — see
:mod:`maelstrom.cli_async`.

Every path a daemon uses hangs off one directory, its *daemon root*: see
:class:`DaemonPaths`. The root comes from ``MAEL_AGENT_ROOT``, which each
environment writes into its own ``.env``. There is no fallback: a command that
finds no root has no daemon to talk to, rather than someone else's.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .context import get_maelstrom_dir

#: Names the daemon root. The one variable that moves every daemon path at once.
ROOT_ENV = "MAEL_AGENT_ROOT"


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


class RootUnset(Exception):
    """Raised when :data:`ROOT_ENV` names no daemon root.

    The one failure that has no safe default. A guessed root reaches another
    environment's agents, which is how a worktree's test code came to serve as
    the everyday daemon.
    """

    def __init__(self) -> None:
        super().__init__(ROOT_UNSET_MESSAGE)


#: What to tell someone whose environment names no root. Names both starters,
#: because which one they want depends on the worktree they are standing in.
ROOT_UNSET_MESSAGE = (
    f"{ROOT_ENV} is not set. `mael self-env start` runs the everyday daemon; "
    "`mael env start` runs this worktree's."
)


def require_root() -> Path:
    """The daemon root named by :data:`ROOT_ENV`.

    ``~`` is expanded: the value is written by hand into ``.env``, so a tilde
    reaches here, and an unexpanded one makes a directory named ``~`` wherever
    the process happens to be running.

    Raises:
        RootUnset: If the variable is absent or empty.
    """
    override = os.environ.get(ROOT_ENV)
    if not override:
        raise RootUnset()
    return Path(override).expanduser()


def daemon_paths(root: str | Path | None = None) -> DaemonPaths:
    """The paths under ``root``, or under the environment's own.

    Raises:
        RootUnset: If ``root`` is None and the environment names none.
    """
    return DaemonPaths(Path(root).expanduser() if root is not None else require_root())


def all_roots(base: Path | None = None) -> list[DaemonPaths]:
    """Every daemon root on this machine: ``base`` itself, plus each under ``daemons``.

    An environment's daemon lives under ``<base>/daemons/<worktree>``, which is
    where maelstrom's own ``.maelstrom.yaml`` puts it. ``base`` itself is
    listed because the everyday daemon used to live there, and its records
    outlive the move. A root set by hand anywhere else cannot be reached.
    """
    base = base if base is not None else get_maelstrom_dir()
    roots = [DaemonPaths(base)]
    daemons = base / "daemons"
    if daemons.is_dir():
        roots += [
            DaemonPaths(path) for path in sorted(daemons.iterdir()) if path.is_dir()
        ]
    return roots


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


async def open_connection(
    socket_path: str, *, limit: int = STREAM_LIMIT
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """One connection to the daemon, as one seam.

    The sibling of :data:`client_factory`, for the callers that open a socket
    rather than send a command. See "Tests in an agent sandbox" in
    ``CONTRIBUTING.md`` for what a test hands back here.
    """
    return await asyncio.open_unix_connection(socket_path, limit=limit)


#: The phrase every "no daemon here" reply opens with. Callers that must tell
#: an absent daemon from one that answered badly match on this rather than on
#: the whole sentence, so the advice can be reworded without breaking them.
UNREACHABLE_MARKER = "No agent daemon on"


def denied_message(socket_path: str) -> str:
    """What to say when the connect itself was refused.

    Carries no :data:`UNREACHABLE_MARKER`: a denial is not an absent daemon,
    and the callers that match on the marker would kill agents a live daemon
    still holds. See "A sandbox can deny the socket" in
    ``docs/dev/agent-daemon.md``.
    """
    return (
        f"Cannot connect to the agent daemon socket at {socket_path}: "
        "permission denied. The daemon may well be running — a sandbox can "
        "deny a socket connect. Check with `mael agent daemon status` outside "
        "the sandbox, and run `mael admin install` if so."
    )


def unreachable_message(paths: DaemonPaths) -> str:
    """What to say when no daemon answers on ``paths``' socket.

    Names the root, because a command reaching the wrong environment's daemon
    looks the same as no daemon at all. Names both starters, because which one
    is wanted depends on the worktree the caller is standing in.
    """
    return (
        f"{UNREACHABLE_MARKER} {paths.root}. Run `mael self-env start` "
        "(everyday daemon) or `mael env start` (this worktree's)."
    )


#: No daemon holds this root: nothing is listening on the socket.
KIND_UNREACHABLE = "unreachable"

#: The socket is there and the connect was refused, usually by a sandbox.
KIND_DENIED = "denied"


def connect_failure(socket_path: str, error: OSError) -> dict[str, str]:
    """The reply for a connect that did not land.

    Every connect site maps its failure here, so a denial cannot read as an
    absent daemon at one call site and correctly at another. ``kind`` is what
    callers branch on: a caller matching the message text would break on a
    reword, and one of them decides whether a daemon still holds live agents.
    """
    if isinstance(error, PermissionError):
        return {"error": denied_message(socket_path), "kind": KIND_DENIED}
    return {
        "error": unreachable_message(DaemonPaths.for_socket(socket_path)),
        "kind": KIND_UNREACHABLE,
    }


async def request_over_socket(
    socket_path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """One NDJSON round-trip over the daemon's Unix domain socket.

    The body both socket clients share. A connection failure, a reply that
    never comes within :data:`REPLY_TIMEOUT`, a closed connection and a
    malformed line all come back as a reply whose ``error`` explains them,
    never an exception — the same non-fatal contract as ``CmuxResult``.

    No daemon is started. A connection failure comes back as the reply
    :func:`unreachable_message` or :func:`denied_message` describes.
    """
    try:
        reader, writer = await open_connection(socket_path)
    except (OSError, asyncio.TimeoutError) as error:
        return connect_failure(socket_path, error)
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
    #: test can assert which socket a command asked for.
    socket_path: str = ""

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.replies:
            return self.replies.pop(0)
        return {"ok": True}


class AsyncDaemonClient(Protocol):
    """What a caller may ask of the daemon.

    ``request`` is one round-trip. ``attach`` is a long-lived connection
    yielding the agent's raw events until it ends.
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
    """The real client: one NDJSON round-trip over the Unix domain socket.

    Errors reach the caller as data, not exceptions: ``request`` returns a
    reply whose ``error`` explains it, and ``attach`` yields one such dict and
    stops. So a CLI prints a useful line instead of a traceback when the
    daemon is down.
    """

    socket_path: str = field(default_factory=resolve_socket_path)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await request_over_socket(self.socket_path, payload)

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
            reader, writer = await open_connection(self.socket_path)
        except (OSError, asyncio.TimeoutError) as error:
            yield connect_failure(self.socket_path, error)
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


#: The transport every CLI caller goes through, as one seam. Tests override
#: this attribute to drive a command through :class:`RecordingDaemonClient`
#: instead of a real socket. One copy, so a test patching it reaches every
#: caller.
client_factory: Callable[..., AsyncDaemonClient] = SocketAsyncDaemonClient


def client(*, socket_path: str | None = None) -> AsyncDaemonClient:
    """The transport for one command.

    ``socket_path`` addresses a daemon other than this environment's, which is
    how ``--all-roots`` reaches each root in turn.
    """
    kwargs: dict[str, Any] = {}
    if socket_path is not None:
        kwargs["socket_path"] = socket_path
    return client_factory(**kwargs)
