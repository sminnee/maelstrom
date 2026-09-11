"""What a driven agent is doing, derived from its event stream.

Pure model layer, per ``docs/dev/architecture-patterns.md``: the reducer
(:func:`apply_event`), the row builder (:func:`build_agent_row`), the argv, and
the messages written back to the child. No I/O, no clock, no subprocess — so
the state machine is exercisable by replaying a recorded transcript.

The event shapes here were recorded from live agents on v2.1.252 and v2.1.260
and saved as ``tests/fixtures/agent_events/``. ``docs/dev/agent-daemon.md`` documents the
protocol; read it before changing a shape.
"""

import base64
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent_transport import ROOT_ENV
from .util import sanitise_child_env

if TYPE_CHECKING:  # a runtime import would pull a module that shells out to `pgrep`
    from .session_discovery import LiveSessionSet

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


def build_agent_argv(
    permission_mode: str | None = None,
    session_id: str | None = None,
    *,
    model: str | None = None,
    resume: bool = False,
) -> list[str]:
    """The ``claude`` argv for a daemon-driven agent.

    Starts from the same shape as
    :func:`maelstrom.worktree_launcher.build_claude_command` and adds the six
    flags that make the process drivable:

    ``-p`` with ``--input-format``/``--output-format stream-json`` turns stdio
    into the bidirectional NDJSON pipe, and ``--verbose`` is required for the
    stream-json output format.

    ``--permission-prompt-tool stdio`` is easy to leave out and silently defeats
    the whole point: without it a headless agent has nobody to ask, so every
    "ask" decision resolves itself. See ``docs/dev/agent-daemon.md``.

    ``--forward-subagent-text`` puts a subagent's text and thinking blocks on
    the stream beside its tool calls. Without it a subagent's own stream shows
    what it did and never what it said.

    ``--replay-user-messages`` makes the child echo every ``user`` turn it reads
    from stdin back on stdout, marked ``isReplay``. The daemon records no user
    turn itself, so without the flag a ``say`` never reaches the transcript.

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
        "--forward-subagent-text",
        "--replay-user-messages",
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
#: Tells cmux's ``claude`` shim to exec the real binary untouched. Inside a
#: cmux terminal that shim shadows ``claude`` on PATH and injects a
#: ``--settings`` block of hooks calling back into the cmux IDE. A driven agent
#: is not an IDE session, so they only cost it subprocess spawns.
CMUX_HOOKS_DISABLED_ENV = "CMUX_CLAUDE_HOOKS_DISABLED"

#: What a resumed agent is told on its first turn back.
#:
#: A print-mode session sits idle until a user turn arrives, so the resume
#: needs a turn of its own.
DEFAULT_RESUME_PROMPT = "--agent session resumed, continue working--"

#: What a resumed agent that was blocked on a person is told instead.
#:
#: The ask did not survive the restart. Left silent the agent would wait for
#: ever on a reply nobody can now give, so it is told the ask is gone rather
#: than that its own turn was cut short.
LOST_ASK_RESUME_PROMPT = (
    "--user could not answer because the agent restarted, continue working--"
)


def build_agent_env(
    base: dict[str, str], extra: dict[str, str] | None, root: Path | None = None
) -> dict[str, str]:
    """The environment for a driven ``claude`` child.

    Takes ``base`` (the daemon's own environment), drops the variables no child
    should inherit and the two markers that can stop the child writing a
    transcript, asks for persistence outright, turns off cmux's hook injection,
    names ``root`` as the daemon root, then lets ``extra`` win — the
    no-allowlist contract in ``docs/dev/agent-daemon.md`` stands, so a client
    can set any of them back.

    ``root`` is the spawning daemon's own root, so a ``mael agent`` command run
    inside the session reaches the daemon that holds it. Without it the child
    inherits whatever root the daemon's shell named, which is the same root
    only by luck.
    """
    env = sanitise_child_env(base)
    for marker in _CHILD_MARKERS:
        env.pop(marker, None)
    env[FORCE_PERSISTENCE_ENV] = "1"
    if root is not None:
        env[ROOT_ENV] = str(root)
    env[CMUX_HOOKS_DISABLED_ENV] = "1"
    env.update(extra or {})
    return env


@dataclass(frozen=True)
class DaemonIdentity:
    """Which daemon is answering, and what code it is running.

    One socket can be served by a daemon spawned from any worktree, and that
    process holds the modules it imported at start — for days. So "which copy
    is serving me?" is not answerable from the outside without this.

    ``source_tree`` is the tree the serving code was imported from, which is
    the field that catches a stale daemon from another worktree.
    """

    pid: int
    version: str
    executable: str
    source_tree: str
    #: The daemon root; ``socket_path`` and ``spec_dir`` hang off it.
    root: str
    socket_path: str
    spec_dir: str
    started_at: str
    agents: int = 0

    def as_dict(self) -> dict[str, Any]:
        """The wire shape, for the ``ping`` reply."""
        return asdict(self)


def build_daemon_identity(
    *,
    root: str,
    socket_path: str,
    spec_dir: str,
    started_at: str,
    agents: int,
    module_file: str,
    pid: int,
    executable: str,
    version: str,
) -> DaemonIdentity:
    """Assemble a :class:`DaemonIdentity`.

    Pure: every varying input is passed in, so a test pins the whole record
    without patching ``os`` or ``sys``. ``source_tree`` is derived from the
    module's own path — ``src/maelstrom/agent_model.py`` sits two directories
    below the tree root.
    """
    return DaemonIdentity(
        pid=pid,
        version=version,
        executable=executable,
        source_tree=str(Path(module_file).parents[2]),
        root=root,
        socket_path=socket_path,
        spec_dir=spec_dir,
        started_at=started_at,
        agents=agents,
    )


#: A spawn record's three states. ``stopped`` is the deliberate one, and only
#: ``mael agent list --stopped`` shows it. See ``docs/dev/agent-daemon.md``.
SPEC_RUNNING = "running"
SPEC_EXITED = "exited"
SPEC_STOPPED = "stopped"


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

    ``pid`` names the child while the record is ``running``, and is ``None``
    once the child is known to be gone, so a dead record can never name a pid
    the system has since reused. It is how the next daemon tells "my
    predecessor's child is still alive" from "it died". ``started_at`` orders
    two running records on one session. ``last_status`` is what the agent was
    last doing, rewritten on every status change so it survives a daemon that
    never shuts down; it decides which turn the resume sends.
    ``stopped_at_shutdown`` says a daemon stopped this child on its way out, so
    a dead pid is that shutdown's own work rather than a crash. All are set by
    the daemon, never by a client.
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
    pid: int | None = None
    started_at: str = ""
    last_status: str = ""
    stopped_at_shutdown: bool = False


def build_start_payload(
    worktree_path: Path,
    *,
    permission_mode: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    resume: bool = False,
    model: str | None = None,
    prompt: str = "",
) -> dict[str, Any]:
    """The daemon's ``start`` command for a launch. Pure.

    The same wire shape ``orchestrator/sources.py`` sends for a task launch, so
    both callers speak one protocol. Falsy fields are dropped: a taskless
    ``mael add`` sends nothing but the cwd, so the agent draws as a freeAgent
    node.

    ``env`` carries ``MAEL_TASK_ID``, which is what keeps the ``session-end``
    hook closing the task: Claude Code fires hooks as children of the driven
    ``claude``, so they inherit it.
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
    if session_id:
        payload["session"] = session_id
    if env:
        payload["env"] = dict(env)
    return payload


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
        "pid": spec.pid,
        "started_at": spec.started_at,
        "last_status": spec.last_status,
        "stopped_at_shutdown": spec.stopped_at_shutdown,
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
        pid=data.get("pid"),
        started_at=data.get("started_at") or "",
        last_status=data.get("last_status") or "",
        stopped_at_shutdown=bool(data.get("stopped_at_shutdown")),
    )


