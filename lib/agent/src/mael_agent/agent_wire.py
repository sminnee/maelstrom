"""The agent daemon's wire contract: what a client sends it and reads back.

Every name a client needs to build a request or read a reply lives here: the
statuses and modes a row reports, the stream markers, the request payloads, the
replies to an ask, the token counts, and the shapes of a row and a detail. A
client imports this module and never :mod:`mael_daemon.agent_model`, which is the
daemon's reducer. See ``docs/dev/agent-daemon.md`` for the protocol.

Pure: no I/O, no clock, no subprocess.
"""

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

#: Tools whose ``can_use_tool`` request is a question rather than a permission ask.
QUESTION_TOOL = "AskUserQuestion"
PLAN_TOOL = "ExitPlanMode"

# The states an agent can be in. Every one is observed from an event rather
# than inferred, so there is no staleness fudge here and an interrupt is visible.
IDLE = "idle"
PROCESSING = "processing"
AWAITING_PERMISSION = "awaiting-permission"
AWAITING_QUESTION = "awaiting-question"
AWAITING_PLAN_REVIEW = "awaiting-plan-review"
#: A subagent runs on after the turn ended (see CONTEXT.md, "Delegating").
#: The one state the row derives rather than observes: the reducer's own
#: status stays ``IDLE``. In neither tuple below.
DELEGATING = "delegating"
#: Terminal: the child process is gone. An exited agent answers nothing.
EXITED = "exited"

#: States in which the agent is blocked on a person. Narrower than
#: ``INTERRUPTIBLE``, which also covers a turn the agent is running itself.
WAITING = (
    AWAITING_PERMISSION,
    AWAITING_QUESTION,
    AWAITING_PLAN_REVIEW,
)

#: States in which the agent still owes a reply, so a turn exists to interrupt.
INTERRUPTIBLE = (
    PROCESSING,
    AWAITING_PERMISSION,
    AWAITING_QUESTION,
    AWAITING_PLAN_REVIEW,
)

#: The permission modes an agent can run in, in the order a cycle visits them.
MODES = ("plan", "auto", "normal")

#: The mode an approved plan puts an agent into -- see `Permission mode` in
#: CONTEXT.md.
AUTO = "auto"

#: The one mode whose maelstrom word is not claude's: no flag at spawn, and
#: ``default`` on the pipe. Nothing outside this module spells ``default``.
NORMAL = "normal"
WIRE_MODE = {NORMAL: "default"}
_MAELSTROM_MODE = {wire: mael for mael, wire in WIRE_MODE.items()}


def to_wire_mode(mode: str) -> str:
    """``mode`` as the word ``claude`` uses on the pipe."""
    return WIRE_MODE.get(mode, mode)


def from_wire_mode(mode: str) -> str:
    """``mode`` as read off an event, in maelstrom's own words."""
    return _MAELSTROM_MODE.get(mode, mode)


def next_mode(mode: str) -> str:
    """The mode after ``mode`` in the cycle. An unknown mode starts it over."""
    if mode not in MODES:
        return MODES[0]
    return MODES[(MODES.index(mode) + 1) % len(MODES)]


#: How many events to keep per agent for ``attach`` to render on connect.
RECENT_LIMIT = 200

#: Event type the daemon writes once the replayed backlog has all been sent.
#: ``mael agent tail`` without ``-f`` stops there. A marker rather than an idle
#: timeout, because a timeout would race a slow agent and flake. Carries the
#: agent's ``epoch`` and the ``seq`` the replay reached, so a client can come
#: back with a cursor.
BACKLOG_END = "mael_backlog_end"

#: Event type the daemon writes when events a client should have seen are
#: gone: before the replay, when the ring rolled past the client's cursor, or
#: mid-stream, when the client's queue overflowed. Carries ``dropped``.
TRUNCATED = "mael_truncated"

#: The key the daemon stamps every recorded event with: its position in the
#: agent's stream, from 1, per life. In the ``mael_`` namespace so a consumer
#: that dispatches on ``type`` never sees it as an event.
SEQ_KEY = "mael_seq"

