"""The terminal UI behind ``mael agent attach``.

The adapter layer over :mod:`maelstrom.agent_view`: it renders what that module
derives, and sends what the user does back over the control socket. It holds no
protocol knowledge of its own — every decision about what an event means is
made in the view model, so this file is widgets and key handling.

The daemon client, the branch lookup and the clock are all injected, so the app
is drivable headless with no socket and no terminal.

A permission ask, a question and a plan review each open a modal screen. See
:meth:`AttachApp._sync_prompt` for when one opens and closes.
"""

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Markdown, SelectionList, Static

from .agent_model import AGENT_EXITED, INTERRUPTIBLE, RECENT_LIMIT, next_mode
from .agent_transport import AsyncDaemonClient
from .agent_view import (
    AttachView,
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
from .orchestrator.protocol import TranscriptItem

#: How many lines of a tool's output the card shows.
TOOL_OUTPUT_LINES = 4
#: How many lines of a permission request's input the modal shows.
INPUT_PREVIEW_LINES = 40
#: The free-text row every question offers, and the label it is stored under.
OTHER_LABEL = "Other…"

#: Shown while the agent owes a reply. An agent takes seconds to answer, and
#: without this the console looks like it swallowed what was typed.
WORKING_LINE = "… working"

#: Agent states that mean the agent still owes a reply.
BUSY = INTERRUPTIBLE


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_branch_or_blank(cwd: str) -> str:
    """The git branch of ``cwd``, or ``""`` when there is not one to name.

    A footer must never be the reason an attach fails, so every way this can go
    wrong — no directory, no repository, no git — is an empty string.
    """
    if not cwd or not Path(cwd).is_dir():
        return ""
    try:
        from .worktree import get_current_branch

        return get_current_branch(Path(cwd))
    except (subprocess.CalledProcessError, OSError):
        return ""


# --- transcript widgets ----------------------------------------------------


class ToolCallCard(Static):
    """One tool call: a header naming it, and the first lines of its result.

    A ``Read`` shows its header only. Its result is the file, which the agent
    is about to talk about anyway, and printing it would drown the transcript.
    An error or a denial shows in full: that is the case a reader has to act on.

    ``markup=False`` because a tool's title and output are arbitrary text.
    Textual reads ``[…]`` as a markup tag, so a grep pattern or a log line
    would silently render as nothing.
    """

    def __init__(self, item: TranscriptItem) -> None:
        self.item = item
        super().__init__(self._line(), markup=False)

    def refresh_item(self, item: TranscriptItem) -> None:
        self.item = item
        self.update(self._line())

    def _line(self) -> str:
        title = tool_call_title(self.item)
        header = f"{self.item.get('tool', '')}  {title}".rstrip()
        status = self.item.get("status", "running")
        if status == "running":
            return f"{header} …"
        output = str(self.item.get("output") or "")
        if status in ("error", "denied"):
            label = "denied" if status == "denied" else "error"
            body = _indent(output) if output else ""
            return f"{header}\n  {label}{chr(10) + body if body else ''}"
        if classify_tool_call(self.item) == "read" or not output:
            return header
        return f"{header}\n{_indent(_head(output, TOOL_OUTPUT_LINES))}"


class WaitLine(Static):
    """One wait, and how it ended. Patched in place when it does.

    ``markup=False`` for the same reason as :class:`ToolCallCard`: a tool
    description and a denial reason are text, not markup.
    """

    def __init__(self, item: TranscriptItem) -> None:
        self.item = item
        super().__init__(self._line(), markup=False)

    def refresh_item(self, item: TranscriptItem) -> None:
        self.item = item
        self.update(self._line())

    def _line(self) -> str:
        kind = self.item["type"]
        if kind == "question":
            answers = self.item.get("answers")
            head = (
                self.item["questions"][0]["question"] if self.item["questions"] else ""
            )
            if answers:
                return f"answered: {', '.join(str(v) for v in answers.values())}"
            return f"⏸ question: {head}"
        if kind == "plan_review":
            decision = self.item.get("decision")
            if decision == "approve":
                return "plan approved"
            if decision == "deny":
                return f"plan sent back: {self.item.get('reason', '')}".rstrip(": ")
            return "⏸ plan awaiting review"
        decision = self.item.get("decision")
        title = self.item.get("description") or self.item.get("tool", "")
        if decision == "allow":
            return f"✓ allowed: {title}"
        if decision == "deny":
            reason = self.item.get("reason", "")
            return f"✗ denied: {reason or title}"
        return f"⏸ permission: {self.item.get('tool', '')} {title}".rstrip()


def _widget_for(item: TranscriptItem) -> Widget | None:
    """The widget that draws one transcript item, or ``None`` for one that draws nothing."""
    kind = item["type"]
    if kind == "message":
        if item["role"] == "assistant":
            return Markdown(item["markdown"])
        return Static(f"you › {item['markdown']}", classes="user", markup=False)
    if kind == "tool_call":
        if classify_tool_call(item) == "wait":
            return None
        return ToolCallCard(item)
    if kind in ("question", "permission_request", "plan_review"):
        return WaitLine(item)
    if kind == "turn_result":
        return Static(turn_result_line(item), classes="dim")
    return None  # a system/init item is footer material, not a transcript line


# --- the prompts -----------------------------------------------------------


class _DecisionScreen(ModalScreen[dict | None]):
    """The approve-or-deny shape a permission ask and a plan review share.

    ``y`` allows at once. ``n`` reveals a reason field, because a denial the
    agent cannot read is a denial it will simply repeat.
    """

    BINDINGS = [
        Binding("y", "allow", "Approve"),
        Binding("n", "start_deny", "Deny"),
    ]

    def __init__(self, request_id: str) -> None:
        super().__init__()
        self.request_id = request_id

    def compose_actions(self) -> ComposeResult:
        yield Horizontal(
            Button("Approve (y)", id="allow", variant="success"),
            Button("Deny (n)", id="deny", variant="error"),
            id="actions",
        )
        reason = Input(placeholder="Why — the agent reads this", id="reason")
        reason.display = False
        yield reason

    def action_allow(self) -> None:
        self.dismiss({"decision": "allow"})

    def action_start_deny(self) -> None:
        reason = self.query_one("#reason", Input)
        reason.display = True
        reason.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow":
            self.action_allow()
        else:
            self.action_start_deny()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "reason":
            self.dismiss({"decision": "deny", "reason": event.value.strip()})


class PermissionScreen(_DecisionScreen):
    """One tool call the agent wants to make, with the input it proposes."""

    def __init__(self, item: TranscriptItem) -> None:
        super().__init__(item["requestId"])
        self.item = item

    def compose(self) -> ComposeResult:
        title = self.item.get("description") or self.item.get("tool", "")
        yield Static(
            f"{self.item.get('tool', '')} — {title}", id="prompt-title", markup=False
        )
        yield VerticalScroll(
            Static(_pretty(self.item.get("input")), markup=False), id="prompt-body"
        )
        yield from self.compose_actions()


class PlanScreen(_DecisionScreen):
    """The plan the agent wants to leave plan mode with, in full and scrollable."""

    def __init__(self, item: TranscriptItem, markdown: str) -> None:
        super().__init__(item["requestId"])
        self.item = item
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        yield Static("Plan awaiting review", id="prompt-title")
        yield VerticalScroll(Markdown(self.markdown), id="prompt-body")
        yield from self.compose_actions()


class QuestionScreen(ModalScreen[dict | None]):
    """The agent's questions, one page at a time, answered all at once.

    Nothing is sent until the last page. The daemon resolves the request on the
    first answer it receives, so a per-question send would answer the first and
    silently discard the rest — the same reason the web UI batches them.
    """

    BINDINGS = [Binding("enter", "advance", "Next", priority=True)]

    def __init__(self, item: TranscriptItem) -> None:
        super().__init__()
        self.item = item
        self.request_id = item["requestId"]
        self.questions: list[dict] = list(item.get("questions") or [])
        self.step = 0
        #: Chosen option labels per question text, and the free text if any.
        self.chosen: dict[str, list[str]] = {}
        self.other: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        question = self.questions[self.step] if self.questions else {}
        yield Static(
            question.get("header") or "Question", id="prompt-title", markup=False
        )
        yield Static(question.get("question", ""), id="prompt-question", markup=False)
        options = [
            (_option_line(option), option.get("label", ""))
            for option in question.get("options") or []
        ]
        options.append((OTHER_LABEL, OTHER_LABEL))
        # A prompt goes in as Content, not a string: SelectionList has no
        # markup switch, and an option label is model-written text that may
        # hold `[…]`, which Textual would otherwise read as a markup tag.
        yield SelectionList[str](
            *[(Content(line), value) for line, value in options], id="options"
        )
        other = Input(placeholder="Your own answer", id="other")
        other.display = False
        yield other
        yield Button("Answer", id="submit", variant="primary")

    def pick(self, label: str) -> None:
        """Choose ``label`` on the current page, as clicking its row would."""
        self.query_one("#options", SelectionList).select(label)

    def _current(self) -> dict:
        return self.questions[self.step] if self.questions else {}

    def _harvest(self) -> None:
        """Read the page's widgets into the draft answer."""
        question = self._current().get("question", "")
        try:
            options = self.query_one("#options", SelectionList)
        except NoMatches:  # a question with no options composes no list
            self.chosen[question] = []
            return
        selected = self._one_if_single(list(options.selected))
        self.chosen[question] = selected
        if OTHER_LABEL in selected:
            self.other[question] = self.query_one("#other", Input).value.strip()

    def _one_if_single(self, selected: list[str]) -> list[str]:
        """``selected``, cut to its last entry when the question admits one answer.

        The agent offered one answer, so sending two would not be an answer it
        can act on. Keeping the last is what the web UI does.
        """
        if self._current().get("multiSelect"):
            return selected
        return selected[-1:]

    def action_advance(self) -> None:
        self.run_worker(self.submit(), exit_on_error=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_advance()

    def on_selection_list_selected_changed(self) -> None:
        """Show the earlier picks of a single-choice question dropping away.

        The cut itself belongs to ``_harvest``, which every answer goes
        through; this only keeps the list showing what will be sent.
        """
        try:
            options = self.query_one("#options", SelectionList)
            other = self.query_one("#other", Input)
        except NoMatches:  # the message can outrace a recompose
            return
        selected = list(options.selected)
        keep = self._one_if_single(selected)
        for label in selected:
            if label not in keep:
                options.deselect(label)
        other.display = OTHER_LABEL in keep

    async def submit(self) -> None:
        """Advance a page, or dismiss with every answer once on the last one.

        An unanswered page goes nowhere. The agent reads an empty answers map
        as no answer at all, so stopping here is what tells the user, rather
        than the daemon refusing the reply a moment later.
        """
        self._harvest()
        if not self.answers().get(self._current().get("question", "")):
            self.notify("Choose an answer first.", severity="warning")
            return
        if self.step < len(self.questions) - 1:
            self.step += 1
            await self.recompose()
            return
        self.dismiss({"answers": self.answers()})

    def answers(self) -> dict[str, str]:
        """One answer string per question, encoded the way the web UI encodes them.

        Chosen labels join with ``", "``, and the free text is appended as
        another value rather than replacing them — a multi-select can pick both
        an option and Other.
        """
        out: dict[str, str] = {}
        for question in self.questions:
            text = question.get("question", "")
            chosen = self.chosen.get(text, [])
            labels = [label for label in chosen if label != OTHER_LABEL]
            free = self.other.get(text, "").strip() if OTHER_LABEL in chosen else ""
            answer = ", ".join(part for part in [*labels, free] if part)
            if answer:
                out[text] = answer
        return out


# --- the app ---------------------------------------------------------------


class AttachApp(App[None]):
    """Teleport into one driven agent: read what it does, and answer it.

    Every key that must work inside a text field or a modal is bound with
    ``priority``: Textual binds ``ctrl+c`` itself, ``Input`` binds ``ctrl+d``,
    and a modal would otherwise swallow Escape.

    Escape is interrupt, not "close this prompt" — see
    ``docs/dev/agent-daemon.md``.
    """

    CSS = """
    #transcript { height: 1fr; padding: 0 1; }
    #transcript > Markdown { margin: 0; }
    #console { dock: bottom; }
    #status { dock: bottom; height: 1; background: $panel; color: $text-muted; }
    .dim, .user { color: $text-muted; }
    ModalScreen { align: center middle; }
    #prompt-title { text-style: bold; padding: 0 1; }
    #prompt-question { padding: 0 1; }
    #prompt-body { max-height: 20; border: round $primary; padding: 0 1; }
    #actions { height: auto; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "interrupt", "Interrupt", priority=True),
        Binding("shift+tab", "cycle_mode", "Mode", priority=True),
        Binding("ctrl+c", "detach", "Detach", priority=True),
        Binding("ctrl+d", "detach", "Detach", show=False, priority=True),
    ]

    def __init__(
        self,
        agent_id: str,
        client: AsyncDaemonClient,
        *,
        branch_of: Callable[[str], str] = current_branch_or_blank,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.client = client
        self._branch_of = branch_of
        self._clock = clock
        self.view: AttachView = initial_view(agent_id)
        self._branch = ""
        self._widgets: dict[str, Widget] = {}
        #: Widgets built while the backlog replays, mounted in one batch.
        self._backlog_widgets: list[Widget] = []
        #: The request id the open modal is asking about, if one is open.
        self._prompting: str | None = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        working = Static(WORKING_LINE, id="working", classes="dim")
        working.display = False
        yield working
        yield Input(id="console", placeholder="Say something — Enter sends")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#console", Input).focus()
        self._render_status()
        self.run_worker(self._follow(), exclusive=True, exit_on_error=False)

    # -- the stream --

    async def _follow(self) -> None:
        """Read the agent's stream to its end, rendering as it goes.

        An async worker runs on the app's own loop, so this touches widgets
        directly rather than posting messages to itself.
        """
        stream: AsyncIterator[dict[str, Any]] = self.client.attach(self.agent_id)
        async for raw in stream:
            if "error" in raw and "type" not in raw:
                self.notify(str(raw["error"]), severity="error")
                await self._append(
                    Static(f"— {raw['error']}", classes="dim", markup=False)
                )
                self._disable_console()
                return
            await self._on_event(raw)
            if self.view.exited:
                return
        self.view = mark_stream_ended(self.view)
        if self.view.connection_lost:
            await self._append(Static("— connection to the daemon lost", classes="dim"))
            self._disable_console()
            self._render_status()

    async def _on_event(self, raw: dict[str, Any]) -> None:
        was_replaying = not self.view.backlog_done
        self.view, events = apply_stream_event(self.view, raw, self._clock())
        for event in events:
            await self._apply_to_transcript(event, batch=not self.view.backlog_done)

        if was_replaying and self.view.backlog_done:
            await self._flush_backlog()
        if raw.get("type") == AGENT_EXITED:
            await self._append(
                Static(f"— agent exited ({self.view.exit_code})", classes="dim")
            )
            self._disable_console()
        if raw.get("type") in ("system", "result") and self.view.cwd:
            await self._refresh_branch()
        self._set_working(agent_status(self.view) in BUSY)
        self._render_status()
        await self._sync_prompt()

    async def _apply_to_transcript(self, event: dict, *, batch: bool) -> None:
        kind = event.get("type")
        if kind == "transcript.append":
            item = event["item"]
            widget = _widget_for(item)
            if widget is None:
                return
            self._widgets[item["id"]] = widget
            if batch:
                self._backlog_widgets.append(widget)
            else:
                await self._append(widget)
        elif kind == "transcript.update":
            widget = self._widgets.get(event["itemId"])
            item = self._item(event["itemId"])
            if (
                widget is not None
                and item is not None
                and hasattr(widget, "refresh_item")
            ):
                widget.refresh_item(item)  # type: ignore[attr-defined]

    def _item(self, item_id: str) -> TranscriptItem | None:
        for item in transcript_items(self.view):
            if item["id"] == item_id:
                return item
        return None

    async def _flush_backlog(self) -> None:
        """Mount the whole replayed history at once, then jump to the end."""
        transcript = self.query_one("#transcript", VerticalScroll)
        if self.view.truncated:
            note = Static(
                f"… earlier events dropped (the daemon keeps the last {RECENT_LIMIT})",
                classes="dim",
            )
            self._backlog_widgets.insert(0, note)
        if self._backlog_widgets:
            await transcript.mount_all(self._backlog_widgets)
            self._backlog_widgets = []
        transcript.scroll_end(animate=False)

    async def _append(self, widget: Widget) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        at_bottom = transcript.is_vertical_scroll_end
        await transcript.mount(widget)
        if at_bottom:
            transcript.scroll_end(animate=False)

    async def _refresh_branch(self) -> None:
        """Re-read the branch off the event loop.

        The lookup runs git, and a slow or hung repository on the loop freezes
        the whole UI — including the Escape and Ctrl-C bindings that are the
        way out of it.
        """
        cwd = self.view.cwd
        self._branch = await asyncio.to_thread(self._branch_of, cwd)

    def _disable_console(self) -> None:
        self.query_one("#console", Input).disabled = True

    def _set_working(self, working: bool) -> None:
        """Show or hide the line saying the agent owes a reply."""
        try:
            self.query_one("#working", Static).display = working
        except NoMatches:  # the app is composing or tearing down
            return

    def _render_status(self) -> None:
        fields = footer_fields(self.view, self._branch)
        parts = [
            fields[key] for key in ("cwd", "model", "tokens", "branch", "state", "mode")
        ]
        self.query_one("#status", Static).update("  ·  ".join(p for p in parts if p))

    # -- the prompts --

    async def _sync_prompt(self) -> None:
        """Open, keep or close the modal, so it always matches the pending wait.

        One reconciliation rather than an open on request and a close on
        response: a wait can also end because another client answered it, or
        because the turn ended, or because the agent died.
        """
        item = pending_prompt(self.view)
        wanted = item["requestId"] if item else None
        if self._prompting == wanted:
            return
        if self._prompting is not None:
            self._prompting = None
            if isinstance(self.screen, (PermissionScreen, PlanScreen, QuestionScreen)):
                self.pop_screen()
        if item is None:
            return
        self._prompting = wanted
        self.push_screen(self._screen_for(item), self._answered)

    def _screen_for(self, item: TranscriptItem) -> ModalScreen[dict | None]:
        if item["type"] == "question":
            return QuestionScreen(item)
        if item["type"] == "plan_review":
            return PlanScreen(item, plan_markdown(self.view, item))
        return PermissionScreen(item)

    def _answered(self, result: dict | None) -> None:
        """What the user chose in a modal, sent back to the daemon.

        ``None`` is a dismissal this app made — the wait ended elsewhere — so
        there is nothing to send.
        """
        self._prompting = None
        if result is None:
            return
        if "answers" in result:
            payload = {
                "cmd": "answer",
                "id": self.agent_id,
                "answers": result["answers"],
            }
        elif result["decision"] == "allow":
            payload = {"cmd": "approve", "id": self.agent_id}
        else:
            payload = {
                "cmd": "deny",
                "id": self.agent_id,
                "reason": result.get("reason", ""),
            }
        self.run_worker(self._send_answer(payload), exit_on_error=False)

    async def _send_answer(self, payload: dict[str, Any]) -> None:
        reply = await self.client.request(payload)
        if "error" in reply:
            self.notify(str(reply["error"]), severity="error")
            # The wait is still pending, so reconciliation re-opens the prompt.
            await self._sync_prompt()
        elif reply.get("warning"):
            # The wait resolved; something alongside it did not. Reopening the
            # prompt would ask again about a wait the agent has moved past.
            self.notify(str(reply["warning"]), severity="warning")

    # -- what the user does --

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "console":
            return
        text = event.value.strip()
        if not text:
            return
        reply = await self.client.request(
            {"cmd": "say", "id": self.agent_id, "text": text}
        )
        if "error" in reply:
            # Keep what was typed: losing it to a transient error is worse than
            # the error itself.
            self.notify(str(reply["error"]), severity="error")
            return
        event.input.value = ""
        # The agent has not answered yet, so its state is still whatever it was.
        # Show the line now rather than waiting for the first event to arrive.
        self._set_working(True)

    def action_interrupt(self) -> None:
        """Abandon the running turn. A no-op when there is not one."""
        if agent_status(self.view) not in INTERRUPTIBLE:
            return
        self.run_worker(self._interrupt(), exit_on_error=False)

    async def _interrupt(self) -> None:
        reply = await self.client.request({"cmd": "interrupt", "id": self.agent_id})
        if "error" in reply:
            self.notify(str(reply["error"]), severity="error")

    def action_cycle_mode(self) -> None:
        """Move the agent to the next permission mode."""
        self.run_worker(self._cycle_mode(), exit_on_error=False)

    async def _cycle_mode(self) -> None:
        fields = footer_fields(self.view, self._branch)
        mode = next_mode(fields["mode"])
        reply = await self.client.request(
            {"cmd": "set-mode", "id": self.agent_id, "mode": mode}
        )
        if "error" in reply:
            self.notify(str(reply["error"]), severity="error")
        # The footer is not touched here: the child announces the mode it is in.

    def action_detach(self) -> None:
        """Leave, with the agent still running. ``mael agent stop`` ends one."""
        self.exit()


# --- rendering helpers -----------------------------------------------------


def _head(text: str, lines: int) -> str:
    parts = text.splitlines()
    if len(parts) <= lines:
        return text
    return "\n".join(parts[:lines] + [f"… {len(parts) - lines} more lines"])


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())


def _option_line(option: dict) -> str:
    label = option.get("label", "")
    description = option.get("description", "")
    return f"{label} — {description}" if description else label


def _pretty(value: Any) -> str:
    """A tool's proposed input, readable and bounded."""
    if not value:
        return ""
    try:
        text = json.dumps(value, indent=2)
    except (TypeError, ValueError):
        text = str(value)
    return _head(text, INPUT_PREVIEW_LINES)
