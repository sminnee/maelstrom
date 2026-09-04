"""The orchestrator server, end to end over a real port.

The sources are in-memory and the agent host is scripted from the recorded
fixtures, so every case runs against ``Orchestrator`` as the browser would see
it: REST reads, change notices, and commands with their replies.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
import pytest

from maelstrom import task as model
from maelstrom.agent_model import PendingRequest, reply_for_approval
from maelstrom.orchestrator.daemon_bridge import ScriptedAsyncDaemonClient
from maelstrom.orchestrator.routes import build_app, serving
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

    def serving(self):
        """Serve the orchestrator on a free port for the length of the block."""
        return serving(build_app(self.orch), "127.0.0.1", 0)

    @asynccontextmanager
    async def client(self):
        """Serve, and yield an :class:`Api` client on the bound port."""
        async with self.serving() as port:
            async with aiohttp.ClientSession(
                base_url=f"http://127.0.0.1:{port}"
            ) as session:
                yield Api(session)


@asynccontextmanager
async def connect(url: str):
    """One WebSocket client on ``url``, closed with the block."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            yield ws


class Reply:
    """One HTTP reply: its status, its parsed body, and its headers."""

    def __init__(self, status: int, body, headers) -> None:
        self.status = status
        self.body = body
        self.headers = headers


class Api:
    """The server as the browser reaches it: GETs, POSTs, the notice stream, the socket."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def get(self, path: str, **headers) -> Reply:
        async with self.session.get(path, headers=headers) as response:
            return await _reply(response)

    async def get_json(self, path: str):
        """The body of a GET that must succeed."""
        reply = await self.get(path)
        assert reply.status == 200, (reply.status, reply.body)
        return reply.body

    async def post(self, path: str, body=None, *, raw: bytes | None = None) -> Reply:
        kwargs = {"data": raw} if raw is not None else {"json": body or {}}
        async with self.session.post(path, **kwargs) as response:
            return await _reply(response)

    async def patch(self, path: str, body=None) -> Reply:
        async with self.session.patch(path, json=body or {}) as response:
            return await _reply(response)

    async def delete(self, path: str) -> Reply:
        async with self.session.delete(path) as response:
            return await _reply(response)

    @asynccontextmanager
    async def events(self):
        """The change-notice stream, open for the block."""
        async with self.session.get("/api/events") as response:
            assert response.status == 200
            assert response.content_type == "text/event-stream"
            yield EventStream(response)

    def ws(self, path: str = "/"):
        return self.session.ws_connect(path)

    def transcript_stream(self, agent_id: str, from_seq: int | None = None):
        """A socket on one agent's transcript, resuming from ``from_seq`` when given."""
        query = f"?from={from_seq}" if from_seq is not None else ""
        return self.session.ws_connect(f"/api/agents/{agent_id}/stream{query}")


async def ws_next(ws, predicate=lambda message: True, timeout: float = 2.0) -> dict:
    """The next JSON message on ``ws`` matching ``predicate``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        message = json.loads(
            await asyncio.wait_for(ws.receive_str(), max(remaining, 0.01))
        )
        if predicate(message):
            return message


def is_event(kind: str):
    """A live frame carrying an event of ``kind``."""
    return lambda m: "seq" in m and m.get("event", {}).get("type") == kind


async def _reply(response: aiohttp.ClientResponse) -> Reply:
    text = await response.text()
    try:
        body = json.loads(text) if text else None
    except json.JSONDecodeError:
        body = text
    return Reply(response.status, body, response.headers)


class EventStream:
    """A reader of server-sent events: ``next`` skips pings."""

    def __init__(self, response: aiohttp.ClientResponse) -> None:
        self.response = response

    async def next(self, kind: str | None = None, timeout: float = 2.0) -> dict:
        """The next event, or the next of ``kind``."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            event = await asyncio.wait_for(self._read(), max(remaining, 0.01))
            if kind is None or event["event"] == kind:
                return event

    async def change(
        self, kind: str, entity_id: str | None = None, timeout: float = 2.0
    ) -> list[str]:
        """The ids of the next ``change`` notice for ``kind`` (naming ``entity_id``)."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            event = await self.next("change", max(remaining, 0.01))
            data = event["data"]
            if data["kind"] != kind:
                continue
            if entity_id is None or entity_id in data["ids"]:
                return data["ids"]

    async def _read(self) -> dict:
        name = None
        data: list[str] = []
        while True:
            raw = await self.response.content.readline()
            if not raw:
                raise EOFError("the notice stream closed")
            line = raw.decode().rstrip("\n")
            if line == "":
                if name is not None:
                    return {"event": name, "data": json.loads("\n".join(data))}
                continue
            if line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value.lstrip()
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)


async def settled(
    stream: EventStream,
    api: Api,
    kind: str,
    path: str,
    predicate,
    timeout: float = 2.0,
):
    """GET ``path`` after each ``kind`` notice, until ``predicate`` holds of the body.

    The notice says something about the kind changed; the GET says what. A
    change that takes several events lands as several notices, so the read
    repeats until the body is what the test waits for, or ``timeout`` passes
    and the last body seen names what the predicate rejected.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    body = None
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"{path} never settled; last body: {body!r}")
        await stream.change(kind, timeout=remaining)
        body = await api.get_json(path)
        if predicate(body):
            return body


async def recv(ws, timeout: float = 2.0) -> dict:
    return json.loads(await asyncio.wait_for(ws.receive_str(), timeout))


async def say_hello(ws, resume_from: int | None = None) -> list[dict]:
    """Send hello and return every frame up to and including ``ready``."""
    hello = {"type": "hello"}
    if resume_from is not None:
        hello["resumeFrom"] = resume_from
    await ws.send_str(json.dumps(hello))
    received = []
    while True:
        message = await recv(ws)
        received.append(message)
        if "ready" in message:
            return received


async def command(ws, cmd: dict, command_id: str = "c1") -> dict:
    await ws.send_str(json.dumps({"id": command_id, "command": cmd}))
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