#: Which of the two things started a session, read from a transcript's
#: ``entrypoint``. A ``mael`` one was driven by the daemon over its stdio pipe;
#: a ``cli`` one is a person at a terminal.
#:
#: Nothing reads ``kind`` today: only a session with a spawn record is listed,
#: and only the daemon writes one, so every listed session is ``mael``. The
#: field stays because the transcript has it, and :class:`TranscriptMeta` is a
#: faithful reading of the transcript head.
KIND_MAEL = "mael"
KIND_CLI = "cli"

#: The ``entrypoint`` a daemon-driven agent writes. Anything else is a person.
DRIVEN_ENTRYPOINT = "sdk-cli"


@dataclass(frozen=True)
class TranscriptMeta:
    """The head of one Claude session transcript, as a listing needs it.

    Claude writes the transcript, not maelstrom, so this is what says what a
    session was doing: enough to name it and place it. The spawn record says how
    to start it again. The conversation itself is not read.

    ``lines_read`` says how far into the file the reader went, so a test can
    hold the bounded read to its promise.
    """

    session_id: str
    cwd: Path
    branch: str = ""
    kind: str = KIND_CLI
    label: str = ""
    modified_at: float = 0.0
    size: int = 0
    lines_read: int = field(default=0, compare=False)


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


#: The states a subagent passes through. ``running`` until its notification,
#: then whatever the notification said. A parented event after the end puts it
#: back to ``running``.
SUB_RUNNING = "running"
SUB_COMPLETED = "completed"
SUB_FAILED = "failed"
SUB_STOPPED = "stopped"
SUB_STATUSES = (SUB_RUNNING, SUB_COMPLETED, SUB_FAILED, SUB_STOPPED)

#: The task kind a ``task_started`` must carry to open a subagent. A background
#: shell is a task too, keyed by its ``Bash`` call, and is not one.
AGENT_TASK_TYPE = "local_agent"


@dataclass(frozen=True)
class SubagentState:
    """One subagent of an agent: a stream of its own, keyed by a dotted id.

    Its events come stamped with ``parent_tool_use_id`` and live in a ring
    and seq of their own. See ``docs/dev/agent-daemon.md``.
    """

    tool_use_id: str
    description: str = ""
    subagent_type: str = ""
    status: str = SUB_RUNNING
    #: What the notification said, once it came.
    summary: str = ""
    recent: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    seq: int = 0
    last_message: str = ""
    #: When that last message happened. See :attr:`AgentState.last_message_at`.
    last_message_at: str = ""
    #: The asks this subagent is blocked on, by request id, oldest first. Its
    #: own, not the parent's, though the parent's pipe takes the reply.
    pending: dict[str, PendingRequest] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageWindow:
    """One rolling budget the account is spending against.

    The source quantises ``utilization`` to whole percent, so a reader that
    renders a percentage shows what it was given and never a finer figure.
    """

    #: How much of the window is spent, 0.0 to 1.0.
    utilization: float
    #: When the window rolls over, unix seconds.
    resets_at: int


