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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

#: Where the daemon listens for CLI commands. One socket per machine.
DEFAULT_SOCKET_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.sock")


def resolve_socket_path() -> str:
    """The daemon socket path from the environment, or the default."""
    return os.environ.get("MAEL_AGENT_SOCKET") or DEFAULT_SOCKET_PATH


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

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._request(payload))

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        except (OSError, asyncio.TimeoutError) as exc:
            return {"error": f"agent daemon not reachable at {self.socket_path}: {exc}"}
        try:
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
        if not line:
            return {"error": "agent daemon closed the connection without replying"}
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            return {"error": f"agent daemon sent a malformed reply: {exc}"}
