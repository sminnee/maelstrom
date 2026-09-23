"""Harness routing at the orchestrator daemon-client seam."""

import asyncio
from dataclasses import dataclass, field

from maelstrom.agent_cost import build_cost_report
from maelstrom.agent_store import InMemoryMilestoneStore
from maelstrom.orchestrator.daemon_bridge import (
    UNCONFIRMED_LISTS_BEFORE_END,
    DaemonRouter,
    ScriptedAsyncDaemonClient,
)

#: A pinned clock, so a record's start and end are assertable.
STAMP = "2026-09-21T10:00:00+00:00"
#: Long enough before ``STAMP`` to be past the grace period.
LONG_AGO = "2026-09-20T10:00:00+00:00"

# The specification point, not a tuning choice: retiring an agent on a single
# unconfirmed list is the bug this branch fixes. Several tests below drive
# `UNCONFIRMED_LISTS_BEFORE_END - 1` lists and would pass on an empty loop if
# the threshold were ever set to 1.
assert UNCONFIRMED_LISTS_BEFORE_END > 1


def stored_agent(started_at: str = LONG_AGO, **fields) -> dict:
    """A running Agent record, old enough to be retired unless said otherwise."""
    return {
        "id": "ag1",
        "harness": "claude",
        "task_session_id": "s1",
        "cwd": "/worktree",
        "model": "claude:opus",
        "mode": "normal",
        "status": "running",
        "started_at": started_at,
        "ended_at": "",
        **fields,
    }


def live_row(agent_id: str, **fields) -> dict:
    """One row as a daemon's ``list`` reports it."""
    return {
        "id": agent_id,
        "state": "idle",
        "session": "s1",
        "cwd": "/worktree",
        "model": "claude:opus",
        "parent": "",
        **fields,
    }


@dataclass
class Agents:
    rows: dict[str, dict] = field(default_factory=dict)

    async def save(self, agent: dict) -> None:
        self.rows[agent["id"]] = agent

    async def list(self) -> list[dict]:
        return list(self.rows.values())

    async def read(self, agent_id: str) -> dict | None:
        return self.rows.get(agent_id)


def test_router_stores_every_started_agent_with_its_harness_and_mode() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        codex = ScriptedAsyncDaemonClient(next_start_id="thread-1")
        agents = Agents()
        router = DaemonRouter(claude, codex, agents, clock=lambda: STAMP)
        codex_reply = await router.request(
            {
                "cmd": "start",
                "cwd": "/worktree",
                "model": "codex:sol",
                "mode": "plan",
                "session": "task-session-1",
                "env": {"MAEL_TASK_ID": "2026-09-16.4.3"},
            }
        )
        codex_row = dict(agents.rows["thread-1"])
        claude_reply = await router.request(
            {
                "cmd": "start",
                "cwd": "/other-worktree",
                "model": "claude:opus",
                "mode": "auto",
            }
        )
        stopped = await router.request({"cmd": "stop", "id": "thread-1"})
        return (
            codex_reply,
            codex_row,
            claude_reply,
            stopped,
            claude.calls,
            codex.calls,
            agents.rows,
        )

    (
        codex_reply,
        codex_row,
        claude_reply,
        stopped,
        claude_calls,
        codex_calls,
        rows,
    ) = asyncio.run(scenario())

    assert codex_reply == {"ok": True, "id": "thread-1"}
    assert codex_row == {
        "id": "thread-1",
        "harness": "codex",
        "task_session_id": "task-session-1",
        "task_id": "2026-09-16.4.3",
        "cwd": "/worktree",
        "model": "codex:sol",
        "mode": "plan",
        "status": "running",
        "started_at": STAMP,
        "ended_at": "",
    }
    assert claude_reply == {"ok": True, "id": "new1"}
    assert stopped == {"ok": True}
    assert claude_calls[0]["cmd"] == "start"
    assert codex_calls[0]["cmd"] == "start"
    assert rows == {
        "new1": {
            "id": "new1",
            "harness": "claude",
            "task_session_id": "",
            "task_id": "",
            "cwd": "/other-worktree",
            "model": "claude:opus",
            "mode": "auto",
            "status": "running",
            "started_at": STAMP,
            "ended_at": "",
        },
        # Stopped, and still on file: the record outlives the agent so its
        # spend can be read afterwards.
        "thread-1": {
            "id": "thread-1",
            "harness": "codex",
            "task_session_id": "task-session-1",
            "task_id": "2026-09-16.4.3",
            "cwd": "/worktree",
            "model": "codex:sol",
            "mode": "plan",
            "status": "ended",
            "started_at": STAMP,
            "ended_at": STAMP,
            # A deliberate stop, so never revivable.
            "swept": False,
        },
    }


