"""Codex daemon adapter behaviour at the orchestrator host seam."""

import asyncio
from collections.abc import AsyncIterator

from maelstrom.orchestrator.codex_daemon import CodexDaemonClient


class ScriptedBridge:
    """A Codex JSON-RPC bridge with scripted replies and notifications."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.incoming_messages: asyncio.Queue[dict | None] = asyncio.Queue()

    async def request(self, method: str, params: dict):
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-1", "cwd": params["cwd"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"], "cwd": "/worktree"}}
        return {}

    async def incoming(self) -> AsyncIterator[dict]:
        while message := await self.incoming_messages.get():
            yield message


def test_start_creates_a_codex_thread_and_opening_turn():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        reply = await daemon.request(
            {
                "cmd": "start",
                "cwd": "/worktree",
                "model": "sol",
                "prompt": "Implement the Task.",
                "mode": "plan",
            }
        )
        interrupt = await daemon.request({"cmd": "interrupt", "id": "thread-1"})
        return reply, interrupt, bridge.calls

    reply, interrupt, calls = asyncio.run(scenario())

    assert reply == {"ok": True, "id": "thread-1"}
    assert interrupt == {"ok": True}
    assert calls == [
        (
            "thread/start",
            {
                "cwd": "/worktree",
                "model": "gpt-5.6-sol",
                "sandbox": "read-only",
            },
        ),
        (
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "Implement the Task."}],
            },
        ),
        ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}),
    ]


def test_attach_routes_codex_notifications_to_the_started_thread() -> None:
    async def scenario() -> dict:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.request({"cmd": "start", "cwd": "/worktree", "model": "sol"})
        stream = daemon.attach("thread-1")
        await anext(stream)
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"id": "item-1", "type": "agentMessage"},
                },
            }
        )
        event = await anext(stream)
        await stream.aclose()
        return event

    assert asyncio.run(scenario()) == {
        "type": "codex_raw",
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"id": "item-1", "type": "agentMessage"},
        },
    }


def test_restore_resumes_a_stored_codex_thread() -> None:
    async def scenario() -> tuple[dict, list[tuple[str, dict]]]:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.restore(
            [
                {
                    "id": "thread-1",
                    "harness": "codex",
                    "task_session_id": "task-session-1",
                    "cwd": "/worktree",
                    "model": "codex:sol",
                    "mode": "normal",
                }
            ]
        )
        return await daemon.request({"cmd": "list"}), bridge.calls

    listed, calls = asyncio.run(scenario())

    assert calls == [("thread/resume", {"threadId": "thread-1"})]
    assert listed["agents"][0]["id"] == "thread-1"
    assert listed["agents"][0]["session"] == "task-session-1"
