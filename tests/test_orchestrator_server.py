"""The orchestrator server, end to end over a real WebSocket.

The sources are in-memory and the agent host is scripted from the recorded
fixtures, so every case runs against ``Orchestrator`` as the browser would see
it: hello, snapshot or replay, ready, frames, commands and replies.
"""

import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from maelstrom import task as model
from maelstrom.agent_model import PendingRequest, reply_for_approval
from maelstrom.orchestrator.daemon_bridge import ScriptedAsyncDaemonClient
from maelstrom.orchestrator.server import Orchestrator
from maelstrom.orchestrator.sources import InMemoryWorktreeSource, NotebookTaskSource
from maelstrom.worktree import WorktreeSetup

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"
NOW = "2026-09-01T00:00:00Z"
PROJECT = "northwind"
WORKTREE_PATH = "/Users/dev/Projects/northwind/northwind-alpha"


def read_fixture(name: str) -> list[dict]:
    lines = (FIXTURES / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class Harness:
    """An orchestrator over in-memory sources and a scripted agent host."""

    def __init__(self, store, *, desk=None, projects=(PROJECT,), **over):
        self.store = store
        self.version = 0
        self.projects = list(projects)
        self.tasks = NotebookTaskSource(
            store, lambda: list(self.projects), version=lambda: str(self.version)
        )
        self.worktrees = InMemoryWorktreeSource(
            projects=[{"id": PROJECT, "name": PROJECT, "stackTip": "main"}],
            worktrees=[
                {
                    "id": "northwind-alpha",
                    "project": PROJECT,
                    "nato": "alpha",
                    "path": WORKTREE_PATH,
                    "branch": "feat/orders",
                    "base": "main",
                    "isClosed": False,
                    "dirtyFiles": 0,
                    "localCommits": 0,
                    "prNumber": None,
                    "appUrl": "",
                    "appRunning": False,
                    "sessionCount": 0,
                }
            ],
        )
        self.daemon = ScriptedAsyncDaemonClient()
        self.tasks.open_worktree = lambda project, task, branch: WorktreeSetup(
            path=Path(WORKTREE_PATH), name="alpha", action="reused"
        )
        options = {"task_poll": 0.02, "worktree_poll": 0.02, "agent_poll": 0.02}
        options.update(over)
        self.orch = Orchestrator(
            self.tasks,
            self.worktrees,
            self.daemon,
            clock=lambda: NOW,
            desk=desk,
            **options,
        )

    def add_task(self, task_id: str, *, project: str = PROJECT, **fields) -> None:
        title = fields.pop("title", task_id)
        model.create(self.store, project=project, title=title, id=task_id, **fields)
        self.version += 1


async def recv(ws, timeout: float = 2.0) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def say_hello(ws, resume_from: int | None = None) -> list[dict]:
    """Send hello and return every frame up to and including ``ready``."""
    hello = {"type": "hello"}
    if resume_from is not None:
        hello["resumeFrom"] = resume_from
    await ws.send(json.dumps(hello))
    received = []
    while True:
        message = await recv(ws)
        received.append(message)
        if "ready" in message:
            return received


async def command(ws, cmd: dict, command_id: str = "c1") -> dict:
    await ws.send(json.dumps({"id": command_id, "command": cmd}))
    while True:
        message = await recv(ws)
        if "reply" in message and message["reply"]["id"] == command_id:
            return message["reply"]


async def next_frame(ws, predicate=lambda frame: True, timeout: float = 2.0) -> dict:
    """The next event frame matching ``predicate``, skipping replies."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        message = await recv(ws, max(remaining, 0.01))
        if "seq" in message and predicate(message):
            return message


async def frames_until(ws, predicate, timeout: float = 2.0) -> list[dict]:
    """Every event frame up to and including the first matching ``predicate``."""
    frames = []
    while True:
        frame = await next_frame(ws, timeout=timeout)
        frames.append(frame)
        if predicate(frame):
            return frames


def url(server) -> str:
    return f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


@pytest.fixture
def harness(store):
    return Harness(store)


def run(coro):
    return asyncio.run(coro)


def test_hello_gets_a_snapshot_of_the_world_then_ready(harness):
    harness.add_task("NORT-7", command="plan-task")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                received = await say_hello(ws)
        return received

    received = run(scenario())
    assert [tuple(m)[0] for m in received] == ["seq", "ready"]
    snapshot = received[0]
    assert snapshot["event"]["type"] == "snapshot"
    world = snapshot["event"]["world"]
    assert world["tasks"]["northwind/NORT-7"]["phase"] == "planning"
    assert world["tasks"]["northwind/NORT-7"]["actionable"] is True
    assert world["projects"][PROJECT]["stackTip"] == "main"
    assert world["worktrees"]["northwind-alpha"]["path"] == WORKTREE_PATH
    assert received[1]["ready"]["seq"] == snapshot["seq"]


def test_a_task_edit_arrives_as_an_upsert(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.add_task("NORT-8", follows=["NORT-7"])
                return await next_frame(ws, lambda f: f["event"]["type"] == "upsert")

    frame = run(scenario())
    assert frame["event"]["kind"] == "task"
    assert frame["event"]["entity"]["id"] == "northwind/NORT-8"
    assert frame["event"]["entity"]["actionable"] is False


def test_a_resume_inside_the_ring_replays_and_outside_it_snapshots(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as first:
                ready = (await say_hello(first))[-1]["ready"]["seq"]
                harness.add_task("NORT-9")
                await next_frame(first, lambda f: f["event"]["type"] == "upsert")
            async with connect(url(server)) as resumed:
                replayed = await say_hello(resumed, resume_from=ready)
            async with connect(url(server)) as stale:
                snapshotted = await say_hello(stale, resume_from=-100)
        return replayed, snapshotted

    replayed, snapshotted = run(scenario())
    assert [m["event"]["type"] for m in replayed[:-1]] == ["upsert"]
    assert replayed[0]["event"]["entity"]["id"] == "northwind/NORT-9"
    assert [m["event"]["type"] for m in snapshotted[:-1]] == ["snapshot"]


def test_a_resume_older_than_the_ring_gets_a_snapshot(store):
    harness = Harness(store, ring_size=2)

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as first:
                ready = (await say_hello(first))[-1]["ready"]["seq"]
                for n in range(3):
                    harness.add_task(f"NORT-{n}")
                    await next_frame(first, lambda f: f["event"]["type"] == "upsert")
            async with connect(url(server)) as resumed:
                return await say_hello(resumed, resume_from=ready)

    received = run(scenario())
    assert received[0]["event"]["type"] == "snapshot"


def test_a_second_hello_is_refused_and_an_unknown_command_is_invalid(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await ws.send(json.dumps({"type": "hello"}))
                second = await recv(ws)
                unsupported = await command(
                    ws, {"type": "shaping.start", "project": PROJECT, "brief": "x"}
                )
        return second, unsupported

    second, unsupported = run(scenario())
    assert second["reply"]["ok"] is False
    assert second["reply"]["error"]["code"] == "invalid"
    assert unsupported["ok"] is False
    assert unsupported["error"]["code"] == "invalid"


def test_a_command_missing_a_field_is_refused_not_dropped(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                reply = await command(ws, {"type": "agent.say", "agentId": "ag1"})
                probe = await command(
                    ws, {"type": "agent.say", "agentId": "ag1", "text": "hi"}, "c2"
                )
        return reply, probe

    reply, probe = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid"
    assert probe["ok"] is True


def test_a_message_that_is_not_a_hello_first_is_refused(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await ws.send(
                    json.dumps(
                        {"id": "c1", "command": {"type": "agent.stop", "agentId": "x"}}
                    )
                )
                return await recv(ws)

    reply = run(scenario())
    assert reply["reply"]["ok"] is False


# --- agents ------------------------------------------------------------------


def agent_row(agent_id: str = "ag1", **over) -> dict:
    """What ``mael agent list --json`` prints for one agent."""
    row = {
        "id": agent_id,
        "state": "idle",
        "session": "",
        "cwd": WORKTREE_PATH,
        "model": "",
        "waiting_on": "",
        "last_message": "",
        "cost": "",
    }
    row.update(over)
    return row


def split_at_control_response(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """A fixture cut where the user would answer: the backlog, and what follows.

    The cut is the ``control_response`` to a ``control_request`` seen earlier;
    the init handshake's reply comes first and is not it.
    """
    asked: set[str] = set()
    for i, event in enumerate(events):
        if event["type"] == "control_request":
            asked.add(event.get("request_id", ""))
        if event["type"] == "control_response":
            if (event.get("response") or {}).get("request_id") in asked:
                return events[:i], events[i:]
    return events, []


def test_the_snapshot_carries_an_attached_agents_transcript_and_open_attention(harness):
    harness.add_task("NORT-7")
    session = model.session_id_for(PROJECT, "NORT-7")
    backlog, _ = split_at_control_response(read_fixture("question-unanswered.jsonl"))
    harness.daemon.rows["ag1"] = agent_row(session=session, state="awaiting-question")
    harness.daemon.backlog["ag1"] = backlog

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"], list(harness.daemon.attached)

    snapshot, attached = run(scenario())
    assert attached == ["ag1"]
    agent = snapshot["world"]["agents"]["ag1"]
    assert agent["state"] == "awaiting-question"
    assert agent["taskId"] == "northwind/NORT-7"
    assert agent["project"] == PROJECT
    assert agent["worktreeId"] == "northwind-alpha"
    assert agent["pendingRequestId"] == "2ba1273d-d878-4923-ba21-31faa1067613"
    items = snapshot["transcripts"]["ag1"]["items"]
    assert [i["type"] for i in items][-1] == "question"
    assert snapshot["transcripts"]["ag1"]["truncatedBefore"] is False
    open_items = [
        a for a in snapshot["world"]["attention"].values() if a["clearedAt"] is None
    ]
    assert [a["kind"] for a in open_items] == ["question"]
    assert open_items[0]["taskId"] == "northwind/NORT-7"


def test_a_backlog_the_size_of_the_hosts_window_is_marked_truncated(harness):
    from maelstrom.agent_model import RECENT_LIMIT

    events = read_fixture("normal-turn.jsonl")
    padding = [{"type": "rate_limit_event"}] * (RECENT_LIMIT - len(events))
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.backlog["ag1"] = padding + events

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"]

    snapshot = run(scenario())
    assert snapshot["transcripts"]["ag1"]["truncatedBefore"] is True


def test_a_backlog_one_short_of_the_window_is_not_marked_truncated(harness):
    from maelstrom.agent_model import RECENT_LIMIT

    events = read_fixture("normal-turn.jsonl")
    padding = [{"type": "rate_limit_event"}] * (RECENT_LIMIT - 1 - len(events))
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.backlog["ag1"] = padding + events

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"]

    snapshot = run(scenario())
    assert snapshot["transcripts"]["ag1"]["truncatedBefore"] is False


async def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.01)


def test_an_attach_the_host_refuses_is_retried_on_the_next_reconciliation(harness):
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.attach_failures.add("ag1")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await wait_until(lambda: harness.daemon.attached == ["ag1"])
                return harness.orch.log.state["errors"]

    errors = run(scenario())
    assert [e["agentId"] for e in errors] == ["ag1"]
    assert "attach refused" in errors[0]["message"]


def test_a_live_event_arrives_as_a_transcript_append(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                for event in read_fixture("normal-turn.jsonl"):
                    harness.daemon.push("ag1", event)
                appended = await next_frame(
                    ws, lambda f: f["event"]["type"] == "transcript.append"
                )
                idle = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "idle"
                        and f["event"]["entity"]["costUsd"] > 0
                    ),
                )
        return appended, idle

    appended, idle = run(scenario())
    assert appended["event"]["agentId"] == "ag1"
    assert appended["event"]["item"]["type"] == "system"
    assert idle["event"]["entity"]["lastMessage"] == "Hello there, friend"


def test_the_exit_marker_marks_the_agent_exited_and_raises_attention(harness):
    from maelstrom.agent_model import AGENT_EXITED

    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.daemon.push("ag1", {"type": AGENT_EXITED, "exit_code": 2})
                harness.daemon.end_stream("ag1")
                return await frames_until(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "exited"
                    ),
                )

    frames = run(scenario())
    assert frames[-1]["event"]["entity"]["exitCode"] == 2
    attention = [
        f["event"]["entity"] for f in frames if f["event"].get("kind") == "attention"
    ]
    assert [a["kind"] for a in attention] == ["agent_exited"]
    assert attention[0]["agentId"] == "ag1"


def test_reconciliation_attaches_new_agents_and_retires_gone_ones(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.daemon.rows["ag2"] = agent_row("ag2", cwd="/private/tmp")
                appeared = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["id"] == "ag2"
                    ),
                )
                await wait_until(lambda: "ag2" in harness.daemon.attached)
                attached = list(harness.daemon.attached)
                del harness.daemon.rows["ag2"]
                harness.daemon.end_stream("ag2")
                gone = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "exited"
                    ),
                )
        return appeared, attached, gone

    appeared, attached, gone = run(scenario())
    assert appeared["event"]["entity"]["taskId"] == ""
    assert appeared["event"]["entity"]["worktreeId"] == ""
    assert appeared["event"]["entity"]["phase"] == "executing"
    assert attached == ["ag2"]
    assert gone["event"]["entity"]["exitCode"] == 0


def test_a_row_reporting_an_exit_the_stream_never_showed_is_applied(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                return await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "exited"
                    ),
                )

    frame = run(scenario())
    assert frame["event"]["entity"]["exitCode"] == 1


def test_an_agent_that_comes_back_under_its_old_id_is_revived(harness):
    """A resumed agent keeps its id, so the row that returns is the same agent."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await wait_until(lambda: "ag1" in harness.daemon.attached)
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "exited"
                    ),
                )
                harness.daemon.rows["ag1"]["state"] = "idle"
                back = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "idle"
                    ),
                )
                await wait_until(
                    lambda: (
                        harness.daemon.calls.count({"cmd": "attach", "id": "ag1"}) == 2
                    )
                )
                return back

    frame = run(scenario())
    # The re-attached backlog re-normalises into the same transcript, which
    # still holds the turns from before the exit.
    assert frame["event"]["entity"]["exitCode"] is None


