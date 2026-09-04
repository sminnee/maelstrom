"""The agent state machine, replayed against recorded ``claude`` event streams.

Every fixture in ``tests/fixtures/agent_events/`` is a real NDJSON transcript,
captured from ``claude -p --input-format stream-json --output-format stream-json``.
Nothing here is designed from an assumed event shape.

Two plan-review fixtures record the two shapes ``ExitPlanMode`` takes.
``plan-review-with-plan.jsonl`` is the normal one, where the request carries the
plan. ``plan-review.jsonl`` is an agent whose plan-file write a sandbox refused,
so the request arrives bare and the plan is in a message instead.
"""

import json
from pathlib import Path

import pytest

from maelstrom.agent_model import (
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
    AgentSpec,
    AgentState,
    TranscriptMeta,
    apply_event,
    build_agent_argv,
    build_agent_detail,
    build_agent_env,
    build_agent_row,
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
    spec_from_dict,
    spec_to_dict,
    user_message,
)
from maelstrom.session_discovery import LiveSession, LiveSessionSet

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
    # Without this a subagent's stream carries its tool calls only, never its
    # words. Confirmed against v2.1.260.
    assert "--forward-subagent-text" in argv


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
    assert detail["plan"] == state.pending.input["plan"]
    assert detail["plan_file"].endswith(".md")


def test_detail_falls_back_to_the_last_message_when_the_request_is_bare():
    """A sandboxed plan write leaves an empty input, and the text in a message."""
    state = replay("plan-review.jsonl", stop_before_control=True)
    assert state.pending is not None
    assert state.pending.input == {}
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
    assert state.pending is not None
    answers = {"Which colour do you prefer?": "Blue"}
    reply = reply_for_answers(state.pending, answers)
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == answers
    assert payload["updatedInput"]["questions"] == state.pending.input["questions"]


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
    assert state.pending is None


def test_an_interrupt_while_waiting_clears_the_wait():
    """The denial the daemon sends first is what releases the blocked request."""
    state = replay("interrupt-while-waiting.jsonl")
    assert state.status == IDLE
    assert state.pending is None


def test_a_cancelled_request_stops_being_pending():
    """The child withdrew the ask, so nothing can answer it any more."""
    state = replay("interrupt-while-waiting.jsonl", stop_before_control=True)
    assert state.pending is not None
    cancel = {"type": "control_cancel_request", "request_id": state.pending.request_id}
    state = apply_event(state, cancel)
    assert state.pending is None
    assert state.status == PROCESSING


def test_a_cancel_for_another_request_is_ignored():
    state = replay("interrupt-while-waiting.jsonl", stop_before_control=True)
    state = apply_event(
        state, {"type": "control_cancel_request", "request_id": "other"}
    )
    assert state.pending is not None


def test_apply_event_stamps_each_event_with_its_seq_and_leaves_the_input_alone():
    """The stamp lives on the copy in ``recent``: the same dict goes to the child."""
    state = AgentState(agent_id="a1", cwd="/tmp/x")
    events = [{"type": "rate_limit_event"}, {"type": "assistant"}, {"type": "result"}]
    for event in events:
        state = apply_event(state, event)
    assert [e[SEQ_KEY] for e in state.recent] == [1, 2, 3]
    assert state.seq == 3
    assert all(SEQ_KEY not in event for event in events)


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
    """The wait belongs to the parent; the detail says which subagent raised it."""
    state = replay("subagent-permission.jsonl", stop_before_control=True)
    assert state.status == "awaiting-permission"
    assert state.pending is not None
    assert state.pending.tool_name == "WebFetch"
    assert state.pending.subagent == "a1.1"


def test_a_permission_the_parent_asks_for_names_no_subagent():
    state = replay("permission-request.jsonl", stop_before_control=True)
    assert state.pending is not None
    assert state.pending.subagent == ""


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
        "model": "claude-opus-5",
        "mode": "auto",
        "waiting_on": "",
        "cost": "",
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
