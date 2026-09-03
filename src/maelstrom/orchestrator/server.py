"""The orchestrator server: the world over one WebSocket.

The adapter layer, and the only asyncio orchestration in the package. It owns
the :class:`~maelstrom.orchestrator.event_log.EventLog`, polls the task and
worktree sources, keeps one attach stream per agent against the agent host,
answers commands, and serves every client the same seq-stamped frames.

The wire format — hello, snapshot or replay, ready, commands and replies — is
documented in ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from ..agent_model import AGENT_EXITED, BACKLOG_END, RECENT_LIMIT
from ..util import now_iso
from .daemon_bridge import AsyncDaemonClient
from .event_log import RING_SIZE, EventLog
from .normalise import (
    NormaliseContext,
    context_for_agent,
    mark_exited,
    normalise_stream_event,
)
from .protocol import Agent, EventFrame, ServerEvent
from .sources import TaskSource, WorktreeSource
from .world_build import (
    AgentLink,
    agent_entity,
    diff_kind,
    link_agent,
    parse_agent_state,
)

log = logging.getLogger(__name__)

#: How often the notebook's version is checked.
TASK_POLL_SECS = 2.0
#: How often ``list-all`` is re-read. It shells out per worktree, so not often.
WORKTREE_POLL_SECS = 15.0
#: How often the agent host's ``list`` is reconciled against the world.
AGENT_POLL_SECS = 2.0
#: How long adopting an agent waits for its replayed backlog to end.
BACKLOG_TIMEOUT_SECS = 5.0


def _reply(command_id: Any, reply: dict[str, Any]) -> str:
    return json.dumps({"reply": {"id": command_id, **reply}})


def _refused(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class AgentWatch:
    """One agent's attach stream and the normaliser state it feeds."""

    def __init__(self, agent_id: str, ctx: NormaliseContext) -> None:
        self.agent_id = agent_id
        self.ctx = ctx
        self.task: asyncio.Task[None] | None = None
        self.backlog_count = 0
        #: Set once the backlog marker has arrived, or the stream ended first.
        self.caught_up = asyncio.Event()


