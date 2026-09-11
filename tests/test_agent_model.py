"""The agent state machine, replayed against recorded ``claude`` event streams.

Every fixture in ``tests/fixtures/agent_events/`` is a real NDJSON transcript,
captured from ``claude -p --input-format stream-json --output-format stream-json``.
Nothing here is designed from an assumed event shape.

Two plan-review fixtures record the two shapes ``ExitPlanMode`` takes.
``plan-review-with-plan.jsonl`` is the normal one, where the request carries the
plan. ``plan-review.jsonl`` is an agent whose plan-file write a sandbox refused,
so the request arrives bare and the plan is in a message instead.
"""

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from maelstrom.agent_model import (
    AWAITING_PERMISSION,
    EXITED,
    IDLE,
    MESSAGE_CHARS,
    MESSAGE_SUMMARY_CHARS,
    PROCESSING,
    SEQ_KEY,
    SPEC_STOPPED,
    SUB_COMPLETED,
    SUB_FAILED,
    SUB_RUNNING,
    SUB_STOPPED,
    SUBAGENT_LIMIT,
    TS_KEY,
    AgentSpec,
    AgentState,
    PendingRequest,
    TranscriptMeta,
    UsageWindow,
    apply_event,
    build_agent_argv,
    build_agent_detail,
    build_agent_env,
    build_agent_row,
    build_start_payload,
    build_stopped_row,
    build_stopped_rows,
    build_subagent_detail,
    build_subagent_rows,
    interrupt_request,
    mark_exited,
    reply_for_answer,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
    set_mode_request,
    shell_input_message,
    shell_output_message,
    spec_from_dict,
    spec_to_dict,
    user_message,
)
from maelstrom.session_discovery import LiveSession, LiveSessionSet

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"


def only_pending(state: AgentState) -> PendingRequest:
    """The one ask ``state`` holds, for a test about a single wait.

    An agent can hold several. A test that means "the wait" says so through
    this, and fails loudly rather than silently reading the first of many.
    """
    assert len(state.own_pending) == 1, (
        f"expected one wait, found {len(state.own_pending)}"
    )
    return next(iter(state.own_pending.values()))


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
    assert only_pending(state).tool_name == "WebFetch"


def test_a_question_awaits_a_question_not_a_permission():
    """The tool name is what separates the two wait kinds."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    assert state.status == "awaiting-question"
    assert only_pending(state).questions == ["Which colour do you prefer?"]


def test_an_exit_plan_mode_awaits_plan_review():
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert state.status == "awaiting-plan-review"
    assert only_pending(state).tool_name == "ExitPlanMode"


def test_an_assistant_event_does_not_wipe_a_pending_wait():
    """A row must never say ``processing`` while it still names what it waits on."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = apply_event(state, {"type": "assistant", "message": {"content": []}})
    assert state.status == "awaiting-question"


def test_answering_the_pending_request_clears_the_wait():
    state = replay("question-answered.jsonl")
    assert state.status == "idle"
    assert state.own_pending == {}


def test_a_denied_tool_does_not_leave_the_agent_waiting():
    """A hard deny is terminal — the agent carries on, it does not wait."""
    state = replay("permission-denied.jsonl")
    assert state.status == "idle"
    assert state.own_pending == {}


def test_a_result_event_records_the_cost():
    state = replay("normal-turn.jsonl")
    assert state.total_cost_usd == pytest.approx(0.1495855)


def test_a_result_event_adds_its_tokens_to_the_running_total():
    """The turn's four counts, summed: 2 + 14429 + 10121 + 9 off the fixture."""
    state = replay("normal-turn.jsonl")
    assert state.total_tokens == 24561
    assert build_agent_row(state)["tokens"] == 24561


def test_a_second_turn_adds_to_the_token_total_rather_than_replacing_it():
    """A ``result`` reports the turn, not the session — unlike ``total_cost_usd``."""
    state = replay("normal-turn.jsonl")
    state = apply_event(
        state,
        {
            "type": "result",
            "total_cost_usd": 0.1495855,
            "usage": {"input_tokens": 5, "output_tokens": 7},
        },
    )
    assert state.total_tokens == 24561 + 12
    assert state.total_cost_usd == pytest.approx(0.1495855)


def test_a_result_without_usage_leaves_the_token_total_alone():
    """A malformed or usage-free result must not zero what the session spent."""
    state = replay("normal-turn.jsonl")
    state = apply_event(state, {"type": "result", "total_cost_usd": 0.2})
    assert state.total_tokens == 24561


def test_an_assistant_event_records_what_the_prompt_holds():
    """The prompt's three counts, summed: 2 + 10121 + 14429 off the fixture.

    ``output_tokens`` is out. It is what the model wrote, not what the prompt
    holds — it joins the context for the *next* request, which that request's
    own ``assistant`` event then reports.
    """
    state = replay("normal-turn.jsonl")
    assert state.context_tokens == 24552
    assert build_agent_row(state)["context_tokens"] == 24552


def test_a_smaller_later_reading_brings_the_context_down():
    """Occupancy is a level, not a total, so it falls as well as climbs.

    The fall is the point, not a side effect: a compact is what the number is
    read for, and taking the larger of two readings — as the poll deliberately
    does for spend and size, which only climb — would hide exactly that. The
    second reading here is lower than the first for that reason.
    """
    state = replay("normal-turn.jsonl")
    state = apply_event(
        state,
        {
            "type": "assistant",
            "message": {
                "content": [],
                "usage": {"input_tokens": 3, "cache_read_input_tokens": 18159},
            },
        },
    )
    assert state.context_tokens == 18162


def test_an_assistant_event_without_usage_leaves_the_context_alone():
    """A malformed or usage-free event must not read as an emptied context."""
    state = replay("normal-turn.jsonl")
    state = apply_event(state, {"type": "assistant", "message": {"content": []}})
    assert state.context_tokens == 24552


def test_a_result_event_does_not_move_the_context():
    """A ``result`` sums the turn's requests, so its counts are not occupancy.

    Its ``cache_read`` is every request's read added up. Taking it would report
    a context larger than the one the agent actually holds, so the usage here
    is deliberately unlike the assistant reading it must not displace.
    """
    state = replay("normal-turn.jsonl")
    state = apply_event(state, {"type": "result", "usage": {"input_tokens": 999_999}})
    assert state.context_tokens == 24552


def test_a_blocked_agent_still_reports_the_context_it_holds():
    """A wait does not empty the prompt, so the reading outlives the block.

    This is why the level is set above the pending-wait guard. A refactor that
    moved it below the early return would read 0 for every blocked agent —
    which is the state a reader deciding to compact is most often looking at.
    """
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert state.status == "awaiting-permission"
    # The last reading before the wait: 2 + 18123 + 441 off the fixture.
    assert state.context_tokens == 18566


def test_a_dead_agent_is_not_left_looking_like_it_waits():
    """A crashed agent must not keep advertising a wait nobody can answer."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = mark_exited(state, 1)
    assert state.status == EXITED
    assert state.own_pending == {}
    assert build_agent_row(state)["waiting_on"] == ""


def test_an_exited_row_reports_the_exit_code():
    state = mark_exited(AgentState(agent_id="a1", cwd="/tmp/x"), 137)
    assert build_agent_row(state)["state"] == "exited(137)"


# --- replies the daemon writes back ----------------------------------------


def test_reply_for_answer_puts_the_choice_in_updated_input():
    """The agent reads answers from ``updatedInput['answers']``, keyed by question."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    reply = reply_for_answer(only_pending(state), "Green")
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == {
        "Which colour do you prefer?": "Green"
    }
    assert reply["response"]["request_id"] == only_pending(state).request_id


