"""What ``mael agent attach`` shows, derived from one agent's raw event stream.

Pure model layer, per ``docs/dev/architecture-patterns.md``, and the sibling of
``session_view``: a reducer over the attach stream plus the small derivations
the TUI renders. No I/O, no clock, no widgets — so the whole view is
exercisable by replaying a recorded transcript.

The transcript itself is not reduced here. This module carries a one-agent
:class:`~maelstrom.orchestrator.protocol.ClientState` and reduces through
``orchestrator.normalise``, which is golden-tested against the TypeScript
reference.

What that normaliser does not carry, this module adds: token usage, the working
directory, the two ``mael_*`` stream markers, and whether the stream ended
because the agent did or because the connection went.
"""

from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from .agent_model import (
    AGENT_DETAIL,
    AGENT_EXITED,
    BACKLOG_END,
    PLAN_TOOL,
    QUESTION_TOOL,
    TRUNCATED,
)
from .orchestrator.normalise import NormaliseContext, normalise_stream_event
from .orchestrator.normalise import mark_exited as normalise_exited
from .orchestrator.protocol import (
    Agent,
    ClientState,
    ServerEvent,
    TranscriptItem,
    apply_event,
    initial_client_state,
)

#: Item types that carry a ``requestId`` the user can answer.
PROMPT_ITEMS = ("question", "permission_request", "plan_review")


@dataclass(frozen=True)
class TokenUsage:
    """Tokens the session has consumed, summed over its turns.

    A ``result`` reports the turn that just ended, not the session, so each one
    adds to the running total. ``total_cost_usd`` on the same event is the
    session's, which is why the footer's cost is read and its tokens are added.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_creation

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_creation=self.cache_creation + other.cache_creation,
        )


@dataclass(frozen=True)
class AttachView:
    """Everything one attached client knows, derived from the stream it reads."""

    agent_id: str
    #: One agent in ``world.agents``, reduced by the orchestrator protocol so
    #: the TUI and the web UI show the same items.
    client: ClientState
    #: The items this client has built from the transcript events it read.
    #: The server keeps none — the projection is relayed, not stored — so a
    #: client accumulates its own, next to the thing that renders it.
    items: tuple[TranscriptItem, ...] = ()
    ctx: NormaliseContext = field(default_factory=lambda: NormaliseContext(""))
    usage: TokenUsage = TokenUsage()
    cwd: str = ""
    #: Whether the replayed backlog has ended. A wait resolved inside the
    #: backlog must not prompt, so nothing prompts until the marker lands.
    backlog_done: bool = False
    #: The daemon said events this client should have seen are gone: the ring
    #: rolled past them before the attach, or the queue overflowed mid-stream.
    truncated: bool = False
    #: How many events the daemon said were dropped, in total.
    dropped: int = 0
    exit_code: int | None = None
    exited: bool = False
    #: The stream stopped without an exit marker — the daemon or the socket
    #: went, not the agent.
    connection_lost: bool = False


def _blank_agent(agent_id: str) -> Agent:
    """The one agent entry the normaliser needs, with nothing an attach knows."""
    return {
        "id": agent_id,
        "state": "idle",
        "session": "",
        "cwd": "",
        "model": "",
        "permissionMode": "",
        "waitingOn": "",
        "lastMessage": "",
        "costUsd": 0.0,
        "taskId": "",
        "project": "",
        "worktreeId": "",
        "exitCode": None,
        "pendingRequestId": None,
    }


def initial_view(agent_id: str) -> AttachView:
    """A view of an agent whose stream has said nothing yet."""
    client = initial_client_state()
    client["world"]["agents"][agent_id] = _blank_agent(agent_id)
    return AttachView(
        agent_id=agent_id, client=client, ctx=NormaliseContext(agent_id=agent_id)
    )


def apply_stream_event(
    view: AttachView, raw: dict[str, Any], now: str
) -> tuple[AttachView, list[ServerEvent]]:
    """The view after one raw attach event, and the transcript events it made.

    The events come back so a renderer can mount and patch the widgets that
    actually changed, instead of redrawing the whole transcript.
    """
    kind = raw.get("type")

    if kind == BACKLOG_END:
        return replace(view, backlog_done=True), []

    if kind == TRUNCATED:
        dropped = _int_or_none(raw.get("dropped")) or 0
        return replace(view, truncated=True, dropped=view.dropped + dropped), []

    if kind == AGENT_EXITED:
        # Coerced once, so the view and the world cannot disagree about the
        # same exit code.
        exit_code = _int_or_none(raw.get("exit_code"))
        result = normalise_exited(view.client, view.ctx, exit_code, now)
        client = _reduce(view.client, result.events)
        items = _with_items(view.items, result.events)
        return (
            replace(
                view,
                client=client,
                items=items,
                ctx=result.ctx,
                exit_code=exit_code,
                exited=True,
            ),
            result.events,
        )

    if kind == AGENT_DETAIL:
        # The host's opening frame, not one of the agent's own events. It says
        # what the agent waits on; the backlog that follows usually replays the
        # request itself, so nothing is derived from it here.
        return view, []

    if kind == "system" and raw.get("subtype") == "init":
        cwd = raw.get("cwd")
        if isinstance(cwd, str) and cwd:
            view = replace(view, cwd=cwd)

    if kind == "result":
        view = replace(view, usage=view.usage + _usage_of(raw))

    result = normalise_stream_event(view.client, view.ctx, raw, now)
    client = _reduce(view.client, result.events)
    items = _with_items(view.items, result.events)
    return replace(view, client=client, items=items, ctx=result.ctx), result.events


def mark_stream_ended(view: AttachView) -> AttachView:
    """The view of a stream that stopped without saying why.

    An attach ends with an exit marker when the agent's process goes. A stream
    that simply stops means the daemon or the socket went instead, and the
    agent may well still be running — so this is a different thing to say.
    """
    if view.exited:
        return view
    return replace(view, connection_lost=True)


def _reduce(client: ClientState, events: list[ServerEvent]) -> ClientState:
    for event in events:
        client = apply_event(client, event)
    return client


def _with_items(
    items: tuple[TranscriptItem, ...], events: list[ServerEvent]
) -> tuple[TranscriptItem, ...]:
    """``items`` after the transcript events in ``events``.

    The same reduction ``web/src/protocol/reducer.ts`` runs. The server relays
    these events rather than storing what they add up to, so every client
    keeps its own copy.
    """
    out = list(items)
    for event in events:
        kind = event.get("type")
        if kind == "transcript.append":
            out.append(event["item"])
        elif kind == "transcript.update":
            out = [
                {**i, **event["patch"]} if i["id"] == event["itemId"] else i
                for i in out
            ]
    return tuple(out)


def _usage_of(raw: dict[str, Any]) -> TokenUsage:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()
    return TokenUsage(
        input=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
        cache_creation=_int(usage.get("cache_creation_input_tokens")),
    )


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- what the TUI renders --------------------------------------------------


def transcript_items(view: AttachView) -> list[TranscriptItem]:
    """Every item of the agent's transcript, oldest first."""
    return list(view.items)


