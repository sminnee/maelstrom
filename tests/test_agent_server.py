"""The daemon's command surface, driven with a stub child instead of a subprocess."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

from maelstrom.agent_model import BACKLOG_END, apply_event, mark_exited
from maelstrom.agent_server import Agent, AgentDaemon

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"


def replay(name: str, stop_before_control: bool = False):
    """Feed one fixture through the reducer and return the final state."""
    from maelstrom.agent_model import AgentState

    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        state = apply_event(state, event)
        if stop_before_control and event.get("type") == "control_request":
            break
    return state


def _stub_agent(agent_id: str = "a1") -> Agent:
    """An `Agent` with a stub child, so `handle` is testable with no subprocess."""
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    return Agent(agent_id, "/tmp/x", proc)


class _RecordingWriter:
    """A stand-in for ``StreamWriter`` that keeps every line written to it."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


def _recording_writer() -> _RecordingWriter:
    return _RecordingWriter()


async def _handle(daemon: AgentDaemon, payload: dict) -> dict:
    return await daemon.handle(payload)


def test_handle_rejects_an_unknown_agent():
    reply = asyncio.run(
        _handle(AgentDaemon("/tmp/x.sock"), {"cmd": "say", "id": "nope"})
    )
    assert "no such agent" in reply["error"]


def test_handle_rejects_an_unknown_command():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "wat", "id": "a1"}))
    assert "unknown command" in reply["error"]


def test_handle_refuses_to_answer_an_agent_that_is_not_waiting():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "approve", "id": "a1"}))
    assert "not waiting" in reply["error"]


def test_handle_refuses_every_command_against_an_exited_agent():
    """Answering a dead agent must fail loudly, not report a silent success."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(agent.state, 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "say", "id": "a1", "text": "hi"}))
    assert "has exited" in reply["error"]


def test_handle_refuses_to_answer_a_wait_that_is_not_a_question():
    """`answer` on a plan review would send an empty answers map, reading as no answer."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("plan-review.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "answer", "id": "a1", "choice": "yes"}))
    assert "not waiting on a question" in reply["error"]


def test_handle_lists_every_agent():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    reply = asyncio.run(_handle(daemon, {"cmd": "list"}))
    assert [row["id"] for row in reply["agents"]] == ["a1"]


def test_show_returns_one_agents_detail():
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("question-unanswered.jsonl", stop_before_control=True)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1"}))
    assert reply["agent"]["waiting_tool"] == "AskUserQuestion"
    assert reply["agent"]["questions"][0]["options"][0]["label"] == "Red"


def test_show_works_on_an_exited_agent():
    """Inspecting why an agent died is the main reason to run ``show``."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = mark_exited(replay("normal-turn.jsonl"), 1)
    daemon.agents["a1"] = agent
    reply = asyncio.run(_handle(daemon, {"cmd": "show", "id": "a1"}))
    assert reply["agent"]["state"] == "exited(1)"
    assert reply["agent"]["messages"] == ["Hello there, friend"]


def test_show_rejects_an_unknown_agent():
    reply = asyncio.run(
        _handle(AgentDaemon("/tmp/x.sock"), {"cmd": "show", "id": "nope"})
    )
    assert "no such agent" in reply["error"]


def test_attach_marks_where_the_backlog_ends():
    """``tail`` without ``-f`` needs to know when history stops, not guess."""
    daemon = AgentDaemon("/tmp/x.sock")
    agent = _stub_agent()
    agent.state = replay("normal-turn.jsonl")
    daemon.agents["a1"] = agent
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    kinds = [json.loads(line).get("type") for line in writer.lines]
    assert kinds[-1] == BACKLOG_END
    assert kinds.count(BACKLOG_END) == 1
    assert "result" in kinds


def test_attach_still_marks_the_end_for_an_agent_that_said_nothing():
    daemon = AgentDaemon("/tmp/x.sock")
    daemon.agents["a1"] = _stub_agent()
    writer = _recording_writer()

    async def attach_then_disconnect():
        task = asyncio.create_task(daemon._attach("a1", writer))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(attach_then_disconnect())
    assert [json.loads(line).get("type") for line in writer.lines] == [BACKLOG_END]
