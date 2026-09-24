"""The orchestrator server's client for the agent host.

The agent host is the agent daemon's control socket: NDJSON, one request and
one reply, plus ``attach``, a long-lived stream. The server is a client of it
the way ``mael agent tail -f`` is, and never imports the daemon's internals.
See ``docs/dev/agent-daemon.md``, "The control socket protocol".

Tests stand in for the agent host with
:class:`~mael_agent.agent_transport.ScriptedAsyncDaemonClient`.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mael_agent.agent_transport import AsyncDaemonClient
from mael_agent.harness_model import (
    HARNESS_CLAUDE,
    HARNESS_CODEX,
    resolve_model_reference,
)
from mael_common.util import now_iso
from mael_domain.agent_store import (
    AGENT_ENDED,
    AGENT_RUNNING,
    AgentStore,
    new_agent_record,
    register_agent,
)

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
            client, harness = self._client_for(
                payload, await self._harness_of(str(payload.get("id", "")))
            )
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
        agent_id = str(payload.get("id", ""))
        error = str(reply.get("error") or "")
        if command == "stop":
            if "no such agent" in error:
                # The agent is gone, so the stop has done its job. A refusal
                # here would leave the record `running`, restored live on every
                # poll and refused again on every Terminate. Nothing was
                # stopped, though: a daemon at another root may still hold the
                # agent, so a later `list` that names it may revive it.
                await self._end(agent_id, revivable=True)
                reply = {"ok": True}
            elif reply.get("ok"):
                await self._end(agent_id)
        if command == "resume":
            if reply.get("ok"):
                row = await self._live_row(client, agent_id)
                await self._register(agent_id, harness, row)
            elif error.endswith("is running") and (
                row := await self._live_row(client, agent_id)
            ):
                # The daemon holds a live agent the table wrote off. Take it
                # back in rather than offer a Resume the daemon always refuses.
                await self._register(agent_id, harness, row)
                reply = {"ok": True, "id": agent_id}
        return reply

    async def _live_row(
        self, client: DaemonClient, agent_id: str
    ) -> dict[str, Any] | None:
        """The row ``client``'s daemon lists for ``agent_id``, if it is live."""
        reply = await client.request({"cmd": "list"})
        return next(
            (
                row
                for row in reply.get("agents", [])
                if isinstance(row, dict)
                and row.get("id") == agent_id
                and not str(row.get("state", "")).startswith("exited")
            ),
            None,
        )

    async def _register(
        self, agent_id: str, harness: str, row: dict[str, Any] | None = None
    ) -> None:
        """Put a live agent in the live set, reviving its record or writing one.

        With no record, ``row`` — its daemon ``list`` entry, or nothing — is all
        there is to go on.
        """
        self._harnesses[agent_id] = harness
        self._misses.pop(agent_id, None)
        stored = await self.agents.read(agent_id)
        if stored is not None:
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
            row or {},
            # Unknown here. `link_agent` resolves the task by session-id
            # reverse-lookup, so nothing reads this yet.
            task_id="",
            harness=harness,
            started_at=self.clock(),
        )

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
        await self._register(
            agent_id, last_seen[0] if last_seen else HARNESS_CLAUDE, row
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
        user stopped. A ``list`` revives only the first: a daemon that still
        reports a stopped row must not undo the stop. An explicit resume may.
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

    async def _harness_of(self, agent_id: str) -> str:
        """The harness that holds ``agent_id``, from the live set or its record.

        The record is the fallback for an agent outside the live set: a stopped
        Codex agent's resume must still reach the Codex daemon.
        """
        if agent_id in self._harnesses:
            return self._harnesses[agent_id]
        stored = await self.agents.read(agent_id) if agent_id else None
        return str((stored or {}).get("harness") or HARNESS_CLAUDE)

    def _client_for(
        self, payload: dict[str, Any], harness: str
    ) -> tuple[DaemonClient, str]:
        if payload.get("cmd") == "start":
            model = str(payload.get("model") or "")
            harness = resolve_model_reference(model).harness
            if harness == HARNESS_CODEX:
                return self.codex, harness
            if harness == HARNESS_CLAUDE:
                return self.claude, harness
            raise ValueError(f"The {harness} daemon is not available.")
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