def test_reply_for_approval_allows_with_the_input_unchanged():
    state = replay("permission-request.jsonl", stop_before_control=True)
    reply = reply_for_approval(only_pending(state))
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"] == only_pending(state).input


def test_reply_for_denial_carries_the_reason():
    state = replay("permission-request.jsonl", stop_before_control=True)
    reply = reply_for_denial(only_pending(state), "not on a public network")
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


def test_user_message_puts_images_before_the_text():
    """The order a live agent accepts.

    Verified against ``claude -p --input-format stream-json`` on v2.1.261: an
    image block ahead of the text block is read as an image the model can see.
    """
    msg = user_message("what is wrong here?", images=[("image/png", b"\x89PNG!")])

    assert msg["message"]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(b"\x89PNG!").decode(),
            },
        },
        {"type": "text", "text": "what is wrong here?"},
    ]


def test_user_message_carries_an_image_with_no_words():
    """A screenshot on its own is a message. The text block is dropped."""
    msg = user_message("", images=[("image/png", b"\x89PNG!")])

    assert msg["message"]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(b"\x89PNG!").decode(),
            },
        }
    ]


def test_user_message_with_no_images_is_unchanged():
    """The no-image call stays byte-identical: every launch goes through it."""
    plain = user_message("hello")
    assert user_message("hello", images=None) == plain
    assert user_message("hello", images=[]) == plain


# --- argv ------------------------------------------------------------------


def test_argv_carries_the_flags_the_pipe_needs():
    argv = build_agent_argv()
    assert argv[:2] == ["claude", "-p"]
    for flag in ("--input-format", "--output-format", "--verbose"):
        assert flag in argv
    # Without this the agent auto-allows instead of asking, so no wait is ever
    # observable. Confirmed against v2.1.252.
    assert "--permission-prompt-tool" in argv
    # Without this a subagent's stream carries its tool calls only, never its
    # words. Confirmed against v2.1.260.
    assert "--forward-subagent-text" in argv
    # Without this the child echoes no stdin user turn, so a `say` never
    # reaches the transcript. Confirmed against v2.1.261.
    assert "--replay-user-messages" in argv


def test_argv_pins_a_session_id_when_given():
    argv = build_agent_argv(session_id="dead-beef")
    assert argv[argv.index("--session-id") + 1] == "dead-beef"


def test_argv_passes_the_permission_mode_through():
    argv = build_agent_argv(permission_mode="auto")
    assert argv[argv.index("--permission-mode") + 1] == "auto"


def test_argv_resumes_an_existing_session_instead_of_pinning_one():
    # `--session-id <id>` on an id claude already knows is refused, so a resume
    # has to switch flags. Same switch as worktree_launcher.build_claude_command.
    argv = build_agent_argv(session_id="dead-beef", resume=True)
    assert argv[argv.index("--resume") + 1] == "dead-beef"
    assert "--session-id" not in argv


# --- the child's environment ------------------------------------------------


def test_env_drops_the_markers_that_suppress_a_transcript():
    # An inherited CLAUDE_CODE_CHILD_SESSION can stop the child writing a
    # transcript, which is the one thing a resume depends on.
    base = {"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1"}
    env = build_agent_env(base, None)
    assert "CLAUDECODE" not in env
    assert "CLAUDE_CODE_CHILD_SESSION" not in env
    assert env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] == "1"
    assert env["PATH"] == "/bin"


def test_env_leaves_the_caller_the_last_word():
    # The socket contract has no allowlist: a client may set anything, including
    # the vars the scrub removes.
    env = build_agent_env({"PATH": "/bin"}, {"MAEL_TASK_ID": "t1", "CLAUDECODE": "1"})
    assert env["MAEL_TASK_ID"] == "t1"
    assert env["CLAUDECODE"] == "1"


def test_env_drops_the_inherited_virtualenv():
    # The daemon is started as a service from `_main`, so its own VIRTUAL_ENV
    # names `_main`'s venv — the wrong one for an agent in any other worktree.
    env = build_agent_env({"PATH": "/bin", "VIRTUAL_ENV": "/p/_main/.venv"}, None)
    assert "VIRTUAL_ENV" not in env
    assert env["PATH"] == "/bin"


def test_env_lets_a_client_set_the_virtualenv_outright():
    # The socket contract has no allowlist, and the scrub does not add one.
    env = build_agent_env(
        {"VIRTUAL_ENV": "/p/_main/.venv"}, {"VIRTUAL_ENV": "/p/alpha/.venv"}
    )
    assert env["VIRTUAL_ENV"] == "/p/alpha/.venv"


def test_env_does_not_mutate_the_base():
    base = {"CLAUDECODE": "1"}
    build_agent_env(base, None)
    assert base == {"CLAUDECODE": "1"}


# --- the spawn record -------------------------------------------------------


def test_spec_round_trips_through_plain_json():
    spec = AgentSpec(
        agent_id="a1",
        cwd="/w",
        session_id="sid",
        permission_mode="auto",
        model="opus",
        env={"MAEL_TASK_ID": "t1"},
        prompt="go",
        status="exited",
        exit_code=-9,
    )
    assert spec_from_dict(spec_to_dict(spec)) == spec


def test_spec_from_dict_fills_in_what_an_older_record_lacks():
    spec = spec_from_dict({"agent_id": "a1", "cwd": "/w", "session_id": "sid"})
    assert spec.status == "running"
    assert spec.env == {}
    # The fields that name the child: absent from a record an older daemon
    # wrote, and read as "unknown" rather than refused.
    assert spec.pid is None
    assert spec.started_at == ""
    assert spec.last_status == ""


def test_the_record_names_its_child_and_round_trips_it():
    """The pid is what lets the next daemon tell a live child from a dead one."""
    spec = AgentSpec(
        agent_id="a1",
        cwd="/w",
        session_id="sid",
        pid=4242,
        started_at="2026-09-05T10:00:00+00:00",
        last_status="idle",
    )
    data = spec_to_dict(spec)
    assert data["pid"] == 4242
    assert data["started_at"] == "2026-09-05T10:00:00+00:00"
    assert data["last_status"] == "idle"
    assert spec_from_dict(data) == spec


def test_the_row_carries_the_childs_pid():
    """So `mael agent list` and the orchestrator can see which process an agent is."""
    row = build_agent_row(AgentState(agent_id="a1", cwd="/tmp/x", pid=4242))
    assert row["pid"] == 4242
    assert build_agent_row(AgentState(agent_id="a1", cwd="/tmp/x"))["pid"] is None


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


# --- what the agent last said ------------------------------------------------


def test_the_agent_keeps_what_it_last_said():
    """A row that says only "processing" cannot say what the agent is doing."""
    state = replay("normal-turn.jsonl")
    assert state.last_message == "Hello there, friend"


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
    assert state.last_message == ""


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
    assert state.last_message == ""