#: The key the daemon stamps every recorded event with: when it happened, as
#: an ISO 8601 string. In the ``mael_`` namespace for the same reason as
#: :data:`SEQ_KEY`. Empty when the daemon was given no clock.
TS_KEY = "mael_ts"

#: Event type the daemon writes to every attached client once the agent's
#: process has gone, carrying ``exit_code``. The last event of an attach
#: stream, so a client knows the agent ended it, not a dropped connection.
AGENT_EXITED = "mael_agent_exited"

#: Event type of an attach stream's opening frame, carrying
#: :class:`AgentDetail` under ``agent``.
AGENT_DETAIL = "mael_agent_detail"


@dataclass(frozen=True)
class PendingRequest:
    """One ``can_use_tool`` request the agent is blocked on.

    ``request_id`` is what the reply must echo back; the agent stays blocked
    until a ``control_response`` carrying it arrives.
    """

    request_id: str
    tool_name: str
    input: dict[str, Any]
    description: str = ""
    #: The dotted id of the subagent whose tool call this is, else ``""`` for
    #: the agent's own. The wait belongs to the agent either way — the child
    #: blocks on one request at a time, whoever raised it — but a user deciding
    #: wants to know which stream to read.
    subagent: str = ""

    @property
    def questions(self) -> list[str]:
        """The question texts of an ``AskUserQuestion``, else empty.

        The text doubles as the key an answer is filed under, so this is both
        what to show a user and what :func:`reply_for_answer` writes back.
        """
        if self.tool_name != QUESTION_TOOL:
            return []
        return [
            q["question"]
            for q in self.input.get("questions", [])
            if isinstance(q, dict) and "question" in q
        ]

    @property
    def wait_kind(self) -> str:
        """Which of the three waiting states this request puts the agent in.

        The tool name decides. A question and a plan review also carry
        ``requires_user_interaction``, but that flag adds nothing the tool name
        does not already say, so nothing reads it.
        """
        if self.tool_name == QUESTION_TOOL:
            return AWAITING_QUESTION
        if self.tool_name == PLAN_TOOL:
            return AWAITING_PLAN_REVIEW
        return AWAITING_PERMISSION

    @property
    def summary(self) -> str:
        """One line naming what the agent is waiting on."""
        if self.tool_name == QUESTION_TOOL:
            return self.questions[0] if self.questions else self.tool_name
        return self.description or self.tool_name


@dataclass(frozen=True)
class TokenUsage:
    """Tokens some run consumed, split the four ways a ``usage`` block reports.

    The daemon's state and the attach footer share the split, so the two can
    never disagree about what a turn held.
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

    def __sub__(self, other: "TokenUsage") -> "TokenUsage":
        """What was spent between two readings. Never negative: a reset reads 0."""
        return TokenUsage(
            input=max(self.input - other.input, 0),
            output=max(self.output - other.output, 0),
            cache_read=max(self.cache_read - other.cache_read, 0),
            cache_creation=max(self.cache_creation - other.cache_creation, 0),
        )

    def as_row(self) -> dict[str, int]:
        """This figure as a row reports it: the four counts and their total."""
        return {
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_creation": self.cache_creation,
            "total": self.total,
        }


#: The four counts on a ``result``'s ``usage`` that make up a turn's size.
#: See ``docs/dev/agent-daemon.md``, "A turn", for why cache counts are in.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def usage_of(usage: Any) -> TokenUsage:
    """One ``usage`` block, split the four ways. A missing block reads as zero.

    The field names come from :data:`USAGE_FIELDS`, so this split and the
    total :func:`tokens_of` reports can never name different fields.
    """
    if not isinstance(usage, dict):
        return TokenUsage()
    read = {name: _count(usage.get(name)) for name in USAGE_FIELDS}
    return TokenUsage(
        input=read["input_tokens"],
        output=read["output_tokens"],
        cache_read=read["cache_read_input_tokens"],
        cache_creation=read["cache_creation_input_tokens"],
    )


def _count(value: Any) -> int:
    """``value`` when it is a count, else 0. A bool is not a count."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def tokens_of(event: dict[str, Any]) -> int:
    """How many tokens the turn ``event`` reports, or 0 when it reports none.

    The one reader of a ``result``'s ``usage``, shared by the daemon's state
    and ``agent_view``'s per-attach total, so the two can never disagree about
    what a turn cost.
    """
    return _sum_usage(event.get("usage"), USAGE_FIELDS)