def test_list_passes_through_a_live_subagent_of_a_stored_agent() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        codex = ScriptedAsyncDaemonClient()
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "task-session-1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                }
            }
        )
        claude.rows["ag1"] = {
            "id": "ag1",
            "state": "idle",
            "session": "task-session-1",
            "cwd": "/worktree",
            "model": "claude:opus",
            "parent": "",
        }
        claude.rows["ag1.1"] = {
            "id": "ag1.1",
            "state": "idle",
            "session": "",
            "cwd": "/worktree",
            "model": "claude:opus",
            "parent": "ag1",
        }
        router = DaemonRouter(claude, codex, agents)
        return await router.request({"cmd": "list"})

    listed = asyncio.run(scenario())

    ids = {row["id"] for row in listed["agents"]}
    assert ids == {"ag1", "ag1.1"}
    child = next(row for row in listed["agents"] if row["id"] == "ag1.1")
    assert child["parent"] == "ag1"


def test_a_stored_agent_reads_as_exited_only_once_it_is_retired() -> None:
    """A stored id must disappear from ``state`` too, not just from the daemon.

    The server's reconcile loop only exits an agent whose id drops out of
    ``list`` entirely. A stored agent's id never drops out on its own, so the
    row that retires it has to read as ``exited``. Until then it reads ``idle``,
    which is what keeps a working agent off the red fault state — the reported
    bug was this row saying ``exited`` on the first poll after a launch.
    """

    async def scenario():
        agents = Agents(rows={"ag1": stored_agent(task_session_id="task-session-1")})
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(),
            ScriptedAsyncDaemonClient(),
            agents,
            clock=lambda: STAMP,
        )
        first = await router.request({"cmd": "list"})
        for _ in range(UNCONFIRMED_LISTS_BEFORE_END - 1):
            last = await router.request({"cmd": "list"})
        return first, last, agents.rows["ag1"]

    first, last, record = asyncio.run(scenario())

    assert first["agents"] == [
        {
            "id": "ag1",
            "state": "idle",
            "session": "task-session-1",
            "cwd": "/worktree",
            "model": "claude:opus",
            "mode": "normal",
        }
    ]
    assert last["agents"][0]["state"] == "exited"
    assert record["status"] == "ended"
    assert record["ended_at"] == STAMP


def test_an_unconfirmed_row_reports_the_state_the_agent_was_last_seen_in() -> None:
    """A transient miss must not change what the agent is doing.

    The server ends every wait a row does not report as ``awaiting-``, so a
    placeholder state on an agent blocked on a permission ask cancels that ask
    and the user's turn vanishes from the canvas. The last state the daemon
    named is the truthful answer until the sweep gives up.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1", state="awaiting-permission")
        agents = Agents(rows={"ag1": stored_agent()})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        seen = await router.request({"cmd": "list"})
        del claude.rows["ag1"]
        missed = await router.request({"cmd": "list"})
        return seen["agents"][0]["state"], missed["agents"][0]["state"]

    seen, missed = asyncio.run(scenario())

    assert seen == "awaiting-permission"
    assert missed == "awaiting-permission"


def test_an_agent_never_seen_live_reports_idle_while_unconfirmed() -> None:
    """A restored record has no last-seen state, and must not read as exited.

    ``idle`` stands for "the daemon did not say". It is the state that keeps a
    working agent off the red fault state, which is the reported bug.
    """

    async def scenario():
        agents = Agents(rows={"ag1": stored_agent()})
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(),
            ScriptedAsyncDaemonClient(),
            agents,
            clock=lambda: STAMP,
        )
        listed = await router.request({"cmd": "list"})
        return listed["agents"][0]["state"]

    assert asyncio.run(scenario()) == "idle"


def test_list_adopts_a_live_agent_the_store_does_not_know() -> None:
    """`mael add` and `mael agent start` reach the socket, not the store.

    Store-as-truth makes an agent with no record invisible to `list`, so it
    never reaches the canvas. Adopting on read closes that without teaching
    every launch path about the state database.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1", mode="auto")
        agents = Agents()
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        return await router.request({"cmd": "list"}), agents.rows

    listed, rows = asyncio.run(scenario())

    assert [row["id"] for row in listed["agents"]] == ["ag1"]
    assert rows["ag1"] == {
        "id": "ag1",
        "harness": "claude",
        "task_session_id": "s1",
        # Unknown at adoption: `link_agent` resolves the task by session id.
        "task_id": "",
        "cwd": "/worktree",
        "model": "claude:opus",
        "mode": "auto",
        "status": "running",
        "started_at": STAMP,
        "ended_at": "",
    }