def url(port: int) -> str:
    return f"ws://127.0.0.1:{port}"


@pytest.fixture
def harness(store):
    return Harness(store)


def run(coro):
    return asyncio.run(coro)


def test_hello_gets_a_snapshot_of_the_world_then_ready(harness):
    harness.add_task("NORT-7", command="plan-task")

    async def scenario():
        async with harness.serving() as server:
            async with connect(url(server)) as ws:
                received = await say_hello(ws)
        return received

    received = run(scenario())
    assert [tuple(m)[0] for m in received] == ["seq", "ready"]
    snapshot = received[0]
    assert snapshot["event"]["type"] == "snapshot"
    world = snapshot["event"]["world"]
    assert world["tasks"]["northwind/NORT-7"]["command"] == "plan-task"
    assert world["tasks"]["northwind/NORT-7"]["actionable"] is True
    assert world["projects"][PROJECT]["stackTip"] == "main"
    assert world["worktrees"]["northwind-alpha"]["path"] == WORKTREE_PATH
    assert received[1]["ready"]["seq"] == snapshot["seq"]


def test_a_task_edit_arrives_as_an_upsert(harness):
    async def scenario():
        async with harness.serving() as server:
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
        async with harness.serving() as server:
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
        async with harness.serving() as server:
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
        async with harness.serving() as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                await ws.send_str(json.dumps({"type": "hello"}))
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
        async with harness.serving() as server:
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
        async with harness.serving() as server:
            async with connect(url(server)) as ws:
                await ws.send_str(
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


async def transcript_of(api: Api, agent_id: str = "ag1") -> dict:
    return await api.get_json(f"/api/agents/{agent_id}/transcript")


def test_the_world_carries_the_wait_and_the_transcript_holds_the_backlog(harness):
    """The world holds the wait; the transcript holds what the host replayed.

    A client that connects after the backlog was relayed gets the wait from
    the world and the scrollback from the transcript route.
    """
    harness.add_task("NORT-7")
    session = model.session_id_for(PROJECT, "NORT-7")
    backlog, _ = split_at_control_response(read_fixture("question-unanswered.jsonl"))
    harness.daemon.rows["ag1"] = agent_row(session=session, state="awaiting-question")
    harness.daemon.backlog["ag1"] = backlog
    harness.daemon.pending["ag1"] = pending_from(backlog)

    async def scenario():
        async with harness.client() as api:
            agent = await api.get_json("/api/agents/ag1")
            attention = await api.get_json("/api/attention?open=1")
            transcript = await transcript_of(api)
            return (
                agent,
                attention["attention"],
                transcript,
                list(harness.daemon.attached),
            )

    agent, open_items, transcript, attached = run(scenario())
    assert attached == ["ag1"]
    assert agent["state"] == "awaiting-question"
    assert agent["taskId"] == "northwind/NORT-7"
    assert agent["project"] == PROJECT
    assert agent["worktreeId"] == "northwind-alpha"
    assert agent["pendingRequestId"] == "2ba1273d-d878-4923-ba21-31faa1067613"
    assert transcript["agentId"] == "ag1"
    assert [i["type"] for i in transcript["items"]][-1] == "question"
    assert transcript["truncatedBefore"] is False
    assert transcript["seq"] == len(transcript["items"])
    assert [a["kind"] for a in open_items] == ["question"]
    assert open_items[0]["taskId"] == "northwind/NORT-7"


def test_a_backlog_the_size_of_the_hosts_window_is_marked_truncated(harness):
    from maelstrom.agent_model import RECENT_LIMIT

    events = read_fixture("normal-turn.jsonl")
    padding = [{"type": "rate_limit_event"}] * (RECENT_LIMIT - len(events))
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.backlog["ag1"] = padding + events

    async def scenario():
        async with harness.client() as api:
            return await transcript_of(api)

    assert run(scenario())["truncatedBefore"] is True


def test_a_backlog_one_short_of_the_window_is_not_marked_truncated(harness):
    from maelstrom.agent_model import RECENT_LIMIT

    events = read_fixture("normal-turn.jsonl")
    padding = [{"type": "rate_limit_event"}] * (RECENT_LIMIT - 1 - len(events))
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.backlog["ag1"] = padding + events

    async def scenario():
        async with harness.client() as api:
            return await transcript_of(api)

    assert run(scenario())["truncatedBefore"] is False


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
        async with harness.serving() as server:
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
        async with harness.serving() as server:
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
        async with harness.serving() as server:
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
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.daemon.rows["ag2"] = agent_row("ag2", cwd="/private/tmp")
                await stream.change("agent", "ag2")
                appeared = await api.get_json("/api/agents/ag2")
                await wait_until(lambda: "ag2" in harness.daemon.attached)
                attached = list(harness.daemon.attached)
                del harness.daemon.rows["ag2"]
                harness.daemon.end_stream("ag2")
                gone = await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag2",
                    lambda a: a["state"] == "exited",
                )
        return appeared, attached, gone

    appeared, attached, gone = run(scenario())
    assert appeared["taskId"] == ""
    assert appeared["worktreeId"] == ""
    assert attached == ["ag2"]
    assert gone["exitCode"] == 0


def test_a_row_reporting_an_exit_the_stream_never_showed_is_applied(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                return await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "exited",
                )

    assert run(scenario())["exitCode"] == 1