def _say(state: AgentState, text: str) -> AgentState:
    return apply_event(
        state,
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
    )


def test_only_the_last_message_is_kept():
    """Claude's session transcript holds the conversation; this holds one message."""
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), "first")
    state = _say(state, "second")
    assert state.last_message == "second"


def test_a_huge_message_is_truncated_at_capture():
    """One agent must not be able to hold megabytes in the daemon."""
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), "x" * (MESSAGE_CHARS * 3))
    assert len(state.last_message) <= MESSAGE_CHARS


def test_a_message_arriving_during_a_wait_is_still_kept():
    """The pending guard protects the status only. The words are the point."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    state = _say(state, "while you decide, here is the context")
    assert state.status == "awaiting-question"
    assert state.last_message == "while you decide, here is the context"


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


def test_detail_reads_the_plan_the_request_carries():
    """``ExitPlanMode`` carries the plan in its input. Prefer it to any guess."""
    state = replay("plan-review-with-plan.jsonl", stop_before_control=True)
    detail = build_agent_detail(state)
    assert "## Verification" in detail["plan"]
    assert detail["plan"] == only_pending(state).input["plan"]
    assert detail["plan_file"].endswith(".md")


def test_detail_falls_back_to_the_last_message_when_the_request_is_bare():
    """A sandboxed plan write leaves an empty input, and the text in a message."""
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert only_pending(state).input == {}
    detail = build_agent_detail(state)
    assert "Verification" in detail["plan"]
    assert detail["plan_file"] == ""


def test_detail_has_no_plan_when_the_wait_is_not_a_plan_review():
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    assert build_agent_detail(state)["plan"] == ""


def test_detail_shows_the_last_message_in_full():
    """A summary is a table's job. ``show`` is where the whole text belongs."""
    long_text = "y" * (MESSAGE_SUMMARY_CHARS * 4)
    state = _say(AgentState(agent_id="a1", cwd="/tmp/x"), long_text)
    assert build_agent_detail(state)["message"] == long_text


def test_detail_of_an_idle_agent_still_has_every_key():
    keys = set(build_agent_detail(AgentState(agent_id="a1", cwd="/tmp/x")))
    assert keys == set(build_agent_detail(replay("plan-review.jsonl")))


def test_reply_for_answers_files_each_answer_under_its_question():
    """The orchestrator UI answers every question at once, each by its text."""
    state = replay("question-unanswered.jsonl", stop_before_control=True)
    answers = {"Which colour do you prefer?": "Blue"}
    reply = reply_for_answers(only_pending(state), answers)
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == answers
    assert (
        payload["updatedInput"]["questions"] == only_pending(state).input["questions"]
    )


def test_interrupt_request_is_a_control_request_with_the_interrupt_subtype():
    """Interrupt is a host->child control_request, not a user message."""
    request = interrupt_request("req-7")
    assert request["type"] == "control_request"
    assert request["request_id"] == "req-7"
    assert request["request"] == {"subtype": "interrupt"}


def test_an_interrupted_turn_ends_idle():
    """The child answers the interrupt, then closes the turn with an error result."""
    state = replay("interrupt.jsonl")
    assert state.status == IDLE
    assert state.own_pending == {}


def test_an_interrupt_while_waiting_clears_the_wait():
    """The denial the daemon sends first is what releases the blocked request."""
    state = replay("interrupt-while-waiting.jsonl")
    assert state.status == IDLE
    assert state.own_pending == {}


def test_a_cancelled_request_stops_being_pending():
    """The child withdrew the ask, so nothing can answer it any more."""
    state = replay("interrupt-while-waiting.jsonl", stop_before_control=True)
    cancel = {
        "type": "control_cancel_request",
        "request_id": only_pending(state).request_id,
    }
    state = apply_event(state, cancel)
    assert state.own_pending == {}
    assert state.status == PROCESSING


def test_a_cancel_for_another_request_is_ignored():
    state = replay("interrupt-while-waiting.jsonl", stop_before_control=True)
    state = apply_event(
        state, {"type": "control_cancel_request", "request_id": "other"}
    )