def test_an_agent_wrongly_retired_while_alive_keeps_its_own_record() -> None:
    """A live agent whose record says `ended` was written off by mistake.

    Six such records exist on this machine, retired by the sweep this branch
    replaces. The agent is demonstrably alive, so the record goes back to
    `running` — it must not be overwritten as a fresh adoption, which would
    lose the task it was started for and claim it began just now.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1")
        agents = Agents(
            rows={
                "ag1": stored_agent(
                    status="ended",
                    ended_at="2026-09-20T11:00:00+00:00",
                    task_id="2026-09-16.4.3",
                )
            }
        )
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        listed = await router.request({"cmd": "list"})
        return listed, agents.rows["ag1"]

    listed, record = asyncio.run(scenario())

    assert [row["id"] for row in listed["agents"]] == ["ag1"]
    assert record["status"] == "running"
    assert record["ended_at"] == ""
    # What the record was started with survives the revival.
    assert record["task_id"] == "2026-09-16.4.3"
    assert record["started_at"] == LONG_AGO


def test_a_record_ended_before_the_swept_field_existed_is_revivable() -> None:
    """The six wrongly-retired records on this machine carry no ``swept``.

    They were ended by the sweep this branch replaces, which retired an agent on
    one unconfirmed list. A missing field must therefore read as revivable, or
    they stay written off for ever while their agents run.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1")
        agents = Agents(rows={"ag1": stored_agent(status="ended", task_id="t-1")})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        await router.request({"cmd": "list"})
        return agents.rows["ag1"]

    record = asyncio.run(scenario())

    assert record["status"] == "running"
    assert record["task_id"] == "t-1"


def test_a_stopped_agent_is_not_revived_even_if_a_daemon_still_names_it() -> None:
    """A deliberate stop is settled; only a swept-away record is revivable.

    Reviving exists for a record the sweep wrote off while its agent ran. A
    `stop` is the opposite: the user asked for it. A daemon that still reports
    the row — which is what the wrong-daemon-root condition this branch
    survives looks like — must not undo it.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "claude:opus"}
        )
        await router.request({"cmd": "stop", "id": "a1"})
        # The daemon that answers the poll still holds the row, as one pointed
        # at another root would.
        claude.rows["a1"] = live_row("a1")
        listed = await router.request({"cmd": "list"})
        return listed, agents.rows["a1"]

    listed, record = asyncio.run(scenario())

    assert [row["id"] for row in listed["agents"]] == []
    assert record["status"] == "ended"
    assert record["ended_at"] == STAMP


def test_an_adopted_agent_keeps_the_harness_that_holds_it() -> None:
    """A Codex row adopted as `claude` would route its every later command wrong."""

    async def scenario():
        codex = ScriptedAsyncDaemonClient()
        codex.rows["thread-1"] = live_row("thread-1", model="codex:sol")
        agents = Agents()
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), codex, agents, clock=lambda: STAMP
        )
        await router.request({"cmd": "list"})
        # Routed by the harness the adoption recorded, not the default.
        await router.request({"cmd": "stop", "id": "thread-1"})
        return agents.rows, codex.calls

    rows, codex_calls = asyncio.run(scenario())

    assert rows["thread-1"]["harness"] == "codex"
    assert [call["cmd"] for call in codex_calls] == ["list", "stop"]


def test_list_does_not_adopt_a_live_subagent() -> None:
    """A subagent has no record of its own — see Agent record in CONTEXT.md."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1")
        claude.rows["ag1.1"] = live_row("ag1.1", parent="ag1", session="")
        agents = Agents()
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        listed = await router.request({"cmd": "list"})
        return listed, agents.rows

    listed, rows = asyncio.run(scenario())

    assert list(rows) == ["ag1"]
    assert {row["id"] for row in listed["agents"]} == {"ag1", "ag1.1"}


