"""What `mael agent attach` shows, derived from one agent's raw event stream."""

import json
from pathlib import Path

from maelstrom.agent_model import AGENT_EXITED, BACKLOG_END
from maelstrom.agent_view import (
    agent_status,
    apply_stream_event,
    classify_tool_call,
    footer_fields,
    initial_view,
    mark_stream_ended,
    pending_prompt,
    plan_markdown,
    tool_call_title,
    transcript_items,
    turn_result_line,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"

NOW = "2026-01-01T00:00:00Z"


def replay(
    name: str,
    *,
    stop_before_control: bool = False,
    end_backlog: bool = True,
    limit: int | None = None,
):
    """Feed one fixture through the view reducer as an attach backlog."""
    view = initial_view("a1")
    lines = [
        line for line in (FIXTURES / name).read_text().splitlines() if line.strip()
    ]
    if limit is not None:
        lines = lines[:limit]
    for line in lines:
        event = json.loads(line)
        view, _ = apply_stream_event(view, event, NOW)
        if stop_before_control and event.get("type") == "control_request":
            break
    if end_backlog:
        view, _ = apply_stream_event(view, {"type": BACKLOG_END}, NOW)
    return view


def items_of(view, kind: str):
    return [item for item in transcript_items(view) if item["type"] == kind]


def test_normal_turn_yields_one_assistant_message_and_a_turn_line():
    view = replay("normal-turn.jsonl")
    messages = [i for i in items_of(view, "message") if i["role"] == "assistant"]
    assert len(messages) == 1
    assert "Hello there, friend" in messages[0]["markdown"]
    assert turn_result_line(items_of(view, "turn_result")[0]).startswith("turn success")


def test_a_tool_call_carries_its_result_and_status():
    view = replay("permission-request.jsonl")
    calls = items_of(view, "tool_call")
    assert calls
    assert calls[-1]["status"] in ("done", "error", "denied")
    assert calls[-1]["output"]


def test_a_denied_tool_call_is_marked_denied():
    view = replay("permission-denied.jsonl")
    assert [c["status"] for c in items_of(view, "tool_call")] == ["denied"]


def test_bash_title_prefers_the_description():
    item = {
        "type": "tool_call",
        "tool": "Bash",
        "input": {"description": "List files", "command": "ls -la"},
    }
    assert classify_tool_call(item) == "bash"
    assert tool_call_title(item) == "List files"
    assert tool_call_title({**item, "input": {"command": "ls -la"}}) == "ls -la"


def test_edit_title_is_the_file_path():
    item = {"type": "tool_call", "tool": "Edit", "input": {"file_path": "/tmp/a.py"}}
    assert classify_tool_call(item) == "edit"
    assert tool_call_title(item) == "/tmp/a.py"


def test_a_wait_raising_call_draws_nothing():
    """The wait line that follows renders the prompt, so the call draws no card."""
    for tool in ("ExitPlanMode", "AskUserQuestion"):
        item = {"type": "tool_call", "tool": tool, "input": {}}
        assert classify_tool_call(item) == "wait", tool


def test_generic_title_falls_back_to_url_query_description():
    base = {"type": "tool_call", "tool": "WebFetch"}
    assert classify_tool_call(base) == "generic"
    assert tool_call_title({**base, "input": {"url": "https://x"}}) == "https://x"
    assert tool_call_title({**base, "input": {"query": "cats"}}) == "cats"
    assert tool_call_title({**base, "input": {"description": "d"}}) == "d"
    assert tool_call_title({**base, "input": {}}) == ""


def test_a_thinking_block_is_not_an_item():
    view = initial_view("a1")
    raw = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "said"},
            ],
        },
    }
    view, _ = apply_stream_event(view, raw, NOW)
    assert [i["markdown"] for i in items_of(view, "message")] == ["said"]


def test_a_wait_answered_in_the_backlog_does_not_prompt():
    """Answering happened before this client attached; do not re-ask."""
    view = replay("question-answered.jsonl")
    assert pending_prompt(view) is None


def test_an_unanswered_wait_prompts_once_the_backlog_ends():
    mid = replay(
        "question-unanswered.jsonl", stop_before_control=True, end_backlog=False
    )
    assert pending_prompt(mid) is None  # still replaying history
    view, _ = apply_stream_event(mid, {"type": BACKLOG_END}, NOW)
    prompt = pending_prompt(view)
    assert prompt is not None
    assert prompt["type"] == "question"


