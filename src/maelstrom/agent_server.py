"""The daemon: N ``claude`` children on this machine, and the socket driving them.

Each agent is a normal ``claude`` process with different I/O plumbing::

    claude -p --input-format stream-json --output-format stream-json --verbose

which is a bidirectional NDJSON pipe over plain stdio. The daemon reads each
child's event stream, derives state through
:func:`maelstrom.agent_model.apply_event`, and writes answers back on the
child's stdin. No MCP server, no WebSocket, no lockfile, no ports.

Agent state lives in memory only, so there is no store Protocol — an agent dies
with the daemon holding it. See ``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from .agent_model import (
    AWAITING_QUESTION,
    EXITED,
    AgentState,
    apply_event,
    build_agent_argv,
    build_agent_row,
    mark_exited,
    reply_for_answer,
    reply_for_approval,
    reply_for_denial,
    user_message,
)
from .agent_transport import resolve_socket_path

#: How far one attached client may fall behind before it starts losing events.
WATCHER_QUEUE_LIMIT = 1000


def _offer(queue: "asyncio.Queue[dict[str, Any]]", event: dict[str, Any]) -> None:
    """Give ``event`` to a watcher, dropping the oldest when it is full.

    A client that stops reading — a paused pager, a suspended terminal — must
    not grow the daemon's memory without limit. Dropping the oldest event keeps
    the live tail, which is what an attached viewer wants.
    """
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        queue.put_nowait(event)


class Agent:
    """One ``claude`` child process, its state, and the clients watching it.

    Owns the child's stdin for the life of the agent. Closing it would end the
    session after one turn, which is what a bare ``claude -p`` does; holding it
    open is what makes the process a long-lived, drivable agent.
    """

    def __init__(self, agent_id: str, cwd: str, proc: asyncio.subprocess.Process):
        self.state = AgentState(agent_id=agent_id, cwd=cwd)
        self.proc = proc
        self.watchers: list[asyncio.Queue[dict[str, Any]]] = []
        # Held, not fire-and-forget: a task only the event loop references can
        # be garbage-collected mid-stream, which would freeze the agent's state
        # while `mael agent list` still showed it running.
        self.pump_task: asyncio.Task[None] | None = None

    async def send(self, message: dict[str, Any]) -> None:
        """Write one NDJSON message to the child's stdin."""
        if self.proc.stdin is None or self.proc.stdin.is_closing():
            return
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def pump(self) -> None:
        """Read the child's stream to its end, then record that the child died.

        The stream ending is the only notice the daemon gets that a child has
        gone, so marking the agent ``exited`` here is what stops a crashed agent
        advertising a wait nobody can answer.
        """
        assert self.proc.stdout is not None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a non-JSON line is noise, not a state change
                self.state = apply_event(self.state, event)
                for queue in list(self.watchers):
                    _offer(queue, event)
        finally:
            self.state = mark_exited(self.state, await self.proc.wait())

    async def stop(self) -> None:
        """End the agent: close its stdin, then kill it if it does not exit."""
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()