def test_apply_event_stamps_each_event_with_its_seq_and_leaves_the_input_alone():
    """The stamp lives on the copy in ``recent``: the same dict goes to the child."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    events = [{"type": "rate_limit_event"}, {"type": "assistant"}, {"type": "result"}]
    for event in events:
        state = apply_event(state, event, now="2026-09-05T10:00:00Z")
    assert [e[SEQ_KEY] for e in state.recent] == [1, 2, 3]
    assert [e[TS_KEY] for e in state.recent] == ["2026-09-05T10:00:00Z"] * 3
    assert state.seq == 3
    assert all(SEQ_KEY not in event and TS_KEY not in event for event in events)


def test_an_event_with_no_clock_is_stamped_unstamped():
    """``now`` defaults to empty, so "we do not know when" stays representable."""
    state = apply_event(AgentState(agent_id="a1", cwd="/tmp/x"), {"type": "result"})
    assert state.recent[-1][TS_KEY] == ""


# --- when an event happened: its own clock, or ours ------------------------


def test_an_event_with_its_own_timestamp_keeps_it():
    """The resume rule. A ``--resume`` replays days-old turns through the live
    pump, so preferring the event's own clock is what stops one claiming it
    just happened."""
    state = apply_event(
        AgentState(agent_id="a1", cwd="/tmp/x"),
        {"type": "assistant", "timestamp": "2026-09-04T09:15:00Z"},
        now="2026-09-05T22:00:00Z",
    )
    assert state.recent[-1][TS_KEY] == "2026-09-04T09:15:00Z"


def test_a_frame_with_no_timestamp_takes_the_receive_clock():
    """``control_request`` and friends only ever arrive live."""
    state = apply_event(
        AgentState(agent_id="a1", cwd="/tmp/x"),
        {"type": "control_request", "request_id": "r1"},
        now="2026-09-05T22:00:00Z",
    )
    assert state.recent[-1][TS_KEY] == "2026-09-05T22:00:00Z"


def test_a_replayed_fixture_keeps_every_recorded_conversation_time():
    """A whole recorded stream, replayed with an absurd ``now``: every turn
    Claude timestamped keeps its own time, and nothing else invents one."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    # Pair by seq, not by position: a subagent event goes to a different ring,
    # so positional pairing would silently compare the wrong two events.
    raw: dict[int, dict] = {}
    for line in (FIXTURES / "normal-turn.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        state = apply_event(state, event, now="2099-01-01T00:00:00Z")
        raw[state.seq] = event
    own = [
        (e, raw[e[SEQ_KEY]]) for e in state.recent if raw[e[SEQ_KEY]].get("timestamp")
    ]
    assert own, "the fixture must carry timestamped turns"
    assert all(e[TS_KEY] == r["timestamp"] for e, r in own)
    borrowed = [e for e in state.recent if not raw[e[SEQ_KEY]].get("timestamp")]
    assert borrowed and all(e[TS_KEY] == "2099-01-01T00:00:00Z" for e in borrowed)


def test_an_ended_subagents_summary_carries_the_time_it_arrived():
    """A row shows ``last_message`` and ``last_message_at`` as a pair, and an
    ended subagent's message is its summary. The stamp must be the summary's,
    not that of the last thing it said before finishing."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in (FIXTURES / "subagent-turn.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        # The notification carries no clock of its own, so it takes ours.
        now = (
            "2026-09-04T09:00:00Z"
            if event.get("subtype") == "task_notification"
            else ""
        )
        state = apply_event(state, event, now=now)
    sub = state.subagents["a1.1"]
    assert sub.summary
    [row] = build_subagent_rows(state)
    assert row["last_message"].startswith("`docs/dev` exists")
    assert row["last_message_at"] == "2026-09-04T09:00:00Z"


def test_last_message_at_moves_only_when_the_last_message_does():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    said = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Done."}]},
        "timestamp": "2026-09-04T09:15:00Z",
    }
    state = apply_event(state, said, now="2026-09-05T22:00:00Z")
    assert state.last_message == "Done."
    assert state.last_message_at == "2026-09-04T09:15:00Z"
    state = apply_event(state, {"type": "result"}, now="2026-09-05T23:00:00Z")
    assert state.last_message_at == "2026-09-04T09:15:00Z"


# --- the permission mode, read off the stream ------------------------------


def test_init_carries_the_permission_mode():
    """Every recorded transcript's ``system``/``init`` names the mode it runs in."""
    assert replay("interrupt.jsonl").permission_mode == "auto"
    assert replay("plan-review-with-plan.jsonl").permission_mode == "plan"


def test_the_wire_word_default_reads_as_normal():
    """``default`` is claude's word for the mode maelstrom calls ``normal``."""
    assert replay("normal-turn.jsonl").permission_mode == "normal"


def test_a_status_event_changes_the_mode():
    """``plan-review.jsonl`` starts in plan and leaves it when the plan is approved."""
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert state.permission_mode == "plan"
    assert replay("plan-review.jsonl").permission_mode == "normal"


def test_a_status_event_without_a_mode_leaves_the_mode_alone():
    state = AgentState(agent_id="a1", cwd="/tmp/x", permission_mode="auto")
    state = apply_event(state, {"type": "system", "subtype": "status", "status": None})
    assert state.permission_mode == "auto"


def test_the_row_carries_the_mode():
    assert build_agent_row(replay("interrupt.jsonl"))["mode"] == "auto"


def test_argv_omits_the_flag_for_normal():
    """`normal` is the absence of `--permission-mode`, not a value it takes."""
    assert "--permission-mode" not in build_agent_argv(permission_mode="normal")
    assert build_agent_argv(permission_mode="plan")[-2:] == [
        "--permission-mode",
        "plan",
    ]


def test_set_mode_request_asks_the_child_to_change_mode():
    request = set_mode_request("r1", "normal")
    assert request == {
        "type": "control_request",
        "request_id": "r1",
        "request": {"subtype": "set_permission_mode", "mode": "default"},
    }


# --- the usage windows, read off the stream --------------------------------


def test_a_rate_limit_event_records_both_windows():
    """``rate_limit_event`` is how the account's budget reaches the daemon.

    The percentages are the source's own: it quantises to whole percent, so
    ``0.07`` is 7% and nothing finer is available to round.
    """
    state = replay("normal-turn.jsonl")
    five_hour, seven_day = state.usage.five_hour, state.usage.seven_day
    assert five_hour is not None and seven_day is not None
    assert (five_hour.utilization, five_hour.resets_at) == (0.05, 1788241800)
    assert (seven_day.utilization, seven_day.resets_at) == (0.24, 1788480000)


def test_the_last_reading_wins():
    """A stream carries several. The freshest is the one the account is at."""
    state = replay("subagent-turn.jsonl")
    assert state.usage.five_hour.utilization == 0.45


def test_a_reading_is_stamped_with_when_it_was_seen():
    """A reading with no clock is stale the moment no agent is running, so
    the time it was taken travels with it rather than being inferred later."""
    state = apply_event(
        AgentState(agent_id="a1", cwd="/tmp/x"),
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {"five_hour": {"utilization": 0.5, "resetsAt": 1}}
            },
        },
        now="2026-09-11T10:00:00Z",
    )
    assert state.usage.at == "2026-09-11T10:00:00Z"


def test_an_event_without_windows_leaves_the_last_reading_standing():
    """A partial event is not news that the budget is empty."""
    state = replay("normal-turn.jsonl")
    state = apply_event(state, {"type": "rate_limit_event", "rate_limit_info": {}})
    assert state.usage.five_hour.utilization == 0.05


def test_an_event_carrying_one_window_leaves_the_other_standing():
    """The windows are reported together but need not be. A five-hour figure
    on its own says nothing about the week, so replacing the whole reading
    would blank a good seven-day one the moment the source sent one window."""
    state = replay("normal-turn.jsonl")
    state = apply_event(
        state,
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {"five_hour": {"utilization": 0.6, "resetsAt": 9}}
            },
        },
        now="2026-09-11T10:00:00Z",
    )
    assert state.usage.five_hour == UsageWindow(utilization=0.6, resets_at=9)
    assert state.usage.seven_day == UsageWindow(utilization=0.24, resets_at=1788480000)
    assert state.usage.at == "2026-09-11T10:00:00Z"


def test_a_stream_with_no_reading_has_no_usage():
    """Nothing to say is said as nothing, never as zero."""
    state = replay("interrupt.jsonl")
    assert state.usage.five_hour is None
    assert state.usage.seven_day is None


def test_a_rate_limit_event_does_not_disturb_the_status():
    """It is a fact about the account, not about what this agent is doing."""
    state = replay("normal-turn.jsonl")
    assert state.status == IDLE


# --- the stopped listing ----------------------------------------------------


def _meta(**kw) -> TranscriptMeta:
    fields = {
        "session_id": "s1",
        "cwd": Path("/w/alpha"),
        "branch": "feat/x",
        "label": "Improve plan mode",
        "modified_at": 1_000.0,
    }
    fields.update(kw)
    return TranscriptMeta(**fields)


def _spec(session_id: str, **kw) -> AgentSpec:
    fields = {
        "agent_id": session_id,
        "cwd": "/w/alpha",
        "session_id": session_id,
        "status": SPEC_STOPPED,
    }
    fields.update(kw)
    return AgentSpec(**fields)


def _specs(*session_ids: str) -> dict[str, AgentSpec]:
    """A record per session, which is what makes each one resumable."""
    return {session_id: _spec(session_id) for session_id in session_ids}


def test_a_stopped_row_names_the_agent_it_would_resume():
    """The id is the whole point: ``mael agent resume`` cannot be typed without it."""
    row = build_stopped_row(_meta(), _spec("s1", agent_id="a1"), "", now=1_060.0)
    assert row["id"] == "a1"
    assert row["session"] == "s1"
    assert row["cwd"] == "/w/alpha"
    assert row["branch"] == "feat/x"
    assert row["label"] == "Improve plan mode"


def test_a_record_supplies_the_model_and_permission_mode():
    """These are unrecoverable from a transcript, so the record is why it is kept."""
    spec = AgentSpec(
        agent_id="s1",
        cwd="/w/alpha",
        session_id="s1",
        model="opus",
        permission_mode="auto",
        status=SPEC_STOPPED,
    )
    row = build_stopped_row(_meta(), spec, "", now=1_000.0)
    assert row["model"] == "opus"
    assert row["mode"] == "auto"