def test_a_revived_agent_loses_the_attention_its_exit_raised(harness):
    """The exit is over, so the item asking someone to look at it must go."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                raised = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "attention"
                    ),
                )
                harness.daemon.rows["ag1"]["state"] = "idle"
                cleared = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "attention"
                        and f["event"]["entity"]["clearedAt"] is not None
                    ),
                )
                return raised, cleared

    raised, cleared = run(scenario())
    assert raised["event"]["entity"]["kind"] == "agent_exited"
    assert cleared["event"]["entity"]["id"] == raised["event"]["entity"]["id"]


def test_a_revived_agent_links_to_the_task_that_arrived_while_it_was_gone(harness):
    """An agent away during a task's arrival must still find it on the way back."""
    session = model.session_id_for(PROJECT, "NORT-7")
    harness.daemon.rows["ag1"] = agent_row(session=session)

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "exited"
                    ),
                )
                # The task appears only while the agent is gone, so the link it
                # held at exit is stale by the time it comes back.
                harness.add_task("NORT-7")
                harness.version += 1
                await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert" and f["event"]["kind"] == "task"
                    ),
                )
                harness.daemon.rows["ag1"]["state"] = "idle"
                # Every agent frame from the moment it comes back. The revive
                # pass must carry the link itself: a later poll fixing it leaves
                # a stale link on screen in between.
                frames = []
                while True:
                    frame = await next_frame(
                        ws,
                        lambda f: (
                            f["event"]["type"] == "upsert"
                            and f["event"]["kind"] == "agent"
                        ),
                    )
                    frames.append(frame["event"]["entity"])
                    if frame["event"]["entity"]["taskId"]:
                        return frames

    frames = run(scenario())
    # The first frame of the revive already names the task, so nothing renders
    # an agent that has come back with no task on it.
    assert frames[0]["taskId"] == "northwind/NORT-7"