#: The three counts on an ``assistant``'s ``usage`` that make up the prompt.
#: ``output_tokens`` is out: it is what the model wrote, not what the prompt
#: holds. See ``docs/dev/agent-daemon.md``, "A turn".
CONTEXT_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def context_of(event: dict[str, Any]) -> int:
    """How large ``event``'s prompt was, or 0 when it reports no usage.

    Reads an ``assistant`` event, where ``usage`` sits under ``message`` and
    describes the one request that event answers — the context the agent held
    at that moment. A ``result``'s ``usage`` cannot answer this: it sums the
    turn's requests, so its cache counts run past the window the agent has.
    """
    message = event.get("message")
    usage = message.get("usage") if isinstance(message, dict) else None
    return _sum_usage(usage, CONTEXT_FIELDS)


def _sum_usage(usage: Any, field_names: tuple[str, ...]) -> int:
    """``field_names`` off a ``usage`` block, summed, skipping what is not a count.

    A missing or malformed count is 0, never an error: a size is worth showing
    approximately, and no stream event is worth a crash. A renamed field
    upstream therefore reads low rather than raising.
    """
    if not isinstance(usage, dict):
        return 0
    return sum(_count(usage.get(field_name)) for field_name in field_names)


def build_start_payload(
    worktree_path: Path,
    *,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    resume: bool = False,
    model: str | None = None,
    execute_model: str | None = None,
    prompt: str = "",
    system_prompt_file: Path | None = None,
) -> dict[str, Any]:
    """The daemon's ``start`` command for a launch. Pure.

    The same wire shape ``orchestrator/sources.py`` sends for a task launch, so
    both callers speak one protocol. Falsy fields are dropped: a taskless
    ``mael add`` sends nothing but the cwd, so the agent draws as a freeAgent
    node.

    ``env`` carries ``MAEL_TASK_ID``, which is what keeps the ``session-end``
    hook closing the task: Claude Code fires hooks as children of the driven
    ``claude``, so they inherit it.

    ``system_prompt_file`` is the file that teaches the child the markers.
    Callers pass :func:`~mael_domain.shared_dir.agent_prompt_file`.
    """
    # `resume` is always sent: False means "claim a fresh session", which is a
    # decision, not an omission. Every other falsy field means "the caller did
    # not say", so the daemon's own default applies.
    payload: dict[str, Any] = {
        "cmd": "start",
        "cwd": str(worktree_path),
        "resume": resume,
    }
    if prompt:
        payload["prompt"] = prompt
    if permission_mode:
        payload["mode"] = permission_mode
    if model:
        payload["model"] = model
    if execute_model:
        payload["execute_model"] = execute_model
    if session_id:
        payload["session"] = session_id
    if env:
        payload["env"] = dict(env)
    if system_prompt_file:
        payload["system_prompt_file"] = str(system_prompt_file)
    return payload


def build_resume_payload(
    agent_id: str, *, text: str = "", system_prompt_file: Path | None = None
) -> dict[str, Any]:
    """The daemon's ``resume`` command. Pure.

    ``text`` is the turn the agent gets back; without it the daemon picks one
    (see "The resume rules" in ``docs/dev/agent-daemon.md``).
    ``system_prompt_file`` overrides the one on the spawn record, so a record
    written before the daemon kept one still resumes with it.
    """
    payload: dict[str, Any] = {"cmd": "resume", "id": agent_id}
    if text:
        payload["text"] = text
    if system_prompt_file:
        payload["system_prompt_file"] = str(system_prompt_file)
    return payload


def plan_from_pending(
    pending: PendingRequest | None, last_message: str
) -> tuple[str, str]:
    """The plan under review and the file holding it, else two empty strings.

    ``ExitPlanMode`` carries the plan in its own ``input``, under ``plan``, with
    ``planFilePath`` naming the file the agent wrote it to. Read it from there.

    The fallback covers an agent that could not write its plan file: the write is
    denied, ``input`` arrives empty, and the agent puts the plan in an ordinary
    message instead. Recorded in ``plan-review.jsonl``, where a sandbox refused
    the write. Then the last message is the best available text, and there is no
    file to name.
    """
    if pending is None or pending.wait_kind != AWAITING_PLAN_REVIEW:
        return "", ""
    plan = pending.input.get("plan") or ""
    if plan:
        return plan, pending.input.get("planFilePath") or ""
    return last_message, ""


