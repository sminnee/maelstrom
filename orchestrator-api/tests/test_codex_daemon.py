"""Codex daemon adapter behaviour at the orchestrator host seam."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from mael_agent.agent_wire import AGENT_EXITED, BACKLOG_END, SEQ_KEY, TRUNCATED
from mael_orchestrator.codex_daemon import CodexDaemonClient

NOW = "2026-11-03T00:00:00Z"
RESUMED = json.loads(
    (Path(__file__).parent / "fixtures" / "codex-thread-resume.json").read_text()
)


class ScriptedBridge:
    """A Codex JSON-RPC bridge with scripted replies and notifications.

    ``fail`` names methods whose request raises, as a dropped proxy does.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.responses: list[tuple[str | int, object]] = []
        self.rejections: list[tuple[str | int, int]] = []
        self.fail: dict[str, RuntimeError] = {}
        self.resumed: dict = {"thread": {"id": "thread-1", "cwd": "/worktree"}}
        self.incoming_messages: asyncio.Queue[dict | Exception | None] = asyncio.Queue()

    async def request(self, method: str, params: dict):
        self.calls.append((method, params))
        if method in self.fail:
            raise self.fail[method]
        if method == "thread/start":
            return {"thread": {"id": "thread-1", "cwd": params["cwd"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "thread/resume":
            return self.resumed
        return {}

    async def incoming(self) -> AsyncIterator[dict]:
        while message := await self.incoming_messages.get():
            if isinstance(message, Exception):
                raise message
            yield message

    async def respond(self, request_id: str | int, result: object) -> None:
        if "respond" in self.fail:
            raise self.fail.pop("respond")
        self.responses.append((request_id, result))

    async def reject(self, request_id: str | int, code: int, message: str) -> None:
        self.rejections.append((request_id, code))


def daemon_for(bridge: ScriptedBridge) -> CodexDaemonClient:
    return CodexDaemonClient(bridge, clock=lambda: NOW)


async def started(bridge: ScriptedBridge, **payload) -> CodexDaemonClient:
    daemon = daemon_for(bridge)
    await daemon.request(
        {"cmd": "start", "cwd": "/worktree", "model": "sol", **payload}
    )
    return daemon


async def attached(daemon: CodexDaemonClient, from_seq: int = 0, epoch: str = ""):
    """An attach stream past its backlog, and the backlog it replayed."""
    stream = daemon.attach("thread-1", from_seq, epoch)
    backlog = []
    async for event in stream:
        backlog.append(event)
        if event.get("type") == BACKLOG_END:
            break
    return stream, backlog


def notify(method: str, **params) -> dict:
    return {"method": method, "params": {"threadId": "thread-1", **params}}


def approval(request_id, method="item/commandExecution/requestApproval", **params):
    return {
        "id": request_id,
        "method": method,
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "command": "/bin/zsh -lc 'git status'",
            "commandActions": [{"type": "unknown", "command": "git status"}],
            "cwd": "/worktree",
            **params,
        },
    }


def calls_of(bridge: ScriptedBridge, method: str) -> list[dict]:
    return [params for name, params in bridge.calls if name == method]


def test_start_gives_the_thread_its_instructions_env_and_opening_turn(tmp_path):
    prompt_file = tmp_path / "agent-prompt.md"
    prompt_file.write_text("Use <note> to report progress.\n")

    async def scenario():
        bridge = ScriptedBridge()
        reply = await (
            await started(
                bridge,
                prompt="Implement the Task.",
                mode="plan",
                env={"MAEL_TASK_ID": "maelstrom/2026-11-03.1"},
                system_prompt_file=str(prompt_file),
            )
        ).request({"cmd": "list"})
        return reply, bridge.calls

    listed, calls = asyncio.run(scenario())

    assert calls == [
        (
            "thread/start",
            {
                "cwd": "/worktree",
                "model": "gpt-5.6-sol",
                "sandbox": "read-only",
                "developerInstructions": "Use <note> to report progress.\n",
                "config": {
                    "shell_environment_policy": {
                        "set": {"MAEL_TASK_ID": "maelstrom/2026-11-03.1"}
                    }
                },
            },
        ),
        (
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "Implement the Task."}],
                "sandboxPolicy": {"type": "readOnly"},
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
            },
        ),
    ]
    [row] = listed["agents"]
    assert (row["state"], row["mode"], row["description"]) == ("processing", "plan", "")


