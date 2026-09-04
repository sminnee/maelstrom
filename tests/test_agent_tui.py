"""The attach TUI, driven headless through Textual's own test pilot.

No socket and no terminal: the app takes its client in the constructor, and
``run_test`` gives it a headless driver. ``client.push`` delivers a live event
the way the daemon would.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maelstrom.agent_model import AGENT_EXITED, BACKLOG_END
from maelstrom.agent_tui import AttachApp

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"

NOW = "2026-01-01T00:00:00Z"


def events(name: str, *, stop_before_control: bool = False) -> list[dict]:
    """One fixture as a list of raw events."""
    out: list[dict] = []
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        out.append(event)
        if stop_before_control and event.get("type") == "control_request":
            break
    return out


@dataclass
class ScriptedAsyncDaemonClient:
    """An async client that replays a backlog, then whatever a test pushes."""

    backlog: list[dict] = field(default_factory=list)
    replies: dict[str, dict] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    attached: list[str] = field(default_factory=list)
    #: Set when a test wants one command to fail.
    error_for: str = ""

    def __post_init__(self) -> None:
        self._live: asyncio.Queue[dict] = asyncio.Queue()

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if payload.get("cmd") == self.error_for:
            return {"error": "the daemon said no"}
        return self.replies.get(str(payload.get("cmd")), {"ok": True})

    async def attach(self, agent_id: str):
        self.attached.append(agent_id)
        try:
            for event in self.backlog:
                yield event
            yield {"type": BACKLOG_END}
            while True:
                yield await self._live.get()
        finally:
            if agent_id in self.attached:
                self.attached.remove(agent_id)

    def push(self, event: dict) -> None:
        self._live.put_nowait(event)


def make_app(backlog=None, **kwargs) -> tuple[AttachApp, ScriptedAsyncDaemonClient]:
    client = ScriptedAsyncDaemonClient(backlog=list(backlog or []), **kwargs)
    app = AttachApp("a1", client, branch_of=lambda _cwd: "main", clock=lambda: NOW)
    return app, client


def drive(body):
    """Run one async test body on a fresh loop."""
    return asyncio.run(body())


def widget_text(widget) -> str:
    """Whatever one transcript widget draws, as plain text."""
    from textual.widgets import Markdown

    if isinstance(widget, Markdown):
        return widget.source
    return str(getattr(widget._render(), "plain", widget._render()))


def transcript_text(app: AttachApp) -> str:
    return "\n".join(
        widget_text(widget) for widget in app.query("#transcript").first().children
    )


def working_shown(app: AttachApp) -> bool:
    """Whether the line saying the agent owes a reply is on screen."""
    return app.query_one("#working").display


def status_text(app: AttachApp) -> str:
    return widget_text(app.query_one("#status"))


def test_the_backlog_fills_the_transcript_on_connect():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "Hello there, friend" in transcript_text(app)
            assert client.attached == ["a1"]

    drive(body)


def test_enter_sends_say_and_clears_the_console():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            console = app.query_one("#console")
            console.value = "carry on"
            await pilot.press("enter")
            await pilot.pause()
            assert {"cmd": "say", "id": "a1", "text": "carry on"} in client.calls
            assert console.value == ""

    drive(body)


def test_empty_input_sends_nothing():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#console").value = "   "
            await pilot.press("enter")
            await pilot.pause()
            assert [c for c in client.calls if c.get("cmd") == "say"] == []

    drive(body)


def test_the_console_keeps_its_text_on_a_daemon_error():
    """Losing what you typed to a transient daemon error is unforgivable."""
    app, client = make_app(events("normal-turn.jsonl"), error_for="say")

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            console = app.query_one("#console")
            console.value = "keep me"
            await pilot.press("enter")
            await pilot.pause()
            assert console.value == "keep me"

    drive(body)


def test_a_live_permission_request_opens_the_prompt():
    from maelstrom.agent_tui import PermissionScreen

    raw = events("permission-request.jsonl", stop_before_control=True)
    app, client = make_app(raw[:-1])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            client.push(raw[-1])
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PermissionScreen)

    drive(body)


def test_y_approves():
    raw = events("permission-request.jsonl", stop_before_control=True)
    app, client = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert {"cmd": "approve", "id": "a1"} in client.calls

    drive(body)


def test_n_asks_for_a_reason_then_denies():
    raw = events("permission-request.jsonl", stop_before_control=True)
    app, client = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            app.screen.query_one("#reason").value = "not on a public network"
            await pilot.press("enter")
            await pilot.pause()
            deny = [c for c in client.calls if c.get("cmd") == "deny"]
            assert deny == [
                {"cmd": "deny", "id": "a1", "reason": "not on a public network"}
            ]

    drive(body)


def test_a_question_prompt_sends_every_answer_at_once():
    raw = events("question-unanswered.jsonl", stop_before_control=True)
    app, client = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            screen.pick("Green")
            await screen.submit()
            await pilot.pause()
            answer = [c for c in client.calls if c.get("cmd") == "answer"]
            assert answer == [
                {
                    "cmd": "answer",
                    "id": "a1",
                    "answers": {"Which colour do you prefer?": "Green"},
                }
            ]

    drive(body)


def _question_request(*, multi: bool, request_id: str = "r1") -> dict:
    """One `AskUserQuestion` asking a two-option question."""
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "question": "Which colour?",
                        "header": "Colour",
                        "multiSelect": multi,
                        "options": [
                            {"label": "Green", "description": "calm"},
                            {"label": "Red", "description": "bold"},
                        ],
                    }
                ]
            },
        },
    }


def test_a_multi_select_answer_joins_the_labels():
    app, client = make_app([_question_request(multi=True)])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            screen.pick("Green")
            screen.pick("Red")
            await screen.submit()
            await pilot.pause()
            assert client.calls[-1]["answers"] == {"Which colour?": "Green, Red"}

    drive(body)


def test_a_single_select_answer_keeps_only_the_last_choice():
    """The agent offered one answer, so two would not be a valid answer at all."""
    app, client = make_app([_question_request(multi=False)])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            screen.pick("Green")
            screen.pick("Red")
            await screen.submit()
            await pilot.pause()
            assert client.calls[-1]["answers"] == {"Which colour?": "Red"}

    drive(body)


def test_submitting_no_answer_sends_nothing():
    """An empty answers map reads to the agent as no answer at all."""
    app, client = make_app([_question_request(multi=False)])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await app.screen.submit()
            await pilot.pause()
            assert [c for c in client.calls if c.get("cmd") == "answer"] == []

    drive(body)


def test_a_plan_review_shows_the_plan_and_approves():
    from maelstrom.agent_tui import PlanScreen

    raw = events("plan-review-with-plan.jsonl", stop_before_control=True)
    app, client = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PlanScreen)
            assert "## Verification" in app.screen.markdown
            await pilot.press("y")
            await pilot.pause()
            assert {"cmd": "approve", "id": "a1"} in client.calls

    drive(body)


def test_a_response_from_another_client_dismisses_the_prompt():
    from maelstrom.agent_tui import PermissionScreen

    raw = events("permission-request.jsonl", stop_before_control=True)
    app, client = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, PermissionScreen)
            request_id = raw[-1]["request_id"]
            client.push(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": request_id,
                        "response": {"behavior": "allow", "updatedInput": {}},
                    },
                }
            )
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, PermissionScreen)

    drive(body)


def test_a_wait_already_answered_in_the_backlog_does_not_prompt():
    from maelstrom.agent_tui import QuestionScreen

    app, _ = make_app(events("question-answered.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert not isinstance(app.screen, QuestionScreen)

    drive(body)


def test_escape_interrupts_while_processing():
    raw = events("normal-turn.jsonl")
    # Everything up to the last assistant message: the turn has not ended.
    mid = [e for e in raw if e.get("type") != "result"]
    app, client = make_app(mid)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert {"cmd": "interrupt", "id": "a1"} in client.calls

    drive(body)


def test_escape_is_a_noop_when_idle():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert [c for c in client.calls if c.get("cmd") == "interrupt"] == []

    drive(body)


def test_ctrl_c_detaches_without_stopping_the_agent():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
        assert [c for c in client.calls if c.get("cmd") == "stop"] == []
        assert client.attached == []

    drive(body)


def test_the_exit_marker_disables_the_console():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            client.push({"type": AGENT_EXITED, "exit_code": 0})
            await pilot.pause()
            await pilot.pause()
            assert app.query_one("#console").disabled
            assert "agent exited (0)" in transcript_text(app)

    drive(body)


def test_the_footer_names_the_branch_model_and_wait_kind():
    raw = events("permission-request.jsonl", stop_before_control=True)
    app, _ = make_app(raw)

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            text = status_text(app)
            assert "main" in text
            assert "claude-opus-5" in text
            assert "awaiting-permission" in text
            assert "normal" in text

    drive(body)


def test_shift_tab_cycles_the_permission_mode():
    """The footer is not touched here: the child's own status event moves it."""
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()
            assert {"cmd": "set-mode", "id": "a1", "mode": "plan"} in client.calls

    drive(body)