@dataclass(frozen=True)
class Usage:
    """The account's budget, as the stream last reported it.

    One account spans every agent on the machine, so this is a fact about the
    host rather than about the agent whose stream carried it. A window nobody
    has reported is ``None``: nothing to say is said as nothing, never as zero.

    ``at`` is when the daemon saw the reading. A reading only arrives while an
    agent takes a turn, so one with no agent running goes stale, and a reader
    needs the time to know whether to trust the number.
    """

    five_hour: UsageWindow | None = None
    seven_day: UsageWindow | None = None
    #: When this reading was seen, ISO 8601. Empty when no clock was stamped.
    at: str = ""


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
    #: The asks this agent raised itself, by request id, oldest first. Not its
    #: subagents' — :func:`open_asks` is what spans both, and is what a reader
    #: answering or reporting a wait wants. Claude Code does not serialise the
    #: asks; see ``docs/dev/agent-daemon.md``, "A subagent's permission ask".
    own_pending: dict[str, PendingRequest] = field(default_factory=dict)
    model: str = ""
    #: The mode the child runs in, in maelstrom's words. Read off the stream,
    #: never from the spawn record.
    permission_mode: str = ""
    total_cost_usd: float = 0.0
    #: Tokens this session has consumed, summed over its turns. Kept here
    #: rather than derived from :data:`recent`, which is a capped ring: a long
    #: session drops the turns that made up most of its total. See
    #: ``docs/dev/agent-daemon.md``, "A turn".
    total_tokens: int = 0
    #: What the agent's prompt last held: a level, not a total. Read off the
    #: newest ``assistant`` event, so it advances mid-turn and each reading
    #: replaces the one before. This is the number a reader deciding to
    #: compact wants — see ``docs/dev/agent-daemon.md``, "A turn".
    context_tokens: int = 0
    #: The account's budget, as this agent's stream last reported it. Every
    #: agent on the machine reports the same account, so a reader wanting the
    #: machine's figure takes the freshest across agents rather than this one.
    usage: Usage = field(default_factory=Usage)
    #: Exit code of the child, once it has gone. ``None`` while it is alive.
    exit_code: int | None = None
    #: The child's pid while it is alive; ``None`` before the spawn and after
    #: the exit, so a row never names a pid the system may have reused.
    pid: int | None = None
    #: The most recent events, for ``attach`` and ``list`` to render without
    #: replaying the transcript from disk. Each carries the ``mael_seq`` and
    #: ``mael_ts`` it was stamped with.
    recent: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    #: How many events this life has seen: the ``mael_seq`` of the last one.
    seq: int = 0
    #: The last thing the agent said. A row shows one line of it, and a plan
    #: review with no plan in its input falls back to it. The conversation
    #: itself is Claude's session transcript on disk, not this field.
    last_message: str = ""
    #: When the agent last said that, as an ISO 8601 string. Empty until it
    #: has said anything, or when the daemon stamped no clock.
    last_message_at: str = ""
    #: The subagents this agent has spawned, by dotted id, oldest first. A
    #: nested one is ``X.1.1``. See :class:`SubagentState`.
    subagents: dict[str, SubagentState] = field(default_factory=dict)
    #: Every dotted id ever handed out, by the ``Agent`` call's tool use id.
    #: Outlives eviction, so an ordinal is never reused.
    subagent_ids: dict[str, str] = field(default_factory=dict)
    #: The same dotted ids, by the ``task_started`` id Claude Code knows the
    #: subagent as. A ``can_use_tool`` names its asker under ``agent_id``, and
    #: this is what turns that into a dotted id.
    subagent_tasks: dict[str, str] = field(default_factory=dict)


#: How many events to keep per agent for ``attach`` to render on connect.
RECENT_LIMIT = 200

#: How many subagents to keep per agent. See :func:`_make_room`.
SUBAGENT_LIMIT = 50

#: How much of the last message to keep, so a whole plan survives the fallback
#: in :func:`plan_from_pending` without the field growing without bound.
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

#: The key the daemon stamps every recorded event with: when it happened, as
#: an ISO 8601 string. In the ``mael_`` namespace for the same reason as
#: :data:`SEQ_KEY`. Empty when the daemon was given no clock.
TS_KEY = "mael_ts"

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


def _stamp(event: dict[str, Any], now: str) -> str:
    """When the event happened: its own clock, or ours if it has none.

    A ``--resume`` replays days-old ``assistant`` and ``user`` turns through
    the live pump, and those carry Claude's own ``timestamp``. Preferring it is
    what stops a resumed turn claiming it just happened. The frames with no
    timestamp (``control_request``, most ``system`` and ``result`` frames) only
    ever arrive live, so our clock is right for them.
    """
    ts = event.get("timestamp")
    return ts if isinstance(ts, str) and ts else now


def _said(event: dict[str, Any], now: str) -> tuple[str, str] | None:
    """What ``event`` said and when, or ``None`` when it said nothing.

    An agent and a subagent capture the same thing into different states, so
    the capture lives here rather than in either. Both fields move together or
    neither does: a row shows them as a pair, and a message dated by an earlier
    one is worse than no date at all.
    """
    texts = _message_texts(event)
    if not texts:
        return None
    return texts[-1][:MESSAGE_CHARS], _stamp(event, now)


def _with_last_message(
    state: AgentState, event: dict[str, Any], now: str
) -> AgentState:
    """``state`` with the last text in ``event`` as what the agent last said."""
    said = _said(event, now)
    if said is None:
        return state
    return replace(state, last_message=said[0], last_message_at=said[1])


def _one_line(text: str, limit: int = MESSAGE_SUMMARY_CHARS) -> str:
    """``text`` collapsed to one short line, for a table cell."""
    collapsed = " ".join(text.split())
    return collapsed[:limit]


def _mode_of(event: dict[str, Any]) -> str:
    """The permission mode an event announces, in maelstrom's words, else empty."""
    mode = event.get("permissionMode")
    return from_wire_mode(mode) if isinstance(mode, str) and mode else ""


def _usage_window(window: Any) -> UsageWindow | None:
    """One window off a ``rate_limit_event``, or ``None`` if it is not one.

    A window missing its utilisation is not a window: a reader cannot draw a
    budget without the figure, and a default would be a number nobody reported.
    """
    if not isinstance(window, dict) or "utilization" not in window:
        return None
    try:
        return UsageWindow(
            utilization=float(window["utilization"]),
            resets_at=int(window.get("resetsAt") or 0),
        )
    except (TypeError, ValueError):
        return None