def test_a_response_from_elsewhere_clears_the_prompt():
    view = replay("question-unanswered.jsonl", stop_before_control=True)
    prompt = pending_prompt(view)
    assert prompt is not None
    response = {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": prompt["requestId"],
            "response": {"behavior": "allow", "updatedInput": {"answers": {"q": "a"}}},
        },
    }
    view, _ = apply_stream_event(view, response, NOW)
    assert pending_prompt(view) is None


def test_plan_markdown_comes_from_the_request_or_falls_back_to_the_last_message():
    with_plan = replay("plan-review-with-plan.jsonl", stop_before_control=True)
    item = pending_prompt(with_plan)
    assert item is not None
    assert "## Verification" in plan_markdown(with_plan, item)

    bare = replay("plan-review.jsonl", stop_before_control=True)
    item = pending_prompt(bare)
    assert item is not None
    assert "Verification" in plan_markdown(bare, item)


def test_a_full_backlog_is_marked_truncated():
    from maelstrom.agent_model import RECENT_LIMIT

    view = initial_view("a1")
    noise = {"type": "rate_limit_event"}
    for _ in range(RECENT_LIMIT):
        view, _ = apply_stream_event(view, noise, NOW)
    view, _ = apply_stream_event(view, {"type": BACKLOG_END}, NOW)
    assert view.truncated


def test_the_footer_reads_cwd_model_and_tokens_from_the_stream():
    view = replay("normal-turn.jsonl")
    fields = footer_fields(view, "main")
    assert fields["cwd"] == "spike"
    assert fields["model"] == "claude-opus-5"
    assert fields["branch"] == "main"
    assert fields["state"] == "idle"
    assert view.usage.output > 0
    assert fields["tokens"]


def test_the_exit_marker_ends_the_view():
    view = replay("normal-turn.jsonl")
    view, _ = apply_stream_event(view, {"type": AGENT_EXITED, "exit_code": 2}, NOW)
    assert view.exited
    assert view.exit_code == 2
    assert agent_status(view) == "exited"


def test_a_stream_that_just_stops_is_a_lost_connection():
    view = mark_stream_ended(replay("normal-turn.jsonl"))
    assert view.connection_lost
    assert not view.exited


def _result(input_tokens: int, output_tokens: int) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_tokens_add_up_across_turns():
    """`result.usage` reports one turn, so a session total has to accumulate."""
    view = initial_view("a1")
    view, _ = apply_stream_event(view, _result(10, 5), NOW)
    view, _ = apply_stream_event(view, _result(3, 7), NOW)
    assert view.usage.input == 13
    assert view.usage.output == 12
    assert view.usage.total == 25


def test_a_result_without_usage_adds_nothing():
    view = initial_view("a1")
    view, _ = apply_stream_event(view, _result(10, 5), NOW)
    view, _ = apply_stream_event(view, {"type": "result", "subtype": "success"}, NOW)
    assert view.usage.total == 15


def test_an_exit_marker_after_an_exit_is_not_a_lost_connection():
    view = replay("normal-turn.jsonl")
    view, _ = apply_stream_event(view, {"type": AGENT_EXITED, "exit_code": 0}, NOW)
    assert not mark_stream_ended(view).connection_lost


def test_a_user_turn_starts_the_work():
    """A message to the agent is the start of a turn, whoever sent it."""
    from maelstrom.agent_model import user_message

    view = replay("normal-turn.jsonl")
    assert agent_status(view) == "idle"
    view, _ = apply_stream_event(view, user_message("carry on"), NOW)
    assert agent_status(view) == "processing"


def test_a_tool_result_does_not_start_the_work():
    """A tool result is a user event, but it is the child talking to itself."""
    view = replay("normal-turn.jsonl")
    result = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    }
    view, _ = apply_stream_event(view, result, NOW)
    assert agent_status(view) == "idle"


def test_tool_cards_match_the_typescript_reference():
    """`web/src/session/toolCards.ts` is the reference; this is a hand port.

    Nothing else ties the two together, so the golden is what catches a
    one-sided change. `UPDATE_GOLDEN=1 pnpm test` in `web/` re-records it.
    """
    golden = json.loads((FIXTURES / "normalised" / "tool-cards.json").read_text())
    for row in golden:
        item = {"type": "tool_call", "tool": row["tool"], "input": row["input"]}
        assert classify_tool_call(item) == row["kind"], row["tool"]
        assert tool_call_title(item) == row["title"], row["tool"]