# --- commands ----------------------------------------------------------------


def waiting_on(harness, fixture: str, **row) -> tuple[list[dict], list[dict]]:
    """Park ``ag1`` in the wait ``fixture`` records, and return the cut.

    The host knows what the agent waits on, so the fake is told too: that is
    what it builds its echoed reply from, as the real daemon builds one from
    its own ``PendingRequest``.
    """
    backlog, rest = split_at_control_response(read_fixture(fixture))
    harness.daemon.rows["ag1"] = agent_row(**row)
    harness.daemon.backlog["ag1"] = backlog
    harness.daemon.pending["ag1"] = pending_from(backlog)
    return backlog, rest


def pending_from(events: list[dict]) -> PendingRequest:
    """The request the last ``can_use_tool`` in ``events`` is waiting on."""
    asks = [
        e
        for e in events
        if e["type"] == "control_request"
        and (e.get("request") or {}).get("subtype") == "can_use_tool"
    ]
    ask = asks[-1]
    request = ask["request"]
    return PendingRequest(
        request_id=ask.get("request_id", ""),
        tool_name=request.get("tool_name", ""),
        input=request.get("input") or {},
        description=request.get("description", "") or "",
    )


def pending_of(snapshot: dict) -> str:
    return snapshot["world"]["agents"]["ag1"]["pendingRequestId"]


