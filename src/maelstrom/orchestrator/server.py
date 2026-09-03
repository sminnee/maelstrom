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
from ..desk_store import DeskStore, InMemoryDeskStore
from ..task_launch import LaunchBlocked
from ..util import now_iso
from . import desk as desk_model
from .daemon_bridge import AsyncDaemonClient
from .desk import DeskTable
from .event_log import RING_SIZE, EventLog
from .normalise import (
    NormaliseContext,
    context_for_agent,
    mark_exited,
    normalise_stream_event,
    revive_agent,
)
from .protocol import Agent, EventFrame, ServerEvent
from .sources import TaskSource, WorktreeSource
from .validate import validate_command
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
        desk: DeskStore | None = None,
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
        self.desk = desk if desk is not None else InMemoryDeskStore()
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
        #: Tasks whose launch is under way, so a second launch is refused.
        self._launching: set[str] = set()

    # -- running --

    async def start(self) -> None:
        """Read every source once, then keep them fresh in the background."""
        await self.refresh_tasks()
        await self._load_desk()
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
        await self._prune_desk()

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

    # -- the desk --

    async def _load_desk(self) -> None:
        """Put the stored desk in the world, less any task the notebook lost."""
        stored = await self._run(self.desk.load)
        await self._set_desk(self._pruned(stored))

    async def _prune_desk(self) -> None:
        """Drop desk entries for tasks that are no longer in the notebook."""
        pruned = self._pruned(self.log.state["world"]["desk"])
        await self._set_desk(pruned)

    def _pruned(self, table: DeskTable) -> DeskTable:
        """``table`` pruned against the projects the last task read covered."""
        tasks = self.log.state["world"]["tasks"]
        return desk_model.prune(table, tasks, {t["project"] for t in tasks.values()})

    async def _set_desk(self, table: DeskTable) -> None:
        """Save the desk, then publish what changed about it.

        Saving first is what makes a restart show the desk the last client
        saw, rather than one change behind it.
        """
        old = self.log.state["world"]["desk"]
        if table == old:
            return
        await self._run(self.desk.save, table)
        await self.publish(diff_kind("desk", old, table))

    async def _desk_add(self, command: dict[str, Any]) -> dict[str, Any]:
        await self._add_to_desk(command["taskId"])
        return {"ok": True, "result": {}}

    async def _add_to_desk(self, task_id: str) -> None:
        table = self.log.state["world"]["desk"]
        await self._set_desk(desk_model.add(table, task_id, self.clock()))

    async def _desk_remove(self, command: dict[str, Any]) -> dict[str, Any]:
        table = self.log.state["world"]["desk"]
        await self._set_desk(desk_model.remove(table, command["taskId"]))
        return {"ok": True, "result": {}}

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
            state, exit_code = parse_agent_state(row.get("state", ""))
            if agents[agent_id]["state"] == "exited":
                if state != "exited":
                    # A resume keeps the agent id, so the row that came back is
                    # this agent alive again, not a new one.
                    await self._revive(row, state)
                continue
            if state == "exited":
                await self._exit(agent_id, exit_code)
                continue
            if agent_id not in self._watches:
                await self._attach(agent_id)
            await self._relink(row)
        for agent_id, agent in list(agents.items()):
            if agent_id not in rows and agent["state"] != "exited":
                await self._exit(agent_id, 0)

    async def _revive(self, row: dict[str, Any], state: str) -> None:
        """Bring an exited agent back: clear its exit, and follow it again.

        The re-attached backlog re-normalises into the same transcript, which
        still holds the turns from before the exit, so nothing is lost and
        nothing is duplicated. The links are resolved again in the same pass: a
        task or worktree that arrived while the agent was gone would otherwise
        be missing from it until some later poll took the live-agent branch.
        """
        agent_id = row["id"]
        watch = self._watches.pop(agent_id, None)
        if watch is not None and watch.task is not None:
            watch.task.cancel()
        ctx = context_for_agent(self.log.state, agent_id)
        link = self._link(row)
        out = revive_agent(
            self.log.state,
            ctx,
            state,
            self.clock(),
            task_id=link.task_id,
            project=link.project,
            worktree_id=link.worktree_id,
            phase=link.phase,
        )
        await self.publish(out.events)
        await self._attach(agent_id)

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
                    # that size may have lost older ones. It does not say
                    # which, so a full backlog is marked either way.
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
        """Run one command and return its reply: ``ok`` with a result, or an error.

        Every command is validated against the world first, so the host is
        only asked things it can do. The agent commands then become one host
        request each; the world change comes back as events, either from the
        host's stream or synthesised here as the reply shape the host itself
        would have written (a ``control_response``, a ``user`` turn), which the
        normaliser handles like any other stream event. The two desk commands
        reach the host not at all: the desk is the server's own table.
        Everything but those eight answers ``invalid``: documents, comments,
        task creation and shaping are out of scope for this server.
        """
        kind = str(command.get("type"))
        handlers = {
            "agent.approve": self._approve,
            "agent.deny": self._deny,
            "agent.answer": self._answer,
            "agent.say": self._say,
            "agent.stop": self._stop,
            "agent.resume": self._resume_agent,
            "agent.launch": self._launch,
            "desk.add": self._desk_add,
            "desk.remove": self._desk_remove,
        }
        handler = handlers.get(kind)
        if handler is None:
            return _refused("invalid", f"Unsupported command: {kind}")
        error = validate_command(self.log.state["world"], command)
        if error:
            return {"ok": False, "error": error}
        return await handler(command)

    async def _ask_host(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """One host request; the mapped refusal, or ``None`` on success."""
        reply = await self.daemon.request(payload)
        if "error" in reply:
            return _refused(_code_for(reply["error"]), reply["error"])
        return None

    async def _resolve(
        self, agent_id: str, request_id: str, response: dict[str, Any]
    ) -> None:
        """Apply the reply the host wrote, as the stream would show it.

        The host does not echo its own ``control_response`` into the stream,
        so the wait would otherwise stay open in the world. If a later host
        does echo it, the normaliser ignores a response for a request no
        longer pending, so nothing is applied twice.
        """
        watch = self._watches.get(agent_id)
        if watch is None:
            log.warning("agent %s answered, but its stream is not attached", agent_id)
            return
        raw = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": response,
            },
        }
        await self._normalise(watch, raw)

    def _pending_input(self, agent_id: str) -> dict[str, Any]:
        watch = self._watches.get(agent_id)
        pending = watch.ctx.pending if watch else None
        return dict(pending.input) if pending else {}

    async def _approve(self, command: dict[str, Any]) -> dict[str, Any]:
        agent_id = command["agentId"]
        refused = await self._ask_host({"cmd": "approve", "id": agent_id})
        if refused:
            return refused
        await self._resolve(
            agent_id,
            command["requestId"],
            {"behavior": "allow", "updatedInput": self._pending_input(agent_id)},
        )
        return {"ok": True, "result": {}}

    async def _deny(self, command: dict[str, Any]) -> dict[str, Any]:
        agent_id = command["agentId"]
        reason = command["reason"]
        refused = await self._ask_host(
            {"cmd": "deny", "id": agent_id, "reason": reason}
        )
        if refused:
            return refused
        await self._resolve(
            agent_id, command["requestId"], {"behavior": "deny", "message": reason}
        )
        return {"ok": True, "result": {}}

    async def _answer(self, command: dict[str, Any]) -> dict[str, Any]:
        agent_id = command["agentId"]
        answers = dict(command["answers"])
        refused = await self._ask_host(
            {"cmd": "answer", "id": agent_id, "answers": answers}
        )
        if refused:
            return refused
        await self._resolve(
            agent_id,
            command["requestId"],
            {
                "behavior": "allow",
                "updatedInput": {**self._pending_input(agent_id), "answers": answers},
            },
        )
        return {"ok": True, "result": {}}

    async def _say(self, command: dict[str, Any]) -> dict[str, Any]:
        agent_id = command["agentId"]
        text = command["text"]
        refused = await self._ask_host({"cmd": "say", "id": agent_id, "text": text})
        if refused:
            return refused
        watch = self._watches.get(agent_id)
        if watch is not None:
            # The host does not echo a user turn either; show what was said.
            raw = {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                },
            }
            await self._normalise(watch, raw)
        return {"ok": True, "result": {}}

    async def _stop(self, command: dict[str, Any]) -> dict[str, Any]:
        agent_id = command["agentId"]
        refused = await self._ask_host({"cmd": "stop", "id": agent_id})
        if refused:
            return refused
        # The host drops a stopped agent, so its stream ends without a marker
        # and the next list no longer names it: this is its clean exit.
        await self._exit(agent_id, 0)
        return {"ok": True, "result": {}}

    async def _resume_agent(self, command: dict[str, Any]) -> dict[str, Any]:
        """Ask the host to start an exited agent again, under its own id.

        The world changes when the next reconcile sees the row live again, so
        nothing is synthesised here — unlike the reply commands, the host's own
        ``list`` is the evidence the agent is back.
        """
        payload: dict[str, Any] = {"cmd": "resume", "id": command["agentId"]}
        text = str(command.get("text", "")).strip()
        if text:
            payload["text"] = text
        refused = await self._ask_host(payload)
        if refused:
            return refused
        return {"ok": True, "result": {}}

    async def _launch(self, command: dict[str, Any]) -> dict[str, Any]:
        task_id = command["taskId"]
        if task_id in self._launching:
            return _refused("invalid", f"Task {task_id} is already launching")
        self._launching.add(task_id)
        try:
            try:
                request = await self._run(
                    self.tasks.launch, task_id, command.get("model")
                )
            except KeyError:
                return _refused("unknown_id", f"No task {task_id}")
            except LaunchBlocked as exc:
                await self.refresh_tasks(force=True)
                return _refused("invalid", str(exc))
            # From here the task is in-progress; any failure to start an
            # agent for it must put it back, or it strands with no agent.
            try:
                reply = await self.daemon.request(request.payload)
                agent_id = reply.get("id") if "error" not in reply else None
                if not agent_id:
                    error = reply.get("error") or "agent host sent no agent id"
                    await self._run(self.tasks.rollback, request)
                    await self.refresh_tasks(force=True)
                    return _refused(_code_for(error), error)
                await self.refresh_tasks(force=True)
                await self._add_to_desk(task_id)
                await self._adopt(_started_row(agent_id, request.payload))
            except Exception as exc:  # noqa: BLE001 — the rollback must run
                log.exception("launch of %s failed after the task moved", task_id)
                await self._run(self.tasks.rollback, request)
                await self.refresh_tasks(force=True)
                return _refused("invalid", f"Launch failed: {exc}")
            return {"ok": True, "result": {"agentId": agent_id}}
        finally:
            self._launching.discard(task_id)

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
                try:
                    reply = await self.handle_command(command)
                except (KeyError, TypeError, AttributeError) as exc:
                    # A field the validator did not check was missing or the
                    # wrong shape: the client's bug, answered as one.
                    reply = _refused("invalid", f"Malformed command: {exc!r}")
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


def _started_row(agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The list row a just-started agent would have, before the host lists it."""
    return {
        "id": agent_id,
        "state": "idle",
        "session": payload["session"],
        "cwd": payload["cwd"],
        "model": payload["model"] or "",
        "waiting_on": "",
        "last_message": "",
        "cost": "",
    }


def _code_for(error: str) -> str:
    """The wire code for one of the host's error strings."""
    if "no such agent" in error:
        return "unknown_id"
    if "has exited" in error:
        return "agent_exited"
    if "not waiting on a question" in error:
        return "wrong_wait_kind"
    if "not waiting" in error:
        return "not_waiting"
    return "invalid"


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
