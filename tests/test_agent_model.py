"""The agent state machine, replayed against recorded ``claude`` event streams.

Every fixture in ``tests/fixtures/agent_events/`` is a real NDJSON transcript,
captured from ``claude -p --input-format stream-json --output-format stream-json``
on v2.1.252. Nothing here is designed from an assumed event shape.
"""

import json
from pathlib import Path

import pytest

from maelstrom.agent_model import (
    EXITED,
    AgentState,
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
