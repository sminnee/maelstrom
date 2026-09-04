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

#: States in which the agent still owes a reply, so a turn exists to interrupt.
INTERRUPTIBLE = (
    PROCESSING,
    AWAITING_PERMISSION,
    AWAITING_QUESTION,
    AWAITING_PLAN_REVIEW,
)

#: The permission modes an agent can run in, in the order a cycle visits them.
MODES = ("plan", "auto", "normal")

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


def build_agent_argv(
    permission_mode: str | None = None,
    session_id: str | None = None,
    *,
    model: str | None = None,
    resume: bool = False,
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

    ``resume`` swaps ``--session-id`` for ``--resume``, which continues the
    session ``claude`` already has on disk instead of claiming a new id. The
    same switch ``worktree_launcher.build_claude_command`` makes for a pane.

    ``permission_mode`` is maelstrom's word. ``normal`` is the absence of the
    flag rather than a value it takes, so it emits nothing: ``claude`` refuses
    ``--permission-mode normal``.
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
    if permission_mode and permission_mode != NORMAL:
        argv += ["--permission-mode", permission_mode]
    if model:
        argv += ["--model", model]
    if session_id:
        argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    return argv


#: Markers ``claude`` sets in a session's own environment. Inherited by a child
#: they can suppress the transcript a resume depends on, so a driven agent is
#: spawned without them. The VS Code extension scrubs the same two.
_CHILD_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION")
#: Asks for the transcript even where an inherited marker would have skipped it.
FORCE_PERSISTENCE_ENV = "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"

#: What a resumed agent is told on its first turn back.
#:
#: A print-mode session sits idle until a user turn arrives, and a permission
#: the agent was blocked on does not survive the restart. So the resume needs a
#: turn of its own, and that turn has to say why it came.
DEFAULT_RESUME_PROMPT = (
    "Your previous process ended unexpectedly. Your last turn may be "
    "incomplete. Check the working tree and continue from where you left off."
)


def build_agent_env(
    base: dict[str, str], extra: dict[str, str] | None
) -> dict[str, str]:
    """The environment for a driven ``claude`` child.

    Takes ``base`` (the daemon's own environment), drops the two markers that
    can stop the child writing a transcript, asks for persistence outright, then
    lets ``extra`` win — the no-allowlist contract in
    ``docs/dev/agent-daemon.md`` stands.
    """
    env = dict(base)
    for marker in _CHILD_MARKERS:
        env.pop(marker, None)
    env[FORCE_PERSISTENCE_ENV] = "1"
    env.update(extra or {})
    return env


#: A spawn record's two states. ``stop`` deletes the record instead.
SPEC_RUNNING = "running"
SPEC_EXITED = "exited"


@dataclass(frozen=True)
class AgentSpec:
    """What it takes to spawn one agent again, after the daemon has gone.

    Where to run, which session to continue, and the argv and environment to
    rebuild. See ``docs/dev/agent-daemon.md``.

    ``session_id`` is always set, because the daemon mints one when the caller
    gives none. A child that died before its ``system/init`` is resumable all
    the same.

    ``prompt`` is kept so a child that died before its first turn can be started
    again with the prompt it never got. Whether a resume replays instead is
    decided by the transcript on disk, not by this record.

    ``env`` is the caller's own extra vars only, never the daemon's environment
    — that is re-read at spawn time.
    """

    agent_id: str
    cwd: str
    session_id: str
    permission_mode: str | None = None
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    status: str = SPEC_RUNNING
    exit_code: int | None = None


def spec_to_dict(spec: AgentSpec) -> dict[str, Any]:
    """``spec`` as the plain JSON the store writes."""
    return {
        "agent_id": spec.agent_id,
        "cwd": spec.cwd,
        "session_id": spec.session_id,
        "permission_mode": spec.permission_mode,
        "model": spec.model,
        "env": dict(spec.env),
        "prompt": spec.prompt,
        "status": spec.status,
        "exit_code": spec.exit_code,
    }


def spec_from_dict(data: dict[str, Any]) -> AgentSpec:
    """An :class:`AgentSpec` from stored JSON, defaulting what it lacks.

    A record written by an older daemon is missing fields rather than wrong, so
    every optional one falls back rather than raising — a resume is worth
    attempting on a partial record.
    """
    return AgentSpec(
        agent_id=data["agent_id"],
        cwd=data["cwd"],
        session_id=data["session_id"],
        permission_mode=data.get("permission_mode"),
        model=data.get("model"),
        env=dict(data.get("env") or {}),
        prompt=data.get("prompt", ""),
        status=data.get("status", SPEC_RUNNING),
        exit_code=data.get("exit_code"),
    )


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
    #: The mode the child runs in, in maelstrom's words. Read off the stream,
    #: never from the spawn record.
    permission_mode: str = ""
    total_cost_usd: float = 0.0
    #: Exit code of the child, once it has gone. ``None`` while it is alive.
    exit_code: int | None = None
    #: The most recent events, for ``attach`` and ``list`` to render without
    #: replaying the transcript from disk. Each carries the ``mael_seq`` it
    #: was stamped with.
    recent: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: How many events this life has seen: the ``mael_seq`` of the last one.
    seq: int = 0
    #: The last thing the agent said. A row shows one line of it, and a plan
    #: review with no plan in its input falls back to it. The conversation
    #: itself is Claude's session transcript on disk, not this field.
    last_message: str = ""


#: How many events to keep per agent for ``attach`` to render on connect.
RECENT_LIMIT = 200

#: How much of the last message to keep, so a whole plan survives the fallback
#: in :func:`_plan_details` without the field growing without bound.
MESSAGE_CHARS = 8000
#: How much of the last message a table cell holds.
MESSAGE_SUMMARY_CHARS = 60

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

#: Event type the daemon writes to every attached client once the agent's
#: process has gone, carrying ``exit_code``. The last event of an attach
#: stream, so a client knows the agent ended it, not a dropped connection.
AGENT_EXITED = "mael_agent_exited"

#: Event type of an attach stream's opening frame, carrying
#: :func:`build_agent_detail` under ``agent``.
AGENT_DETAIL = "mael_agent_detail"


def _message_texts(event: dict[str, Any]) -> list[str]:
    """The text the agent chose to say in one ``assistant`` event.

    ``text`` blocks only. A ``thinking`` block is reasoning the agent did not
    choose to say, and a ``tool_use`` block is an action rather than words —
    both are already visible in ``waiting_on`` when they matter.
    """
    blocks = event.get("message", {}).get("content", []) or []
    return [
        block["text"]
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]


def _with_last_message(state: AgentState, event: dict[str, Any]) -> AgentState:
    """``state`` with the last text in ``event`` as what the agent last said."""
    texts = _message_texts(event)
    if not texts:
        return state
    return replace(state, last_message=texts[-1][:MESSAGE_CHARS])


def _one_line(text: str, limit: int = MESSAGE_SUMMARY_CHARS) -> str:
    """``text`` collapsed to one short line, for a table cell."""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _mode_of(event: dict[str, Any]) -> str:
    """The permission mode an event announces, in maelstrom's words, else empty."""
    mode = event.get("permissionMode")
    return from_wire_mode(mode) if isinstance(mode, str) and mode else ""


def apply_event(state: AgentState, event: dict[str, Any]) -> AgentState:
    """The state after one event from the agent's stream.

    Pure: no I/O, no clock. Anything the daemon does *because* of a transition
    (writing a reply, waking an attached client) is the caller's job.

    An unrecognised event only lands in ``recent`` — the stream carries plenty
    the state machine has no opinion on (``rate_limit_event``, hook chatter),
    and none of it should disturb the derived status.

    ``recent`` holds a stamped copy of the event, never the caller's dict: the
    same dict is also written to the child, which must not see the stamp.
    """
    seq = state.seq + 1
    recent = (state.recent + ({**event, SEQ_KEY: seq},))[-RECENT_LIMIT:]
    state = replace(state, recent=recent, seq=seq)
    kind = event.get("type")

    if kind == "system" and event.get("subtype") == "init":
        return replace(
            state,
            session_id=event.get("session_id", "") or state.session_id,
            model=event.get("model", "") or state.model,
            permission_mode=_mode_of(event) or state.permission_mode,
        )

    if kind == "system" and event.get("subtype") == "status":
        # The child announces its own mode changes here too — see
        # docs/dev/agent-daemon.md, "Changing the permission mode".
        return replace(state, permission_mode=_mode_of(event) or state.permission_mode)

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

    if kind == "control_cancel_request":
        # The child withdrew the ask, so nothing can answer it any more.
        # Without this the agent goes on advertising a wait the child no
        # longer holds, and a reply would carry a dead request id.
        if (
            state.pending is not None
            and event.get("request_id") == state.pending.request_id
        ):
            return replace(state, status=PROCESSING, pending=None)
        return state

    if kind == "control_response":
        # The wait is over: either we answered, or another client did. Either
        # way the agent is running again.
        answered = (event.get("response") or {}).get("request_id")
        if state.pending is not None and answered == state.pending.request_id:
            return replace(state, status=PROCESSING, pending=None)
        return state

    if kind == "assistant":
        # Capture above the guard: the guard protects the status, not the words,
        # and a plan review needs the text the agent wrote just before it asked.
        state = _with_last_message(state, event)
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
        "mode": state.permission_mode,
        "waiting_on": state.pending.summary if state.pending else "",
        "last_message": _one_line(state.last_message),
        "cost": f"{state.total_cost_usd:.4f}" if state.total_cost_usd else "",
    }


