"""Codex daemon adapter behaviour at the orchestrator host seam."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from mael_orchestrator.codex_daemon import CodexDaemonClient


class ScriptedBridge:
    """A Codex JSON-RPC bridge with scripted replies and notifications."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[tuple[str | int, object]] = []
        self.response_error: RuntimeError | None = None
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

    async def respond(self, request_id: str | int, result: object) -> None:
        if self.response_error is not None:
            raise self.response_error
        self.responses.append((request_id, result))


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


def test_codex_approval_uses_the_common_control_request_boundary() -> None:
    async def scenario() -> tuple[list[dict], list[tuple[str | int, object]], dict]:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.request({"cmd": "start", "cwd": "/worktree", "model": "sol"})
        stream = daemon.attach("thread-1")
        await anext(stream)
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "command-1",
                    "command": "git status --short",
                    "cwd": "/worktree",
                    "reason": "Read the worktree state",
                },
            }
        )
        request = await anext(stream)
        reply = await daemon.request(
            {
                "cmd": "approve",
                "id": "thread-1",
                "request": "approval-1",
            }
        )
        response = await anext(stream)
        listed = await daemon.request({"cmd": "list"})
        await stream.aclose()
        return [request, response], bridge.responses, {"reply": reply, "listed": listed}

    events, responses, result = asyncio.run(scenario())

    assert events == [
        {
            "type": "control_request",
            "request_id": "approval-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_use_id": "command-1",
                "tool_name": "Bash",
                "input": {
                    "command": "git status --short",
                    "cwd": "/worktree",
                },
                "description": "Read the worktree state",
            },
        },
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "approval-1",
                "response": {"behavior": "allow"},
            },
        },
    ]
    assert responses == [("approval-1", {"decision": "accept"})]
    assert result["reply"] == {"ok": True}
    assert result["listed"]["agents"][0]["state"] == "processing"


def test_codex_file_change_can_be_denied_through_the_common_control_boundary() -> None:
    async def scenario() -> tuple[dict, dict, list[tuple[str | int, object]]]:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.request({"cmd": "start", "cwd": "/worktree", "model": "sol"})
        stream = daemon.attach("thread-1")
        await anext(stream)
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "id": 7,
                "method": "item/fileChange/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "change-1",
                    "changes": [{"path": "app.py", "kind": "update"}],
                },
            }
        )
        request = await anext(stream)
        await daemon.request(
            {
                "cmd": "deny",
                "id": "thread-1",
                "request": "7",
                "reason": "Keep app.py unchanged",
            }
        )
        response = await anext(stream)
        await stream.aclose()
        return request, response, bridge.responses

    request, response, responses = asyncio.run(scenario())

    assert request["request"] == {
        "subtype": "can_use_tool",
        "tool_use_id": "change-1",
        "tool_name": "Edit",
        "input": {"changes": [{"path": "app.py", "kind": "update"}]},
        "description": "Codex Edit request",
    }
    assert responses == [(7, {"decision": "decline"})]
    assert response["response"] == {
        "subtype": "success",
        "request_id": "7",
        "response": {"behavior": "deny", "message": "Keep app.py unchanged"},
    }


def test_a_failed_codex_approval_response_stays_pending_for_retry() -> None:
    async def scenario() -> tuple[str, dict, list[tuple[str | int, object]]]:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.request({"cmd": "start", "cwd": "/worktree", "model": "sol"})
        stream = daemon.attach("thread-1")
        await anext(stream)
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "command-1",
                    "command": "git status",
                },
            }
        )
        await anext(stream)
        bridge.response_error = RuntimeError("bridge closed")
        with pytest.raises(RuntimeError, match="bridge closed"):
            await daemon.request(
                {"cmd": "approve", "id": "thread-1", "request": "approval-1"}
            )
        waiting = await daemon.request({"cmd": "list"})
        waiting_state = waiting["agents"][0]["state"]
        bridge.response_error = None
        retried = await daemon.request(
            {"cmd": "approve", "id": "thread-1", "request": "approval-1"}
        )
        await stream.aclose()
        return waiting_state, retried, bridge.responses

    waiting_state, retried, responses = asyncio.run(scenario())

    assert waiting_state == "awaiting-permission"
    assert retried == {"ok": True}
    assert responses == [("approval-1", {"decision": "accept"})]


def test_codex_approval_does_not_restart_a_completed_turn() -> None:
    async def scenario() -> dict:
        bridge = ScriptedBridge()
        daemon = CodexDaemonClient(bridge)
        await daemon.request({"cmd": "start", "cwd": "/worktree", "model": "sol"})
        stream = daemon.attach("thread-1")
        await anext(stream)
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "command-1",
                    "command": "git status",
                },
            }
        )
        await anext(stream)
        await bridge.incoming_messages.put(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turnId": "turn-1"},
            }
        )
        await anext(stream)
        await daemon.request(
            {"cmd": "approve", "id": "thread-1", "request": "approval-1"}
        )
        listed = await daemon.request({"cmd": "list"})
        await stream.aclose()
        return listed

    result = asyncio.run(scenario())

    assert result["agents"][0]["state"] == "idle"


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