def test_say_sends_text_and_images_and_a_compact_compacts():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        await daemon.request(
            {
                "cmd": "say",
                "id": "thread-1",
                "text": "Look at this.",
                "attachments": [{"path": "/tmp/shot.png", "media_type": "image/png"}],
            }
        )
        await daemon.request(
            {"cmd": "say", "id": "thread-1", "text": "/compact Keep it"}
        )
        return bridge

    bridge = asyncio.run(scenario())

    [turn] = calls_of(bridge, "turn/start")
    assert turn["input"] == [
        {"type": "text", "text": "Look at this."},
        {"type": "localImage", "path": "/tmp/shot.png"},
    ]
    assert calls_of(bridge, "thread/compact/start") == [{"threadId": "thread-1"}]


def test_a_mode_change_reaches_the_next_turn():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        reply = await daemon.request(
            {"cmd": "set-mode", "id": "thread-1", "mode": "plan"}
        )
        announced = await anext(stream)
        await daemon.request({"cmd": "say", "id": "thread-1", "text": "Plan it."})
        return reply, announced, bridge

    reply, announced, bridge = asyncio.run(scenario())

    assert reply == {"ok": True, "mode": "plan"}
    assert announced["method"] == "thread/settings/updated"
    assert announced["params"]["threadSettings"]["sandboxPolicy"] == {
        "type": "readOnly"
    }
    [turn] = calls_of(bridge, "turn/start")
    assert turn["sandboxPolicy"] == {"type": "readOnly"}


def test_normal_mode_asks_before_each_untrusted_command():
    """As a Claude normal session asks, and unlike auto."""

    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge, prompt="Go.", mode="normal")
        await daemon.request({"cmd": "set-mode", "id": "thread-1", "mode": "auto"})
        await daemon.request({"cmd": "say", "id": "thread-1", "text": "Go on."})
        return [turn["approvalPolicy"] for turn in calls_of(bridge, "turn/start")]

    assert asyncio.run(scenario()) == ["untrusted", "on-request"]


def test_a_shell_command_runs_on_the_thread():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        await daemon.request({"cmd": "run", "id": "thread-1", "command": "git status"})
        return bridge

    bridge = asyncio.run(scenario())

    assert calls_of(bridge, "thread/shellCommand") == [
        {"threadId": "thread-1", "command": "git status"}
    ]


def test_a_bridge_failure_is_an_error_reply_and_unsupported_commands_refuse():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        bridge.fail["turn/start"] = RuntimeError("The Codex app-server proxy closed.")
        said = await daemon.request({"cmd": "say", "id": "thread-1", "text": "Hi"})
        answered = await daemon.request({"cmd": "answer", "id": "thread-1"})
        return said, answered

    said, answered = asyncio.run(scenario())

    assert said == {"ok": False, "error": "The Codex app-server proxy closed."}
    assert answered["ok"] is False


def test_an_attach_replays_the_ring_after_its_cursor():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, first = await attached(daemon)
        for n in range(3):
            await bridge.incoming_messages.put(
                notify("turn/started", turn={"id": f"t{n}"})
            )
            await anext(stream)
        await stream.aclose()
        _, from_two = await attached(daemon, 2, first[-1]["epoch"])
        _, foreign = await attached(daemon, 2, "another-life")
        return from_two, foreign

    from_two, foreign = asyncio.run(scenario())

    assert [e[SEQ_KEY] for e in from_two if SEQ_KEY in e] == [3]
    assert [e[SEQ_KEY] for e in foreign if SEQ_KEY in e] == [1, 2, 3]
    assert from_two[-1] == {
        "type": BACKLOG_END,
        "epoch": from_two[-1]["epoch"],
        "seq": 3,
    }


def test_an_attach_says_what_the_ring_dropped(monkeypatch):
    monkeypatch.setattr("mael_orchestrator.codex_daemon.RECENT_LIMIT", 2)

    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        for n in range(4):
            await bridge.incoming_messages.put(
                notify("turn/started", turn={"id": f"t{n}"})
            )
            await anext(stream)
        await stream.aclose()
        _, backlog = await attached(daemon)
        return backlog

    backlog = asyncio.run(scenario())

    assert backlog[1] == {"type": TRUNCATED, "dropped": 2}


def test_restore_rebuilds_the_ring_from_the_turns_codex_kept():
    async def scenario():
        bridge = ScriptedBridge()
        bridge.resumed = RESUMED
        daemon = daemon_for(bridge)
        thread_id = RESUMED["thread"]["id"]
        await daemon.restore(
            [
                {
                    "id": thread_id,
                    "harness": "codex",
                    "status": "running",
                    "task_session_id": "task-session-1",
                    "cwd": "/worktree",
                    "model": "codex:luna",
                    "mode": "normal",
                },
                {"id": "gone", "harness": "codex", "status": "ended"},
            ]
        )
        stream = daemon.attach(thread_id)
        backlog = []
        async for event in stream:
            backlog.append(event)
            if event.get("type") == BACKLOG_END:
                break
        return backlog, await daemon.request({"cmd": "list"}), bridge.calls

    backlog, listed, calls = asyncio.run(scenario())

    assert [params["threadId"] for _, params in calls] == [RESUMED["thread"]["id"]]
    replayed = [
        (e["method"], e["params"].get("item", {}).get("type"))
        for e in backlog
        if e.get("type") == "codex_raw"
    ]
    assert replayed == [
        ("item/completed", "userMessage"),
        ("item/completed", "reasoning"),
        ("item/completed", "agentMessage"),
        ("item/completed", "commandExecution"),
        ("item/completed", "agentMessage"),
        ("turn/completed", None),
        ("item/completed", "commandExecution"),
        ("turn/completed", None),
    ]
    [row] = listed["agents"]
    assert (row["session"], row["state"]) == ("task-session-1", "idle")