def agent_status(view: AttachView) -> str:
    """The agent's state, in the vocabulary of ``mael agent list``."""
    agent = view.client["world"]["agents"].get(view.agent_id)
    return agent["state"] if agent else "idle"


def pending_prompt(view: AttachView) -> TranscriptItem | None:
    """The wait this client should ask the user about, if any.

    Nothing prompts while the backlog is still replaying: a request answered
    before this client attached is still in the history, and re-asking it would
    put a modal in front of a question the agent has already moved past.
    """
    if not view.backlog_done or view.exited or view.connection_lost:
        return None
    agent = view.client["world"]["agents"].get(view.agent_id)
    request_id = agent.get("pendingRequestId") if agent else None
    if not request_id:
        return None
    for item in reversed(transcript_items(view)):
        if item["type"] in PROMPT_ITEMS and item.get("requestId") == request_id:
            return item
    return None


def plan_markdown(view: AttachView, item: TranscriptItem) -> str:
    """The plan under review, from the document the normaliser filed it in.

    The document already holds the fallback for a plan the agent could not
    write to a file: the normaliser puts the last thing the agent said there
    when ``ExitPlanMode`` arrives with no ``plan``.
    """
    document_id = item.get("documentId")
    if not document_id:
        return ""
    document = view.client["world"]["documents"].get(document_id)
    return document["markdown"] if document else ""


#: Which card draws a tool call. A port of ``web/src/session/toolCards.ts``, so
#: the TUI and the web UI classify the same call the same way.
_TOOL_KINDS = {
    "Bash": "bash",
    "Edit": "edit",
    "Write": "write",
    "Read": "read",
    QUESTION_TOOL: "wait",
    PLAN_TOOL: "wait",
}


def classify_tool_call(item: TranscriptItem) -> str:
    return _TOOL_KINDS.get(_str(item.get("tool")), "generic")


def tool_call_title(item: TranscriptItem) -> str:
    """One line naming the call, for the card header."""
    inp = item.get("input")
    inp = inp if isinstance(inp, dict) else {}
    kind = classify_tool_call(item)
    if kind == "bash":
        return _str(inp.get("description")) or _str(inp.get("command"))
    if kind in ("edit", "write", "read"):
        return _str(inp.get("file_path"))
    return (
        _str(inp.get("url")) or _str(inp.get("query")) or _str(inp.get("description"))
    )


def turn_result_line(item: TranscriptItem) -> str:
    """A finished turn as one dim line: how it ended, what it cost, how long."""
    parts = [f"turn {_str(item.get('subtype')) or 'success'}"]
    cost = item.get("costUsd")
    if isinstance(cost, (int, float)) and cost:
        parts.append(f"${cost:.2f}")
    duration = item.get("durationMs")
    if isinstance(duration, (int, float)) and duration:
        parts.append(f"{duration / 1000:.1f}s")
    return " · ".join(parts)


def footer_fields(view: AttachView, branch: str) -> dict[str, str]:
    """The status footer: where the agent is, what it runs on, and what it does."""
    agent = view.client["world"]["agents"].get(view.agent_id)
    return {
        "cwd": PurePosixPath(view.cwd).name if view.cwd else "",
        "model": agent["model"] if agent else "",
        "tokens": _tokens(view.usage),
        "branch": branch,
        "state": agent_status(view),
        "mode": agent["permissionMode"] if agent else "",
    }


def _tokens(usage: TokenUsage) -> str:
    """Total tokens, short enough for a footer."""
    total = usage.total
    if not total:
        return ""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M tok"
    if total >= 1000:
        return f"{total / 1000:.0f}k tok"
    return f"{total} tok"


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