async def command_with_frames(
    ws, cmd: dict, command_id: str = "c1", *, until=None
) -> tuple[dict, list[dict]]:
    """Send a command; return its reply and the frames it caused.

    A command that writes to the child is a pure relay: the host echoes what
    it wrote, so the consequences arrive on the attach stream and may follow
    the reply rather than precede it. ``until`` says which frame ends the
    wait; with none, only the frames before the reply are collected.
    """
    await ws.send(json.dumps({"id": command_id, "command": cmd}))
    frames: list[dict] = []
    reply = None
    while True:
        message = await recv(ws)
        if "reply" in message and message["reply"]["id"] == command_id:
            reply = message["reply"]
            if until is None or not reply["ok"]:
                return reply, frames
            continue
        if "seq" in message:
            frames.append(message)
            if reply is not None and until(frames):
                return reply, frames


def has_agent_state(state: str):
    """``until`` for a command that leaves the agent in ``state``."""

    def done(frames: list[dict]) -> bool:
        agents = entities_of(frames, "agent")
        return bool(agents) and agents[-1]["state"] == state

    return done


def has_a_transcript_append(frames: list[dict]) -> bool:
    return any(f["event"]["type"] == "transcript.append" for f in frames)


def entities_of(frames: list[dict], kind: str) -> list[dict]:
    return [
        f["event"]["entity"]
        for f in frames
        if f["event"].get("kind") == kind and f["event"]["type"] == "upsert"
    ]