def test_an_approval_and_its_answer_are_recorded_and_approve_needs_no_name():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(approval(0))
        request = await anext(stream)
        _, rejoined = await attached(daemon)
        reply = await daemon.request({"cmd": "approve", "id": "thread-1"})
        answer = await anext(stream)
        listed = await daemon.request({"cmd": "list"})
        await stream.aclose()
        return request, rejoined[0], answer, reply, listed, bridge.responses

    request, detail, answer, reply, listed, responses = asyncio.run(scenario())

    assert (request["method"], request["id"]) == (approval(0)["method"], 0)
    # A watcher that joins later learns the wait from the detail frame.
    assert detail["agent"]["waiting_tool"] == "Bash"
    assert detail["agent"]["waiting_input"] == {
        "command": "git status",
        "cwd": "/worktree",
    }
    assert reply == {"ok": True}
    assert responses == [(0, {"decision": "accept"})]
    assert (answer["id"], answer["result"]) == (0, {"decision": "accept"})
    assert listed["agents"][0]["state"] == "processing"


def test_a_deny_cancels_when_codex_offers_no_decline():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(
            approval(4, availableDecisions=["accept", "cancel"])
        )
        await anext(stream)
        await daemon.request(
            {"cmd": "deny", "id": "thread-1", "request": "4", "reason": "Not now"}
        )
        response = await anext(stream)
        await stream.aclose()
        return response, bridge.responses

    response, responses = asyncio.run(scenario())

    assert responses == [(4, {"decision": "cancel"})]
    assert (response["result"], response["mael_reason"]) == (
        {"decision": "cancel"},
        "Not now",
    )


def test_a_remembered_approve_sends_the_rule_and_errors_without_one():
    rule = {
        "acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["git", "status"]}
    }

    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(
            approval(5, availableDecisions=["accept", rule, "cancel"])
        )
        await bridge.incoming_messages.put(
            approval(6, availableDecisions=["accept", "cancel"])
        )
        await anext(stream)
        await anext(stream)
        remembered = await daemon.request(
            {"cmd": "approve", "id": "thread-1", "request": "5", "remember": True}
        )
        refused = await daemon.request(
            {"cmd": "approve", "id": "thread-1", "request": "6", "remember": True}
        )
        await stream.aclose()
        return remembered, refused, bridge.responses

    remembered, refused, responses = asyncio.run(scenario())

    assert remembered == {"ok": True}
    assert responses == [(5, {"decision": rule})]
    assert refused["ok"] is False
    assert "offers no remember" in refused["error"]


def test_interrupt_declines_open_approvals_first_and_needs_a_turn():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        idle = await daemon.request({"cmd": "interrupt", "id": "thread-1"})
        await daemon.request({"cmd": "say", "id": "thread-1", "text": "Go."})
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(approval(0))
        await anext(stream)
        await daemon.request({"cmd": "interrupt", "id": "thread-1"})
        await stream.aclose()
        return idle, bridge

    idle, bridge = asyncio.run(scenario())

    assert idle["ok"] is False
    assert bridge.responses == [(0, {"decision": "decline"})]
    assert bridge.calls[-1] == (
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "turn-1"},
    )


def test_a_server_request_maelstrom_cannot_answer_is_refused_and_shown():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(
            {
                "id": 9,
                "method": "item/tool/requestUserInput",
                "params": {"threadId": "thread-1", "questions": []},
            }
        )
        shown = await anext(stream)
        await stream.aclose()
        return shown, bridge.rejections

    shown, rejections = asyncio.run(scenario())

    assert rejections == [(9, -32601)]
    assert shown["method"] == "item/tool/requestUserInput"


def test_a_dropped_connection_ends_the_watch_and_the_next_command_resubscribes():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(RuntimeError("proxy closed"))
        ended = [event async for event in stream]
        await daemon.request({"cmd": "say", "id": "thread-1", "text": "Still there?"})
        return ended, bridge.calls

    ended, calls = asyncio.run(scenario())

    assert ended == []
    assert ("thread/resume", {"threadId": "thread-1", "excludeTurns": True}) in calls
    assert calls[-1][0] == "turn/start"