def test_a_stopped_row_names_the_task_the_session_ran_for():
    row = build_stopped_row(_meta(), _spec("s1"), "2026-09-04.2", now=1_000.0)
    assert row["task"] == "2026-09-04.2"


def test_a_stopped_row_reports_how_long_ago_the_session_last_wrote():
    row = build_stopped_row(
        _meta(modified_at=1_000.0), _spec("s1"), "", now=1_000.0 + 7200
    )
    assert row["age"] == "2h"


def test_stopped_rows_drop_a_session_that_is_still_live():
    """Two children on one transcript fight, and ``resume`` refuses one for it."""
    live = LiveSessionSet(
        sessions=[LiveSession(pid=1, cwd=Path("/w/alpha"), session_id="s1")]
    )
    rows = build_stopped_rows(
        [_meta(session_id="s1"), _meta(session_id="s2")],
        _specs("s1", "s2"),
        {},
        live,
        now=1_000.0,
    )
    assert [row["id"] for row in rows] == ["s2"]


def test_stopped_rows_keep_a_session_that_only_shares_a_worktree():
    """One PR per parent means siblings share a worktree; that is not the same session."""
    live = LiveSessionSet(
        sessions=[LiveSession(pid=1, cwd=Path("/w/alpha"), session_id="other")]
    )
    rows = build_stopped_rows(
        [_meta(session_id="s1")], _specs("s1"), {}, live, now=1_000.0
    )
    assert [row["id"] for row in rows] == ["s1"]


def test_stopped_rows_drop_a_hand_started_session_running_in_the_same_cwd():
    """A bare ``claude`` reports no session id, so the cwd is the only key left.

    Its transcript is on disk all the same. Without this it would be offered for
    resume while its own process is still writing to it.
    """
    live = LiveSessionSet(
        sessions=[LiveSession(pid=1, cwd=Path("/w/alpha"), session_id=None)]
    )
    rows = build_stopped_rows(
        [_meta(session_id="s1"), _meta(session_id="s2", cwd=Path("/w/bravo"))],
        _specs("s1", "s2"),
        {},
        live,
        now=1_000.0,
    )
    assert [row["id"] for row in rows] == ["s2"]


def test_stopped_rows_merge_a_record_and_a_transcript_for_one_session():
    """A record and a transcript for the same session are one resumable thing."""
    spec = AgentSpec(
        agent_id="s1",
        cwd="/w/alpha",
        session_id="s1",
        model="opus",
        status=SPEC_STOPPED,
    )
    rows = build_stopped_rows(
        [_meta(session_id="s1")],
        {"s1": spec},
        {"s1": "2026-09-04.2"},
        LiveSessionSet(sessions=[]),
        now=1_000.0,
    )
    assert len(rows) == 1
    assert rows[0]["model"] == "opus"
    assert rows[0]["task"] == "2026-09-04.2"


def test_stopped_rows_are_newest_first():
    """A listing to pick a resume from wants what was just stopped at the top."""
    rows = build_stopped_rows(
        [
            _meta(session_id="old", modified_at=1.0),
            _meta(session_id="new", modified_at=9.0),
        ],
        _specs("old", "new"),
        {},
        LiveSessionSet(sessions=[]),
        now=10.0,
    )
    assert [row["id"] for row in rows] == ["new", "old"]


def test_stopped_rows_drop_a_session_with_no_record():
    """A resume reads the record, so a session without one cannot be resumed.

    Its transcript is on disk and ``claude --resume`` would replay it, but
    ``_resume`` needs the model, permission mode and env the record holds.
    Listing one offers a resume that can only fail with ``no such agent``.
    """
    rows = build_stopped_rows(
        [_meta(session_id="kept"), _meta(session_id="orphan")],
        _specs("kept"),
        {},
        LiveSessionSet(sessions=[]),
        now=1_000.0,
    )
    assert [row["id"] for row in rows] == ["kept"]


# --- subagents: a parented event is a stream of its own -----------------------


def _parented(parent_id: str, text: str, **extra) -> dict:
    """One ``assistant`` text event a subagent produced."""
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "parent_tool_use_id": parent_id,
        "task_description": extra.pop("description", "a task"),
        **extra,
    }


def _agent_call(tool_use_id: str, **extra) -> dict:
    """One ``assistant`` event carrying an ``Agent`` tool call."""
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}
            ]
        },
        "parent_tool_use_id": None,
        **extra,
    }


def _notification(tool_use_id: str, status: str = "completed", summary: str = "done"):
    return {
        "type": "system",
        "subtype": "task_notification",
        "task_id": "t",
        "tool_use_id": tool_use_id,
        "status": status,
        "summary": summary,
    }


def test_the_parent_ring_holds_none_of_its_subagents_events():
    """What the parent said and did, and nothing a subagent did."""
    state = replay("subagent-turn.jsonl")
    assert all(e.get("parent_tool_use_id") is None for e in state.recent)
    raw = (FIXTURES / "subagent-turn.jsonl").read_text().splitlines()
    parent_lines = [
        line for line in raw if json.loads(line).get("parent_tool_use_id") is None
    ]
    assert state.seq == len(parent_lines)
    assert [e[SEQ_KEY] for e in state.recent] == list(range(1, state.seq + 1))


def test_the_parents_last_message_is_never_a_subagents():
    state = replay("subagent-turn.jsonl", stop_before_control=False)
    # The subagent speaks between the Agent call and its result; the parent's
    # own last words are what the row shows.
    assert "docs/dev" in state.last_message
    assert state.last_message.startswith("The subagent")


def test_a_subagent_opens_under_a_dotted_id_with_its_description():
    state = replay("subagent-turn.jsonl")
    sub = state.subagents["a1.1"]
    assert sub.description == "List and summarise docs/dev"
    assert sub.subagent_type == "Explore"
    assert sub.tool_use_id == "toolu_01GYXSgBQ1wcW9LA8SSvM5uJ"


def test_a_subagent_ring_carries_its_own_seq():
    sub = replay("subagent-turn.jsonl").subagents["a1.1"]
    assert all(e.get("parent_tool_use_id") == sub.tool_use_id for e in sub.recent)
    assert [e[SEQ_KEY] for e in sub.recent] == list(range(1, sub.seq + 1))
    assert sub.seq == len(sub.recent) > 0


def test_a_subagent_runs_until_its_notification_then_carries_the_summary():
    raw = (FIXTURES / "subagent-turn.jsonl").read_text().splitlines()
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for line in raw:
        event = json.loads(line)
        if event.get("subtype") == "task_notification":
            assert state.subagents["a1.1"].status == SUB_RUNNING
            assert state.subagents["a1.1"].summary == ""
        state = apply_event(state, event)
    sub = state.subagents["a1.1"]
    assert sub.status == SUB_COMPLETED
    assert sub.summary.startswith("`docs/dev` exists")
    assert "docs/dev" in sub.last_message


def test_a_subagents_last_message_is_what_it_last_said():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "looking"))
    state = apply_event(state, _parented("t1", "found it"))
    assert state.subagents["a1.1"].last_message == "found it"
    assert state.last_message == ""


def test_a_second_subagent_is_dot_two():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "one"))
    state = apply_event(state, _parented("t2", "two"))
    assert list(state.subagents) == ["a1.1", "a1.2"]
    assert state.subagents["a1.2"].tool_use_id == "t2"