def updates_of(frames: list[dict]) -> list[dict]:
    return [f["event"] for f in frames if f["event"]["type"] == "transcript.update"]


def test_approve_reaches_the_host_and_resolves_the_wait(harness):
    waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                snapshot = (await say_hello(ws))[0]["event"]
                return await command_with_frames(
                    ws,
                    {
                        "type": "agent.approve",
                        "agentId": "ag1",
                        "requestId": pending_of(snapshot),
                    },
                    until=has_agent_state("processing"),
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c1", "ok": True, "result": {}}
    assert {"cmd": "approve", "id": "ag1"} in harness.daemon.calls
    agent = entities_of(frames, "agent")[-1]
    assert agent["state"] == "processing"
    assert agent["pendingRequestId"] is None
    assert updates_of(frames)[0]["patch"] == {"decision": "allow"}
    cleared = [a for a in entities_of(frames, "attention") if a["clearedAt"]]
    assert len(cleared) == 1


def test_deny_sends_the_reason_and_records_it(harness):
    waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                snapshot = (await say_hello(ws))[0]["event"]
                return await command_with_frames(
                    ws,
                    {
                        "type": "agent.deny",
                        "agentId": "ag1",
                        "requestId": pending_of(snapshot),
                        "reason": "not on this network",
                    },
                    until=has_agent_state("processing"),
                )

    reply, frames = run(scenario())
    assert reply["ok"] is True
    assert {
        "cmd": "deny",
        "id": "ag1",
        "reason": "not on this network",
    } in harness.daemon.calls
    assert updates_of(frames)[0]["patch"] == {
        "decision": "deny",
        "reason": "not on this network",
    }


def test_answer_sends_the_answers_map_and_files_it_on_the_question(harness):
    waiting_on(harness, "question-unanswered.jsonl")
    answers = {"Which colour do you prefer?": "Blue"}

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                snapshot = (await say_hello(ws))[0]["event"]
                return await command_with_frames(
                    ws,
                    {
                        "type": "agent.answer",
                        "agentId": "ag1",
                        "requestId": pending_of(snapshot),
                        "answers": answers,
                    },
                    until=has_agent_state("processing"),
                )

    reply, frames = run(scenario())
    assert reply["ok"] is True
    assert {"cmd": "answer", "id": "ag1", "answers": answers} in harness.daemon.calls
    assert updates_of(frames)[0]["patch"] == {"answers": answers}


def test_say_sends_the_text_and_shows_it_as_a_user_message(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws,
                    {"type": "agent.say", "agentId": "ag1", "text": "also the README"},
                    until=has_a_transcript_append,
                )

    reply, frames = run(scenario())
    assert reply["ok"] is True
    assert {
        "cmd": "say",
        "id": "ag1",
        "text": "also the README",
    } in harness.daemon.calls
    item = next(
        f["event"]["item"] for f in frames if f["event"]["type"] == "transcript.append"
    )
    assert item["type"] == "message"
    assert item["role"] == "user"
    assert item["markdown"] == "also the README"


def test_a_wait_answered_outside_the_server_clears_in_the_world(harness):
    """The case the old design could not handle: it only learned of its own answers.

    ``mael agent approve`` writes the reply, and the host echoes it onto the
    stream. The server holds no opinion about how a wait is answered, so the
    UI clears either way.
    """
    backlog, _ = waiting_on(harness, "permission-request.jsonl")
    pending = harness.daemon.pending["ag1"]

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                snapshot = (await say_hello(ws))[0]["event"]
                assert pending_of(snapshot) == pending.request_id
                # Nobody asked this server; the answer was made elsewhere.
                harness.daemon.push("ag1", reply_for_approval(pending))
                frame = await next_frame(
                    ws,
                    lambda f: (
                        f["event"]["type"] == "upsert"
                        and f["event"]["kind"] == "agent"
                        and f["event"]["entity"]["state"] == "processing"
                    ),
                )
                return frame["event"]["entity"]

    agent = run(scenario())
    assert agent["pendingRequestId"] is None
    assert agent["waitingOn"] == ""


def test_stop_reaches_the_host_and_marks_the_agent_exited_cleanly(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws, {"type": "agent.stop", "agentId": "ag1"}
                )

    reply, frames = run(scenario())
    assert reply["ok"] is True
    assert {"cmd": "stop", "id": "ag1"} in harness.daemon.calls
    agent = entities_of(frames, "agent")[-1]
    assert agent["state"] == "exited"
    assert agent["exitCode"] == 0


def test_a_refused_command_replies_with_its_code_and_publishes_nothing(harness):
    waiting_on(harness, "question-unanswered.jsonl")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                ready = (await say_hello(ws))[-1]["ready"]["seq"]
                unknown = await command(
                    ws,
                    {"type": "agent.approve", "agentId": "nobody", "requestId": "x"},
                    "c1",
                )
                stale = await command(
                    ws,
                    {"type": "agent.approve", "agentId": "ag1", "requestId": "old"},
                    "c2",
                )
                wrong = await command(
                    ws,
                    {
                        "type": "agent.approve",
                        "agentId": "ag1",
                        "requestId": "2ba1273d-d878-4923-ba21-31faa1067613",
                    },
                    "c3",
                )
                return ready, harness.orch.log.seq, unknown, stale, wrong

    ready, after, unknown, stale, wrong = run(scenario())
    assert unknown["error"]["code"] == "unknown_id"
    assert stale["error"]["code"] == "stale_request"
    assert wrong["error"]["code"] == "wrong_wait_kind"
    assert after == ready
    host_calls = [
        c["cmd"] for c in harness.daemon.calls if c["cmd"] not in ("list", "attach")
    ]
    assert host_calls == []


def test_a_host_refusal_maps_to_the_matching_code(harness):
    waiting_on(harness, "permission-request.jsonl")
    harness.daemon.replies["approve"] = [{"error": "agent ag1 is not waiting"}]

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                snapshot = (await say_hello(ws))[0]["event"]
                return await command(
                    ws,
                    {
                        "type": "agent.approve",
                        "agentId": "ag1",
                        "requestId": pending_of(snapshot),
                    },
                )

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "not_waiting"


def test_launch_starts_an_agent_for_the_task_and_moves_it_in_progress(harness):
    harness.add_task(
        "NORT-7",
        command="plan-task",
        mode="auto",
        model="claude-opus-5",
        content="Do it.",
    )
    session = model.session_id_for(PROJECT, "NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws, {"type": "agent.launch", "taskId": "northwind/NORT-7"}
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c1", "ok": True, "result": {"agentId": "new1"}}
    start = next(c for c in harness.daemon.calls if c["cmd"] == "start")
    assert start["cwd"] == WORKTREE_PATH
    assert start["prompt"] == "/plan-task NORT-7\n\nDo it."
    assert start["mode"] == "auto"
    assert start["model"] == "claude-opus-5"
    assert start["session"] == session
    assert start["env"] == {
        "MAEL_TASK_ID": "NORT-7",
        "MAEL_TASK_PARENT": "NORT-7",
        "MAEL_TASK_SESSION_ID": session,
    }
    assert model.load(harness.store, PROJECT, "NORT-7").status == "in-progress"
    assert entities_of(frames, "task")[-1]["status"] == "in-progress"
    agent = entities_of(frames, "agent")[-1]
    assert agent["id"] == "new1"
    assert agent["taskId"] == "northwind/NORT-7"
    assert agent["worktreeId"] == "northwind-alpha"
    assert agent["phase"] == "planning"
    # A task launched from the UI joins the desk.
    assert entities_of(frames, "desk")[-1]["id"] == "task:northwind/NORT-7"


def test_a_launch_the_host_refuses_rolls_the_task_back_to_todo(harness):
    harness.add_task("NORT-7")
    harness.daemon.replies["start"] = [
        {"error": "could not start claude: no such file"}
    ]

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(
                    ws, {"type": "agent.launch", "taskId": "northwind/NORT-7"}
                )

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid"
    assert "no such file" in reply["error"]["message"]
    assert model.load(harness.store, PROJECT, "NORT-7").status == "todo"


def test_a_launch_blocked_by_a_failed_sync_leaves_the_task_todo(store):
    from maelstrom.worktree import SyncResult

    harness = Harness(store)
    harness.add_task("NORT-7")
    harness.tasks.open_worktree = lambda project, task, branch: WorktreeSetup(
        path=Path(WORKTREE_PATH),
        name="alpha",
        action="recycled",
        sync=SyncResult(success=False, branch=branch, message="rebase conflict"),
    )

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(
                    ws, {"type": "agent.launch", "taskId": "northwind/NORT-7"}
                )

    reply = run(scenario())
    assert reply["ok"] is False
    assert "rebase conflict" in reply["error"]["message"]
    assert model.load(harness.store, PROJECT, "NORT-7").status == "todo"
    assert not [c for c in harness.daemon.calls if c["cmd"] == "start"]


# -- the desk --


def test_two_projects_may_share_a_notebook_id(store):
    harness = Harness(store, projects=("northwind", "askastro"))
    harness.add_task("2026-06-11.1", project="northwind")
    harness.add_task("2026-06-11.1", project="askastro")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return await say_hello(ws)

    world = run(scenario())[0]["event"]["world"]
    assert set(world["tasks"]) == {
        "northwind/2026-06-11.1",
        "askastro/2026-06-11.1",
    }


def test_desk_add_publishes_an_upsert_then_replies(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws, {"type": "desk.add", "id": "task:northwind/NORT-7"}
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c1", "ok": True, "result": {}}
    entry = entities_of(frames, "desk")[-1]
    assert entry["id"] == "task:northwind/NORT-7"
    assert entry["addedAt"] == NOW


def test_set_status_moves_the_task_then_publishes_and_replies(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws,
                    {
                        "type": "task.setStatus",
                        "taskId": "northwind/NORT-7",
                        "status": "done",
                    },
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c1", "ok": True, "result": {}}
    assert entities_of(frames, "task")[-1]["status"] == "done"
    assert model.load(harness.store, PROJECT, "NORT-7").status == "done"


def test_update_writes_only_the_fields_it_was_given(harness):
    harness.add_task("NORT-7", branch="feat/orders")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command_with_frames(
                    ws,
                    {
                        "type": "task.update",
                        "taskId": "northwind/NORT-7",
                        "fields": {"title": "Export the orders"},
                    },
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c1", "ok": True, "result": {}}
    assert entities_of(frames, "task")[-1]["title"] == "Export the orders"
    task = model.load(harness.store, PROJECT, "NORT-7")
    assert task.title == "Export the orders"
    assert task.branch == "feat/orders"


def test_a_write_to_a_task_the_notebook_lost_is_unknown_id(harness):
    """The world knew the task; the notebook no longer holds it."""
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                model.delete(harness.store, PROJECT, "NORT-7")
                return await command(
                    ws,
                    {
                        "type": "task.update",
                        "taskId": "northwind/NORT-7",
                        "fields": {"title": "Gone"},
                    },
                )

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "unknown_id"


def test_desk_remove_publishes_a_remove_then_replies(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.add", "id": "task:northwind/NORT-7"})
                return await command_with_frames(
                    ws,
                    {"type": "desk.remove", "id": "task:northwind/NORT-7"},
                    command_id="c2",
                )

    reply, frames = run(scenario())
    assert reply == {"id": "c2", "ok": True, "result": {}}
    removes = [f["event"] for f in frames if f["event"].get("kind") == "desk"]
    assert removes[-1] == {
        "type": "remove",
        "kind": "desk",
        "id": "task:northwind/NORT-7",
    }


def test_a_second_desk_add_is_ok_and_publishes_nothing(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.add", "id": "task:northwind/NORT-7"})
                before = harness.orch.log.seq
                reply = await command(
                    ws,
                    {"type": "desk.add", "id": "task:northwind/NORT-7"},
                    command_id="c2",
                )
                return reply, before, harness.orch.log.seq

    reply, before, after = run(scenario())
    assert reply["ok"] is True
    assert after == before


def test_a_task_deleted_from_the_notebook_leaves_the_desk(harness):
    harness.add_task("NORT-7")
    # A second task keeps the project in the reading. A project with no tasks
    # at all is indistinguishable from one the scan missed, so its desk
    # entries are kept.
    harness.add_task("NORT-8")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.add", "id": "task:northwind/NORT-7"})
                model.delete(harness.store, PROJECT, "NORT-7")
                harness.version += 1
                await next_frame(
                    ws,
                    lambda f: (
                        f["event"].get("kind") == "desk"
                        and f["event"]["type"] == "remove"
                    ),
                )
                return harness.orch.log.state["world"]["desk"]

    assert run(scenario()) == {}


def test_a_live_agent_joins_the_desk(harness):
    """Running work is always drawn, so the server puts it on the desk itself."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return harness.orch.log.state["world"]["desk"]

    assert list(run(scenario())) == ["agent:ag1"]


def test_an_agent_with_a_task_joins_the_desk_under_its_task(harness):
    harness.add_task("NORT-7")
    session = model.session_id_for(PROJECT, "NORT-7")
    harness.daemon.rows["ag1"] = agent_row(session=session)

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return harness.orch.log.state["world"]["desk"]

    assert list(run(scenario())) == ["task:northwind/NORT-7"]


def test_the_desk_entry_outlives_the_agent(harness):
    """Only a dismiss clears an entry, so a stopped agent stays on the canvas."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                del harness.daemon.rows["ag1"]
                await harness.orch.refresh_agents()
                world = harness.orch.log.state["world"]
                return world["desk"], world["agents"]["ag1"]["state"]

    desk, state = run(scenario())
    assert state == "exited"
    assert list(desk) == ["agent:ag1"]


def test_a_second_agent_poll_publishes_nothing(harness):
    """The 2s poll must not thrash the desk file, nor the clients."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                before = harness.orch.log.seq
                await harness.orch.refresh_agents()
                return before, harness.orch.log.seq

    before, after = run(scenario())
    assert after == before


def test_a_dismissed_entry_is_not_re_added_by_the_next_poll(harness):
    """A dismiss is the user's decision, so the poll must not undo it."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.remove", "id": "agent:ag1"})
                await harness.orch.refresh_agents()
                return harness.orch.log.state["world"]["desk"]

    assert run(scenario()) == {}


def test_an_agent_already_exited_when_it_is_adopted_does_not_join_the_desk(harness):
    """Only running work joins by itself; a dead agent needs the user to ask."""
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return harness.orch.log.state["world"]["desk"]

    assert run(scenario()) == {}


def test_a_free_agent_entry_the_host_has_forgotten_is_dropped_at_load(store):
    """A restart rebuilds the agents, so an entry naming none can never draw."""
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()
    desk.save(
        {
            "agent:gone": {"id": "agent:gone", "addedAt": NOW},
            "task:northwind/NORT-7": {"id": "task:northwind/NORT-7", "addedAt": NOW},
        }
    )
    harness = Harness(store, desk=desk)
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"]["world"]["desk"]

    assert list(run(scenario())) == ["task:northwind/NORT-7"]


def test_an_agent_adopted_at_start_keeps_its_entry_through_the_load(store):
    """The load merges onto the world, so the join that ran first is not lost."""
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()
    desk.save(
        {"task:northwind/NORT-7": {"id": "task:northwind/NORT-7", "addedAt": NOW}}
    )
    harness = Harness(store, desk=desk)
    harness.add_task("NORT-7")
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                world = (await say_hello(ws))[0]["event"]["world"]
                return world["desk"], list(world["tasks"])

    desk, tasks = run(scenario())
    assert tasks == ["northwind/NORT-7"]
    assert sorted(desk) == ["agent:ag1", "task:northwind/NORT-7"]


def test_a_free_agent_entry_whose_agent_is_live_survives_the_load(store):
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()
    desk.save({"agent:ag1": {"id": "agent:ag1", "addedAt": NOW}})
    harness = Harness(store, desk=desk)
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"]["world"]["desk"]

    assert list(run(scenario())) == ["agent:ag1"]


def test_the_desk_survives_a_restart(store):
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()

    async def scenario(harness):
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.add", "id": "task:northwind/NORT-7"})

    first = Harness(store, desk=desk)
    first.add_task("NORT-7")
    run(scenario(first))

    second = Harness(store, desk=desk)

    async def read_back():
        async with second.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                return (await say_hello(ws))[0]["event"]["world"]["desk"]

    assert list(run(read_back())) == ["task:northwind/NORT-7"]


def test_a_project_the_scan_misses_keeps_its_desk_entries(store):
    """A project briefly absent must not cost the user the desk it holds."""
    harness = Harness(store, projects=("northwind", "askastro"))
    harness.add_task("NORT-7", project="northwind")
    harness.add_task("ASK-1", project="askastro")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await command(ws, {"type": "desk.add", "id": "task:askastro/ASK-1"})
                # The project disappears from the scan, as an unmounted volume
                # or a renamed directory would make it.
                harness.projects = ["northwind"]
                harness.version += 1
                await harness.orch.refresh_tasks()
                return harness.orch.log.state["world"]["desk"]

    assert list(run(scenario())) == ["task:askastro/ASK-1"]


def test_resume_reaches_the_host_for_an_exited_agent(harness):
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(ws, {"type": "agent.resume", "agentId": "ag1"})

    reply = run(scenario())
    assert reply["ok"] is True
    assert {"cmd": "resume", "id": "ag1"} in harness.daemon.calls


def test_resume_of_an_agent_that_is_running_is_refused(harness):
    """Validation catches it before the host, so nothing is spawned twice."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(ws, {"type": "agent.resume", "agentId": "ag1"})

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid"


def test_a_host_that_says_the_agent_is_running_refuses_the_resume(harness):
    """The host is the authority: it may know of a child the world does not."""
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")
    harness.daemon.replies["resume"] = [{"error": "agent ag1 is running"}]

    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(ws, {"type": "agent.resume", "agentId": "ag1"})

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid"
    assert "is running" in reply["error"]["message"]


def test_resume_of_an_agent_the_world_does_not_know_is_refused(harness):
    async def scenario():
        async with harness.orch.serving("127.0.0.1", 0) as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(ws, {"type": "agent.resume", "agentId": "nope"})

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "unknown_id"
