"""The agent state machine, replayed against recorded ``claude`` event streams.

Every fixture in ``tests/fixtures/agent_events/`` is a real NDJSON transcript,
captured from ``claude -p --input-format stream-json --output-format stream-json``
on v2.1.252. Nothing here is designed from an assumed event shape.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from maelstrom import agent_cli
from maelstrom.agent_daemon import (
    EXITED,
    Agent,
    AgentDaemon,
    AgentState,
    RecordingDaemonClient,
    SocketDaemonClient,
    apply_event,
    build_agent_argv,
    build_agent_row,
    mark_exited,
    reply_for_answer,
    reply_for_approval,
    reply_for_denial,
    user_message,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"


def _stub_agent(agent_id: str = "a1") -> Agent:
    """An `Agent` with a stub child, so `handle` is testable with no subprocess."""
    proc = MagicMock()
    proc.stdin.is_closing.return_value = True
    return Agent(agent_id, "/tmp/x", proc)


def replay(name: str, stop_before_control: bool = False) -> AgentState:
    """Feed one fixture through the reducer and return the final state.

    With ``stop_before_control`` the replay halts on the first ``control_request``
    without answering it — that is what the daemon sees while an agent waits.
    """
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        state = apply_event(state, event)
        if stop_before_control and event.get("type") == "control_request":
            break
    return state


# --- states derived from real transcripts ---------------------------------


def test_a_completed_turn_ends_idle():
    state = replay("normal-turn.jsonl")
    assert state.status == "idle"
    assert state.session_id == "029ed263-b318-4d4e-a661-32f9c9f23f19"


def test_an_assistant_message_marks_the_agent_processing():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, {"type": "assistant", "message": {"content": []}})
    assert state.status == "processing"


def test_a_permission_request_awaits_permission():
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert state.status == "awaiting-permission"
    assert state.pending is not None
    assert state.pending.tool_name == "WebFetch"


def test_a_question_awaits_a_question_not_a_permission():
    """The tool name is what separates the two wait kinds."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    assert state.status == "awaiting-question"
    assert state.pending is not None
    assert state.pending.questions == ["Which colour do you prefer?"]


def test_an_exit_plan_mode_awaits_plan_review():
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert state.status == "awaiting-plan-review"
    assert state.pending is not None
    assert state.pending.tool_name == "ExitPlanMode"


def test_an_assistant_event_does_not_wipe_a_pending_wait():
    """A row must never say ``processing`` while it still names what it waits on."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = apply_event(state, {"type": "assistant", "message": {"content": []}})
    assert state.status == "awaiting-question"
    assert state.pending is not None


def test_answering_the_pending_request_clears_the_wait():
    state = replay("question-answered.jsonl")
    assert state.status == "idle"
    assert state.pending is None


def test_a_denied_tool_does_not_leave_the_agent_waiting():
    """A hard deny is terminal — the agent carries on, it does not wait."""
    state = replay("permission-denied.jsonl")
    assert state.status == "idle"
    assert state.pending is None


def test_a_result_event_records_the_cost():
    state = replay("normal-turn.jsonl")
    assert state.total_cost_usd == pytest.approx(0.1495855)


def test_a_dead_agent_is_not_left_looking_like_it_waits():
    """A crashed agent must not keep advertising a wait nobody can answer."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = mark_exited(state, 1)
    assert state.status == EXITED
    assert state.pending is None
    assert build_agent_row(state)["waiting_on"] == ""


def test_an_exited_row_reports_the_exit_code():
    state = mark_exited(AgentState(agent_id="a1", cwd="/tmp/x"), 137)
    assert build_agent_row(state)["state"] == "exited(137)"


# --- replies the daemon writes back ----------------------------------------