def test_an_unseen_id_opens_a_subagent_named_by_the_frame():
    """No ``task_started`` came, so the frame's own description is all there is."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t9", "hi", description="Find the tests"))
    sub = state.subagents["a1.1"]
    assert sub.description == "Find the tests"
    assert sub.seq == 1


def test_a_parented_event_after_the_end_reopens_the_subagent():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "one"))
    state = apply_event(state, _notification("t1"))
    assert state.subagents["a1.1"].status == SUB_COMPLETED
    state = apply_event(state, _parented("t1", "more"))
    assert state.subagents["a1.1"].status == SUB_RUNNING
    assert state.subagents["a1.1"].seq == 2
    assert list(state.subagents) == ["a1.1"]


def test_a_failed_or_stopped_notification_is_kept_as_such():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "one"))
    state = apply_event(state, _parented("t2", "two"))
    state = apply_event(state, _notification("t1", "failed", "boom"))
    state = apply_event(state, _notification("t2", "stopped", ""))
    assert state.subagents["a1.1"].status == SUB_FAILED
    assert state.subagents["a1.1"].summary == "boom"
    assert state.subagents["a1.2"].status == SUB_STOPPED


def test_a_notification_for_no_known_subagent_is_ignored():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _notification("nope"))
    assert state.subagents == {}


def test_a_background_shell_task_is_not_a_subagent():
    """``subagent-background.jsonl`` has a ``local_bash`` task inside the subagent."""
    state = replay("subagent-background.jsonl")
    assert list(state.subagents) == ["a1.1"]


def test_a_backgrounded_subagent_outlives_the_parents_tool_result():
    """The parent's ``tool_result`` arrives at launch, so it must not end anything."""
    raw = (FIXTURES / "subagent-background.jsonl").read_text().splitlines()
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    seq_at_result = None
    for line in raw:
        event = json.loads(line)
        state = apply_event(state, event)
        # The first turn ends while the subagent runs; a second turn reports
        # its summary once the notification wakes the parent.
        if event.get("type") == "result" and seq_at_result is None:
            assert state.subagents["a1.1"].status == SUB_RUNNING
            seq_at_result = state.subagents["a1.1"].seq
    assert seq_at_result is not None
    assert state.subagents["a1.1"].seq > seq_at_result
    assert state.subagents["a1.1"].status == SUB_COMPLETED