class AgentDaemon:
    """The N agents on this machine, and the control socket the CLI talks to."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or resolve_socket_path()
        self.agents: dict[str, Agent] = {}

    # -- lifecycle --

    async def start_agent(
        self,
        cwd: str,
        prompt: str = "",
        *,
        permission_mode: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Spawn an agent in ``cwd`` and return its id."""
        agent_id = agent_id or uuid.uuid4().hex[:8]
        argv = build_agent_argv(
            permission_mode=permission_mode, session_id=session_id, model=model
        )
        # stderr joins stdout so a child that dies early — a bad --model, an
        # expired login — leaves its reason in the event buffer. pump() skips
        # the non-JSON lines, and `mael agent attach` still shows them.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        agent = Agent(agent_id, cwd, proc)
        self.agents[agent_id] = agent
        agent.pump_task = asyncio.create_task(agent.pump())
        if prompt:
            await agent.send(user_message(prompt))
        return agent_id

    # -- the command surface the CLI drives --

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one CLI command and return its reply."""
        command = payload.get("cmd")

        if command == "start":
            try:
                agent_id = await self.start_agent(
                    payload["cwd"],
                    payload.get("prompt", ""),
                    permission_mode=payload.get("mode"),
                    model=payload.get("model"),
                    session_id=payload.get("session"),
                )
            except OSError as exc:
                # Without this the exception escapes `handle`, the connection
                # closes unanswered, and the CLI blames the daemon for a
                # `claude` that is simply not on PATH.
                return {"error": f"could not start claude: {exc}"}
            return {"ok": True, "id": agent_id}

        if command == "list":
            return {"agents": [build_agent_row(a.state) for a in self.agents.values()]}

        agent = self.agents.get(payload.get("id", ""))
        if agent is None:
            return {"error": f"no such agent: {payload.get('id', '')}"}

        # `Agent.send` returns silently on a closed stdin, so without this every
        # command against a dead agent would report success.
        if agent.state.status == EXITED and command != "stop":
            return {"error": f"agent {agent.state.agent_id} has exited"}

        if command == "say":
            await agent.send(user_message(payload["text"]))
            return {"ok": True}

        if command == "stop":
            await agent.stop()
            self.agents.pop(agent.state.agent_id, None)
            return {"ok": True}

        pending = agent.state.pending
        if command in ("answer", "approve", "deny"):
            if pending is None:
                return {"error": f"agent {agent.state.agent_id} is not waiting"}
            if command == "answer":
                # A non-question wait has no question texts, so an answer would
                # go out as an empty map — which the agent reads as no answer at
                # all. Fail instead of resolving the wait wrongly.
                if pending.wait_kind != AWAITING_QUESTION:
                    return {
                        "error": (
                            f"agent {agent.state.agent_id} is not waiting on a "
                            f"question — use approve or deny"
                        )
                    }
                await agent.send(reply_for_answer(pending, payload["choice"]))
            elif command == "approve":
                await agent.send(reply_for_approval(pending))
            else:
                await agent.send(reply_for_denial(pending, payload.get("reason", "")))
            return {"ok": True}

        return {"error": f"unknown command: {command}"}

    # -- the socket --

    async def serve(self) -> None:
        """Listen on the control socket until cancelled, then stop every agent.

        Refuses to start when another daemon already answers on the socket.
        Unlinking blindly would leave that daemon listening on a path no client
        can reach, holding agents nothing can stop.

        Raises:
            RuntimeError: If a daemon is already serving this socket.
        """
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if await self._socket_is_live(path):
            raise RuntimeError(f"a daemon is already serving {path}")
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._on_client, str(path))
        try:
            async with server:
                await server.serve_forever()
        finally:
            # Orphaned children would keep running with their stdin held by a
            # dead parent, so shutdown has to reach them.
            await asyncio.gather(
                *(agent.stop() for agent in self.agents.values()),
                return_exceptions=True,
            )
            self.agents.clear()
            path.unlink(missing_ok=True)

    @staticmethod
    async def _socket_is_live(path: Path) -> bool:
        """Whether something already answers on ``path``.

        A stale socket file left by a killed daemon refuses the connection, so
        it reads as free — which is what lets the next daemon replace it.
        """
        if not path.exists():
            return False
        try:
            _, writer = await asyncio.open_unix_connection(str(path))
        except OSError:
            return False
        writer.close()
        return True

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """One CLI connection: NDJSON commands in, NDJSON replies out.

        ``attach`` holds the connection open and streams the agent's events;
        every other command is a single round-trip.
        """
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {}
                if payload.get("cmd") == "attach":
                    await self._attach(payload.get("id", ""), writer)
                    return
                reply = await self.handle(payload)
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()

    async def _attach(self, agent_id: str, writer: asyncio.StreamWriter) -> None:
        """Stream one agent's events to a client until it disconnects.

        Replays the buffered ``recent`` events first, so a client that attaches
        mid-turn sees the context it arrived into rather than starting blank.
        """
        agent = self.agents.get(agent_id)
        if agent is None:
            writer.write(
                (json.dumps({"error": f"no such agent: {agent_id}"}) + "\n").encode()
            )
            await writer.drain()
            return
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=WATCHER_QUEUE_LIMIT
        )
        agent.watchers.append(queue)
        try:
            for event in agent.state.recent:
                writer.write((json.dumps(event) + "\n").encode())
            await writer.drain()
            while True:
                event = await queue.get()
                writer.write((json.dumps(event) + "\n").encode())
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            return
        finally:
            if queue in agent.watchers:
                agent.watchers.remove(queue)