def test_stop_ends_the_watch_with_an_exit():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await daemon.request({"cmd": "stop", "id": "thread-1"})
        return [event async for event in stream], await daemon.request({"cmd": "list"})

    ended, listed = asyncio.run(scenario())

    assert ended == [{"type": AGENT_EXITED, "exit_code": 0}]
    assert listed["agents"] == []


def test_a_failed_approval_answer_stays_pending_for_a_retry():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(approval(0))
        await anext(stream)
        bridge.fail["respond"] = RuntimeError("The Codex app-server proxy closed.")
        failed = await daemon.request({"cmd": "approve", "id": "thread-1"})
        waiting = (await daemon.request({"cmd": "list"}))["agents"][0]["state"]
        retried = await daemon.request({"cmd": "approve", "id": "thread-1"})
        await stream.aclose()
        return failed, waiting, retried, bridge.responses

    failed, waiting, retried, responses = asyncio.run(scenario())

    assert failed["ok"] is False
    assert waiting == "awaiting-permission"
    assert retried == {"ok": True}
    assert responses == [(0, {"decision": "accept"})]


def test_a_file_change_approval_waits_as_an_edit():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
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
        await anext(stream)
        await stream.aclose()
        _, backlog = await attached(daemon)
        return backlog[0]["agent"]

    detail = asyncio.run(scenario())

    assert (detail["waiting_tool"], detail["waiting_input"]) == (
        "Edit",
        {"changes": [{"path": "app.py", "kind": "update"}]},
    )


def test_a_turn_end_drops_the_approvals_it_held():
    """Codex took the request away, so it must not be answered, or listed."""

    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge, prompt="Go.")
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(approval(0))
        await anext(stream)
        await bridge.incoming_messages.put(
            notify("turn/completed", turn={"id": "turn-1", "status": "interrupted"})
        )
        await anext(stream)
        approved = await daemon.request({"cmd": "approve", "id": "thread-1"})
        listed = await daemon.request({"cmd": "list"})
        await stream.aclose()
        return approved, listed

    approved, listed = asyncio.run(scenario())

    assert approved["ok"] is False
    assert listed["agents"][0]["state"] == "idle"


def test_resume_brings_a_thread_back_in_the_launch_worktree_and_says_its_text():
    async def scenario():
        bridge = ScriptedBridge()
        bridge.resumed = RESUMED
        daemon = daemon_for(bridge)
        thread_id = RESUMED["thread"]["id"]
        reply = await daemon.request(
            {
                "cmd": "resume",
                "id": thread_id,
                "text": "Carry on.",
                "session": "s1",
                "cwd": "/worktree-2",
            }
        )
        return thread_id, reply, bridge, await daemon.request({"cmd": "list"})

    thread_id, reply, bridge, listed = asyncio.run(scenario())

    assert reply == {"ok": True, "id": thread_id}
    assert calls_of(bridge, "thread/resume") == [
        {"threadId": thread_id, "cwd": "/worktree-2"}
    ]
    [turn] = calls_of(bridge, "turn/start")
    assert turn["input"] == [{"type": "text", "text": "Carry on."}]
    assert listed["agents"][0]["session"] == "s1"


def test_a_start_whose_opening_turn_fails_leaves_no_thread():
    async def scenario():
        bridge = ScriptedBridge()
        bridge.fail["turn/start"] = RuntimeError("Codex did not answer turn/start.")
        daemon = daemon_for(bridge)
        reply = await daemon.request(
            {"cmd": "start", "cwd": "/worktree", "model": "sol", "prompt": "Go."}
        )
        return reply, await daemon.request({"cmd": "list"}), bridge

    reply, listed, bridge = asyncio.run(scenario())

    assert reply["ok"] is False
    assert listed["agents"] == []
    assert calls_of(bridge, "thread/unsubscribe") == [{"threadId": "thread-1"}]


def test_a_thread_that_did_not_resubscribe_is_tried_again():
    async def scenario():
        bridge = ScriptedBridge()
        daemon = await started(bridge)
        stream, _ = await attached(daemon)
        await bridge.incoming_messages.put(RuntimeError("proxy closed"))
        [event async for event in stream]
        bridge.fail["thread/resume"] = RuntimeError("proxy still down")
        await daemon.request({"cmd": "list"})
        await daemon.request({"cmd": "set-mode", "id": "thread-1", "mode": "auto"})
        bridge.fail.pop("thread/resume")
        await daemon.request({"cmd": "set-mode", "id": "thread-1", "mode": "auto"})
        return calls_of(bridge, "thread/resume")

    assert len(asyncio.run(scenario())) == 2