def test_a_subagent_of_a_subagent_is_dot_one_dot_one():
    """The ring holding the spawning call decides the level, not the tool name."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _agent_call("outer"))
    state = apply_event(state, _agent_call("inner", parent_tool_use_id="outer"))
    state = apply_event(state, _parented("inner", "deep"))
    assert list(state.subagents) == ["a1.1", "a1.1.1"]
    assert state.subagents["a1.1.1"].seq == 1
    assert state.subagents["a1.1"].seq == 1  # the inner call only


def test_task_started_opens_the_subagent_before_it_speaks():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    started = {
        "type": "system",
        "subtype": "task_started",
        "task_id": "t",
        "tool_use_id": "t1",
        "description": "Scan the docs",
        "subagent_type": "Explore",
        "task_type": "local_agent",
    }
    state = apply_event(state, started)
    sub = state.subagents["a1.1"]
    assert sub.status == SUB_RUNNING
    assert sub.description == "Scan the docs"
    assert sub.seq == 0
    # And the parent's ring kept the system event, as it keeps any other.
    assert state.recent[-1]["subtype"] == "task_started"


def test_past_the_limit_the_oldest_finished_subagent_goes_and_no_ordinal_returns():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for n in range(SUBAGENT_LIMIT):
        state = apply_event(state, _parented(f"t{n}", "x"))
    # The first stays running; the second finished, so it is the one to go.
    state = apply_event(state, _notification("t1"))
    state = apply_event(state, _parented("t-new", "y"))
    assert len(state.subagents) == SUBAGENT_LIMIT
    assert "a1.2" not in state.subagents
    assert "a1.1" in state.subagents
    assert state.subagents[f"a1.{SUBAGENT_LIMIT + 1}"].tool_use_id == "t-new"


def test_an_evicted_subagent_that_speaks_again_comes_back_under_its_old_id():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    for n in range(SUBAGENT_LIMIT):
        state = apply_event(state, _parented(f"t{n}", "x"))
    state = apply_event(state, _notification("t1"))
    state = apply_event(state, _parented("t-new", "y"))
    assert "a1.2" not in state.subagents
    state = apply_event(state, _parented("t1", "again"))
    assert state.subagents["a1.2"].tool_use_id == "t1"
    assert state.subagents["a1.2"].seq == 1


def test_a_permission_a_subagent_asks_for_names_the_subagent():
    """The wait is the subagent's; the parent still reports it.

    The reply goes to the parent's pipe, so a user looking at the parent has
    to see that something under it is blocked. A parent reading `processing`
    while a subagent waits is the failure this whole mechanism exists to stop.
    """
    state = replay("subagent-permission.jsonl", stop_before_control=True)
    assert state.own_pending == {}, "the wait belongs to the subagent"
    assert state.subagents["a1.1"].pending
    row = build_agent_row(state)
    assert row["state"] == "awaiting-permission"
    assert row["waiting_on"] == "https://example.com"


def test_a_permission_the_parent_asks_for_names_no_subagent():
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert only_pending(state).subagent == ""


def test_mark_exited_leaves_the_subagents_alone():
    state = replay("subagent-turn.jsonl")
    before = state.subagents
    assert mark_exited(state, 0).subagents == before


# --- the rows and detail a subagent renders as ------------------------------


def test_a_top_level_row_has_no_parent_and_no_description():
    row = build_agent_row(AgentState(agent_id="a1", cwd="/tmp/x"))
    assert row["parent"] == ""
    assert row["description"] == ""


def test_subagent_rows_take_the_row_shape_under_the_parent():
    state = replay("subagent-turn.jsonl")
    [row] = build_subagent_rows(state)
    last_message = row.pop("last_message")
    assert row == {
        "id": "a1.1",
        "parent": "a1",
        "description": "List and summarise docs/dev",
        "state": "exited(0)",
        "session": "67abe140-d302-472e-aae5-99d423dfa180",
        "cwd": "/tmp/x",
        "pid": None,
        "model": "claude-opus-5",
        "mode": "auto",
        "waiting_on": "",
        "last_message_at": "",
        "cost": "",
        # All blank: a subagent has no session, so its spend, its size and its
        # context are counted in the parent's totals, not again here.
        "tokens": 0,
        "context_tokens": 0,
    }
    assert last_message.startswith("`docs/dev` exists")
    assert "\n" not in last_message
    assert set(row) | {"last_message"} == set(build_agent_row(state))


def test_a_running_subagent_row_is_processing_and_shows_its_last_words():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "reading the docs"))
    [row] = build_subagent_rows(state)
    assert row["state"] == "processing"
    assert row["last_message"] == "reading the docs"


def test_an_ended_subagent_row_shows_its_summary_over_its_last_words():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "still working"))
    state = apply_event(state, _notification("t1", "completed", "the answer"))
    [row] = build_subagent_rows(state)
    assert row["last_message"] == "the answer"
    assert build_subagent_detail(state, "a1.1")["message"] == "the answer"


def test_a_failed_or_stopped_subagent_row_is_exited_one():
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    state = apply_event(state, _parented("t1", "one"))
    state = apply_event(state, _parented("t2", "two"))
    state = apply_event(state, _notification("t1", "failed", "boom"))
    state = apply_event(state, _notification("t2", "stopped", ""))
    rows = build_subagent_rows(state)
    assert [r["state"] for r in rows] == ["exited(1)", "exited(1)"]
    # A summary wins; without one the last words stand.
    assert [r["last_message"] for r in rows] == ["boom", "two"]


def test_the_parents_detail_lists_its_subagents():
    state = replay("subagent-turn.jsonl")
    detail = build_agent_detail(state)
    assert [r["id"] for r in detail["subagents"]] == ["a1.1"]
    assert detail["waiting_subagent"] == ""


def test_the_parents_detail_names_the_subagent_a_wait_came_from():
    state = replay("subagent-permission.jsonl", stop_before_control=True)
    assert build_agent_detail(state)["waiting_subagent"] == "a1.1"


def test_a_subagents_detail_is_its_row_plus_its_message_in_full():
    state = replay("subagent-turn.jsonl")
    detail = build_subagent_detail(state, "a1.1")
    [row] = build_subagent_rows(state)
    assert row.items() <= detail.items()
    assert detail["message"] == state.subagents["a1.1"].last_message
    assert "\n" in detail["message"]
    assert detail["subagents"] == []
    assert set(detail) == set(build_agent_detail(state))


class TestBuildStartPayload:
    """The ``start`` command the daemon is sent, held to an exact shape.

    It must match the payload ``orchestrator/sources.py`` sends for a task
    launch — one wire shape for both callers, not two.
    """

    def test_task_launch_carries_every_field(self):
        assert build_start_payload(
            Path("/wt/alpha"),
            permission_mode="auto",
            env={"MAEL_TASK_ID": "t1", "MAEL_TASK_PARENT": "t1"},
            session_id="sess-1",
            resume=True,
            model="opus",
            prompt="do the thing",
        ) == {
            "cmd": "start",
            "cwd": "/wt/alpha",
            "prompt": "do the thing",
            "mode": "auto",
            "model": "opus",
            "session": "sess-1",
            "env": {"MAEL_TASK_ID": "t1", "MAEL_TASK_PARENT": "t1"},
            "resume": True,
        }

    def test_taskless_launch_is_just_the_cwd(self):
        # `mael add` / `mael open`: no prompt, no session id, no env. The agent
        # draws as a freeAgent node in the orchestrator UI.
        assert build_start_payload(Path("/wt/alpha")) == {
            "cmd": "start",
            "cwd": "/wt/alpha",
            "resume": False,
        }

    def test_empty_env_and_falsy_model_are_omitted(self):
        # A falsy model means "inherit the user's default"; an empty env dict
        # would be noise on the wire.
        assert build_start_payload(
            Path("/wt/alpha"), prompt="hi", model="", env={}
        ) == {"cmd": "start", "cwd": "/wt/alpha", "prompt": "hi", "resume": False}

    def test_resume_is_always_sent(self):
        # `resume` is the one falsy field that carries meaning: False says
        # "claim a fresh session", not "the caller did not say".
        assert build_start_payload(Path("/wt/alpha"))["resume"] is False


# --- a shell command -------------------------------------------------------


def test_shell_input_message_wraps_the_command_in_the_harness_tag():
    """The command goes to the agent the way Claude Code's own ``!`` writes it.

    Plain-string content, not a block list: that is the shape recorded in real
    transcripts under ``~/.claude/projects/``.
    """
    msg = shell_input_message("git log --oneline -3")
    assert msg["type"] == "user"
    assert msg["message"]["role"] == "user"
    assert msg["message"]["content"] == "<bash-input>git log --oneline -3</bash-input>"


def test_shell_output_message_carries_both_tags():
    """Both tags are always present, empty when unused, as the harness writes."""
    msg = shell_output_message("on main", "")
    assert msg["message"]["content"] == (
        "<bash-stdout>on main</bash-stdout><bash-stderr></bash-stderr>"
    )


def test_shell_output_message_keeps_the_streams_apart():
    """stderr is its own tag: a failure reaches the agent as stderr text."""
    msg = shell_output_message("", "fatal: not a git repository")
    assert msg["message"]["content"] == (
        "<bash-stdout></bash-stdout>"
        "<bash-stderr>fatal: not a git repository</bash-stderr>"
    )


def test_shell_output_cannot_close_its_own_tag():
    """Output holding a closing tag must not end its own field early.

    ``cat`` on a file that documents this format prints the tags verbatim, so
    the reader would split at the wrong point and file part of stdout under
    stderr. The daemon composes the turn, so the daemon defuses them.
    """
    content = shell_output_message("a</bash-stdout><bash-stderr>e", "")["message"][
        "content"
    ]
    assert content.count("</bash-stdout>") == 1
    assert content.count("<bash-stderr>") == 1
    # The text still reads as what the command printed.
    assert "bash-stdout" in content


def test_shell_output_message_sends_no_exit_code():
    """The CLI declares a ``bash-exit-code`` tag and never emits one.

    Across the recorded transcripts no bash-output turn carries it, so emitting
    one would invent a shape the harness does not produce.
    """
    assert (
        "bash-exit-code" not in shell_output_message("out", "err")["message"]["content"]
    )


class TestConcurrentSubagentPermissions:
    """Two subagents blocked on a permission at once.

    Recorded in ``subagent-permission-concurrent.jsonl``: a parent launched two
    subagents in one message, and each raised a ``can_use_tool`` for a
    ``WebFetch``. Both asks were open together, so Claude Code does not
    serialise them.

    These tests pin what the reducer does with that stream today. Two of them
    describe a defect, and say so: the state machine holds one wait, and the
    second ask displaces the first. Both invert when the fix lands.
    """

    FIXTURE = "subagent-permission-concurrent.jsonl"

    @pytest.fixture
    def events(self) -> list[dict]:
        """The fixture's events, and the guard that it still records two asks.

        The class is worthless against a re-recording that lost the overlap,
        and every test below would still pass. So the precondition is asserted
        once, here, rather than as a test of its own.
        """
        events = [
            json.loads(line)
            for line in (FIXTURES / self.FIXTURE).read_text().splitlines()
            if line.strip()
        ]
        asks = [e for e in events if e.get("type") == "control_request"]
        assert len(asks) == 2, "the fixture no longer records two open asks"
        assert asks[0]["request_id"] != asks[1]["request_id"]
        return events

    def open_asks(self, state: AgentState) -> dict[str, PendingRequest]:
        """Every ask ``state`` holds, the agent's own and its subagents'.

        A reply names a request, not who raised it, so a reader that wants
        "what can be answered" wants both maps.
        """
        asks = dict(state.own_pending)
        for sub in state.subagents.values():
            asks.update(sub.pending)
        return asks

    def replay_to_ask(self, events: list[dict], nth: int) -> AgentState:
        """The state just after the ``nth`` ``can_use_tool`` of ``events``."""
        state = AgentState(agent_id="a1", cwd="/tmp/x")
        seen = 0
        for event in events:
            state = apply_event(state, event)
            if event.get("type") == "control_request":
                seen += 1
                if seen == nth:
                    break
        return state

    def test_each_ask_names_its_own_subagent_on_the_wire(self, events):
        """``agent_id`` on the request is the subagent's ``task_started`` id.

        This is the join that makes attribution possible. Nothing reads it yet,
        so the assertion is against the recording rather than the reducer.
        """
        started = {
            e["task_id"]
            for e in events
            if e.get("type") == "system" and e.get("subtype") == "task_started"
        }
        asked = {
            e["request"]["agent_id"]
            for e in events
            if e.get("type") == "control_request"
        }
        assert len(asked) == 2
        assert asked <= started

    def test_the_second_ask_does_not_displace_the_first(self, events):
        """Both waits are held, so either can still be answered."""
        asks = [e["request_id"] for e in events if e.get("type") == "control_request"]
        first = self.replay_to_ask(events, 1)
        assert list(self.open_asks(first)) == asks[:1]

        second = self.replay_to_ask(events, 2)
        assert list(self.open_asks(second)) == asks, "the first wait was displaced"

    def test_answering_one_leaves_the_other_answerable(self, events):
        """The point of the change: one answer releases one subagent.

        The fixture answers only the second ask. The first must survive as a
        wait the user can still act on, rather than being cleared with it.
        """
        answered = {
            e["response"]["request_id"]
            for e in events
            if e.get("type") == "control_response"
        }
        asked = [e["request_id"] for e in events if e.get("type") == "control_request"]
        unanswered = [r for r in asked if r not in answered]
        assert unanswered, "the fixture answers every ask"

        state = AgentState(agent_id="a1", cwd="/tmp/x")
        for event in events:
            state = apply_event(state, event)
        assert list(self.open_asks(state)) == unanswered
        assert state.subagents["a1.1"].pending, "the unanswered ask was cleared with it"

    def test_each_ask_is_attributed_to_the_subagent_that_raised_it(self, events):
        """``agent_id`` names the subagent, so a ring scan is not needed.

        A recording of a parent carries no parented events, so the subagent
        rings hold no ``tool_use`` block to scan. The ask says whose it is.
        """
        first = self.replay_to_ask(events, 1)
        assert [p.subagent for p in self.open_asks(first).values()] == ["a1.1"]

        second = self.replay_to_ask(events, 2)
        assert [p.subagent for p in self.open_asks(second).values()] == ["a1.1", "a1.2"]

    def test_an_evicted_subagent_is_not_named(self, events):
        """``subagent_tasks`` outlives eviction, as ``subagent_ids`` does.

        A dotted id whose state has gone names no stream a reader could open,
        so the ask reads as the parent's own rather than pointing at nothing.
        """
        state = self.replay_to_ask(events, 1)
        assert [p.subagent for p in self.open_asks(state).values()] == ["a1.1"]

        # Evict the subagent the ask named, keeping the task map.
        gone = replace(state, subagents={})
        again = apply_event(
            gone,
            {
                "type": "control_request",
                "request_id": "r-late",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "WebFetch",
                    "agent_id": next(iter(state.subagent_tasks)),
                },
            },
        )
        assert again.own_pending["r-late"].subagent == ""

    def test_each_subagents_wait_is_its_own(self, events):
        """The wait is recorded on the subagent that raised it.

        A subagent has no process, so the reply still goes to the parent. The
        record is the subagent's, which is what lets its row say what it waits
        on rather than looking merely busy.
        """
        asks = [e["request_id"] for e in events if e.get("type") == "control_request"]
        state = self.replay_to_ask(events, 2)
        assert list(state.subagents["a1.1"].pending) == asks[:1]
        assert list(state.subagents["a1.2"].pending) == asks[1:]

    def test_a_waiting_subagents_row_says_what_it_waits_on(self, events):
        """`processing` would read as busy, and hide a subagent that is stuck."""
        state = self.replay_to_ask(events, 2)
        rows = {r["id"]: r for r in build_subagent_rows(state)}
        assert rows["a1.1"]["state"] == AWAITING_PERMISSION
        assert rows["a1.1"]["waiting_on"] == "https://example.com"
        assert rows["a1.2"]["state"] == AWAITING_PERMISSION

    def test_a_notified_subagent_stops_advertising_its_wait(self, events):
        """An ended subagent holds nothing a reply could reach.

        Claude Code should not notify a blocked subagent, but a wait left on
        an `exited` row would be advertised for ever and answerable never.
        """
        state = self.replay_to_ask(events, 2)
        assert state.subagents["a1.1"].pending
        ended = apply_event(
            state,
            {
                "type": "system",
                "subtype": "task_notification",
                "tool_use_id": state.subagents["a1.1"].tool_use_id,
                "status": "completed",
                "summary": "done",
            },
        )
        assert ended.subagents["a1.1"].pending == {}

    def test_a_waiting_subagents_detail_names_the_request(self, events):
        """`show` on the dotted id is how a user learns what to answer."""
        state = self.replay_to_ask(events, 1)
        detail = build_subagent_detail(state, "a1.1")
        [ask] = state.subagents["a1.1"].pending.values()
        assert detail["request_id"] == ask.request_id
        assert detail["waiting_kind"] == AWAITING_PERMISSION
        assert detail["waiting_tool"] == "WebFetch"
        # The asker is this subagent, so it names no other.
        assert detail["waiting_subagent"] == ""

    def test_a_dead_agent_does_not_report_a_subagents_wait(self, events):
        """An exited agent answers nothing, whoever was waiting under it.

        `mark_exited` clears the agent's own asks. A subagent's would
        otherwise still be reported, so a dead agent would render as
        answerable and every reply to it be refused.
        """
        state = self.replay_to_ask(events, 2)
        assert build_agent_row(state)["state"] == AWAITING_PERMISSION
        dead = mark_exited(state, 1)
        row = build_agent_row(dead)
        assert row["state"] == "exited(1)"
        assert row["waiting_on"] == ""

    def test_both_subagents_are_known_even_so(self, events):
        """The subagents themselves open fine; only the wait loses them."""
        state = AgentState(agent_id="a1", cwd="/tmp/x")
        for event in events:
            state = apply_event(state, event)
        assert sorted(state.subagents) == ["a1.1", "a1.2"]


class TestNestedSubagentPermission:
    """A subagent of a subagent asking for a permission.

    Recorded in ``subagent-permission-nested.jsonl``. The claim under test is
    the one the findings document rests on: ``agent_id`` names a nested
    subagent as exactly as it names a direct one.
    """

    FIXTURE = "subagent-permission-nested.jsonl"

    def test_the_ask_names_the_nested_subagent_on_the_wire(self):
        """``agent_id`` matches the ``task_started`` of the deeper subagent."""
        events = [
            json.loads(line)
            for line in (FIXTURES / self.FIXTURE).read_text().splitlines()
            if line.strip()
        ]
        started = {
            e["task_id"]: e
            for e in events
            if e.get("type") == "system" and e.get("subtype") == "task_started"
        }
        [ask] = [e for e in events if e.get("type") == "control_request"]
        asked = ask["request"]["agent_id"]
        assert asked in started
        # The deeper of the two: spawned by the subagent, not by the parent.
        assert started[asked]["spawn_depth"] == 2