def test_set_mode_updates_the_stored_record() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        router = DaemonRouter(claude, ScriptedAsyncDaemonClient(), agents)
        await router.request(
            {
                "cmd": "start",
                "cwd": "/worktree",
                "model": "claude:opus",
                "mode": "normal",
            }
        )
        reply = await router.request({"cmd": "set-mode", "id": "a1", "mode": "auto"})
        return reply, agents.rows

    reply, rows = asyncio.run(scenario())

    assert reply == {"ok": True}
    assert rows["a1"]["mode"] == "auto"


def test_router_refuses_opencode_daemon_launch() -> None:
    async def scenario():
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), ScriptedAsyncDaemonClient(), Agents()
        )
        return await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "opencode:glm"}
        )

    assert asyncio.run(scenario()) == {
        "ok": False,
        "error": "The opencode daemon is not available.",
    }


# --- an agent's record outlives the agent -----------------------------------


def test_stop_ends_the_record_rather_than_deleting_it() -> None:
    """An agent's spend must survive it, so the row stays and gains an end."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        router = DaemonRouter(claude, ScriptedAsyncDaemonClient(), agents)
        await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "claude:opus"}
        )
        await router.request({"cmd": "stop", "id": "a1"})
        return agents.rows

    rows = asyncio.run(scenario())

    # The row stays on file; only its status moves.
    assert rows["a1"]["status"] == "ended"
    assert rows["a1"]["ended_at"]


def test_stopping_an_agent_the_daemon_no_longer_holds_ends_its_record() -> None:
    """The agent is gone, so the stop has done its job."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.replies["stop"] = [{"error": "no such agent: ag1"}]
        agents = Agents(rows={"ag1": stored_agent()})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        reply = await router.request({"cmd": "stop", "id": "ag1"})
        return reply, agents.rows["ag1"]

    reply, record = asyncio.run(scenario())

    assert reply == {"ok": True}
    # Revivable: nothing was stopped, so a daemon at another root that still
    # holds the agent may bring it back.
    assert record == stored_agent(status="ended", ended_at=STAMP, swept=True)


def test_a_resumed_agent_is_listed_again_after_a_stop() -> None:
    """An explicit resume may undo a stop, where a mere sighting may not."""
    stored = stored_agent(task_id="t-1")

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1")
        agents = Agents(rows={"ag1": dict(stored)})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        await router.request({"cmd": "stop", "id": "ag1"})
        reply = await router.request({"cmd": "resume", "id": "ag1"})
        listed = await router.request({"cmd": "list"})
        return reply, listed, agents.rows["ag1"]

    reply, listed, record = asyncio.run(scenario())

    assert reply == {"ok": True, "id": "ag1"}
    assert [row["id"] for row in listed["agents"]] == ["ag1"]
    # Revived onto its own record: the task and the real start survive.
    assert record == {**stored, "swept": False}


def test_resuming_an_agent_the_daemon_already_runs_registers_it() -> None:
    """The daemon holds a live agent the table wrote off: take it back in."""
    ended = stored_agent(status="ended", ended_at=LONG_AGO, swept=False)

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1")
        agents = Agents(rows={"ag1": dict(ended)})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        reply = await router.request({"cmd": "resume", "id": "ag1"})
        listed = await router.request({"cmd": "list"})
        return reply, listed, agents.rows["ag1"]

    reply, listed, record = asyncio.run(scenario())

    assert reply == {"ok": True, "id": "ag1"}
    assert [row["id"] for row in listed["agents"]] == ["ag1"]
    assert record == {**ended, "status": "running", "ended_at": ""}