def _usage_of(event: dict[str, Any], last: Usage, now: str) -> Usage | None:
    """The reading a ``rate_limit_event`` carries, or ``None`` if it carries none.

    ``None`` rather than an empty :class:`Usage` so the caller can leave the
    last good reading standing: a partial event is not news that the budget is
    empty.

    A window the event omits is carried over from ``last`` for the same reason,
    one window down. The two windows are reported together in every recording,
    but a five-hour figure on its own says nothing about the week — so
    replacing the whole reading would blank a good seven-day one the moment the
    source sent one window.
    """
    info = event.get("rate_limit_info")
    windows = info.get("unifiedWindows") if isinstance(info, dict) else None
    if not isinstance(windows, dict):
        return None
    five_hour = _usage_window(windows.get("five_hour"))
    seven_day = _usage_window(windows.get("seven_day"))
    if five_hour is None and seven_day is None:
        return None
    return Usage(
        five_hour=five_hour or last.five_hour,
        seven_day=seven_day or last.seven_day,
        at=now,
    )


def apply_event(
    state: AgentState, event: dict[str, Any], *, now: str = ""
) -> AgentState:
    """The state after one event from the agent's stream.

    ``now`` is when the caller saw the event. It is stamped onto the ring copy
    under :data:`TS_KEY`, unless the event carries a clock of its own — see
    :func:`_stamp`. It defaults to empty rather than to a clock read here, so
    "we do not know when" stays representable and the reducer stays pure.

    Pure: no I/O, no clock. Anything the daemon does *because* of a transition
    (writing a reply, waking an attached client) is the caller's job.

    An unrecognised event only lands in ``recent`` — the stream carries plenty
    the state machine has no opinion on (hook chatter), and none of it should
    disturb the derived status. ``rate_limit_event`` is the one event read for
    a fact about the account rather than about the agent: it moves ``usage``
    and nothing else.

    ``recent`` holds a stamped copy of the event, never the caller's dict: the
    same dict is also written to the child, which must not see the stamp.

    An event with a ``parent_tool_use_id`` belongs to a subagent, and goes to
    that subagent's ring and nowhere else: the parent's ring, seq, message,
    status and pending are what the parent did, and a subagent's chatter must
    not move any of them.
    """
    if event.get("parent_tool_use_id"):
        return _apply_subagent_event(state, event, now)

    seq = state.seq + 1
    recent = (state.recent + ({**event, SEQ_KEY: seq, TS_KEY: _stamp(event, now)},))[
        -RECENT_LIMIT:
    ]
    state = replace(state, recent=recent, seq=seq)
    kind = event.get("type")

    if kind == "rate_limit_event":
        # The account's budget, not this agent's business: it moves ``usage``
        # and never ``status``. A partial event leaves the last reading up.
        usage = _usage_of(event, state.usage, _stamp(event, now))
        return state if usage is None else replace(state, usage=usage)

    if kind == "system" and event.get("subtype") == "task_started":
        if event.get("task_type") == AGENT_TASK_TYPE and event.get("tool_use_id"):
            return _open_subagent(
                state,
                str(event["tool_use_id"]),
                description=str(event.get("description") or ""),
                subagent_type=str(event.get("subagent_type") or ""),
                task_id=str(event.get("task_id") or ""),
            )
        return state

    if kind == "system" and event.get("subtype") == "task_notification":
        return _end_subagent(state, event, now)

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
            subagent=_asker(state, str(request.get("agent_id") or "")),
        )
        if pending.subagent:
            return _with_subagent_pending(state, pending)
        return _with_pending(state, {**state.own_pending, pending.request_id: pending})

    if kind == "control_cancel_request":
        # The child withdrew the ask, so nothing can answer it any more.
        # Without this the agent goes on advertising a wait the child no
        # longer holds, and a reply would carry a dead request id.
        return _without_pending(state, str(event.get("request_id") or ""))

    if kind == "control_response":
        # The wait is over: either we answered, or another client did. The
        # agent runs again only once nothing else is outstanding.
        answered = (event.get("response") or {}).get("request_id")
        return _without_pending(state, str(answered or ""))

    if kind == "assistant":
        # Capture above the guard: the guard protects the status, not the words,
        # and a plan review needs the text the agent wrote just before it asked.
        state = _with_last_message(state, event, now)
        # Also above the guard, and for the same reason: a blocked agent still
        # holds its context, so the reading is worth keeping even when the
        # status below is not. A usage-free event leaves the level alone.
        if context := context_of(event):
            state = replace(state, context_tokens=context)
        # A pending wait outranks assistant output. Streaming partials and
        # parallel tool blocks can arrive after a request opens, and letting one
        # set PROCESSING would render a row saying "processing" that still names
        # what it waits on.
        if state.own_pending:
            return state
        return replace(state, status=PROCESSING)

    if kind == "result":
        return replace(
            state,
            status=IDLE,
            own_pending={},
            total_cost_usd=float(event.get("total_cost_usd") or 0.0),
            total_tokens=state.total_tokens + tokens_of(event),
            session_id=event.get("session_id", "") or state.session_id,
        )

    return state


#: The four counts on a ``result``'s ``usage`` that make up a turn's size.
#: See ``docs/dev/agent-daemon.md``, "A turn", for why cache counts are in.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


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
    total = 0
    for field_name in field_names:
        value = usage.get(field_name)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def subagent_of(state: AgentState, event: dict[str, Any]) -> str:
    """The dotted id of the subagent ``event`` belongs to, or ``""`` for the agent.

    Answers for the state *after* :func:`apply_event` took the event, which is
    when the caller wants to know which ring the stamped copy landed in. An
    id whose state was evicted answers ``""``: there is no ring to read.
    """
    parent = event.get("parent_tool_use_id")
    if not parent:
        return ""
    dotted = state.subagent_ids.get(str(parent), "")
    return dotted if dotted in state.subagents else ""


def _oldest(pending: dict[str, PendingRequest]) -> PendingRequest | None:
    """The ask that opened first, or ``None`` when none is open.

    A row and a detail report one wait, so they report this one. A client that
    wants them all reads ``pending`` itself.
    """
    for request in pending.values():
        return request
    return None


