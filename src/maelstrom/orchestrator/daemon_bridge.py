"""The orchestrator server's client for the agent host.

The agent host is the agent daemon's control socket: NDJSON, one request and
one reply, plus ``attach``, a long-lived stream. The server is a client of it
the way ``mael agent tail -f`` is, and never imports the daemon's internals.
See ``docs/dev/agent-daemon.md``, "The control socket protocol".

Storage layer, mirroring :mod:`maelstrom.agent_transport`: a Protocol, the real
socket client, and a scripted fake that records calls.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..agent_model import BACKLOG_END
from ..agent_transport import ensure_daemon, request_over_socket, resolve_socket_path


class AsyncDaemonClient(Protocol):
    """One request-reply, or one attach stream, against the agent host."""

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``payload`` and return the host's reply."""
        ...

    def attach(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        """Stream one agent's events: the backlog, the marker, then live events.

        The stream ends when the host closes it — after the exit marker, or
        because the host went away. An unknown agent yields one ``error`` dict.
        """
        ...


_END = object()


@dataclass
class ScriptedAsyncDaemonClient:
    """In-memory fake: scripted rows and streams, every call recorded.

    ``rows`` is what ``list`` answers with. ``backlog`` is what an attach
    replays before the marker. ``push`` delivers a live event to an attached
    stream and ``end_stream`` closes it, so a test can play a fixture as the
    host would. ``replies`` are consumed in order for any request; once they
    run out, ``list`` answers from ``rows``, ``start`` adds a row, and every
    other command answers ``{"ok": True}``.
    """

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    backlog: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    replies: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    next_start_id: str = "new1"
    #: Agent ids whose next attach answers with an error instead of a stream.
    attach_failures: set[str] = field(default_factory=set)
    _queues: dict[str, list[asyncio.Queue[Any]]] = field(default_factory=dict)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.replies:
            return self.replies.pop(0)
        command = payload.get("cmd")
        if command == "list":
            return {"agents": list(self.rows.values())}
        if command == "start":
            agent_id = self.next_start_id
            self.rows[agent_id] = {
                "id": agent_id,
                "state": "idle",
                "session": payload.get("session") or "",
                "cwd": payload["cwd"],
                "model": payload.get("model") or "",
                "waiting_on": "",
                "last_message": "",
                "cost": "",
            }
            return {"ok": True, "id": agent_id}
        if command == "stop":
            self.rows.pop(payload.get("id", ""), None)
        return {"ok": True}

    async def attach(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        self.calls.append({"cmd": "attach", "id": agent_id})
        if agent_id in self.attach_failures:
            self.attach_failures.discard(agent_id)
            yield {"error": f"attach refused: {agent_id}"}
            return
        if agent_id not in self.rows:
            yield {"error": f"no such agent: {agent_id}"}
            return
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._queues.setdefault(agent_id, []).append(queue)
        try:
            for event in self.backlog.get(agent_id, []):
                yield event
            yield {"type": BACKLOG_END}
            while True:
                event = await queue.get()
                if event is _END:
                    return
                yield event
        finally:
            self._queues[agent_id].remove(queue)

    def push(self, agent_id: str, event: dict[str, Any]) -> None:
        """Deliver one live event to every stream attached to ``agent_id``."""
        for queue in self._queues.get(agent_id, []):
            queue.put_nowait(event)

    def end_stream(self, agent_id: str) -> None:
        """Close every stream attached to ``agent_id``, as the host would on exit."""
        for queue in self._queues.get(agent_id, []):
            queue.put_nowait(_END)

    @property
    def attached(self) -> list[str]:
        """Agent ids with a stream open right now."""
        return [agent_id for agent_id, queues in self._queues.items() if queues]


@dataclass
class SocketAsyncDaemonClient:
    """The real client, over the daemon's Unix domain socket.

    Runs on the server's own event loop, so it shares the async body with
    :class:`~maelstrom.agent_transport.SocketDaemonClient` rather than that
    class itself, which wraps ``asyncio.run`` and cannot nest. A connection
    failure comes back as a reply whose ``error`` explains it, never an
    exception.
    """

    socket_path: str = field(default_factory=resolve_socket_path)
    autostart: bool = True

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await request_over_socket(
            self.socket_path, payload, autostart=self.autostart
        )

    async def attach(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        try:
            reader, writer = await self._connect()
        except (OSError, asyncio.TimeoutError) as exc:
            yield {"error": f"agent daemon not reachable at {self.socket_path}: {exc}"}
            return
        try:
            writer.write(
                (json.dumps({"cmd": "attach", "id": agent_id}) + "\n").encode()
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

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.autostart:
            await ensure_daemon(self.socket_path)
        return await asyncio.open_unix_connection(self.socket_path)