def test_reply_for_answer_puts_the_choice_in_updated_input():
    """The agent reads answers from ``updatedInput['answers']``, keyed by question."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    assert state.pending is not None
    reply = reply_for_answer(state.pending, "Green")
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == {
        "Which colour do you prefer?": "Green"
    }
    assert reply["response"]["request_id"] == state.pending.request_id


def test_reply_for_approval_allows_with_the_input_unchanged():
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert state.pending is not None
    reply = reply_for_approval(state.pending)
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"] == state.pending.input


def test_reply_for_denial_carries_the_reason():
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert state.pending is not None
    reply = reply_for_denial(state.pending, "not on a public network")
    payload = reply["response"]["response"]
    assert payload["behavior"] == "deny"
    assert payload["message"] == "not on a public network"


def test_user_message_is_a_stream_json_user_turn():
    msg = user_message("also update the README")
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["message"]["content"] == [
        {"type": "text", "text": "also update the README"}
    ]


# --- argv ------------------------------------------------------------------


def test_argv_carries_the_flags_the_pipe_needs():
    argv = build_agent_argv()
    assert argv[:2] == ["claude", "-p"]
    for flag in ("--input-format", "--output-format", "--verbose"):
        assert flag in argv
    # Without this the agent auto-allows instead of asking, so no wait is ever
    # observable. Confirmed against v2.1.252.
    assert "--permission-prompt-tool" in argv


def test_argv_pins_a_session_id_when_given():
    argv = build_agent_argv(session_id="dead-beef")
    assert argv[argv.index("--session-id") + 1] == "dead-beef"


def test_argv_passes_the_permission_mode_through():
    argv = build_agent_argv(permission_mode="auto")
    assert argv[argv.index("--permission-mode") + 1] == "auto"


# --- the row `mael agent list` renders -------------------------------------


def test_row_reports_the_wait_kind_not_just_busy():
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    row = build_agent_row(state)
    assert row["state"] == "awaiting-question"
    assert row["waiting_on"] == "Which colour do you prefer?"


def test_row_of_an_idle_agent_has_nothing_pending():
    row = build_agent_row(replay("normal-turn.jsonl"))
    assert row["state"] == "idle"
    assert row["waiting_on"] == ""


def test_every_row_key_is_always_present():
    """Same contract as ``build_session_row`` — no key is ever missing."""
    keys = set(build_agent_row(AgentState(agent_id="a1", cwd="/tmp/x")))
    assert keys == set(build_agent_row(replay("plan-review.jsonl")))


# --- the daemon command surface --------------------------------------------


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


# --- the transport fake ----------------------------------------------------


def run_cli(argv: list[str], replies: list[dict] | None = None):
    """Drive `mael agent` through the fake transport, and return (result, client)."""
    client = RecordingDaemonClient(replies=list(replies or []))
    agent_cli._client_factory = lambda: client
    try:
        return CliRunner().invoke(agent_cli.agent, argv), client
    finally:
        agent_cli._client_factory = SocketDaemonClient


def test_start_sends_the_cwd_and_the_prompt():
    result, client = run_cli(
        ["start", ".", "--prompt", "go", "--mode", "auto"], [{"id": "a1"}]
    )
    assert result.exit_code == 0
    assert result.output.strip() == "a1"
    sent = client.calls[0]
    assert sent["cmd"] == "start"
    assert sent["prompt"] == "go"
    assert sent["mode"] == "auto"
    assert Path(sent["cwd"]).is_absolute()


def test_answer_sends_the_choice():
    _, client = run_cli(["answer", "a1", "Green"])
    assert client.calls == [{"cmd": "answer", "id": "a1", "choice": "Green"}]


def test_deny_sends_the_reason():
    _, client = run_cli(["deny", "a1", "--reason", "not now"])
    assert client.calls[0]["reason"] == "not now"


def test_list_renders_the_wait_kind():
    rows = [
        build_agent_row(replay("question-unanswered.jsonl", stop_before_control=True))
    ]
    result, _ = run_cli(["list"], [{"agents": rows}])
    assert "awaiting-question" in result.output
    assert "Which colour do you prefer?" in result.output


def test_list_says_so_when_nothing_runs():
    result, _ = run_cli(["list"], [{"agents": []}])
    assert "No agents running." in result.output


def test_a_daemon_error_exits_non_zero():
    """The CLI must fail loudly, not print an error and report success."""
    result, _ = run_cli(["say", "a1", "hi"], [{"error": "agent a1 has exited"}])
    assert result.exit_code == 1
    assert "has exited" in result.output