def test_resuming_an_agent_with_no_record_writes_one_from_its_row() -> None:
    """`mael agent resume` reaches the daemon without the router, so no record."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.rows["ag1"] = live_row("ag1", state="exited(0)", cwd="/elsewhere")
        agents = Agents()
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        await router.request({"cmd": "resume", "id": "ag1"})
        return agents.rows["ag1"]

    record = asyncio.run(scenario())

    assert record["cwd"] == "/elsewhere"
    assert record["task_session_id"] == "s1"
    assert record["status"] == "running"


def test_a_running_refusal_for_an_agent_the_daemon_does_not_list_passes() -> None:
    """A lost child still `running` in the daemon's record is not a live agent."""
    ended = stored_agent(status="ended", ended_at=LONG_AGO, swept=False)

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.replies["resume"] = [{"error": "agent ag1 is running"}]
        agents = Agents(rows={"ag1": dict(ended)})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        reply = await router.request({"cmd": "resume", "id": "ag1"})
        return reply, agents.rows["ag1"]

    reply, record = asyncio.run(scenario())

    assert reply == {"error": "agent ag1 is running"}
    assert record == ended


def test_a_stopped_codex_agents_resume_goes_to_the_codex_daemon() -> None:
    """Routed by the stored record, since a stop drops it from the live set."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        codex = ScriptedAsyncDaemonClient(next_start_id="thread-1")
        # What the real Codex adapter answers: it cannot resume.
        codex.replies["resume"] = [
            {"ok": False, "error": "Codex does not support 'resume'."}
        ]
        router = DaemonRouter(claude, codex, Agents(), clock=lambda: STAMP)
        await router.request({"cmd": "start", "cwd": "/worktree", "model": "codex:sol"})
        await router.request({"cmd": "stop", "id": "thread-1"})
        reply = await router.request({"cmd": "resume", "id": "thread-1"})
        return reply, claude.calls

    reply, claude_calls = asyncio.run(scenario())

    assert reply == {"ok": False, "error": "Codex does not support 'resume'."}
    assert claude_calls == []


def test_a_started_record_opens_at_running_with_a_start_time() -> None:
    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        router = DaemonRouter(claude, ScriptedAsyncDaemonClient(), agents)
        await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "claude:opus"}
        )
        return agents.rows["a1"]

    row = asyncio.run(scenario())

    assert row["status"] == "running"
    assert row["started_at"]
    assert row["ended_at"] == ""


def test_an_ended_record_is_not_listed_as_a_live_agent() -> None:
    """The table now holds ended agents; ``list`` is about live ones.

    Without this an agent stopped weeks ago would come back as an ``exited``
    row on every poll, and the server's reconcile loop could never retire it.
    """

    async def scenario():
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "s1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                    "status": "ended",
                    "ended_at": "2026-09-20T09:00:00Z",
                },
                "ag2": {
                    "id": "ag2",
                    "harness": "claude",
                    "task_session_id": "s2",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                    "status": "running",
                },
            }
        )
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), ScriptedAsyncDaemonClient(), agents
        )
        return await router.request({"cmd": "list"})

    listed = asyncio.run(scenario())

    assert [row["id"] for row in listed["agents"]] == ["ag2"]


def test_a_record_written_before_status_existed_still_lists() -> None:
    """Every row in an existing database has no ``status``; none may vanish."""

    async def scenario():
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "s1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                }
            }
        )
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(), ScriptedAsyncDaemonClient(), agents
        )
        return await router.request({"cmd": "list"})

    listed = asyncio.run(scenario())

    assert [row["id"] for row in listed["agents"]] == ["ag1"]


def test_seeing_an_agent_again_forgives_its_earlier_misses() -> None:
    """The threshold counts consecutive misses, so a blip must not accumulate.

    A sighting must clear the count, not decrement it: the agent survives a
    whole fresh run of misses afterwards, which only a true reset allows.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        agents = Agents(rows={"ag1": stored_agent()})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        for _ in range(UNCONFIRMED_LISTS_BEFORE_END - 1):
            await router.request({"cmd": "list"})
        claude.rows["ag1"] = live_row("ag1")
        await router.request({"cmd": "list"})
        del claude.rows["ag1"]
        for _ in range(UNCONFIRMED_LISTS_BEFORE_END - 1):
            await router.request({"cmd": "list"})
        return agents.rows

    assert asyncio.run(scenario())["ag1"]["status"] == "running"


