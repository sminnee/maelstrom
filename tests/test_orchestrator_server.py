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