def open_asks(state: AgentState) -> dict[str, PendingRequest]:
    """Every ask blocked under ``state``, by request id, oldest first.

    The agent's own and its subagents'. A reply names a request, not who
    raised it, and it goes to this agent's pipe either way — so a caller
    answering a wait, or reporting one, wants both.
    """
    asks = dict(state.own_pending)
    for sub in state.subagents.values():
        asks.update(sub.pending)
    return asks


def _wait_status(pending: dict[str, PendingRequest], fallback: str) -> str:
    """The status an agent holding ``pending`` reports.

    The oldest open ask decides. An agent has one status but may hold a
    question and a permission at once, and the oldest is the one a user is
    asked to clear first.
    """
    for request in pending.values():
        return request.wait_kind
    return fallback


def _with_pending(state: AgentState, pending: dict[str, PendingRequest]) -> AgentState:
    """``state`` holding ``pending``, with the status that follows from it."""
    return replace(state, own_pending=pending, status=_wait_status(pending, PROCESSING))


def _with_subagent_pending(state: AgentState, pending: PendingRequest) -> AgentState:
    """``state`` with ``pending`` filed on the subagent that raised it."""
    sub = state.subagents[pending.subagent]
    held = {**sub.pending, pending.request_id: pending}
    return replace(
        state,
        subagents={**state.subagents, pending.subagent: replace(sub, pending=held)},
    )


def _without_pending(state: AgentState, request_id: str) -> AgentState:
    """``state`` with ``request_id`` answered, withdrawn or otherwise retired.

    Looks on the agent and on every subagent: a reply names a request, not who
    raised it. An id nothing holds changes nothing — a response whose request
    was never ours is not our wait ending.
    """
    if not request_id:
        return state
    if request_id in state.own_pending:
        rest = {k: v for k, v in state.own_pending.items() if k != request_id}
        return _with_pending(state, rest)
    for dotted, sub in state.subagents.items():
        if request_id in sub.pending:
            rest = {k: v for k, v in sub.pending.items() if k != request_id}
            return replace(
                state,
                subagents={**state.subagents, dotted: replace(sub, pending=rest)},
            )
    return state


def _asker(state: AgentState, task_id: str) -> str:
    """The dotted id of the subagent ``task_id`` names, else ``""``.

    ``task_id`` is what a ``can_use_tool`` carries under ``agent_id``. Like
    :data:`AgentState.subagent_ids`, the map outlives eviction, so an id whose
    state has gone answers ``""``: it names no stream a reader could open.
    """
    dotted = state.subagent_tasks.get(task_id, "")
    return dotted if dotted in state.subagents else ""


def _holds_call(recent: tuple[dict[str, Any], ...], tool_use_id: str) -> bool:
    """Whether ``recent`` carries the ``tool_use`` block with ``tool_use_id``."""
    for event in reversed(recent):
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []) or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("id") == tool_use_id
            ):
                return True
    return False


def _ring_holding_call(state: AgentState, tool_use_id: str) -> str:
    """The dotted id of the subagent whose ring holds ``tool_use_id``, else ``""``.

    The parent's ring is not searched: ``""`` is the answer for a call the
    parent made, and also for one that rolled out of every ring. A subagent
    opens once and asks rarely, so the scan is not on the hot path.

    Only :func:`_open_subagent` calls this, to place a new subagent at its
    level. A ``can_use_tool`` names its asker under ``agent_id`` and needs no
    scan; ``task_started`` gives a depth but not a parent, so a spawn still
    does.

    Placement is best effort, and carries the blind spot attribution no longer
    has. A parent's own recording holds none of its subagents' events, and a
    ring holds :data:`RECENT_LIMIT`, so a nested spawn whose parented events
    are missing opens flat — ``X.2`` where ``X.1.1`` was meant. The dotted id
    then understates the tree.
    """
    if not tool_use_id:
        return ""
    for dotted, sub in state.subagents.items():
        if _holds_call(sub.recent, tool_use_id):
            return dotted
    return ""


def _open_subagent(
    state: AgentState,
    tool_use_id: str,
    *,
    description: str = "",
    subagent_type: str = "",
    task_id: str = "",
) -> AgentState:
    """``state`` with a subagent open for ``tool_use_id``.

    A known id that is still open is left as it is. A known id whose state was
    evicted, or a new id, gets a fresh :class:`SubagentState` — under its old
    dotted id in the first case, and under the next ordinal at its level in the
    second. The level is the ring that holds the spawning call: a call in the
    parent's ring opens ``X.n``, one in ``X.1``'s ring opens ``X.1.n``.
    """
    dotted = state.subagent_ids.get(tool_use_id)
    if dotted is not None and dotted in state.subagents:
        return state
    if dotted is None:
        owner = _ring_holding_call(state, tool_use_id)
        prefix = f"{owner or state.agent_id}."
        used = sum(
            1
            for existing in state.subagent_ids.values()
            if existing.startswith(prefix) and "." not in existing[len(prefix) :]
        )
        dotted = f"{prefix}{used + 1}"
    subagents = _make_room(state.subagents)
    subagents[dotted] = SubagentState(
        tool_use_id=tool_use_id,
        description=description,
        subagent_type=subagent_type,
    )
    tasks = state.subagent_tasks
    if task_id:
        tasks = {**tasks, task_id: dotted}
    return replace(
        state,
        subagents=subagents,
        subagent_ids={**state.subagent_ids, tool_use_id: dotted},
        subagent_tasks=tasks,
    )


def _make_room(subagents: dict[str, SubagentState]) -> dict[str, SubagentState]:
    """A copy of ``subagents`` with room for one more under :data:`SUBAGENT_LIMIT`.

    Drops the first one that is not running, in the order they opened, with a
    reopened one last. When every one is running nothing goes: a live stream
    is worth more than the limit.
    """
    copy = dict(subagents)
    if len(copy) < SUBAGENT_LIMIT:
        return copy
    for dotted, sub in copy.items():
        if sub.status != SUB_RUNNING:
            del copy[dotted]
            break
    return copy


