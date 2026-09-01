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
    MESSAGE_CHARS,
    MESSAGE_LIMIT,
    MESSAGE_SUMMARY_CHARS,
    AgentState,
    apply_event,
    build_agent_argv,
    build_agent_detail,
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


# --- retained messages -----------------------------------------------------


def test_the_agent_keeps_what_it_said():
    """A row that says only "processing" cannot say what the agent is doing."""
    state = replay("normal-turn.jsonl")
    assert state.messages == ("Hello there, friend",)


def test_a_tool_call_is_not_a_message():
    """A tool call is an action, not something the agent chose to say."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(
        state,
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        },
    )
    assert state.messages == ()


def test_a_thinking_block_is_not_a_message():
    """Reasoning the agent did not choose to say is not a message."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(
        state,
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "hmm"}]},
        },
    )
    assert state.messages == ()


def _say(state: AgentState, text: str) -> AgentState:
    return apply_event(
        state,
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
    )


def test_only_the_last_few_messages_are_kept():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for i in range(MESSAGE_LIMIT + 3):
        state = _say(state, f"line {i}")
    assert len(state.messages) == MESSAGE_LIMIT
    assert state.messages[-1] == f"line {MESSAGE_LIMIT + 2}"


def test_a_huge_message_is_truncated_at_capture():
    """Bounding the count alone would still let one agent hold megabytes."""
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), "x" * (MESSAGE_CHARS * 3))
    assert len(state.messages[0]) <= MESSAGE_CHARS


def test_a_message_arriving_during_a_wait_is_still_kept():
    """The pending guard protects the status only. The words are the point."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = _say(state, "while you decide, here is the context")
    assert state.status == "awaiting-question"
    assert state.messages[-1] == "while you decide, here is the context"


def test_the_row_shows_what_the_agent_last_said():
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), "done with the tests")
    assert build_agent_row(state)["last_message"] == "done with the tests"


def test_the_row_message_is_one_short_line():
    """A table cell is one line, however the agent laid its message out."""
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), "first\nsecond " + "y" * 200)
    cell = build_agent_row(state)["last_message"]
    assert "\n" not in cell
    assert cell.startswith("first second")
    assert len(cell) <= MESSAGE_SUMMARY_CHARS


# --- the detail `mael agent show` renders ----------------------------------


def test_detail_is_a_superset_of_the_row():
    """``show`` and ``list`` must never disagree about the same agent."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    detail = build_agent_detail(state)
    assert build_agent_row(state).items() <= detail.items()


def test_detail_carries_every_option_with_its_description():
    """The options never reached the user before — that is the whole point."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    detail = build_agent_detail(state)
    question = detail["questions"][0]
    assert question["question"] == "Which colour do you prefer?"
    assert question["header"] == "Colour"
    assert question["multi_select"] is False
    assert [o["label"] for o in question["options"]] == ["Red", "Green", "Blue"]
    assert question["options"][1]["description"] == "Natural, calm, fresh."


def test_detail_names_the_waiting_tool_and_its_input():
    state = replay("permission-request.jsonl", stop_before_control=True)
    detail = build_agent_detail(state)
    assert detail["waiting_kind"] == "awaiting-permission"
    assert detail["waiting_tool"] == "WebFetch"
    assert detail["waiting_input"]["url"]
    assert detail["questions"] == []


def test_detail_finds_the_plan_exit_plan_mode_does_not_carry():
    """``ExitPlanMode`` has an empty input; the plan is the text just before it."""
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert state.pending is not None
    assert state.pending.input == {}
    assert "Verification" in build_agent_detail(state)["plan"]


def test_detail_has_no_plan_when_the_wait_is_not_a_plan_review():
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    assert build_agent_detail(state)["plan"] == ""


def test_detail_shows_the_last_messages_in_full():
    """A summary is a table's job. ``show`` is where the whole text belongs."""
    long_text = "y" * (MESSAGE_SUMMARY_CHARS * 4)
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), long_text)
    assert build_agent_detail(state)["messages"] == [long_text]


def test_detail_of_an_idle_agent_still_has_every_key():
    keys = set(build_agent_detail(AgentState(agent_id="a1", cwd="/tmp/x")))
    assert keys == set(build_agent_detail(replay("plan-review.jsonl")))