def _question_details(pending: PendingRequest | None) -> list[dict[str, Any]]:
    """Each question of an ``AskUserQuestion``, with its options, else empty."""
    if pending is None or pending.tool_name != QUESTION_TOOL:
        return []
    details = []
    for question in pending.input.get("questions", []):
        if not isinstance(question, dict):
            continue
        details.append(
            {
                "question": question.get("question", ""),
                "header": question.get("header", ""),
                "multi_select": bool(question.get("multiSelect")),
                "options": [
                    {
                        "label": option.get("label", ""),
                        "description": option.get("description", ""),
                    }
                    for option in question.get("options", [])
                    if isinstance(option, dict)
                ],
            }
        )
    return details


def user_message(
    text: str, images: Sequence[tuple[str, bytes]] | None = None
) -> dict[str, Any]:
    """A user turn, the way the stream-json input format wants it.

    This is the only way text reaches the agent — the initial prompt and every
    later follow-up are the same shape.

    ``images`` are ``(media_type, data)`` pairs sent as base64 image blocks
    ahead of the text, which is what the child accepts and what makes the model
    see the image on this turn rather than after reading a file. An image with
    no words is a message in its own right, so the text block is dropped when
    the text is empty and an image is present.
    """
    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(data).decode(),
            },
        }
        for media_type, data in images or ()
    ]
    if text or not content:
        content.append({"type": "text", "text": text})
    return {"type": "user", "message": {"role": "user", "content": content}}