def _end_subagent(state: AgentState, event: dict[str, Any], now: str) -> AgentState:
    """``state`` with the subagent ``event`` notifies about ended as it says.

    A notification carries no ``task_type``, so the ``subagent_ids`` lookup is
    what keeps a background shell's notification out: its ``tool_use_id`` was
    never given a dotted id.

    The summary takes the notification's own stamp. A row shows the message and
    its time as a pair, and an ended subagent's message is its summary — so the
    stamp of the last thing it said before finishing would date the wrong text.
    """
    dotted = state.subagent_ids.get(str(event.get("tool_use_id") or ""))
    sub = state.subagents.get(dotted or "")
    if dotted is None or sub is None:
        return state
    status = str(event.get("status") or "")
    if status not in SUB_STATUSES or status == SUB_RUNNING:
        status = SUB_COMPLETED
    summary = str(event.get("summary") or "")
    ended = replace(
        sub,
        status=status,
        summary=summary,
        last_message_at=_stamp(event, now) if summary else sub.last_message_at,
        # A subagent that has gone holds nothing a reply could reach.
        pending={},
    )
    return replace(state, subagents={**state.subagents, dotted: ended})


def _apply_subagent_event(
    state: AgentState, event: dict[str, Any], now: str
) -> AgentState:
    """One event a subagent produced, into that subagent's ring.

    Opens the subagent when its ``task_started`` never came, or came before
    this daemon was watching: the frame's own ``task_description`` and
    ``subagent_type`` name it then. Puts an ended subagent back to running —
    a subagent that speaks is not finished, whatever its notification said.
    """
    tool_use_id = str(event["parent_tool_use_id"])
    state = _open_subagent(
        state,
        tool_use_id,
        description=str(event.get("task_description") or ""),
        subagent_type=str(event.get("subagent_type") or ""),
    )
    dotted = state.subagent_ids[tool_use_id]
    sub = state.subagents[dotted]
    seq = sub.seq + 1
    recent = (sub.recent + ({**event, SEQ_KEY: seq, TS_KEY: _stamp(event, now)},))[
        -RECENT_LIMIT:
    ]
    last_message = sub.last_message
    last_message_at = sub.last_message_at
    if event.get("type") == "assistant":
        said = _said(event, now)
        if said is not None:
            last_message, last_message_at = said
    updated = replace(
        sub,
        recent=recent,
        seq=seq,
        last_message=last_message,
        last_message_at=last_message_at,
        status=SUB_RUNNING,
    )
    return replace(state, subagents={**state.subagents, dotted: updated})


def mark_exited(state: AgentState, exit_code: int | None) -> AgentState:
    """The state of an agent whose child process has gone.

    Clears ``pending``: a request nobody can answer must not keep advertising
    itself, or ``mael agent answer`` reports success against a dead process.
    Clears ``pid`` too: the process is gone, and the number may be reused.
    The subagents stay as they are: their rings are still worth reading.
    """
    return replace(state, status=EXITED, own_pending={}, exit_code=exit_code, pid=None)


def freshest_usage(states: Iterable[AgentState]) -> dict[str, Any] | None:
    """The account's budget as the most recent reading reports it, or ``None``.

    Every agent on the machine spends one account, so their readings answer the
    same question and the newest is the answer. An agent that has heard nothing
    yet holds an empty :class:`Usage`, and must not outvote one that has heard
    something — hence the sort by ``at`` over readings that carry a window.
    """
    readings = [
        s.usage
        for s in states
        if s.usage.five_hour is not None or s.usage.seven_day is not None
    ]
    if not readings:
        return None
    newest = max(readings, key=lambda u: u.at)
    return {
        "five_hour": asdict(newest.five_hour) if newest.five_hour else None,
        "seven_day": asdict(newest.seven_day) if newest.seven_day else None,
        "at": newest.at,
    }


def build_agent_row(state: AgentState, spawn_session: str = "") -> dict[str, Any]:
    """Everything ``mael agent list`` shows about one agent, as a flat dict.

    Every key is always present; a field with nothing to report is an empty
    string. Same contract as ``session_cli.build_session_row``, so ``--json``
    can emit it as-is.

    ``waiting_on`` is the point of the whole mechanism: an agent that is blocked
    says *what on*, not merely that it is busy.

    ``parent`` and ``description`` are empty here: a top-level agent has no
    parent, and its prompt is not a description. :func:`build_subagent_rows`
    fills both.

    ``spawn_session`` is the id the spawn record pinned, and the row prefers
    it: the task link joins on the pinned id, and a ``/clear`` moves the one
    the agent reports. See ``docs/reference/environment.md``. It falls back to
    the reported id, because only the daemon writes a record and a row is worth
    serving without one.
    """
    # An exited agent answers nothing, whoever was waiting under it: a reply
    # needs a live pipe. So the exit outranks any ask still on file.
    if state.status == EXITED:
        status = state.status
        if state.exit_code is not None:
            status = f"{EXITED}({state.exit_code})"
        asks: dict[str, PendingRequest] = {}
    else:
        asks = open_asks(state)
        status = _wait_status(asks, state.status)
    return {
        "id": state.agent_id,
        "parent": "",
        "description": "",
        "state": status,
        "session": spawn_session or state.session_id,
        "cwd": state.cwd,
        "pid": state.pid,
        "model": state.model,
        "mode": state.permission_mode,
        "waiting_on": oldest.summary if (oldest := _oldest(asks)) else "",
        "last_message": _one_line(state.last_message),
        "last_message_at": state.last_message_at,
        "cost": f"{state.total_cost_usd:.4f}" if state.total_cost_usd else "",
        "tokens": state.total_tokens,
        "context_tokens": state.context_tokens,
    }