def test_an_agent_that_comes_back_under_its_old_id_is_revived(harness):
    """A resumed agent keeps its id, so the row that returns is the same agent."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                await wait_until(lambda: "ag1" in harness.daemon.attached)
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "exited",
                )
                harness.daemon.rows["ag1"]["state"] = "idle"
                back = await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "idle",
                )
                await wait_until(
                    lambda: (
                        harness.daemon.calls.count({"cmd": "attach", "id": "ag1"}) == 2
                    )
                )
                return back

    # The re-attached backlog re-normalises into the same transcript, which
    # still holds the turns from before the exit.
    assert run(scenario())["exitCode"] is None


def test_a_revived_agent_loses_the_attention_its_exit_raised(harness):
    """The exit is over, so the item asking someone to look at it must go."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                raised = await settled(
                    stream,
                    api,
                    "attention",
                    "/api/attention?open=1",
                    lambda body: len(body["attention"]) == 1,
                )
                harness.daemon.rows["ag1"]["state"] = "idle"
                await settled(
                    stream,
                    api,
                    "attention",
                    "/api/attention?open=1",
                    lambda body: body["attention"] == [],
                )
                everything = await api.get_json("/api/attention")
                return raised["attention"][0], everything["attention"]

    raised, everything = run(scenario())
    assert raised["kind"] == "agent_exited"
    assert raised["agentId"] == "ag1"
    [cleared] = everything
    assert cleared["id"] == raised["id"]
    assert cleared["clearedAt"] is not None


def test_a_revived_agent_links_to_the_task_that_arrived_while_it_was_gone(harness):
    """An agent away during a task's arrival must still find it on the way back."""
    session = model.session_id_for(PROJECT, "NORT-7")
    harness.daemon.rows["ag1"] = agent_row(session=session)

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.daemon.rows["ag1"]["state"] = "exited(1)"
                await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "exited",
                )
                # The task appears only while the agent is gone, so the link it
                # held at exit is stale by the time it comes back.
                harness.add_task("NORT-7")
                await stream.change("task", "northwind/NORT-7")
                harness.daemon.rows["ag1"]["state"] = "idle"
                # Every agent frame from the moment it comes back. The revive
                # pass must carry the link itself: a later poll fixing it leaves
                # a stale link on screen in between.
                await stream.change("agent", "ag1")
                return await api.get_json("/api/agents/ag1")

    agent = run(scenario())
    # The first notice of the revive already finds the task on the agent, so
    # nothing renders an agent that has come back with no task on it.
    assert agent["state"] == "idle"
    assert agent["taskId"] == "northwind/NORT-7"


def test_a_wait_older_than_the_hosts_window_is_raised_from_the_detail_frame(harness):
    """The gap the detail frame fills: the request fell out of the backlog.

    The host still knows what the agent waits on, so the wait is answerable
    even though no ``control_request`` replays.
    """
    backlog, _ = split_at_control_response(read_fixture("permission-request.jsonl"))
    pending = pending_from(backlog)
    harness.daemon.rows["ag1"] = agent_row(state="awaiting-permission")
    # The window rolled past the request: the host replays none of it.
    harness.daemon.backlog["ag1"] = []
    harness.daemon.pending["ag1"] = pending

    async def scenario():
        async with harness.client() as api:
            agent = await api.get_json("/api/agents/ag1")
            attention = await api.get_json("/api/attention?open=1")
            transcript = await transcript_of(api)
            return agent, attention["attention"], transcript["items"]

    agent, open_items, items = run(scenario())
    assert agent["pendingRequestId"] == pending.request_id
    assert agent["pendingRequest"]["type"] == "permission_request"
    assert [i["type"] for i in items] == ["permission_request"]
    assert [a["requestId"] for a in open_items] == [pending.request_id]


def test_a_wait_the_backlog_replays_is_raised_once(harness):
    """The detail frame names the same wait the backlog carries; one item wins."""
    backlog, _ = waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.client() as api:
            attention = await api.get_json("/api/attention?open=1")
            transcript = await transcript_of(api)
            return attention["attention"], transcript["items"]

    open_items, items = run(scenario())
    assert len([i for i in items if i["type"] == "permission_request"]) == 1
    assert len(open_items) == 1


def test_a_revive_mints_no_item_id_twice(harness):
    """A resume re-attaches, and the host replays its window into the same transcript.

    The re-normalised replay gets fresh ids, seeded past the ones already
    handed out, so no two items share an id however many lives the agent has.
    """
    events = read_fixture("normal-turn.jsonl")
    harness.daemon.rows["ag1"] = agent_row()
    harness.daemon.backlog["ag1"] = events

    async def scenario():
        async with harness.client() as api:
            first = (await transcript_of(api))["items"]
            # The agent dies and is resumed under its own id: the host
            # replays the same window to the re-attach.
            harness.daemon.rows["ag1"]["state"] = "exited(1)"
            await harness.orch.refresh_agents()
            harness.daemon.rows["ag1"]["state"] = "idle"
            await harness.orch.refresh_agents()
            attaches = len([c for c in harness.daemon.calls if c["cmd"] == "attach"])
            return first, (await transcript_of(api))["items"], attaches

    first, after, attaches = run(scenario())
    # The revive really did re-attach and replay, or this proves nothing.
    assert attaches == 2
    assert len(first) > 0
    ids = [i["id"] for i in after]
    assert len(ids) == len(set(ids)), "an item was published under two ids"
    assert [i["id"] for i in first] == ids[: len(first)]


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


async def pending_id(api: Api, agent_id: str = "ag1") -> str:
    return (await api.get_json(f"/api/agents/{agent_id}"))["pendingRequestId"]


async def no_notice(stream: EventStream, within: float = 0.2) -> None:
    """Assert the stream stays silent for ``within`` seconds."""
    try:
        event = await stream.next("change", within)
    except asyncio.TimeoutError:
        return
    raise AssertionError(f"unexpected notice: {event}")


def host_calls(harness) -> list[str]:
    """The commands that reached the host, less the reads every run makes."""
    return [
        c["cmd"] for c in harness.daemon.calls if c["cmd"] not in ("list", "attach")
    ]


def test_approve_reaches_the_host_and_resolves_the_wait(harness):
    waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                request_id = await pending_id(api)
                reply = await api.post(
                    "/api/agents/ag1/approve", {"requestId": request_id}
                )
                agent = await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "processing",
                )
                attention = await api.get_json("/api/attention")
                return reply, agent, attention["attention"]

    reply, agent, attention = run(scenario())
    assert reply.status == 200
    assert reply.body == {}
    assert {"cmd": "approve", "id": "ag1"} in harness.daemon.calls
    assert agent["pendingRequestId"] is None
    assert agent["pendingRequest"] is None
    assert [a["clearedAt"] is not None for a in attention] == [True]


