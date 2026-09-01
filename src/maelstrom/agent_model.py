"""What a driven agent is doing, derived from its event stream.

Pure model layer, per ``docs/dev/architecture-patterns.md``: the reducer
(:func:`apply_event`), the row builder (:func:`build_agent_row`), the argv, and
the messages written back to the child. No I/O, no clock, no subprocess — so
the state machine is exercisable by replaying a recorded transcript, the way
``session_view.build_session_row`` is.

The event shapes here were recorded from a live agent on v2.1.252 and saved as
``tests/fixtures/agent_events/``. ``docs/dev/agent-daemon.md`` documents the
protocol; read it before changing a shape.
"""

from dataclasses import dataclass, field, replace
from typing import Any

#: Tools whose ``can_use_tool`` request is a question rather than a permission ask.
QUESTION_TOOL = "AskUserQuestion"
PLAN_TOOL = "ExitPlanMode"

# The states an agent can be in. Unlike the hook-derived states in
# ``session_view``, every one of these is observed from an event rather than
# inferred, so there is no staleness fudge here and an interrupt is visible.
IDLE = "idle"
PROCESSING = "processing"
AWAITING_PERMISSION = "awaiting-permission"
AWAITING_QUESTION = "awaiting-question"
AWAITING_PLAN_REVIEW = "awaiting-plan-review"
#: Terminal: the child process is gone. An exited agent answers nothing.
EXITED = "exited"


def build_agent_argv(
    permission_mode: str | None = None,
    session_id: str | None = None,
    *,
    model: str | None = None,
) -> list[str]:
    """The ``claude`` argv for a daemon-driven agent.

    Starts from the same shape as
    :func:`maelstrom.worktree_launcher.build_claude_command` and adds the four
    flags that make the process drivable:

    ``-p`` with ``--input-format``/``--output-format stream-json`` turns stdio
    into the bidirectional NDJSON pipe, and ``--verbose`` is required for the
    stream-json output format.

    ``--permission-prompt-tool stdio`` is easy to leave out and silently defeats
    the whole point: without it a headless agent has nobody to ask, so every
    "ask" decision resolves itself. See ``docs/dev/agent-daemon.md``.

    The prompt is not an argv argument — it is written to the child's stdin as a
    ``user`` message, which is also how every later message reaches it.
    """
    argv = [
        "claude",
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-prompt-tool",
        "stdio",
    ]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    if model:
        argv += ["--model", model]
    if session_id:
        argv += ["--session-id", session_id]
    return argv


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
class AgentState:
    """Everything the daemon knows about one agent, derived from its events.

    Immutable so :func:`apply_event` is a plain reducer: replaying a transcript
    yields the same state every time, which is what makes the state machine
    testable without a subprocess.
    """

    agent_id: str
    cwd: str
    session_id: str = ""
    status: str = IDLE
    pending: PendingRequest | None = None
    model: str = ""
    total_cost_usd: float = 0.0
    #: Exit code of the child, once it has gone. ``None`` while it is alive.
    exit_code: int | None = None
    #: The most recent events, for ``attach`` and ``list`` to render without
    #: replaying the transcript from disk.
    recent: tuple[dict[str, Any], ...] = field(default_factory=tuple)


#: How many events to keep per agent for ``attach`` to render on connect.
RECENT_LIMIT = 200


def apply_event(state: AgentState, event: dict[str, Any]) -> AgentState:
    """The state after one event from the agent's stream.

    Pure: no I/O, no clock. Anything the daemon does *because* of a transition
    (writing a reply, waking an attached client) is the caller's job.

    An unrecognised event only lands in ``recent`` — the stream carries plenty
    the state machine has no opinion on (``rate_limit_event``, hook chatter),
    and none of it should disturb the derived status.
    """
    recent = (state.recent + (event,))[-RECENT_LIMIT:]
    state = replace(state, recent=recent)
    kind = event.get("type")

    if kind == "system" and event.get("subtype") == "init":
        return replace(
            state,
            session_id=event.get("session_id", "") or state.session_id,
            model=event.get("model", "") or state.model,
        )

    if kind == "control_request":
        request = event.get("request") or {}
        if request.get("subtype") != "can_use_tool":
            return state
        pending = PendingRequest(
            request_id=event.get("request_id", ""),
            tool_name=request.get("tool_name", ""),
            input=request.get("input") or {},
            description=request.get("description", "") or "",
        )
        return replace(state, status=pending.wait_kind, pending=pending)

    if kind == "control_response":
        # The wait is over: either we answered, or another client did. Either
        # way the agent is running again.
        answered = (event.get("response") or {}).get("request_id")
        if state.pending is not None and answered == state.pending.request_id:
            return replace(state, status=PROCESSING, pending=None)
        return state

    if kind == "assistant":
        # A pending wait outranks assistant output. Streaming partials and
        # parallel tool blocks can arrive after a request opens, and letting one
        # set PROCESSING would render a row saying "processing" that still names
        # what it waits on.
        if state.pending is not None:
            return state
        return replace(state, status=PROCESSING)

    if kind == "result":
        return replace(
            state,
            status=IDLE,
            pending=None,
            total_cost_usd=float(event.get("total_cost_usd") or 0.0),
            session_id=event.get("session_id", "") or state.session_id,
        )

    return state


def mark_exited(state: AgentState, exit_code: int | None) -> AgentState:
    """The state of an agent whose child process has gone.

    Clears ``pending``: a request nobody can answer must not keep advertising
    itself, or ``mael agent answer`` reports success against a dead process.
    """
    return replace(state, status=EXITED, pending=None, exit_code=exit_code)


def build_agent_row(state: AgentState) -> dict[str, Any]:
    """Everything ``mael agent list`` shows about one agent, as a flat dict.

    Every key is always present; a field with nothing to report is an empty
    string. Same contract as ``session_view.build_session_row``, so ``--json``
    can emit it as-is.

    ``waiting_on`` is the point of the whole mechanism: an agent that is blocked
    says *what on*, not merely that it is busy.
    """
    status = state.status
    if status == EXITED and state.exit_code is not None:
        status = f"{EXITED}({state.exit_code})"
    return {
        "id": state.agent_id,
        "state": status,
        "session": state.session_id,
        "cwd": state.cwd,
        "model": state.model,
        "waiting_on": state.pending.summary if state.pending else "",
        "cost": f"{state.total_cost_usd:.4f}" if state.total_cost_usd else "",
    }


# --- messages written back to the child ------------------------------------


def user_message(text: str) -> dict[str, Any]:
    """A user turn, the way the stream-json input format wants it.

    This is the only way text reaches the agent — the initial prompt and every
    later follow-up are the same shape.
    """
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


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


def reply_for_answer(pending: PendingRequest, choice: str) -> dict[str, Any]:
    """Answer an ``AskUserQuestion`` with ``choice``.

    An answer is not a separate message — it rides back on the same allow, in
    ``updatedInput['answers']``, keyed by the question's own text. Allowing the
    call without that key is what "the user did not answer the questions" means
    to the agent, so a bare :func:`reply_for_approval` here would look like an
    answer and silently be none.

    A ``choice`` applies to every question asked. Multi-question prompts are
    rare; a per-question answer is a later refinement of this same field.
    """
    answers = {question: choice for question in pending.questions}
    payload = dict(pending.input)
    payload["answers"] = answers
    return _control_response(
        pending.request_id, {"behavior": "allow", "updatedInput": payload}
    )