def _subagent_status(sub: SubagentState) -> str:
    """A subagent's status in the words a row uses for an agent.

    ``processing`` while it runs, or the wait it is blocked on. A finished one
    is ``exited``, with 0 for completed and 1 for failed or stopped, so a
    reader of ``list`` needs no second vocabulary.
    """
    if sub.status == SUB_RUNNING:
        return _wait_status(sub.pending, PROCESSING)
    return f"{EXITED}({0 if sub.status == SUB_COMPLETED else 1})"


def _subagent_message(sub: SubagentState) -> str:
    """What a subagent's row and detail show: the summary once ended, else its words."""
    if sub.status != SUB_RUNNING and sub.summary:
        return sub.summary
    return sub.last_message


def build_subagent_row(state: AgentState, dotted: str) -> dict[str, Any]:
    """One subagent of ``state``, in the shape of :func:`build_agent_row`.

    ``parent`` names the agent whose stream it came from — always the top-level
    agent, even for a nested subagent, because that is whose child process
    carries it. ``session``, ``cwd``, ``pid``, ``model`` and ``mode`` are the
    parent's: a subagent runs inside the parent's process, in its directory,
    under its mode. ``cost`` is empty and both token counts are 0 for the same
    reason: a subagent has no session of its own, so its spend and its size are
    in the parent's totals, and repeating them here would double-count. Its
    context is the parent's prompt, which the parent's own row already reports.
    ``waiting_on`` is its own — a subagent that asks is blocked itself, and a
    row saying only ``processing`` would hide that.
    """
    sub = state.subagents[dotted]
    return {
        "id": dotted,
        "parent": state.agent_id,
        "description": _one_line(sub.description),
        "state": _subagent_status(sub),
        "session": state.session_id,
        "cwd": state.cwd,
        "pid": state.pid,
        "model": state.model,
        "mode": state.permission_mode,
        "waiting_on": oldest.summary if (oldest := _oldest(sub.pending)) else "",
        "last_message": _one_line(_subagent_message(sub)),
        "last_message_at": sub.last_message_at,
        "cost": "",
        "tokens": 0,
        "context_tokens": 0,
    }


def build_subagent_rows(state: AgentState) -> list[dict[str, Any]]:
    """Every subagent of ``state`` as a row, oldest first."""
    return [build_subagent_row(state, dotted) for dotted in state.subagents]


#: Columns a stopped row carries, in the order ``mael agent list --stopped``
#: prints them.
STOPPED_COLUMNS = ["id", "age", "task", "branch", "label", "cwd"]


def age_of(seconds: float) -> str:
    """``seconds`` as the one short unit a table cell holds.

    Rounds down, so "2h" means at least two hours. Anything under a minute is
    "now" — a listing of stopped sessions never needs second precision. ``ago``
    in ``web/src/protocol/time.ts`` is this rule for the UI, which says "<1m"
    rather than "now".
    """
    if seconds < 60:
        return "now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return "now"


def build_stopped_row(
    meta: TranscriptMeta,
    spec: AgentSpec,
    task_id: str,
    *,
    now: float,
) -> dict[str, Any]:
    """One resumable session, as ``mael agent list --stopped`` shows it.

    ``id`` is the agent id, which is what ``mael agent resume`` takes.
    ``session`` holds the session id, which is what ``claude --resume`` replays.

    ``spec`` is required: only a session with a record can be resumed, so
    :func:`build_stopped_rows` never builds a row without one.

    Every key is always present, on the same contract as
    :func:`build_agent_row`, so ``--json`` can emit it as-is.
    """
    return {
        "id": spec.agent_id,
        "session": meta.session_id,
        "age": age_of(max(now - meta.modified_at, 0.0)),
        "task": task_id,
        "branch": meta.branch,
        "label": _one_line(meta.label, STOPPED_LABEL_CHARS),
        "cwd": str(meta.cwd),
        "model": spec.model or "",
        "mode": spec.permission_mode or "",
        "modified_at": meta.modified_at,
    }


#: How much of a transcript's label a table cell holds.
STOPPED_LABEL_CHARS = 80


def build_stopped_rows(
    metas: list[TranscriptMeta],
    specs: dict[str, AgentSpec],
    tasks: dict[str, str],
    live: "LiveSessionSet",
    *,
    now: float,
) -> list[dict[str, Any]]:
    """Every session that can be resumed, newest first.

    ``specs`` and ``tasks`` are keyed by session id, so a record and a
    transcript for one session merge into one row.

    A session with no record is dropped. ``_resume`` reads the model, permission
    mode and env from the record, so a transcript alone cannot be resumed —
    listing one offers a resume that can only fail.

    A session still running is subtracted, on two keys. Its session id is the
    precise one, but a ``claude`` started by hand reports none — for those the
    working directory is all there is, so a transcript is dropped when a live
    session with no id runs in the same place.
    """
    id_free_cwds = {s.cwd.resolve() for s in live.sessions if not s.session_id}
    rows = [
        build_stopped_row(meta, spec, tasks.get(meta.session_id, ""), now=now)
        for meta in metas
        for spec in [specs.get(meta.session_id)]
        if spec is not None
        and live.for_session_id(meta.session_id) is None
        and meta.cwd.resolve() not in id_free_cwds
    ]
    return sorted(rows, key=lambda row: row["modified_at"], reverse=True)