def _control_response(request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The ``control_response`` envelope every reply shares."""
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": payload,
        },
    }


def interrupt_request(request_id: str) -> dict[str, Any]:
    """Ask the child to abandon the turn it is running.

    Unlike every other message here this is a request the host makes of the
    child, not a reply to one, so it carries its own ``request_id`` for the
    child's ``control_response`` to echo. The child then closes the turn with
    an error-subtype ``result``.

    An interrupt does not answer a pending ``can_use_tool``. Deny that first.
    """
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {"subtype": "interrupt"},
    }


def set_mode_request(request_id: str, mode: str) -> dict[str, Any]:
    """Ask the child to run the rest of the session in ``mode``.

    A host-originated request, like :func:`interrupt_request`, so it carries its
    own ``request_id``. Unlike an interrupt the reply matters: the child refuses
    a mode it does not know.

    ``mode`` is maelstrom's word; the wire gets claude's.
    """
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {"subtype": "set_permission_mode", "mode": to_wire_mode(mode)},
    }


def reply_for_approval(pending: PendingRequest) -> dict[str, Any]:
    """Allow the pending call, with its input unchanged.

    ``updatedInput`` is not optional: the CLI runs the tool with whatever it
    carries, so echoing the original input is what "approve as proposed" means.
    """
    return _control_response(
        pending.request_id, {"behavior": "allow", "updatedInput": pending.input}
    )


def reply_for_denial(pending: PendingRequest, reason: str = "") -> dict[str, Any]:
    """Deny the pending call. ``reason`` reaches the agent as the tool result."""
    return _control_response(
        pending.request_id,
        {"behavior": "deny", "message": reason or "Denied by mael agent"},
    )


def reply_for_answers(
    pending: PendingRequest, answers: dict[str, str]
) -> dict[str, Any]:
    """Answer an ``AskUserQuestion`` with one answer per question.

    An answer is not a separate message — it rides back on the same allow, in
    ``updatedInput['answers']``, keyed by each question's own text. Allowing
    the call without that key is what "the user did not answer the questions"
    means to the agent, so a bare :func:`reply_for_approval` would look like an
    answer and silently be none.

    The orchestrator UI answers every question at once this way;
    :func:`reply_for_answer` is the one-choice-for-all form the CLI uses.

    Raises:
        ValueError: If ``answers`` is empty — the agent reads an empty map as
            no answer at all, so sending it would resolve the wait wrongly.
    """
    if not answers:
        raise ValueError("no answers given")
    payload = dict(pending.input)
    payload["answers"] = dict(answers)
    return _control_response(
        pending.request_id, {"behavior": "allow", "updatedInput": payload}
    )


def reply_for_answer(pending: PendingRequest, choice: str) -> dict[str, Any]:
    """Answer an ``AskUserQuestion`` with ``choice``.

    A ``choice`` applies to every question asked. Multi-question prompts are
    rare; :func:`reply_for_answers` is the per-question form.
    """
    answers = {question: choice for question in pending.questions}
    return reply_for_answers(pending, answers)


#: What ``list`` may be asked for. ``running`` is the default and is what the
#: orchestrator reads: live and exited-this-daemon agents, and nothing else.
SCOPE_RUNNING = "running"
SCOPE_STOPPED = "stopped"
SCOPE_ALL = "all"
SCOPES = (SCOPE_RUNNING, SCOPE_STOPPED, SCOPE_ALL)


# --- the shapes a reply carries ----------------------------------------------


class AgentRow(TypedDict):
    """One agent or subagent, as ``list`` reports it. Every key is always set."""

    id: str
    parent: str
    description: str
    state: str
    session: str
    cwd: str
    pid: int | None
    model: str
    mode: str
    waiting_on: str
    last_message: str
    last_message_at: str
    last_note: str
    last_note_at: str
    cost: str
    tokens: int
    subagent_tokens: dict[str, int]
    context_tokens: int


class PendingFields(TypedDict):
    """The detail keys a wait fills. Empty values when none is open."""

    request_id: str
    waiting_kind: str
    waiting_tool: str
    waiting_input: dict[str, Any]
    waiting_subagent: str
    questions: list[dict[str, Any]]
    plan: str
    plan_file: str


class AgentDetail(AgentRow, PendingFields):
    """One agent, as ``show`` and an attach stream's opening frame report it."""

    message: str
    subagents: list[AgentRow]


class StoppedRow(TypedDict):
    """One resumable session, as ``list --stopped`` reports it.

    ``id`` is the agent id ``resume`` takes; ``session`` is the session id
    ``claude --resume`` replays. The task each ran for is the client's join.
    """

    id: str
    session: str
    age: str
    branch: str
    label: str
    cwd: str
    model: str
    mode: str
    modified_at: float


def pending_fields(pending: PendingRequest | None, message: str) -> PendingFields:
    """The detail keys a wait fills, or their empty values when none is open.

    ``message`` is the last thing the asker said: a plan review whose input
    carries no plan falls back to it. The one source of these keys, for an
    agent's detail and a subagent's alike.
    """
    plan, plan_file = plan_from_pending(pending, message)
    return {
        "request_id": pending.request_id if pending else "",
        "waiting_kind": pending.wait_kind if pending else "",
        "waiting_tool": pending.tool_name if pending else "",
        "waiting_input": dict(pending.input) if pending else {},
        "waiting_subagent": pending.subagent if pending else "",
        "questions": _question_details(pending),
        "plan": plan,
        "plan_file": plan_file,
    }


def detail_frame(
    agent_id: str, cwd: str, pending: PendingRequest | None
) -> AgentDetail:
    """The detail of an agent that holds nothing but ``pending``, if any.

    For a stand-in host that keeps no reducer state: it describes a wait in the
    shape the real host would. ``test_agent_model`` holds it equal to the
    daemon's own detail for the same agent.
    """
    return {
        "id": agent_id,
        "parent": "",
        "description": "",
        "state": pending.wait_kind if pending else IDLE,
        "session": "",
        "cwd": cwd,
        "pid": None,
        "model": "",
        "mode": "",
        "waiting_on": pending.summary if pending else "",
        "last_message": "",
        "last_message_at": "",
        "last_note": "",
        "last_note_at": "",
        "cost": "",
        "tokens": 0,
        "subagent_tokens": TokenUsage().as_row(),
        "context_tokens": 0,
        "message": "",
        **pending_fields(pending, ""),
        "subagents": [],
    }
