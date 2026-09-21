"""The orchestrator server's client for the agent host.

The agent host is the agent daemon's control socket: NDJSON, one request and
one reply, plus ``attach``, a long-lived stream. The server is a client of it
the way ``mael agent tail -f`` is, and never imports the daemon's internals.
See ``docs/dev/agent-daemon.md``, "The control socket protocol".

Storage layer, mirroring :mod:`maelstrom.agent_transport`: a Protocol, the real
socket client, and a scripted fake that records calls.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ..agent_model import (
    AGENT_DETAIL,
    BACKLOG_END,
    SEQ_KEY,
    TRUNCATED,
    AgentState,
    PendingRequest,
    build_agent_detail,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
)
from ..agent_store import AGENT_ENDED, AGENT_RUNNING, AgentStore, new_agent_record
from ..agent_transport import AsyncDaemonClient, attach_command
from ..harness_model import HARNESS_CLAUDE, HARNESS_CODEX, resolve_model_reference
from ..util import now_iso

DaemonClient = AsyncDaemonClient


@dataclass
class DaemonRouter:
    """Route an agent operation through the Harness that owns it."""

    claude: DaemonClient
    codex: DaemonClient
    agents: AgentStore
    #: What stamps a record's start and end. Injected so a test can pin them.
    clock: Callable[[], str] = now_iso
    _harnesses: dict[str, str] = field(default_factory=dict, init=False)
    _agents: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _restored: bool = field(default=False, init=False)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("cmd", ""))
        if command == "list":
            await self._restore()
            claude = await self.claude.request(payload)
            codex = await self.codex.request(payload)
            live_rows: dict[str, dict[str, Any]] = {}
            for row in [*claude.get("agents", []), *codex.get("agents", [])]:
                if isinstance(row, dict) and row.get("id"):
                    live_rows[str(row["id"])] = row
            rows: list[dict[str, Any]] = []
            lost: list[str] = []
            for agent_id, agent in self._agents.items():
                live = live_rows.get(agent_id)
                rows.append(_stored_agent_row(agent, live))
                if live is None:
                    lost.append(agent_id)
                # A live subagent has no record of its own — see Agent
                # record in CONTEXT.md — so it rides through unchanged.
                rows.extend(
                    row for row in live_rows.values() if row.get("parent") == agent_id
                )
            # An agent the daemon no longer holds has ended, however it went.
            # Only `stop` writes the status directly, and a crash, an outside
            # kill and a `gc` reap all bypass it — so a record left `running`
            # would be restored live for ever and report `exited` on every poll.
            for agent_id in lost:
                await self._end(agent_id)
            return {"agents": rows, "usage": claude.get("usage")}
        await self._restore()
        try:
            client, harness = self._client_for(payload)
        except ValueError as error:
            return {"ok": False, "error": str(error)}
        reply = await client.request(payload)
        if command == "start" and reply.get("ok") and reply.get("id"):
            agent_id = str(reply["id"])
            self._harnesses[agent_id] = harness
            agent = new_agent_record(
                agent_id,
                harness=harness,
                task_session_id=str(payload.get("session") or ""),
                # Not read yet: link_agent still resolves the task by
                # session-id reverse-lookup. Persisted now so it is there
                # when a reader needs it.
                task_id=str(payload.get("env", {}).get("MAEL_TASK_ID", "")),
                cwd=str(payload.get("cwd") or ""),
                model=str(payload.get("model") or ""),
                mode=str(payload.get("mode") or "normal"),
                started_at=self.clock(),
            )
            self._agents[agent_id] = agent
            await self.agents.save(agent)
        if command == "set-mode" and reply.get("ok"):
            agent_id = str(payload.get("id", ""))
            if agent := self._agents.get(agent_id):
                agent["mode"] = str(payload.get("mode") or agent["mode"])
                await self.agents.save(agent)
        if command == "stop" and reply.get("ok"):
            await self._end(str(payload.get("id", "")))
        return reply

    async def _end(self, agent_id: str) -> None:
        """Retire one agent's record, keeping the row.

        The record stays: the spend recorded against it is the point of keeping
        it. It drops out of the live set instead, so `list` stops reporting it.
        """
        self._harnesses.pop(agent_id, None)
        if agent := self._agents.pop(agent_id, None):
            await self.agents.save(
                {**agent, "status": AGENT_ENDED, "ended_at": self.clock()}
            )

    def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        client = (
            self.codex
            if self._harnesses.get(agent_id) == HARNESS_CODEX
            else self.claude
        )
        return client.attach(agent_id, from_seq, epoch)

    def _client_for(self, payload: dict[str, Any]) -> tuple[DaemonClient, str]:
        if payload.get("cmd") == "start":
            model = str(payload.get("model") or "")
            harness = resolve_model_reference(model).harness
            if harness == HARNESS_CODEX:
                return self.codex, harness
            if harness == HARNESS_CLAUDE:
                return self.claude, harness
            raise ValueError(f"The {harness} daemon is not available.")
        harness = self._harnesses.get(str(payload.get("id", "")), HARNESS_CLAUDE)
        return (
            (self.codex, harness)
            if harness == HARNESS_CODEX
            else (self.claude, harness)
        )

    async def _restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        agents = await self.agents.list()
        # Live ones only. The table holds every agent Maelstrom ever started,
        # and an ended one restored here would come back as an `exited` row on
        # every poll — which the server's reconcile loop could never retire,
        # because it retires an id that drops out of `list`.
        self._agents = {
            str(agent["id"]): agent
            for agent in agents
            if agent.get("id") and agent.get("status", AGENT_RUNNING) != AGENT_ENDED
        }
        restore = getattr(self.codex, "restore", None)
        if restore is not None:
            await restore(agents)
        for agent_id, agent in self._agents.items():
            if agent.get("harness"):
                self._harnesses[agent_id] = str(agent["harness"])


def _stored_agent_row(
    agent: dict[str, Any], live: dict[str, Any] | None
) -> dict[str, Any]:
    """The canonical Agent record with fresh harness state when available.

    A stored agent absent from the daemon's own ``list`` (killed outside
    ``mael agent stop``, or reaped by ``gc``) is reported ``exited`` rather
    than left with no ``state`` at all: the server's reconcile loop only
    retires an agent whose id disappears from ``list``, and a stored id never
    does that on its own.
    """
    return {
        "state": "exited",
        **(live or {}),
        "id": agent["id"],
        "session": agent["task_session_id"],
        "cwd": agent["cwd"],
        "model": agent["model"],
        "mode": agent["mode"],
    }


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

    Also like the host, it stamps every backlog and pushed event with a
    ``mael_seq`` and closes the backlog with the agent's ``epoch``, honours a
    cursor from that epoch, and writes ``mael_truncated`` first when
    ``truncated`` names the agent.
    """

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: What ``list`` reports as the account's budget, as the real host does.
    #: ``None`` is a host that has heard no reading yet.
    usage: dict[str, Any] | None = None
    backlog: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    replies: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    next_start_id: str = "new1"
    #: Agent ids whose next attach answers with an error instead of a stream.
    attach_failures: set[str] = field(default_factory=set)
    #: Agent ids whose attach opens a live stream but withholds the backlog
    #: marker, as a host that is slow to replay does.
    hold_backlog: set[str] = field(default_factory=set)
    #: What each agent is waiting on, so an answer can be echoed as the host
    #: builds it. Kept by the fake because the real host derives it from the
    #: stream it already reads.
    pending: dict[str, PendingRequest] = field(default_factory=dict)
    #: Per agent, how many events the host says it dropped before the backlog.
    truncated: dict[str, int] = field(default_factory=dict)
    #: Per agent, the epoch its backlog marker carries.
    epochs: dict[str, str] = field(default_factory=dict)
    _queues: dict[str, list[asyncio.Queue[Any]]] = field(default_factory=dict)
    _seqs: dict[str, int] = field(default_factory=dict)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        # The real client writes to a socket and awaits the reply, so it
        # yields the loop on every call. Match that, or no test can
        # interleave a command with a poll.
        await asyncio.sleep(0)
        self.calls.append(payload)
        command = str(payload.get("cmd"))
        if self.replies.get(command):
            return self.replies[command].pop(0)
        if command == "list":
            return {"agents": list(self.rows.values()), "usage": self.usage}
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
                # The host describes the agent it started. The row a launch
                # builds for itself cannot, so the two rows differ.
                "description": f"started in {payload['cwd']}",
                "waiting_on": "",
                "last_message": "",
                "last_note": "",
                "last_note_at": "",
                "cost": "",
            }
            return {"ok": True, "id": agent_id}
        if command == "stop":
            self.rows.pop(payload.get("id", ""), None)
        return {"ok": True}

    async def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append(attach_command(agent_id, from_seq, epoch))
        if agent_id in self.attach_failures:
            self.attach_failures.discard(agent_id)
            yield {"error": f"attach refused: {agent_id}"}
            return
        if agent_id not in self.rows:
            yield {"error": f"no such agent: {agent_id}"}
            return
        own_epoch = self.epochs.setdefault(agent_id, f"epoch-{agent_id}")
        if epoch != own_epoch:
            from_seq = 0
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._queues.setdefault(agent_id, []).append(queue)
        try:
            yield {"type": AGENT_DETAIL, "agent": self._detail(agent_id)}
            backlog = [
                self._stamped(agent_id, e) for e in self.backlog.get(agent_id, [])
            ]
            self.backlog[agent_id] = backlog
            if from_seq == 0 and self.truncated.get(agent_id):
                yield {"type": TRUNCATED, "dropped": self.truncated[agent_id]}
            for event in backlog:
                if event[SEQ_KEY] > from_seq:
                    yield event
            if agent_id not in self.hold_backlog:
                yield {
                    "type": BACKLOG_END,
                    "epoch": own_epoch,
                    "seq": self._seqs.get(agent_id, 0),
                }
            while True:
                event = await queue.get()
                if event is _END:
                    return
                yield event
        finally:
            self._queues[agent_id].remove(queue)

    def _stamped(self, agent_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """``event`` with the next seq for ``agent_id``, or as it is when it has one."""
        if SEQ_KEY in event:
            return event
        seq = self._seqs.get(agent_id, 0) + 1
        self._seqs[agent_id] = seq
        return {**event, SEQ_KEY: seq}

    def push(self, agent_id: str, event: dict[str, Any]) -> None:
        """Deliver one live event to every stream attached to ``agent_id``.

        A daemon marker travels as it is; anything else is stamped, as the
        host stamps what it records.
        """
        if not str(event.get("type", "")).startswith("mael_"):
            event = self._stamped(agent_id, event)
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
            state = replace(
                state,
                own_pending={pending.request_id: pending},
                status=pending.wait_kind,
            )
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