def test_agent_text_in_square_brackets_survives():
    """Agent output is text, never Textual markup: `[…]` must not vanish."""
    marker = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}],
        },
    }
    app, _ = make_app(events("normal-turn.jsonl") + [marker])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "[Request interrupted by user]" in transcript_text(app)

    drive(body)


def test_a_tool_call_title_in_square_brackets_survives():
    call = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"description": "grep for [a-z] in the tree"},
                }
            ],
        },
    }
    app, _ = make_app([call])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "[a-z]" in transcript_text(app)

    drive(body)


def test_an_option_label_in_square_brackets_survives():
    """An option label is model-written text, and may hold anything."""
    from textual.widgets import SelectionList

    from maelstrom.agent_tui import QuestionScreen

    request = {
        "type": "control_request",
        "request_id": "r1",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "AskUserQuestion",
            "input": {
                "questions": [
                    {
                        "question": "Which pattern?",
                        "header": "Pattern",
                        "options": [{"label": "[a-z]+", "description": "lower case"}],
                    }
                ]
            },
        },
    }
    app, _ = make_app([request])

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, QuestionScreen)
            options = app.screen.query_one("#options", SelectionList)
            rendered = " ".join(str(o.prompt) for o in options.options)
            assert "[a-z]+" in rendered

    drive(body)


def test_sending_shows_a_working_line_at_once():
    """The agent takes seconds to answer; the console must not look ignored."""
    app, _ = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not working_shown(app)
            app.query_one("#console").value = "carry on"
            await pilot.press("enter")
            await pilot.pause()
            assert working_shown(app)

    drive(body)


def test_the_working_line_goes_when_the_turn_ends():
    app, client = make_app(events("normal-turn.jsonl"))

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#console").value = "carry on"
            await pilot.press("enter")
            await pilot.pause()
            assert working_shown(app)
            client.push({"type": "result", "subtype": "success"})
            await pilot.pause()
            await pilot.pause()
            assert not working_shown(app)

    drive(body)


def test_a_failed_send_shows_no_working_line():
    app, _ = make_app(events("normal-turn.jsonl"), error_for="say")

    async def body():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#console").value = "carry on"
            await pilot.press("enter")
            await pilot.pause()
            assert not working_shown(app)

    drive(body)
