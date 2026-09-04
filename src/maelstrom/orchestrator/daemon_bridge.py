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
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..agent_model import (
    AGENT_DETAIL,
    BACKLOG_END,
    AgentState,
    PendingRequest,
    build_agent_detail,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
)
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
    host would. ``replies`` scripts answers per command, consumed in order;
    with none left, ``list`` answers from ``rows``, ``start`` adds a row,
    ``stop`` drops one, and every other command answers ``{"ok": True}``.

    Like the real host, it echoes the ``control_response`` it writes to the
    child onto every attached stream, so a client learns of a reply the same
    way it learns of one made elsewhere. A ``say`` is not echoed, because the
    child replays a user turn itself.
    """

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    backlog: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    replies: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    next_start_id: str = "new1"
    #: Agent ids whose next attach answers with an error instead of a stream.
    attach_failures: set[str] = field(default_factory=set)
    #: What each agent is waiting on, so an answer can be echoed as the host
    #: builds it. Kept by the fake because the real host derives it from the
    #: stream it already reads.
    pending: dict[str, PendingRequest] = field(default_factory=dict)
    _queues: dict[str, list[asyncio.Queue[Any]]] = field(default_factory=dict)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        command = str(payload.get("cmd"))
        if self.replies.get(command):
            return self.replies[command].pop(0)
        if command == "list":
            return {"agents": list(self.rows.values())}
        echo = self._echo_for(payload, command)
        if echo is not None:
            self.push(str(payload.get("id", "")), echo)
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
            yield {"type": AGENT_DETAIL, "agent": self._detail(agent_id)}
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

    def _detail(self, agent_id: str) -> dict[str, Any]:
        """The opening frame the real host builds from its own ``AgentState``.

        Built from the same ``PendingRequest`` the echo uses, through the
        daemon's own :func:`build_agent_detail`, so the fake cannot describe a
        wait in a shape the host would not.
        """
        state = AgentState(agent_id=agent_id, cwd=self.rows[agent_id].get("cwd", ""))
        pending = self.pending.get(agent_id)
        if pending is not None:
            state = replace(state, pending=pending, status=pending.wait_kind)
        return build_agent_detail(state)

    def _echo_for(self, payload: dict[str, Any], command: str) -> dict[str, Any] | None:
        """The reply the host would echo onto the stream for ``payload``.

        The reply shapes are the daemon's own
        (:mod:`maelstrom.agent_model`), built against the request the agent is
        waiting on. ``None`` for a command that echoes nothing.
        """
        agent_id = str(payload.get("id", ""))
        pending = self.pending.get(agent_id)
        if pending is None:
            return None
        if command == "approve":
            return reply_for_approval(pending)
        if command == "deny":
            return reply_for_denial(pending, str(payload.get("reason", "")))
        if command == "answer":
            return reply_for_answers(pending, dict(payload.get("answers") or {}))
        return None

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
