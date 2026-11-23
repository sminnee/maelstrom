"""Who is on the far end of a daemon socket connection: the caller of an **Audit record**.

The kernel gives the peer's pid and uid, which a client cannot forge. The
pid's ancestry, read from the process table, names what ran the command: a
``mael agent run`` in a worktree, an agent's shell, the orchestrator server.

Linux reads ``SO_PEERCRED`` and ``/proc``. macOS reads ``LOCAL_PEERCRED`` and
``LOCAL_PEERPID``, then ``ps`` and ``lsof`` per ancestor. Each lookup gives what it can: a part it
cannot read is ``None``, and a fault is never raised.
"""

import asyncio
import logging
import os
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: How many processes the ancestry holds, the caller first.
ANCESTRY_DEPTH = 5

# macOS: <sys/un.h>. Python does not name them.
_SOL_LOCAL = 0
_LOCAL_PEERCRED = 0x001
_LOCAL_PEERPID = 0x002
#: ``struct xucred``: version, uid, a short count, then 16 gids.
_XUCRED = "IIh2x16I"


@dataclass(frozen=True)
class Ancestor:
    """One process of the caller's ancestry."""

    pid: int
    cmdline: str | None
    cwd: str | None


@dataclass(frozen=True)
class Peer:
    """The process on the far end of a connection, as the kernel names it."""

    pid: int | None
    uid: int | None


def peer_of(writer: asyncio.StreamWriter) -> Peer:
    """The peer of the Unix socket connection behind ``writer``."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return Peer(None, None)
    try:
        if sys.platform == "linux":
            creds = sock.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII")
            )
            pid, uid, _gid = struct.unpack("iII", creds)
            return Peer(pid, uid)
        if sys.platform == "darwin":
            # From the kernel, not ``ps``: this runs on the loop, at each accept.
            pid = struct.unpack(
                "i", sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERPID, struct.calcsize("i"))
            )[0]
            xucred = sock.getsockopt(
                _SOL_LOCAL, _LOCAL_PEERCRED, struct.calcsize(_XUCRED)
            )
            uid = struct.unpack_from("II", xucred)[1]
            return Peer(pid, uid)
    except OSError as exc:
        log.warning("could not read the socket peer: %s", exc)
    return Peer(None, None)


def ancestry(pid: int, depth: int = ANCESTRY_DEPTH) -> list[Ancestor]:
    """``pid`` and up to ``depth - 1`` of its parents, the caller first.

    Stops at pid 1 or at a process it cannot read.
    """
    chain: list[Ancestor] = []
    current: int | None = pid
    while current is not None and current > 1 and len(chain) < depth:
        cmdline, parent = _read(current)
        if cmdline is None and parent is None:
            break
        chain.append(Ancestor(current, cmdline, _cwd(current)))
        current = parent
    return chain


def caller_record(peer: Peer) -> dict[str, Any]:
    """The ``caller`` field of an **Audit record**.

    A fault in the ancestry read leaves the pid and uid, with no ancestry.
    """
    chain: list[Ancestor] = []
    if peer.pid is not None:
        try:
            chain = ancestry(peer.pid)
        except Exception:  # noqa: BLE001 — the command must still run
            log.warning(
                "could not read the ancestry of pid %s", peer.pid, exc_info=True
            )
    return {
        "pid": peer.pid,
        "uid": peer.uid,
        "ancestry": [{"pid": a.pid, "cmdline": a.cmdline, "cwd": a.cwd} for a in chain],
    }


def _read(pid: int) -> tuple[str | None, int | None]:
    """The command line of ``pid`` and its parent's pid."""
    if sys.platform == "linux":
        proc = Path("/proc") / str(pid)
        try:
            argv = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            cmdline = " ".join(a.decode(errors="replace") for a in argv)
            stat = (proc / "stat").read_text()
            # The name in field 2 may hold spaces and parens: split after it.
            parent = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None, None
        return cmdline, parent
    out = _run(["ps", "-o", "ppid=,command=", "-p", str(pid)])
    if not out:
        return None, None
    ppid, _, command = out.strip().partition(" ")
    try:
        return command.strip(), int(ppid)
    except ValueError:
        return None, None


def _cwd(pid: int) -> str | None:
    """The working directory of ``pid``, when this user may read it."""
    if sys.platform == "linux":
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
    out = _run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"])
    for line in (out or "").splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def _run(argv: list[str]) -> str | None:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None