def test_deny_sends_the_reason_and_records_it(harness):
    waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                request_id = await pending_id(api)
                reply = await api.post(
                    "/api/agents/ag1/deny",
                    {"requestId": request_id, "reason": "not on this network"},
                )
                await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "processing",
                )
                return reply, (await transcript_of(api))["items"]

    reply, items = run(scenario())
    assert reply.status == 200
    assert {
        "cmd": "deny",
        "id": "ag1",
        "reason": "not on this network",
    } in harness.daemon.calls
    request = next(i for i in items if i["type"] == "permission_request")
    assert request["decision"] == "deny"
    assert request["reason"] == "not on this network"


def test_answer_sends_the_answers_map_and_files_it_on_the_question(harness):
    waiting_on(harness, "question-unanswered.jsonl")
    answers = {"Which colour do you prefer?": "Blue"}

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                request_id = await pending_id(api)
                reply = await api.post(
                    "/api/agents/ag1/answer",
                    {"requestId": request_id, "answers": answers},
                )
                await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "processing",
                )
                return reply, (await transcript_of(api))["items"]

    reply, items = run(scenario())
    assert reply.status == 200
    assert {"cmd": "answer", "id": "ag1", "answers": answers} in harness.daemon.calls
    question = next(i for i in items if i["type"] == "question")
    assert question["answers"] == answers


def test_say_reaches_the_host_and_the_childs_replay_shows_it(harness):
    """The host does not echo a user turn: the child replays it on its stdout.

    Echoing it as well would put the turn on the stream twice, and the
    normaliser mints a fresh item id per copy — so the message would render
    twice. The server relays the command and waits for the replay.
    """
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream, api.transcript_stream("ag1") as ws:
                await stream.next("reset")
                await ws_next(ws, lambda m: m["type"] == "transcript.snapshot")
                reply = await api.post(
                    "/api/agents/ag1/say", {"text": "also the README"}
                )
                # Nothing changes until the child replays the turn.
                await no_notice(stream)
                harness.daemon.push(
                    "ag1",
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "also the README"}],
                        },
                        "isReplay": True,
                    },
                )
                frame = await ws_next(ws, is_event("transcript.append"))
                return reply, frame["event"]["item"]

    reply, item = run(scenario())
    assert reply.status == 200
    assert {
        "cmd": "say",
        "id": "ag1",
        "text": "also the README",
    } in harness.daemon.calls
    assert item["type"] == "message"
    assert item["role"] == "user"
    assert item["markdown"] == "also the README"


def test_a_wait_answered_outside_the_server_clears_in_the_world(harness):
    """The case the old design could not handle: it only learned of its own answers.

    ``mael agent approve`` writes the reply, and the host echoes it onto the
    stream. The server holds no opinion about how a wait is answered, so the
    UI clears either way.
    """
    waiting_on(harness, "permission-request.jsonl")
    pending = harness.daemon.pending["ag1"]

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                assert await pending_id(api) == pending.request_id
                # Nobody asked this server; the answer was made elsewhere.
                harness.daemon.push("ag1", reply_for_approval(pending))
                return await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "processing",
                )

    agent = run(scenario())
    assert agent["pendingRequestId"] is None
    assert agent["waitingOn"] == ""


