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
from datetime import datetime
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
from ..agent_store import (
    AGENT_ENDED,
    AGENT_RUNNING,
    AgentStore,
    new_agent_record,
    register_agent,
)
from ..agent_transport import AsyncDaemonClient, attach_command
from ..harness_model import HARNESS_CLAUDE, HARNESS_CODEX, resolve_model_reference
from ..util import now_iso

DaemonClient = AsyncDaemonClient

#: How many consecutive ``list`` calls must fail to name a stored agent before
#: its record is retired. One is not evidence: a daemon restart, a slow socket
#: and a router pointed at another worktree's daemon root all look the same on
#: a single list, and retiring on the first one draws a working agent as dead.
UNCONFIRMED_LISTS_BEFORE_END = 5

#: How long after its start a record is never retired, however many lists miss
#: it. The daemon takes a moment to hold a new agent, and every poll inside
#: that window misses it — the very case this bug was reported for.
START_GRACE_SECONDS = 60.0


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
    #: Per agent, how many consecutive lists have failed to name it. Reset on
    #: every sighting, so it counts a run of misses rather than a total.
    _misses: dict[str, int] = field(default_factory=dict, init=False)
    #: Per agent, the last row a daemon reported for it and which daemon did.
    #: The state is what an unconfirmed row falls back to, so a transient miss
    #: does not change what the agent is doing; the harness is what an adopted
    #: record is written with. Kept only for agents in the live set.
    _last_seen: dict[str, tuple[str, dict[str, Any]]] = field(
        default_factory=dict, init=False
    )

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = str(payload.get("cmd", ""))
        if command == "list":
            await self._restore()
            claude = await self.claude.request(payload)
            codex = await self.codex.request(payload)
            live_rows: dict[str, dict[str, Any]] = {}
            for reply, harness in ((claude, HARNESS_CLAUDE), (codex, HARNESS_CODEX)):
                for row in reply.get("agents", []):
                    if isinstance(row, dict) and row.get("id"):
                        live_rows[str(row["id"])] = row
                        self._last_seen[str(row["id"])] = (harness, row)
            # A live top-level row the store does not know is an agent started
            # outside the router — `mael add` and `mael agent start` both reach
            # the socket directly. Adopt it rather than dropping it, or it is
            # invisible to every reader of this `list`.
            for agent_id, row in live_rows.items():
                if not row.get("parent") and agent_id not in self._agents:
                    await self._adopt(agent_id, row)
            # Count the misses before the rows are built, so a row that says
            # `exited` is the same decision that retires the record rather
            # than one list behind it. A reply that errored carries no agents,
            # which says nothing about what is alive, so it counts as no
            # information: neither a sighting nor a miss.
            retiring: set[str] = set()
            if not (claude.get("error") or codex.get("error")):
                for agent_id, agent in self._agents.items():
                    if agent_id in live_rows:
                        self._misses.pop(agent_id, None)
                        continue
                    self._misses[agent_id] = self._misses.get(agent_id, 0) + 1
                    if self._is_retiring(agent_id, agent):
                        retiring.add(agent_id)
            rows: list[dict[str, Any]] = []
            for agent_id, agent in self._agents.items():
                last_seen = self._last_seen.get(agent_id)
                rows.append(
                    _stored_agent_row(
                        agent,
                        live_rows.get(agent_id),
                        retiring=agent_id in retiring,
                        last_state=str(last_seen[1].get("state", ""))
                        if last_seen
                        else "",
                    )
                )
                # A live subagent has no record of its own — see Agent
                # record in CONTEXT.md — so it rides through unchanged.
                rows.extend(
                    row for row in live_rows.values() if row.get("parent") == agent_id
                )
            # An agent the daemon no longer holds has ended, however it went.
            # Only `stop` writes the status directly, and a crash, an outside
            # kill and a `gc` reap all bypass it — so a record left `running`
            # would be restored live for ever and report `exited` on every poll.
            for agent_id in retiring:
                await self._end(agent_id, revivable=True)
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

    async def _adopt(self, agent_id: str, row: dict[str, Any]) -> None:
        """Take a live agent into the live set, writing a record if it has none.

        Adopting on read keeps every launch path working without teaching each
        one about the state database. Three cases, and the store tells them
        apart: a record the sweep wrote off comes back, a record the user
        stopped stays stopped, and an agent with no record gets one.
        """
        stored = await self.agents.read(agent_id)
        if stored is not None and not _is_revivable(stored):
            # A deliberate stop is settled. A daemon that still reports the row
            # — the wrong-daemon-root condition this guard survives — must not
            # undo it.
            return
        last_seen = self._last_seen.get(agent_id)
        harness = last_seen[0] if last_seen else HARNESS_CLAUDE
        self._harnesses[agent_id] = harness
        if stored is not None:
            # Demonstrably alive, so the sweep wrote the record off by mistake —
            # it did that to six live agents on this machine. Revived onto its
            # own row, which keeps the task and the start the agent really had.
            agent = {
                **stored,
                "status": AGENT_RUNNING,
                "ended_at": "",
                "swept": False,
            }
            self._agents[agent_id] = agent
            await self.agents.save(agent)
            return
        self._agents[agent_id] = await register_agent(
            self.agents,
            agent_id,
            row,
            # Unknown at adoption. `link_agent` resolves the task by session-id
            # reverse-lookup, so nothing reads this yet.
            task_id="",
            harness=harness,
            started_at=self.clock(),
        )

    def _is_retiring(self, agent_id: str, agent: dict[str, Any]) -> bool:
        """Whether this agent's run of misses is now enough to write it off."""
        if self._misses.get(agent_id, 0) < UNCONFIRMED_LISTS_BEFORE_END:
            return False
        return _seconds_since(str(agent.get("started_at") or ""), self.clock()) >= (
            START_GRACE_SECONDS
        )

    async def _end(self, agent_id: str, *, revivable: bool = False) -> None:
        """Retire one agent's record, keeping the row.

        The record stays: the spend recorded against it is the point of keeping
        it. It drops out of the live set instead, so `list` stops reporting it.

        ``revivable`` marks a record the sweep wrote off rather than one the
        user stopped. Only the first may come back: a ``stop`` is settled, and a
        daemon that still reports the row must not undo it.
        """
        self._harnesses.pop(agent_id, None)
        # The miss count and the last-seen row go with the record: an id adopted
        # again later must start its own run of misses, not inherit the one that
        # retired it, and must not report a state from its previous life.
        self._misses.pop(agent_id, None)
        self._last_seen.pop(agent_id, None)
        if agent := self._agents.pop(agent_id, None):
            await self.agents.save(
                {
                    **agent,
                    "status": AGENT_ENDED,
                    "ended_at": self.clock(),
                    # Says which way the record ended, so a later `list` that
                    # finds the agent alive knows whether it may come back.
                    "swept": revivable,
                }
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


def _is_revivable(agent: dict[str, Any]) -> bool:
    """Whether an ended record may come back when its agent turns up alive.

    A record carries ``swept`` to say which way it ended. One written before
    that field existed was ended by the old sweep, which retired an agent on a
    single unconfirmed list — so those are revivable, and the six live agents it
    wrongly wrote off on this machine are repaired by being seen. Only a record
    a ``stop`` ended says ``swept: False``.
    """
    return bool(agent.get("swept", True))


def _seconds_since(stamp: str, now: str) -> float:
    """How long ago ``stamp`` was, read against ``now``.

    A record with no readable start is treated as arbitrarily old: it predates
    the field, so it is certainly not the just-launched agent the grace period
    protects.
    """
    try:
        elapsed = datetime.fromisoformat(now) - datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        # `TypeError` for a naive stamp against an aware clock: `started_at` is
        # free-form text read back out of a JSON blob, so neither operand is
        # this module's to trust.
        return float("inf")
    return elapsed.total_seconds()


def _stored_agent_row(
    agent: dict[str, Any],
    live: dict[str, Any] | None,
    *,
    retiring: bool,
    last_state: str = "",
) -> dict[str, Any]:
    """The canonical Agent record with fresh harness state when available.

    A stored agent the daemon did not name keeps ``last_state``, the state it
    was last seen in, and reads ``exited`` on the one row that retires it. The
    server ends every wait a row does not report as ``awaiting-``, so a
    placeholder here would cancel a live agent's open permission ask on a
    single transient miss. An agent never seen live has no such state and reads
    ``idle``, which means "the daemon did not say".

    The server's reconcile loop only exits an agent whose id disappears from
    ``list``, and a stored id never does that on its own, so the retiring row
    is its only chance to say the agent has gone.
    """
    return {
        "state": "exited" if retiring else (last_state or "idle"),
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
    ``stop`` drops one, ``resume`` brings one back live, and every other
    command answers ``{"ok": True}``.

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
    #: Rows a ``stop`` dropped, which a ``resume`` brings back, as the host's
    #: spawn records do.
    _stopped: dict[str, dict[str, Any]] = field(default_factory=dict)
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
            if row := self.rows.pop(payload.get("id", ""), None):
                self._stopped[row["id"]] = {**row, "state": "exited(0)"}
        if command == "resume":
            agent_id = str(payload.get("id", ""))
            row = self.rows.get(agent_id) or self._stopped.get(agent_id)
            if row is None:
                return {"error": f"no such agent: {agent_id}"}
            if not str(row.get("state", "")).startswith("exited"):
                return {"error": f"agent {agent_id} is running"}
            self._stopped.pop(agent_id, None)
            self.rows[agent_id] = {**row, "state": "idle"}
            return {"ok": True, "id": agent_id}
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
