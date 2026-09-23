"""The daemon's wire contract: requests, replies, and the shapes a reply carries.

A reply is built against an ask recorded from a live ``claude``, read straight
off its fixture. The reducer that would read the same ask is
:mod:`mael_daemon.agent_model`'s, and is not needed to build a reply.
"""

import base64
import json
from pathlib import Path

from mael_agent.agent_wire import (
    AWAITING_PLAN_REVIEW,
    PendingRequest,
    build_resume_payload,
    build_start_payload,
    interrupt_request,
    pending_fields,
    reply_for_answer,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
    set_mode_request,
    user_message,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_events"


def recorded_ask(name: str) -> PendingRequest:
    """The first ``can_use_tool`` ask recorded in fixture ``name``."""
    for line in (FIXTURES / name).read_text().splitlines():
        event = json.loads(line) if line.strip() else {}
        request = event.get("request") or {}
        if event.get("type") == "control_request" and (
            request.get("subtype") == "can_use_tool"
        ):
            return PendingRequest(
                request_id=event["request_id"],
                tool_name=request["tool_name"],
                input=request.get("input") or {},
                description=request.get("description") or "",
            )
    raise AssertionError(f"{name} records no ask")


def test_reply_for_answer_puts_the_choice_in_updated_input():
    """The agent reads answers from ``updatedInput['answers']``, keyed by question."""
    state = recorded_ask("question-unanswered.jsonl")
    reply = reply_for_answer(state, "Green")
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == {
        "Which colour do you prefer?": "Green"
    }
    assert reply["response"]["request_id"] == state.request_id


def test_reply_for_approval_allows_with_the_input_unchanged():
    state = recorded_ask("permission-request.jsonl")
    reply = reply_for_approval(state)
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"] == state.input


def test_reply_for_denial_carries_the_reason():
    state = recorded_ask("permission-request.jsonl")
    reply = reply_for_denial(state, "not on a public network")
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


def test_reply_for_answers_files_each_answer_under_its_question():
    """The orchestrator UI answers every question at once, each by its text."""
    state = recorded_ask("question-unanswered.jsonl")
    answers = {"Which colour do you prefer?": "Blue"}
    reply = reply_for_answers(state, answers)
    payload = reply["response"]["response"]
    assert payload["behavior"] == "allow"
    assert payload["updatedInput"]["answers"] == answers
    assert payload["updatedInput"]["questions"] == state.input["questions"]


def test_interrupt_request_is_a_control_request_with_the_interrupt_subtype():
    """Interrupt is a host->child control_request, not a user message."""
    request = interrupt_request("req-7")
    assert request["type"] == "control_request"
    assert request["request_id"] == "req-7"
    assert request["request"] == {"subtype": "interrupt"}


def test_set_mode_request_asks_the_child_to_change_mode():
    request = set_mode_request("r1", "normal")
    assert request == {
        "type": "control_request",
        "request_id": "r1",
        "request": {"subtype": "set_permission_mode", "mode": "default"},
    }


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
            execute_model="sonnet",
            prompt="do the thing",
            system_prompt_file=Path("/s/agent-prompt.md"),
        ) == {
            "cmd": "start",
            "cwd": "/wt/alpha",
            "prompt": "do the thing",
            "mode": "auto",
            "model": "opus",
            "execute_model": "sonnet",
            "session": "sess-1",
            "env": {"MAEL_TASK_ID": "t1", "MAEL_TASK_PARENT": "t1"},
            "resume": True,
            "system_prompt_file": "/s/agent-prompt.md",
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
            Path("/wt/alpha"), prompt="hi", model="", env={}, system_prompt_file=None
        ) == {"cmd": "start", "cwd": "/wt/alpha", "prompt": "hi", "resume": False}

    def test_resume_is_always_sent(self):
        # `resume` is the one falsy field that carries meaning: False says
        # "claim a fresh session", not "the caller did not say".
        assert build_start_payload(Path("/wt/alpha"))["resume"] is False


# --- the keys a wait fills ----------------------------------------------------


def test_no_wait_fills_every_key_with_its_empty_value():
    assert pending_fields(None, "last words") == {
        "request_id": "",
        "waiting_kind": "",
        "waiting_tool": "",
        "waiting_input": {},
        "waiting_subagent": "",
        "questions": [],
        "plan": "",
        "plan_file": "",
    }


def test_a_bare_plan_review_falls_back_to_the_last_message():
    """A sandbox refused the plan-file write, so the plan is in a message."""
    ask = PendingRequest(request_id="r1", tool_name="ExitPlanMode", input={})
    assert pending_fields(ask, "1. do the thing") == {
        "request_id": "r1",
        "waiting_kind": AWAITING_PLAN_REVIEW,
        "waiting_tool": "ExitPlanMode",
        "waiting_input": {},
        "waiting_subagent": "",
        "questions": [],
        "plan": "1. do the thing",
        "plan_file": "",
    }


# --- resume -------------------------------------------------------------------


def test_a_resume_carries_the_text_and_the_prompt_file():
    assert build_resume_payload(
        "a1", text="carry on", system_prompt_file=Path("/s/agent-prompt.md")
    ) == {
        "cmd": "resume",
        "id": "a1",
        "text": "carry on",
        "system_prompt_file": "/s/agent-prompt.md",
    }


def test_a_bare_resume_is_just_the_id():
    """No text: the daemon picks the turn. No file: the record's stands."""
    assert build_resume_payload("a1") == {"cmd": "resume", "id": "a1"}
