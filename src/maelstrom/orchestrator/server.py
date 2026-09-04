"""The orchestrator server: the world, its sources, and the clients watching it.

The service layer, and the only asyncio orchestration in the package. It owns
the :class:`~maelstrom.orchestrator.event_log.EventLog`, polls the task and
worktree sources, keeps one attach stream per agent against the agent host,
answers commands, and serves every client the same seq-stamped frames.
:mod:`~maelstrom.orchestrator.routes` puts it on the network.

The wire format — hello, snapshot or replay, ready, commands and replies — is
documented in ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any

from aiohttp import WSMsgType, web

from ..agent_model import AGENT_DETAIL, AGENT_EXITED, BACKLOG_END, RECENT_LIMIT
from ..desk_store import DeskStore, InMemoryDeskStore
from ..task_launch import LaunchBlocked
from ..util import now_iso
from . import desk as desk_model
from .daemon_bridge import AsyncDaemonClient
from .desk import DeskTable, desk_id_for_agent, desk_id_for_task
from .event_log import RING_SIZE, EventLog
from .normalise import (
    NormaliseContext,
    Normalised,
    apply_agent_detail,
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
        self._clients: set[web.WebSocketResponse] = set()
        # Held while frames are appended and sent, and while a client is
        # handed its snapshot: a frame published between the two would reach
        # the client before the snapshot it is newer than, and be lost.
        self._lock = asyncio.Lock()
        self._task_version: Any = _NEVER
        self._worktree_read = asyncio.Lock()
        self._pollers: list[asyncio.Task[None]] = []
        self._started = asyncio.Event()
        self._watches: dict[str, AgentWatch] = {}
        #: Per agent, the highest transcript-item number handed out so far.
        #: Outlives the agent's exit: a resume reuses the agent id, so the
        #: mark has to survive it or the revived agent re-mints old ids.
        self._item_seeds: dict[str, int] = {}
        #: Tasks whose launch is under way, so a second launch is refused.
        self._launching: set[str] = set()

    # -- running --

    async def start(self) -> None:
        """Read every source once, then keep them fresh in the background."""
        await self.refresh_tasks()
        await self._load_desk()
        await self.refresh_worktrees()
        await self.refresh_agents()
        await self._drop_dead_agent_entries()
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

        async def send_all_to(client: web.WebSocketResponse) -> None:
            for text in texts:
                await client.send_str(text)

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
        """Put the stored desk in the world, less any task the notebook lost.

        This runs before the first agent read, because the read saves the
        desk as it joins agents to it and would overwrite the stored file.
        :meth:`_drop_dead_agent_entries` finishes the job once the agents are
        known.
        """
        stored = await self._run(self.desk.load)
        await self._set_desk(self._pruned(stored))

    async def _drop_dead_agent_entries(self) -> None:
        """Drop a stored ``agent:`` entry for an agent the host no longer has.

        The world's agents are rebuilt from the host on every start, so such
        an entry would draw nothing and could never be dismissed. This runs
        once, after the first agent read. During a run the opposite rule
        applies: an agent stays in the world once seen, which is what keeps a
        stopped agent on the canvas.
        """
        world = self.log.state["world"]
        kept = desk_model.drop_unknown_agents(world["desk"], world["agents"])
        await self._set_desk(kept)

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
        await self._add_to_desk(command["id"])
        return {"ok": True, "result": {}}

    async def _add_to_desk(self, desk_id: str) -> None:
        table = self.log.state["world"]["desk"]
        await self._set_desk(desk_model.add(table, desk_id, self.clock()))

    async def _join_desk(self, agent_id: str) -> None:
        """Put a newly adopted live agent on the desk, under its task or itself.

        An agent already exited when the server first sees it does not join:
        only running work puts itself on the canvas.
        """
        agent = self.log.state["world"]["agents"].get(agent_id)
        if agent is None or agent["state"] == "exited":
            return
        task_id = agent["taskId"]
        desk_id = desk_id_for_task(task_id) if task_id else desk_id_for_agent(agent_id)
        await self._add_to_desk(desk_id)

    async def _desk_remove(self, command: dict[str, Any]) -> dict[str, Any]:
        table = self.log.state["world"]["desk"]
        await self._set_desk(desk_model.remove(table, command["id"]))
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

        A newly adopted live agent joins the desk, under its task when it has
        one and under itself when it does not. The canvas draws running work
        either way; the entry is what keeps it drawn once the agent stops. The
        join happens once, at adoption: a later poll must not re-add an entry
        the user has dismissed.
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
                await self._join_desk(agent_id)
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

        The re-attached backlog is relayed with the ids the items already had,
        so a client holding them applies nothing new. The links are resolved
        again in the same pass: a
        task or worktree that arrived while the agent was gone would otherwise
        be missing from it until some later poll took the live-agent branch.
        """
        agent_id = row["id"]
        watch = self._watches.pop(agent_id, None)
        if watch is not None and watch.task is not None:
            watch.task.cancel()
        ctx = context_for_agent(agent_id, self._next_item_seed(agent_id))
        link = self._link(row)
        out = revive_agent(
            self.log.state,
            ctx,
            state,
            self.clock(),
            task_id=link.task_id,
            project=link.project,
            worktree_id=link.worktree_id,
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
        )
        await self.publish([{"type": "upsert", "kind": "agent", "entity": entity}])
        await self._attach(entity["id"])

    async def _attach(self, agent_id: str) -> None:
        """Follow an agent's stream, and wait for its replayed backlog to end."""
        watch = AgentWatch(
            agent_id, context_for_agent(agent_id, self._next_item_seed(agent_id))
        )
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

    def _next_item_seed(self, agent_id: str) -> int:
        """Where a new context's id counter starts, so it mints no id twice.

        A re-attach gets a fresh context, and the ids it hands out must not
        collide with the ones the previous context already sent to clients.
        """
        return self._item_seeds.get(agent_id, 0)

    async def _relink(self, row: dict[str, Any]) -> None:
        agent = self.log.state["world"]["agents"][row["id"]]
        link = self._link(row)
        linked: Agent = {
            **agent,
            "taskId": link.task_id,
            "project": link.project,
            "worktreeId": link.worktree_id,
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
        detail: dict[str, Any] = {}
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
                if kind == AGENT_DETAIL:
                    # The host's opening frame: what the agent waits on. Held,
                    # not applied yet — the backlog that follows usually
                    # replays the ``control_request`` that opened the wait, and
                    # raising it from both would duplicate the item and its
                    # attention. It is applied at BACKLOG_END, and only for a
                    # wait the backlog did not carry.
                    detail = event.get("agent") or {}
                    continue
                if kind == BACKLOG_END:
                    in_backlog = False
                    # A wait the host named that the backlog did not replay:
                    # the request went out before the host's window, so only
                    # the detail frame knows about it.
                    out = apply_agent_detail(
                        self.log.state, watch.ctx, detail, self.clock()
                    )
                    await self._emit(watch, out)
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
        await self._emit(watch, out)

    async def _emit(self, watch: AgentWatch, out: Normalised) -> None:
        """Take a normaliser's output: keep its context, and publish its events."""
        watch.ctx = out.ctx
        self._item_seeds[watch.agent_id] = out.ctx.next_id - 1
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
        ctx = watch.ctx if watch else context_for_agent(agent_id)
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
        request each, and nothing more: the world change comes back on the
        host's own stream, because the host records what it writes to the
        child. The desk and task commands reach the host not at all: the desk
        is the server's own table, and a task write goes to the notebook.
        Everything but those eleven answers ``invalid``: documents, comments,
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
            "task.setStatus": self._set_status,
            "task.update": self._update_task,
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

    async def _approve(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay({"cmd": "approve", "id": command["agentId"]})

    async def _deny(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {"cmd": "deny", "id": command["agentId"], "reason": command["reason"]}
        )

    async def _answer(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {
                "cmd": "answer",
                "id": command["agentId"],
                "answers": dict(command["answers"]),
            }
        )

    async def _say(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {"cmd": "say", "id": command["agentId"], "text": command["text"]}
        )

    async def _relay(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the host, and let its stream say what happened.

        The four commands that write to the child are pure relays: the wait
        resolves when the host's echoed reply arrives on the attach stream.
        See ``docs/dev/orchestrator-server.md``, "The host owns the control
        plane".
        """
        refused = await self._ask_host(payload)
        if refused:
            return refused
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
                await self._add_to_desk(desk_id_for_task(task_id))
                await self._adopt(_started_row(agent_id, request.payload))
            except Exception as exc:  # noqa: BLE001 — the rollback must run
                log.exception("launch of %s failed after the task moved", task_id)
                await self._run(self.tasks.rollback, request)
                await self.refresh_tasks(force=True)
                return _refused("invalid", f"Launch failed: {exc}")
            return {"ok": True, "result": {"agentId": agent_id}}
        finally:
            self._launching.discard(task_id)

    async def _set_status(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._write_task(
            self.tasks.set_status, command["taskId"], command["status"]
        )

    async def _update_task(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._write_task(
            self.tasks.update, command["taskId"], dict(command["fields"])
        )

    async def _write_task(
        self, write: Callable[..., Any], task_id: str, *args: Any
    ) -> dict[str, Any]:
        """One notebook write, then the upsert it caused, then the reply.

        The refresh is forced, as the launch path forces it: a version-checked
        refresh races the poll, so the client could get its reply first.
        """
        try:
            await self._run(write, task_id, *args)
        except KeyError:
            return _refused("unknown_id", f"No task {task_id}")
        except ValueError as exc:
            return _refused("invalid", str(exc))
        await self.refresh_tasks(force=True)
        return {"ok": True, "result": {}}

    # -- the socket --

    async def handle_connection(self, ws: web.WebSocketResponse) -> None:
        """One client: hello, then snapshot or replay, then ready, then commands.

        ``ws`` is prepared already; this runs until the client goes away. A
        client that connects before the first source reads finish waits for
        them, so its snapshot holds the world rather than an empty one that
        fills in later.
        """
        first = await _next_text(ws)
        if first is None:
            return
        hello = _parse(first)
        if hello.get("type") != "hello":
            await ws.send_str(
                _reply(None, _refused("invalid", "The first message must be a hello"))
            )
            await ws.close()
            return
        await self._started.wait()
        await self._welcome(ws, hello.get("resumeFrom"))
        try:
            while (raw := await _next_text(ws)) is not None:
                message = _parse(raw)
                if message.get("type") == "hello":
                    await ws.send_str(
                        _reply(None, _refused("invalid", "Already said hello"))
                    )
                    continue
                command = message.get("command")
                if not isinstance(command, dict):
                    await ws.send_str(
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
                await ws.send_str(_reply(message.get("id"), reply))
        except ConnectionResetError:
            pass
        finally:
            self._clients.discard(ws)

    async def _welcome(self, ws: web.WebSocketResponse, resume_from: Any) -> None:
        async with self._lock:
            frames = None
            if isinstance(resume_from, int) and not isinstance(resume_from, bool):
                frames = self.log.replay_from(resume_from)
            if frames is None:
                frames = [self.log.snapshot_frame(self.clock())]
            for frame in frames:
                await ws.send_str(json.dumps(frame))
            await ws.send_str(json.dumps({"ready": {"seq": self.log.seq}}))
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


async def _next_text(ws: web.WebSocketResponse) -> str | None:
    """The client's next text frame, or ``None`` once the socket has closed."""
    while True:
        message = await ws.receive()
        if message.type == WSMsgType.TEXT:
            return message.data
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
            return None
        if message.type == WSMsgType.ERROR:
            return None


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