def test_a_daemon_that_answers_with_an_error_retires_nothing() -> None:
    """An errored reply carries no agents, which is not the same as none.

    Read as an empty list it writes off every stored agent at once, which is
    the worst reading of a socket that simply failed.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        claude.replies["list"] = [{"error": "daemon unreachable"}] * (
            UNCONFIRMED_LISTS_BEFORE_END + 1
        )
        agents = Agents(rows={"ag1": stored_agent()})
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        for _ in range(UNCONFIRMED_LISTS_BEFORE_END + 1):
            await router.request({"cmd": "list"})
        return agents.rows

    assert asyncio.run(scenario())["ag1"]["status"] == "running"


def test_a_record_younger_than_the_grace_period_is_never_retired() -> None:
    """A just-started agent is the case the bug was reported for.

    The daemon can take a moment to hold a new agent, and every poll in that
    window misses it. Retiring it draws a working agent red seconds after
    launch.
    """

    async def scenario():
        agents = Agents(rows={"ag1": stored_agent(started_at=STAMP)})
        router = DaemonRouter(
            ScriptedAsyncDaemonClient(),
            ScriptedAsyncDaemonClient(),
            agents,
            clock=lambda: STAMP,
        )
        for _ in range(UNCONFIRMED_LISTS_BEFORE_END + 2):
            await router.request({"cmd": "list"})
        return agents.rows

    assert asyncio.run(scenario())["ag1"]["status"] == "running"


def test_a_live_agent_is_not_ended_by_a_list() -> None:
    """The guard: only an agent the daemon has lost is retired."""

    async def scenario():
        claude = ScriptedAsyncDaemonClient()
        agents = Agents(
            rows={
                "ag1": {
                    "id": "ag1",
                    "harness": "claude",
                    "task_session_id": "s1",
                    "cwd": "/worktree",
                    "model": "claude:opus",
                    "mode": "normal",
                    "status": "running",
                }
            }
        )
        claude.rows["ag1"] = {
            "id": "ag1",
            "state": "idle",
            "session": "s1",
            "cwd": "/worktree",
            "model": "claude:opus",
            "parent": "",
        }
        router = DaemonRouter(claude, ScriptedAsyncDaemonClient(), agents)
        await router.request({"cmd": "list"})
        return agents.rows

    assert asyncio.run(scenario())["ag1"]["status"] == "running"


def test_a_stopped_agents_spend_is_still_on_file() -> None:
    """The branch's headline claim, end to end.

    Started, recorded against, stopped — and the ledger still answers. The
    router and the report are otherwise tested apart, each holding one half of
    this.
    """

    async def scenario():
        claude = ScriptedAsyncDaemonClient(next_start_id="a1")
        agents = Agents()
        milestones = InMemoryMilestoneStore()
        router = DaemonRouter(
            claude, ScriptedAsyncDaemonClient(), agents, clock=lambda: STAMP
        )
        await router.request(
            {"cmd": "start", "cwd": "/worktree", "model": "claude:opus"}
        )
        await milestones.record(
            {
                "agent_id": "a1",
                "name": "shipped",
                "at": STAMP,
                "recognised": True,
                "own_tokens": 40_000,
                "subagent_tokens": 12_000,
                "cost_usd": 1.5,
            }
        )
        await router.request({"cmd": "stop", "id": "a1"})
        return agents.rows["a1"], build_cost_report(await milestones.list("a1"))

    record, [report] = asyncio.run(scenario())

    assert record["status"] == "ended"
    assert report["total_tokens"] == 52_000
    assert [stage["name"] for stage in report["stages"]] == ["shipped"]
