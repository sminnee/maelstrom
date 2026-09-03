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
from maelstrom.orchestrator.daemon_bridge import ScriptedAsyncDaemonClient
from maelstrom.orchestrator.server import Orchestrator
from maelstrom.orchestrator.sources import InMemoryWorktreeSource, NotebookTaskSource

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"
NOW = "2026-09-01T00:00:00Z"
PROJECT = "northwind"
WORKTREE_PATH = "/Users/dev/Projects/northwind/northwind-alpha"


def read_fixture(name: str) -> list[dict]:
    lines = (FIXTURES / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


class Harness:
    """An orchestrator over in-memory sources and a scripted agent host."""

    def __init__(self, store, **over):
        self.store = store
        self.version = 0
        self.tasks = NotebookTaskSource(
            store, lambda: [PROJECT], version=lambda: str(self.version)
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
        options = {"task_poll": 0.02, "worktree_poll": 0.02, "agent_poll": 0.02}
        options.update(over)
        self.orch = Orchestrator(
            self.tasks, self.worktrees, self.daemon, clock=lambda: NOW, **options
        )

    def add_task(self, task_id: str, **fields) -> None:
        title = fields.pop("title", task_id)
        model.create(self.store, project=PROJECT, title=title, id=task_id, **fields)
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
    assert world["tasks"]["NORT-7"]["phase"] == "planning"
    assert world["tasks"]["NORT-7"]["actionable"] is True
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
    assert frame["event"]["entity"]["id"] == "NORT-8"
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
    assert replayed[0]["event"]["entity"]["id"] == "NORT-9"
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
    assert agent["taskId"] == "NORT-7"
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
    assert open_items[0]["taskId"] == "NORT-7"


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