def build_agent_detail(state: AgentState, spawn_session: str = "") -> dict[str, Any]:
    """Everything ``mael agent show`` reports about one agent.

    A superset of :func:`build_agent_row`: the row is spread in, so the two
    commands can never disagree about the same agent. Every key is always
    present, on the same contract as the row. ``spawn_session`` is the row's,
    and is forwarded for the same reason — a detail that reported the moved id
    while the listing reported the pinned one would break that promise in the
    one case it was written for.

    Four keys carry what a row cannot. ``message`` is the last thing the agent
    said in full, where the row holds one line of it. ``request_id`` is what a
    reply must echo back, so a wait is answerable from the detail alone — a row
    carries no request id, so a row alone never is. ``questions`` holds each
    option and its description, which is what a user needs to answer well.
    ``plan`` holds the plan text, and ``plan_file`` the file the agent wrote it
    to.

    ``waiting_subagent`` names the subagent whose tool call the wait is for,
    else ``""``. ``subagents`` lists every subagent as a row, so ``show`` on a
    parent is where a user learns the dotted ids ``attach`` and ``tail`` take.
    """
    pending = _oldest(open_asks(state))
    plan, plan_file = plan_from_pending(pending, state.last_message)
    return {
        **build_agent_row(state, spawn_session),
        "message": state.last_message,
        "request_id": pending.request_id if pending else "",
        "waiting_kind": pending.wait_kind if pending else "",
        "waiting_tool": pending.tool_name if pending else "",
        "waiting_input": dict(pending.input) if pending else {},
        "waiting_subagent": pending.subagent if pending else "",
        "questions": _question_details(pending),
        "plan": plan,
        "plan_file": plan_file,
        "subagents": build_subagent_rows(state),
    }


def build_subagent_detail(state: AgentState, dotted: str) -> dict[str, Any]:
    """Everything ``mael agent show`` reports about one subagent.

    Its row, plus ``message``: the summary in full once it has ended, else the
    last thing it said, and whatever it waits on. ``waiting_subagent`` and
    ``subagents`` are always empty: the asker is this subagent, and a nested
    one is the parent's sibling, not this one's child.
    """
    sub = state.subagents[dotted]
    pending = _oldest(sub.pending)
    plan, plan_file = plan_from_pending(pending, sub.last_message)
    return {
        **build_subagent_row(state, dotted),
        "message": _subagent_message(sub),
        "request_id": pending.request_id if pending else "",
        "waiting_kind": pending.wait_kind if pending else "",
        "waiting_tool": pending.tool_name if pending else "",
        "waiting_input": dict(pending.input) if pending else {},
        # The asker is this subagent, so its detail names no other.
        "waiting_subagent": "",
        "questions": _question_details(pending),
        "plan": plan,
        "plan_file": plan_file,
        "subagents": [],
    }


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


def build_plan_handover_prompt(plan_file: str) -> str:
    """What an agent is told after its plan is approved and its context cleared.

    The file is the handover rather than the text: it is the canonical copy, it
    has no size limit where a retained message is capped at
    :data:`MESSAGE_CHARS`, and it outlives the message that announced it. A plan
    review that names no file never gets here — it is denied instead, because a
    plan nobody could write down is a failed submission.

    The framing carries as much as the path. An agent that does not know its
    context was cleared reads the plan as a reminder of a discussion it believes
    it still holds, and skips the reading the plan assumes it already did.
    """
    return (
        f"Your plan was reviewed and approved. Read {plan_file} and carry it "
        "out.\n\n"
        "This is a fresh conversation: the planning discussion is no longer in "
        "your context, so the plan file is the whole brief. Re-read any file it "
        "names before you change it."
    )


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


#: The tags Claude Code's own ``!`` writes a shell command and its output under.
#: Reusing them means an agent meets a maelstrom shell command in exactly the
#: shape it meets one typed into a terminal. The CLI also declares a
#: ``bash-exit-code`` tag and never emits one, so neither does this.
SHELL_INPUT_TAG = "bash-input"
SHELL_STDOUT_TAG = "bash-stdout"
SHELL_STDERR_TAG = "bash-stderr"


def _untagged(text: str) -> str:
    """``text`` with the shell tag literals defused.

    The turns are tags around raw command output, so output holding a closing
    tag would end its own field early: the reader would split at the wrong
    point and file part of stdout under stderr. Running ``cat`` on a file that
    documents this format does exactly that. A zero-width space after each
    ``<`` keeps the text readable and stops it closing a tag.
    """
    for tag in (SHELL_INPUT_TAG, SHELL_STDOUT_TAG, SHELL_STDERR_TAG):
        text = text.replace(f"</{tag}>", f"<\u200b/{tag}>")
        text = text.replace(f"<{tag}>", f"<\u200b{tag}>")
    return text


def shell_input_message(command: str) -> dict[str, Any]:
    """The user turn naming a shell command the host is about to run.

    The content is a plain string rather than a block list, which is the shape
    the harness itself writes. ``normalise._blocks`` reads both.
    """
    return _string_turn(f"<{SHELL_INPUT_TAG}>{_untagged(command)}</{SHELL_INPUT_TAG}>")


def shell_output_message(stdout: str, stderr: str) -> dict[str, Any]:
    """The user turn carrying what a shell command wrote.

    Both tags are always present, empty when unused, the way the harness writes
    them. The streams stay apart because a failure reaches the agent as stderr
    text — there is no exit code on this format.
    """
    return _string_turn(
        f"<{SHELL_STDOUT_TAG}>{_untagged(stdout)}</{SHELL_STDOUT_TAG}>"
        f"<{SHELL_STDERR_TAG}>{_untagged(stderr)}</{SHELL_STDERR_TAG}>"
    )


def _string_turn(text: str) -> dict[str, Any]:
    """A user turn whose content is a plain string, as the harness writes one."""
    return {"type": "user", "message": {"role": "user", "content": text}}


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


#: What an interrupted tool call is told, and what the turn's error says.
INTERRUPTED_REASON = "Interrupted by user"

#: The slash command that starts a new conversation. It reaches Claude Code as
#: the text of an ordinary user turn, which is the same path a prompt takes.
CLEAR_COMMAND = "/clear"

#: Why a plan review with no plan file is denied — see ``docs/dev/agent-daemon.md``.
#: It reaches the agent verbatim as the tool result, so it says what to do
#: rather than only what went wrong.
NO_PLAN_FILE_REASON = (
    "This plan has no plan file, so it cannot be carried over to a fresh "
    "context. Write the plan to a file, then call ExitPlanMode again."
)

#: What a subagent's orphaned ask is denied with. Its subagent ended while it
#: was open, so nothing can approve it and the caller has to be told.
ENDED_REASON = "The subagent that asked has ended"


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