class Orchestrator:
    """The world, its sources, and the clients watching it."""

    def __init__(
        self,
        tasks: TaskSource,
        worktrees: WorktreeSource,
        daemon: AsyncDaemonClient,
        *,
        clock: Callable[[], str] = now_iso,
        executor: Executor | None = None,
        ring_size: int = RING_SIZE,
        task_poll: float = TASK_POLL_SECS,
        worktree_poll: float = WORKTREE_POLL_SECS,
        agent_poll: float = AGENT_POLL_SECS,
    ) -> None:
        self.tasks = tasks
        self.worktrees = worktrees
        self.daemon = daemon
        self.clock = clock
        self.executor = executor
        self.log = EventLog(ring_size)
        self._task_poll = task_poll
        self._worktree_poll = worktree_poll
        self._agent_poll = agent_poll
        self._clients: set[ServerConnection] = set()
        # Held while frames are appended and sent, and while a client is
        # handed its snapshot: a frame published between the two would reach
        # the client before the snapshot it is newer than, and be lost.
        self._lock = asyncio.Lock()
        self._task_version: Any = _NEVER
        self._worktree_read = asyncio.Lock()
        self._pollers: list[asyncio.Task[None]] = []
        self._started = asyncio.Event()
        self._watches: dict[str, AgentWatch] = {}

    # -- running --

    async def start(self) -> None:
        """Read every source once, then keep them fresh in the background."""
        await self.refresh_tasks()
        await self.refresh_worktrees()
        await self.refresh_agents()
        self._started.set()
        self._pollers = [
            asyncio.create_task(self._poll(self._task_poll, self.refresh_tasks)),
            asyncio.create_task(
                self._poll(self._worktree_poll, self.refresh_worktrees)
            ),
            asyncio.create_task(self._poll(self._agent_poll, self.refresh_agents)),
        ]

    async def stop(self) -> None:
        watching = [w.task for w in self._watches.values() if w.task is not None]
        for task in [*self._pollers, *watching]:
            task.cancel()
        await asyncio.gather(*self._pollers, *watching, return_exceptions=True)
        self._pollers = []

    async def serve(self, host: str, port: int) -> None:
        """Serve until cancelled."""
        async with self.serving(host, port):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def serving(self, host: str, port: int) -> AsyncIterator[Server]:
        """Serve for the length of the block. ``port`` 0 picks a free port.

        The socket binds first, so a port in use fails at once. A client that
        connects before the first source reads finish waits for them, so its
        snapshot holds the world rather than an empty one that fills in later.
        """
        async with serve(self.handle_connection, host, port) as server:
            await self.start()
            try:
                yield server
            finally:
                await self.stop()

    async def _poll(self, interval: float, refresh: Callable[[], Any]) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await refresh()
            except Exception:  # noqa: BLE001 — a poller must outlive one bad read
                log.exception("refresh failed")

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run blocking source work off the loop, when an executor was given."""
        if self.executor is None:
            return fn(*args)
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, fn, *args
        )

    # -- keeping the world fresh --

    async def publish(self, events: list[ServerEvent]) -> list[EventFrame]:
        """Append ``events`` to the log and send the frames to every client."""
        if not events:
            return []
        async with self._lock:
            frames = self.log.append(events, self.clock())
            await self._send_all(frames)
        return frames

    async def _send_all(self, frames: list[EventFrame]) -> None:
        """Send ``frames`` in order to every client; drop a client a send fails on.

        The client set is captured once: ``_welcome`` adds to it at await
        points, so pairing results against a second listing could evict the
        wrong client.
        """
        texts = [json.dumps(frame) for frame in frames]
        clients = list(self._clients)

        async def send_all_to(client: ServerConnection) -> None:
            for text in texts:
                await client.send(text)

        results = await asyncio.gather(
            *(send_all_to(client) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, Exception):
                self._clients.discard(client)

    async def refresh_tasks(self, *, force: bool = False) -> None:
        """Re-read the notebook when its version moved, and publish the difference."""
        version = await self._run(self.tasks.version)
        # An unknown version (a notebook with no commits yet) never matches,
        # so the poll degrades to a re-read rather than a permanent stale table.
        if not force and version is not None and version == self._task_version:
            return
        self._task_version = version
        entities = await self._run(self.tasks.read)
        new = {task["id"]: task for task in entities}
        await self.publish(diff_kind("task", self.log.state["world"]["tasks"], new))

    async def refresh_worktrees(self) -> None:
        """Re-read ``list-all``, one read in flight at a time."""
        if self._worktree_read.locked():
            return
        async with self._worktree_read:
            projects, worktrees = await self._run(self.worktrees.read)
        world = self.log.state["world"]
        events = diff_kind("project", world["projects"], {p["id"]: p for p in projects})
        events += diff_kind(
            "worktree", world["worktrees"], {w["id"]: w for w in worktrees}
        )
        await self.publish(events)

    # -- agents --

    async def refresh_agents(self) -> None:
        """Reconcile the agent host's ``list`` against the world.

        A new id is adopted and attached. An id that is gone has exited: the
        host drops a stopped agent, so ``exited(0)`` is the state it left in.
        A row reporting an exit the stream never showed is applied as-is. A
        live agent whose stream ended without an exit is attached again. And a
        live agent's links are re-resolved, so a task or worktree that arrived
        after the agent still finds it.
        """
        reply = await self.daemon.request({"cmd": "list"})
        if "error" in reply:
            log.warning("agent host: %s", reply["error"])
            return
        rows = {row["id"]: row for row in reply.get("agents", [])}
        agents = self.log.state["world"]["agents"]
        for agent_id, row in rows.items():
            if agent_id not in agents:
                await self._adopt(row)
                continue
            if agents[agent_id]["state"] == "exited":
                continue
            state, exit_code = parse_agent_state(row.get("state", ""))
            if state == "exited":
                await self._exit(agent_id, exit_code)
                continue
            if agent_id not in self._watches:
                await self._attach(agent_id)
            await self._relink(row)
        for agent_id, agent in list(agents.items()):
            if agent_id not in rows and agent["state"] != "exited":
                await self._exit(agent_id, 0)

    def _link(self, row: dict[str, Any]) -> AgentLink:
        world = self.log.state["world"]
        return link_agent(row, worktrees=world["worktrees"], tasks=world["tasks"])

    async def _adopt(self, row: dict[str, Any]) -> None:
        """Put a new agent in the world and start following its stream."""
        link = self._link(row)
        entity = agent_entity(
            row,
            task_id=link.task_id,
            project=link.project,
            worktree_id=link.worktree_id,
            phase=link.phase,
        )
        await self.publish([{"type": "upsert", "kind": "agent", "entity": entity}])
        await self._attach(entity["id"])

    async def _attach(self, agent_id: str) -> None:
        """Follow an agent's stream, and wait for its replayed backlog to end."""
        watch = AgentWatch(agent_id, context_for_agent(self.log.state, agent_id))
        self._watches[agent_id] = watch
        watch.task = asyncio.create_task(self._follow(watch))
        # A host that never sends the backlog marker only delays adoption; it
        # does not block the server.
        try:
            await asyncio.wait_for(watch.caught_up.wait(), BACKLOG_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            log.warning(
                "agent %s: no backlog marker within %ss", agent_id, BACKLOG_TIMEOUT_SECS
            )

    async def _relink(self, row: dict[str, Any]) -> None:
        agent = self.log.state["world"]["agents"][row["id"]]
        link = self._link(row)
        linked: Agent = {
            **agent,
            "taskId": link.task_id,
            "project": link.project,
            "worktreeId": link.worktree_id,
            "phase": link.phase,
        }
        if linked != agent:
            await self.publish([{"type": "upsert", "kind": "agent", "entity": linked}])

    async def _follow(self, watch: AgentWatch) -> None:
        """Normalise one agent's attach stream into the log until it ends.

        The watch is dropped when the stream ends, whatever ended it. An agent
        still listed by the host is attached again on the next reconciliation.
        """
        agent_id = watch.agent_id
        in_backlog = True
        try:
            async for event in self.daemon.attach(agent_id):
                if "error" in event and "type" not in event:
                    await self.publish(
                        [
                            {
                                "type": "error",
                                "message": event["error"],
                                "agentId": agent_id,
                            }
                        ]
                    )
                    return
                kind = event.get("type")
                if kind == BACKLOG_END:
                    in_backlog = False
                    # The host's ring holds RECENT_LIMIT events, so a backlog
                    # that size may have lost older ones. A backlog of exactly
                    # that size that lost nothing is marked too; the host does
                    # not say which, and the UI only says older items may be
                    # missing.
                    if watch.backlog_count >= RECENT_LIMIT:
                        await self.publish(
                            [{"type": "transcript.truncated", "agentId": agent_id}]
                        )
                    watch.caught_up.set()
                    continue
                if kind == AGENT_EXITED:
                    await self._exit(agent_id, event.get("exit_code"), from_stream=True)
                    return
                if in_backlog:
                    watch.backlog_count += 1
                await self._normalise(watch, event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad stream must not take the server down
            log.exception("attach stream for %s failed", agent_id)
        finally:
            watch.caught_up.set()
            if self._watches.get(agent_id) is watch:
                del self._watches[agent_id]

    async def _normalise(self, watch: AgentWatch, raw: dict[str, Any]) -> None:
        out = normalise_stream_event(self.log.state, watch.ctx, raw, self.clock())
        watch.ctx = out.ctx
        await self.publish(out.events)

    async def _exit(
        self, agent_id: str, exit_code: int | None, *, from_stream: bool = False
    ) -> None:
        """Mark an agent exited, once.

        An exit learned from anywhere but the agent's own stream also ends
        that stream: a host that dropped the connection would otherwise leave
        the watch waiting forever.
        """
        agent = self.log.state["world"]["agents"].get(agent_id)
        if agent is None or agent["state"] == "exited":
            return
        watch = self._watches.get(agent_id)
        ctx = watch.ctx if watch else context_for_agent(self.log.state, agent_id)
        out = mark_exited(self.log.state, ctx, exit_code, self.clock())
        if watch:
            watch.ctx = out.ctx
        await self.publish(out.events)
        if watch and watch.task and not from_stream:
            watch.task.cancel()

    # -- commands --

    async def handle_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run one command and return its reply: ``ok`` with a result, or an error."""
        return _refused("invalid", f"Unsupported command: {command.get('type')}")

    # -- the socket --

    async def handle_connection(self, ws: ServerConnection) -> None:
        """One client: hello, then snapshot or replay, then ready, then commands."""
        try:
            first = await ws.recv()
        except ConnectionClosed:
            return
        hello = _parse(first)
        if hello.get("type") != "hello":
            await ws.send(
                _reply(None, _refused("invalid", "The first message must be a hello"))
            )
            await ws.close()
            return
        await self._started.wait()
        await self._welcome(ws, hello.get("resumeFrom"))
        try:
            async for raw in ws:
                message = _parse(raw)
                if message.get("type") == "hello":
                    await ws.send(
                        _reply(None, _refused("invalid", "Already said hello"))
                    )
                    continue
                command = message.get("command")
                if not isinstance(command, dict):
                    await ws.send(
                        _reply(
                            message.get("id"),
                            _refused("invalid", "No command in message"),
                        )
                    )
                    continue
                reply = await self.handle_command(command)
                await ws.send(_reply(message.get("id"), reply))
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    async def _welcome(self, ws: ServerConnection, resume_from: Any) -> None:
        async with self._lock:
            frames = None
            if isinstance(resume_from, int) and not isinstance(resume_from, bool):
                frames = self.log.replay_from(resume_from)
            if frames is None:
                frames = [self.log.snapshot_frame(self.clock())]
            for frame in frames:
                await ws.send(json.dumps(frame))
            await ws.send(json.dumps({"ready": {"seq": self.log.seq}}))
            self._clients.add(ws)


_NEVER = object()


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