def test_stop_reaches_the_host_and_marks_the_agent_exited_cleanly(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            reply = await api.post("/api/agents/ag1/stop")
            return reply, await api.get_json("/api/agents/ag1")

    reply, agent = run(scenario())
    assert reply.status == 200
    assert {"cmd": "stop", "id": "ag1"} in harness.daemon.calls
    assert agent["state"] == "exited"
    assert agent["exitCode"] == 0


def test_a_refused_command_answers_its_code_and_publishes_nothing(harness):
    waiting_on(harness, "question-unanswered.jsonl")

    async def scenario():
        async with harness.client() as api:
            before = published(harness)
            unknown = await api.post("/api/agents/nobody/approve", {"requestId": "x"})
            stale = await api.post("/api/agents/ag1/approve", {"requestId": "old"})
            wrong = await api.post(
                "/api/agents/ag1/approve",
                {"requestId": "2ba1273d-d878-4923-ba21-31faa1067613"},
            )
            assert published(harness) == before
            return unknown, stale, wrong

    unknown, stale, wrong = run(scenario())
    assert (unknown.status, unknown.body["error"]["code"]) == (404, "unknown_id")
    assert (stale.status, stale.body["error"]["code"]) == (409, "stale_request")
    assert (wrong.status, wrong.body["error"]["code"]) == (409, "wrong_wait_kind")
    assert host_calls(harness) == []


@pytest.mark.parametrize(
    ("send", "status", "code"),
    [
        (lambda api: api.post("/api/agents/ag1/say", raw=b"not json"), 400, "invalid"),
        (lambda api: api.post("/api/agents/ag1/say", {}), 400, "invalid"),
        (
            lambda api: api.post("/api/shaping", {"project": PROJECT, "brief": "x"}),
            501,
            "not_implemented",
        ),
    ],
    ids=["a body that is not JSON", "a missing field", "a stub route"],
)
def test_a_request_the_routes_refuse_is_answered_in_the_error_shape(
    harness, send, status, code
):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            before = published(harness)
            reply = await send(api)
            assert published(harness) == before
            return reply

    reply = run(scenario())
    assert (reply.status, reply.body["error"]["code"]) == (status, code)
    assert host_calls(harness) == []


def test_a_host_refusal_maps_to_the_matching_code(harness):
    waiting_on(harness, "permission-request.jsonl")
    harness.daemon.replies["approve"] = [{"error": "agent ag1 is not waiting"}]

    async def scenario():
        async with harness.client() as api:
            request_id = await pending_id(api)
            return await api.post("/api/agents/ag1/approve", {"requestId": request_id})

    reply = run(scenario())
    assert reply.status == 409
    assert reply.body["error"]["code"] == "not_waiting"


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
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                reply = await api.post("/api/tasks/northwind/NORT-7/launch")
                # The reply follows the world changes, so every GET is current.
                task = await api.get_json("/api/tasks/northwind/NORT-7")
                agent = await api.get_json("/api/agents/new1")
                desk = await api.get_json("/api/desk")
                kinds = set()
                for _ in range(3):
                    kinds.add((await stream.next("change"))["data"]["kind"])
                return reply, task, agent, desk["desk"], kinds

    reply, task, agent, desk, kinds = run(scenario())
    assert reply.status == 200
    assert reply.body == {"agentId": "new1"}
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
    assert task["status"] == "in-progress"
    assert agent["taskId"] == "northwind/NORT-7"
    assert agent["worktreeId"] == "northwind-alpha"
    # A task launched from the UI joins the desk.
    assert [e["id"] for e in desk] == ["task:northwind/NORT-7"]
    assert kinds == {"task", "agent", "desk"}


def test_a_launch_the_host_refuses_rolls_the_task_back_to_todo(harness):
    harness.add_task("NORT-7")
    harness.daemon.replies["start"] = [
        {"error": "could not start claude: no such file"}
    ]

    async def scenario():
        async with harness.client() as api:
            reply = await api.post("/api/tasks/northwind/NORT-7/launch")
            return reply, await api.get_json("/api/tasks/northwind/NORT-7")

    reply, task = run(scenario())
    assert reply.status == 400
    assert reply.body["error"]["code"] == "invalid"
    assert "no such file" in reply.body["error"]["message"]
    assert model.load(harness.store, PROJECT, "NORT-7").status == "todo"
    assert task["status"] == "todo"


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
        async with harness.client() as api:
            return await api.post("/api/tasks/northwind/NORT-7/launch")

    reply = run(scenario())
    assert reply.status == 400
    assert "rebase conflict" in reply.body["error"]["message"]
    assert model.load(harness.store, PROJECT, "NORT-7").status == "todo"
    assert not [c for c in harness.daemon.calls if c["cmd"] == "start"]


# -- the desk --


def test_two_projects_may_share_a_notebook_id(store):
    harness = Harness(store, projects=("northwind", "askastro"))
    harness.add_task("2026-06-11.1", project="northwind")
    harness.add_task("2026-06-11.1", project="askastro")

    async def scenario():
        async with harness.serving() as server:
            async with connect(url(server)) as ws:
                return await say_hello(ws)

    world = run(scenario())[0]["event"]["world"]
    assert set(world["tasks"]) == {
        "northwind/2026-06-11.1",
        "askastro/2026-06-11.1",
    }


def test_desk_add_answers_after_the_entry_is_in_the_world(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                reply = await api.post("/api/desk", {"id": "task:northwind/NORT-7"})
                desk = await api.get_json("/api/desk")
                ids = await stream.change("desk")
                return reply, desk["desk"], ids

    reply, desk, ids = run(scenario())
    assert reply.status == 200
    assert reply.body == {}
    [entry] = desk
    assert entry["id"] == "task:northwind/NORT-7"
    assert entry["addedAt"] == NOW
    assert ids == ["task:northwind/NORT-7"]


def test_set_status_moves_the_task_before_it_answers(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                reply = await api.post(
                    "/api/tasks/northwind/NORT-7/status", {"status": "done"}
                )
                task = await api.get_json("/api/tasks/northwind/NORT-7")
                ids = await stream.change("task")
                return reply, task, ids

    reply, task, ids = run(scenario())
    assert reply.status == 200
    assert reply.body == {}
    assert task["status"] == "done"
    assert ids == ["northwind/NORT-7"]
    assert model.load(harness.store, PROJECT, "NORT-7").status == "done"


def test_update_writes_only_the_fields_it_was_given(harness):
    harness.add_task("NORT-7", branch="feat/orders")

    async def scenario():
        async with harness.client() as api:
            reply = await api.patch(
                "/api/tasks/northwind/NORT-7", {"title": "Export the orders"}
            )
            return reply, await api.get_json("/api/tasks/northwind/NORT-7")

    reply, task = run(scenario())
    assert reply.status == 200
    assert reply.body == {}
    assert task["title"] == "Export the orders"
    stored = model.load(harness.store, PROJECT, "NORT-7")
    assert stored.title == "Export the orders"
    assert stored.branch == "feat/orders"


def test_a_write_to_a_task_the_notebook_lost_is_unknown_id(harness):
    """The world knew the task; the notebook no longer holds it."""
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            model.delete(harness.store, PROJECT, "NORT-7")
            return await api.patch("/api/tasks/northwind/NORT-7", {"title": "Gone"})

    reply = run(scenario())
    assert reply.status == 404
    assert reply.body["error"]["code"] == "unknown_id"


def test_desk_remove_takes_a_url_encoded_desk_id(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                await api.post("/api/desk", {"id": "task:northwind/NORT-7"})
                await stream.change("desk")
                reply = await api.delete("/api/desk/task%3Anorthwind%2FNORT-7")
                ids = await stream.change("desk")
                return reply, ids, await api.get_json("/api/desk")

    reply, ids, desk = run(scenario())
    assert reply.status == 200
    assert reply.body == {}
    assert ids == ["task:northwind/NORT-7"]
    assert desk == {"desk": []}


def test_a_second_desk_add_is_ok_and_publishes_nothing(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                await api.post("/api/desk", {"id": "task:northwind/NORT-7"})
                await stream.change("desk")
                reply = await api.post("/api/desk", {"id": "task:northwind/NORT-7"})
                await no_notice(stream)
                return reply

    assert run(scenario()).status == 200


def test_a_task_deleted_from_the_notebook_leaves_the_desk(harness):
    harness.add_task("NORT-7")
    # A second task keeps the project in the reading. A project with no tasks
    # at all is indistinguishable from one the scan missed, so its desk
    # entries are kept.
    harness.add_task("NORT-8")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                await api.post("/api/desk", {"id": "task:northwind/NORT-7"})
                await stream.change("desk")
                model.delete(harness.store, PROJECT, "NORT-7")
                harness.version += 1
                await stream.change("desk", "task:northwind/NORT-7")
                return await api.get_json("/api/desk")

    assert run(scenario()) == {"desk": []}


def test_a_live_agent_joins_the_desk(harness):
    """Running work is always drawn, so the server puts it on the desk itself."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(scenario())["desk"]] == ["agent:ag1"]


def test_an_agent_with_a_task_joins_the_desk_under_its_task(harness):
    harness.add_task("NORT-7")
    session = model.session_id_for(PROJECT, "NORT-7")
    harness.daemon.rows["ag1"] = agent_row(session=session)

    async def scenario():
        async with harness.client() as api:
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(scenario())["desk"]] == ["task:northwind/NORT-7"]


def test_the_desk_entry_outlives_the_agent(harness):
    """Only a dismiss clears an entry, so a stopped agent stays on the canvas."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                del harness.daemon.rows["ag1"]
                agent = await settled(
                    stream,
                    api,
                    "agent",
                    "/api/agents/ag1",
                    lambda a: a["state"] == "exited",
                )
                desk = await api.get_json("/api/desk")
                return desk["desk"], agent["state"]

    desk, state = run(scenario())
    assert state == "exited"
    assert [e["id"] for e in desk] == ["agent:ag1"]


def test_a_second_agent_poll_publishes_nothing(harness):
    """The 2s poll must not thrash the desk file, nor the clients."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                await harness.orch.refresh_agents()
                await no_notice(stream)

    run(scenario())


def test_a_dismissed_entry_is_not_re_added_by_the_next_poll(harness):
    """A dismiss is the user's decision, so the poll must not undo it."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            await api.delete("/api/desk/agent%3Aag1")
            await harness.orch.refresh_agents()
            return await api.get_json("/api/desk")

    assert run(scenario()) == {"desk": []}


def test_an_agent_already_exited_when_it_is_adopted_does_not_join_the_desk(harness):
    """Only running work joins by itself; a dead agent needs the user to ask."""
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")

    async def scenario():
        async with harness.client() as api:
            return await api.get_json("/api/desk")

    assert run(scenario()) == {"desk": []}


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
        async with harness.client() as api:
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(scenario())["desk"]] == ["task:northwind/NORT-7"]


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
        async with harness.client() as api:
            desk = await api.get_json("/api/desk")
            tasks = await api.get_json("/api/tasks")
            return desk["desk"], [t["id"] for t in tasks["tasks"]]

    desk, tasks = run(scenario())
    assert tasks == ["northwind/NORT-7"]
    assert sorted(e["id"] for e in desk) == ["agent:ag1", "task:northwind/NORT-7"]


def test_a_free_agent_entry_whose_agent_is_live_survives_the_load(store):
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()
    desk.save({"agent:ag1": {"id": "agent:ag1", "addedAt": NOW}})
    harness = Harness(store, desk=desk)
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(scenario())["desk"]] == ["agent:ag1"]


def test_the_desk_survives_a_restart(store):
    from maelstrom.desk_store import InMemoryDeskStore

    desk = InMemoryDeskStore()

    async def scenario(harness):
        async with harness.client() as api:
            await api.post("/api/desk", {"id": "task:northwind/NORT-7"})

    first = Harness(store, desk=desk)
    first.add_task("NORT-7")
    run(scenario(first))

    second = Harness(store, desk=desk)

    async def read_back():
        async with second.client() as api:
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(read_back())["desk"]] == ["task:northwind/NORT-7"]


def test_a_project_the_scan_misses_keeps_its_desk_entries(store):
    """A project briefly absent must not cost the user the desk it holds."""
    harness = Harness(store, projects=("northwind", "askastro"))
    harness.add_task("NORT-7", project="northwind")
    harness.add_task("ASK-1", project="askastro")

    async def scenario():
        async with harness.client() as api:
            await api.post("/api/desk", {"id": "task:askastro/ASK-1"})
            # The project disappears from the scan, as an unmounted volume
            # or a renamed directory would make it.
            harness.projects = ["northwind"]
            harness.version += 1
            await harness.orch.refresh_tasks()
            return await api.get_json("/api/desk")

    assert [e["id"] for e in run(scenario())["desk"]] == ["task:askastro/ASK-1"]


def test_resume_reaches_the_host_for_an_exited_agent(harness):
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")

    async def scenario():
        async with harness.client() as api:
            return await api.post("/api/agents/ag1/resume")

    assert run(scenario()).status == 200
    assert {"cmd": "resume", "id": "ag1"} in harness.daemon.calls


def test_resume_of_an_agent_that_is_running_is_refused(harness):
    """Validation catches it before the host, so nothing is spawned twice."""
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            return await api.post("/api/agents/ag1/resume")

    reply = run(scenario())
    assert reply.status == 400
    assert reply.body["error"]["code"] == "invalid"


def test_a_host_that_says_the_agent_is_running_refuses_the_resume(harness):
    """The host is the authority: it may know of a child the world does not."""
    harness.daemon.rows["ag1"] = agent_row(state="exited(1)")
    harness.daemon.replies["resume"] = [{"error": "agent ag1 is running"}]

    async def scenario():
        async with harness.client() as api:
            return await api.post("/api/agents/ag1/resume")

    reply = run(scenario())
    assert reply.status == 400
    assert reply.body["error"]["code"] == "invalid"


def test_resume_of_an_agent_the_world_does_not_know_is_refused(harness):
    async def scenario():
        async with harness.serving() as server:
            async with connect(url(server)) as ws:
                await say_hello(ws)
                return await command(ws, {"type": "agent.resume", "agentId": "nope"})

    reply = run(scenario())
    assert reply["ok"] is False
    assert reply["error"]["code"] == "unknown_id"


# --- reads -------------------------------------------------------------------


def test_the_task_list_ships_slim_rows_and_the_detail_holds_the_prose(harness):
    harness.add_task("NORT-7", command="plan-task", content="Do the thing.")

    async def scenario():
        async with harness.client() as api:
            rows = await api.get("/api/tasks")
            detail = await api.get_json("/api/tasks/northwind/NORT-7")
            missing = await api.get("/api/tasks/northwind/NORT-9")
            return rows, detail, missing

    rows, detail, missing = run(scenario())
    assert rows.status == 200
    assert rows.body["version"] == "1"
    [row] = rows.body["tasks"]
    assert row["id"] == "northwind/NORT-7"
    assert row["actionable"] is True
    assert "content" not in row
    assert "log" not in row
    assert detail["content"] == "Do the thing."
    assert detail["log"] == []
    assert missing.status == 404
    assert missing.body["error"]["code"] == "unknown_id"


def test_the_task_list_answers_304_until_a_task_changes(harness):
    harness.add_task("NORT-7")

    async def scenario():
        async with harness.client() as api:
            first = await api.get("/api/tasks")
            etag = first.headers["ETag"]
            same = await api.get("/api/tasks", **{"If-None-Match": etag})
            harness.add_task("NORT-8")
            await harness.orch.refresh_tasks()
            moved = await api.get("/api/tasks", **{"If-None-Match": etag})
            return same, moved, etag

    same, moved, etag = run(scenario())
    assert same.status == 304
    assert moved.status == 200
    assert moved.headers["ETag"] != etag
    assert [t["id"] for t in moved.body["tasks"]] == [
        "northwind/NORT-7",
        "northwind/NORT-8",
    ]


def test_projects_worktrees_and_the_desk_each_have_a_get(harness):
    async def scenario():
        async with harness.client() as api:
            return (
                await api.get_json("/api/projects"),
                await api.get_json("/api/worktrees"),
                await api.get_json("/api/desk"),
            )

    projects, worktrees, desk = run(scenario())
    assert projects["projects"][0]["stackTip"] == "main"
    assert worktrees["worktrees"][0]["path"] == WORKTREE_PATH
    assert desk == {"desk": []}


def test_an_agent_detail_carries_the_request_it_waits_on(harness):
    harness.add_task("NORT-7")
    session = model.session_id_for(PROJECT, "NORT-7")
    backlog, _ = split_at_control_response(read_fixture("question-unanswered.jsonl"))
    harness.daemon.rows["ag1"] = agent_row(session=session, state="awaiting-question")
    harness.daemon.backlog["ag1"] = backlog
    harness.daemon.pending["ag1"] = pending_from(backlog)

    async def scenario():
        async with harness.client() as api:
            agents = await api.get_json("/api/agents")
            detail = await api.get_json("/api/agents/ag1")
            missing = await api.get("/api/agents/nobody")
            attention = await api.get_json("/api/attention?open=1")
            return agents, detail, missing, attention

    agents, detail, missing, attention = run(scenario())
    [agent] = agents["agents"]
    assert agent["taskId"] == "northwind/NORT-7"
    assert "pendingRequest" not in agent
    assert detail["state"] == "awaiting-question"
    assert detail["pendingRequest"]["type"] == "question"
    assert detail["pendingRequest"]["requestId"] == detail["pendingRequestId"]
    assert missing.status == 404
    assert [a["kind"] for a in attention["attention"]] == ["question"]


def test_an_agent_waiting_on_nothing_has_no_pending_request(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            return await api.get_json("/api/agents/ag1")

    assert run(scenario())["pendingRequest"] is None


def test_the_document_routes_and_the_stubs_answer(harness):
    async def scenario():
        async with harness.client() as api:
            return (
                await api.get_json("/api/documents"),
                await api.get("/api/documents/nope"),
                await api.post("/api/documents/nope/comments", {"body": "x"}),
                await api.post("/api/tasks", {"project": PROJECT, "draft": "x"}),
                await api.get("/api/nothing-here"),
            )

    documents, missing, comment, create, unknown = run(scenario())
    assert documents == {"documents": []}
    assert missing.status == 404
    assert comment.status == 501
    assert comment.body["error"]["code"] == "not_implemented"
    assert create.status == 501
    assert unknown.status == 404
    assert unknown.body["error"]["code"] == "unknown_id"


# --- change notices ----------------------------------------------------------


def test_the_notice_stream_opens_with_a_reset_carrying_the_epoch(harness):
    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                return await stream.next()

    reset = run(scenario())
    assert reset["event"] == "reset"
    assert reset["data"] == {"epoch": harness.orch.epoch}
    assert len(harness.orch.epoch) == 8


def test_a_task_change_arrives_as_a_notice_and_the_get_shows_it(harness):
    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.add_task("NORT-8")
                ids = await stream.change("task")
                return ids, await api.get_json("/api/tasks/northwind/NORT-8")

    ids, task = run(scenario())
    assert ids == ["northwind/NORT-8"]
    assert task["title"] == "NORT-8"


def test_changes_inside_the_coalesce_window_land_as_one_notice_per_kind(harness):
    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                harness.add_task("NORT-1")
                harness.add_task("NORT-2")
                harness.daemon.rows["ag1"] = agent_row()
                # One poll of each source: two task upserts and one agent.
                await harness.orch.refresh_tasks()
                await harness.orch.refresh_agents()
                first = await stream.next("change")
                second = await stream.next("change")
                return first["data"], second["data"]

    first, second = run(scenario())
    by_kind = {n["kind"]: n["ids"] for n in (first, second)}
    assert by_kind["task"] == ["northwind/NORT-1", "northwind/NORT-2"]
    assert by_kind["agent"] == ["ag1"]


def test_a_removed_task_is_named_in_the_notice_and_gone_from_the_get(harness):
    harness.add_task("NORT-7")
    harness.add_task("NORT-8")

    async def scenario():
        async with harness.client() as api:
            async with api.events() as stream:
                await stream.next("reset")
                model.delete(harness.store, PROJECT, "NORT-7")
                harness.version += 1
                ids = await stream.change("task", "northwind/NORT-7")
                return ids, await api.get("/api/tasks/northwind/NORT-7")

    ids, gone = run(scenario())
    assert ids == ["northwind/NORT-7"]
    assert gone.status == 404


# --- transcript streams ------------------------------------------------------


def test_a_fresh_transcript_socket_opens_with_the_snapshot_the_get_serves(harness):
    waiting_on(harness, "question-unanswered.jsonl")

    async def scenario():
        async with harness.client() as api:
            snapshot = await transcript_of(api)
            async with api.transcript_stream("ag1") as ws:
                opening = await ws_next(ws)
            return snapshot, opening

    snapshot, opening = run(scenario())
    assert opening["type"] == "transcript.snapshot"
    assert opening["items"] == snapshot["items"]
    assert opening["seq"] == snapshot["seq"] > 0
    assert opening["truncatedBefore"] is False
    assert [i["type"] for i in opening["items"]][-1] == "question"


def test_a_live_event_arrives_as_a_stamped_frame_on_every_socket(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with (
                api.transcript_stream("ag1") as one,
                api.transcript_stream("ag1") as two,
            ):
                first = await ws_next(one)
                await ws_next(two)
                for event in read_fixture("normal-turn.jsonl"):
                    harness.daemon.push("ag1", event)
                a = await ws_next(one, is_event("transcript.append"))
                b = await ws_next(two, is_event("transcript.append"))
            return first["seq"], a, b

    seq, a, b = run(scenario())
    assert a == b
    assert a["seq"] == seq + 1
    assert a["event"]["agentId"] == "ag1"
    assert a["event"]["item"]["type"] == "system"


def test_approve_patches_the_item_on_the_socket(harness):
    waiting_on(harness, "permission-request.jsonl")

    async def scenario():
        async with harness.client() as api:
            async with api.transcript_stream("ag1") as ws:
                opening = await ws_next(ws)
                request_id = await pending_id(api)
                await api.post("/api/agents/ag1/approve", {"requestId": request_id})
                frame = await ws_next(
                    ws,
                    lambda m: (
                        is_event("transcript.update")(m)
                        and "decision" in m["event"]["patch"]
                    ),
                )
            return opening, frame

    opening, frame = run(scenario())
    request = next(i for i in opening["items"] if i["type"] == "permission_request")
    assert frame["event"]["itemId"] == request["id"]
    assert frame["event"]["patch"] == {"decision": "allow"}


def test_a_resume_inside_the_ring_replays_the_frames_it_missed(harness):
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.transcript_stream("ag1") as ws:
                seq = (await ws_next(ws))["seq"]
            for event in read_fixture("normal-turn.jsonl"):
                harness.daemon.push("ag1", event)
            await wait_until(lambda: harness.orch.transcript_log("ag1").seq > seq)
            await asyncio.sleep(0.05)
            async with api.transcript_stream("ag1", from_seq=seq) as resumed:
                replay = await ws_next(resumed)
            current = await transcript_of(api)
            return seq, replay, current

    seq, replay, current = run(scenario())
    assert replay["type"] == "transcript.replay"
    assert replay["seq"] == current["seq"]
    assert [f["seq"] for f in replay["frames"]] == list(
        range(seq + 1, current["seq"] + 1)
    )
    assert replay["frames"][0]["event"]["type"] == "transcript.append"


def test_a_transcript_resume_older_than_the_ring_gets_a_snapshot(store):
    harness = Harness(store, transcript_ring=2)
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.transcript_stream("ag1") as ws:
                seq = (await ws_next(ws))["seq"]
            for event in read_fixture("normal-turn.jsonl"):
                harness.daemon.push("ag1", event)
            await wait_until(lambda: harness.orch.transcript_log("ag1").seq > seq + 2)
            async with api.transcript_stream("ag1", from_seq=seq) as resumed:
                return await ws_next(resumed)

    opening = run(scenario())
    assert opening["type"] == "transcript.snapshot"
    assert len(opening["items"]) > 2


def test_a_socket_that_falls_behind_is_closed_lagging_and_resumes_from_its_seq(store):
    harness = Harness(store, ws_queue_limit=2)
    harness.daemon.rows["ag1"] = agent_row()

    async def scenario():
        async with harness.client() as api:
            async with api.transcript_stream("ag1") as ws:
                seq = (await ws_next(ws))["seq"]
                # The reader never reads while the whole turn lands.
                for event in read_fixture("normal-turn.jsonl"):
                    harness.daemon.push("ag1", event)
                await wait_until(
                    lambda: harness.orch.transcript_log("ag1").seq > seq + 3
                )
                await asyncio.sleep(0.05)
                received = []
                while True:
                    message = await asyncio.wait_for(ws.receive(), 2.0)
                    if message.type == aiohttp.WSMsgType.CLOSE:
                        break
                    received.append(json.loads(message.data))
                close_code = ws.close_code
            last_read = received[-1]["seq"] if received else seq
            async with api.transcript_stream("ag1", from_seq=last_read) as resumed:
                replay = await ws_next(resumed)
            return close_code, received, replay, seq

    close_code, received, replay, opening_seq = run(scenario())
    assert close_code == 4409
    # It got at most a queue's worth — none, when the overflow came before it
    # read — then the close. The resume brings every frame after the one it
    # last read, with no gap and no repeat.
    assert len(received) <= 2
    last_read = received[-1]["seq"] if received else opening_seq
    assert replay["type"] == "transcript.replay"
    assert [f["seq"] for f in replay["frames"]] == list(
        range(last_read + 1, replay["seq"] + 1)
    )
    assert len(replay["frames"]) > 0


def test_an_unknown_agent_closes_the_socket_4404_and_the_get_is_404(harness):
    async def scenario():
        async with harness.client() as api:
            missing = await api.get("/api/agents/nobody/transcript")
            async with api.transcript_stream("nobody") as ws:
                message = await asyncio.wait_for(ws.receive(), 2.0)
                return missing, message.type, ws.close_code

    missing, kind, code = run(scenario())
    assert missing.status == 404
    assert kind == aiohttp.WSMsgType.CLOSE
    assert code == 4404