def build_agent_detail(state: AgentState) -> dict[str, Any]:
    """Everything ``mael agent show`` reports about one agent.

    A superset of :func:`build_agent_row`: the row is spread in, so the two
    commands can never disagree about the same agent. Every key is always
    present, on the same contract as the row.

    Four keys carry what a row cannot. ``message`` is the last thing the agent
    said in full, where the row holds one line of it. ``request_id`` is what a
    reply must echo back, so a wait is answerable from the detail alone — a row
    carries no request id, so a row alone never is. ``questions`` holds each
    option and its description, which is what a user needs to answer well.
    ``plan`` holds the plan text, and ``plan_file`` the file the agent wrote it
    to.
    """
    pending = state.pending
    plan, plan_file = _plan_details(pending, state.last_message)
    return {
        **build_agent_row(state),
        "message": state.last_message,
        "request_id": pending.request_id if pending else "",
        "waiting_kind": pending.wait_kind if pending else "",
        "waiting_tool": pending.tool_name if pending else "",
        "waiting_input": dict(pending.input) if pending else {},
        "questions": _question_details(pending),
        "plan": plan,
        "plan_file": plan_file,
    }


def _plan_details(pending: PendingRequest | None, last_message: str) -> tuple[str, str]:
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


#: What an interrupted tool call is told, and what the turn's error says.
INTERRUPTED_REASON = "Interrupted by user"


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
