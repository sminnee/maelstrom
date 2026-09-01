"""Drive ``claude`` agents over a stream-json pipe, and hold them for a machine.

It runs each agent as::

    claude -p --input-format stream-json --output-format stream-json --verbose

which is a bidirectional NDJSON pipe over plain stdio. The daemon reads the
event stream, derives what the agent is doing, and writes answers back on the
child's stdin. No MCP server, no WebSocket, no lockfile, no ports.

Model and transport layer, per ``docs/dev/architecture-patterns.md``;
:mod:`maelstrom.agent_cli` is the thin CLI over it. Agent state lives in memory
only, so there is no store Protocol — an agent dies with the daemon holding it.

The event shapes here were recorded from a live agent on v2.1.252 and saved as
``tests/fixtures/agent_events/``. ``docs/dev/agent-daemon.md`` documents the
protocol; read it before changing a shape.
"""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

#: Where the daemon listens for CLI commands. One socket per machine.
DEFAULT_SOCKET_PATH = str(Path.home() / ".maelstrom" / "agent-daemon.sock")

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


def resolve_socket_path() -> str:
    """The daemon socket path from the environment, or the default."""
    return os.environ.get("MAEL_AGENT_SOCKET") or DEFAULT_SOCKET_PATH


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

#: How far one attached client may fall behind before it starts losing events.
WATCHER_QUEUE_LIMIT = 1000


def _offer(queue: "asyncio.Queue[dict[str, Any]]", event: dict[str, Any]) -> None:
    """Give ``event`` to a watcher, dropping the oldest when it is full.

    A client that stops reading — a paused pager, a suspended terminal — must
    not grow the daemon's memory without limit. Dropping the oldest event keeps
    the live tail, which is what an attached viewer wants.
    """
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        queue.put_nowait(event)


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


# --- transport: Protocol + real + fake, mirroring cmux/client.py ------------


class DaemonClient(Protocol):
    """A transport that sends one command to the daemon and returns its reply."""

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send ``payload`` and return the daemon's reply."""
        ...


@dataclass
class RecordingDaemonClient:
    """In-memory fake: records every command and returns scripted replies.

    The agent analogue of ``RecordingCmuxClient``. Replies are consumed in
    order; once they run out it returns ``{"ok": True}``, so a test only has to
    script the calls it actually asserts on.
    """

    replies: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self.replies:
            return self.replies.pop(0)
        return {"ok": True}


@dataclass
class SocketDaemonClient:
    """The real client: one NDJSON round-trip over the Unix domain socket.

    A connection failure surfaces as a reply whose ``error`` explains it, never
    an exception — same non-fatal contract as ``CmuxResult``, so the CLI can
    print a useful line instead of a traceback when the daemon is down.
    """

    socket_path: str = field(default_factory=resolve_socket_path)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(self._request(payload))

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        except (OSError, asyncio.TimeoutError) as exc:
            return {"error": f"agent daemon not reachable at {self.socket_path}: {exc}"}
        try:
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
        if not line:
            return {"error": "agent daemon closed the connection without replying"}
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            return {"error": f"agent daemon sent a malformed reply: {exc}"}


# --- the daemon itself -----------------------------------------------------


class Agent:
    """One ``claude`` child process, its state, and the clients watching it.

    Owns the child's stdin for the life of the agent. Closing it would end the
    session after one turn, which is what a bare ``claude -p`` does; holding it
    open is what makes the process a long-lived, drivable agent.
    """

    def __init__(self, agent_id: str, cwd: str, proc: asyncio.subprocess.Process):
        self.state = AgentState(agent_id=agent_id, cwd=cwd)
        self.proc = proc
        self.watchers: list[asyncio.Queue[dict[str, Any]]] = []
        # Held, not fire-and-forget: a task only the event loop references can
        # be garbage-collected mid-stream, which would freeze the agent's state
        # while `mael agent list` still showed it running.
        self.pump_task: asyncio.Task[None] | None = None

    async def send(self, message: dict[str, Any]) -> None:
        """Write one NDJSON message to the child's stdin."""
        if self.proc.stdin is None or self.proc.stdin.is_closing():
            return
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def pump(self) -> None:
        """Read the child's stream to its end, then record that the child died.

        The stream ending is the only notice the daemon gets that a child has
        gone, so marking the agent ``exited`` here is what stops a crashed agent
        advertising a wait nobody can answer.
        """
        assert self.proc.stdout is not None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a non-JSON line is noise, not a state change
                self.state = apply_event(self.state, event)
                for queue in list(self.watchers):
                    _offer(queue, event)
        finally:
            self.state = mark_exited(self.state, await self.proc.wait())

    async def stop(self) -> None:
        """End the agent: close its stdin, then kill it if it does not exit."""
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()


class AgentDaemon:
    """The N agents on this machine, and the control socket the CLI talks to."""

    def __init__(self, socket_path: str | None = None):
        self.socket_path = socket_path or resolve_socket_path()
        self.agents: dict[str, Agent] = {}

    # -- lifecycle --

    async def start_agent(
        self,
        cwd: str,
        prompt: str = "",
        *,
        permission_mode: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> str:
        """Spawn an agent in ``cwd`` and return its id."""
        agent_id = agent_id or uuid.uuid4().hex[:8]
        argv = build_agent_argv(
            permission_mode=permission_mode, session_id=session_id, model=model
        )
        # stderr joins stdout so a child that dies early — a bad --model, an
        # expired login — leaves its reason in the event buffer. pump() skips
        # the non-JSON lines, and `mael agent attach` still shows them.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        agent = Agent(agent_id, cwd, proc)
        self.agents[agent_id] = agent
        agent.pump_task = asyncio.create_task(agent.pump())
        if prompt:
            await agent.send(user_message(prompt))
        return agent_id

    # -- the command surface the CLI drives --

    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run one CLI command and return its reply."""
        command = payload.get("cmd")

        if command == "start":
            try:
                agent_id = await self.start_agent(
                    payload["cwd"],
                    payload.get("prompt", ""),
                    permission_mode=payload.get("mode"),
                    model=payload.get("model"),
                    session_id=payload.get("session"),
                )
            except OSError as exc:
                # Without this the exception escapes `handle`, the connection
                # closes unanswered, and the CLI blames the daemon for a
                # `claude` that is simply not on PATH.
                return {"error": f"could not start claude: {exc}"}
            return {"ok": True, "id": agent_id}

        if command == "list":
            return {"agents": [build_agent_row(a.state) for a in self.agents.values()]}

        agent = self.agents.get(payload.get("id", ""))
        if agent is None:
            return {"error": f"no such agent: {payload.get('id', '')}"}

        # `Agent.send` returns silently on a closed stdin, so without this every
        # command against a dead agent would report success.
        if agent.state.status == EXITED and command != "stop":
            return {"error": f"agent {agent.state.agent_id} has exited"}

        if command == "say":
            await agent.send(user_message(payload["text"]))
            return {"ok": True}

        if command == "stop":
            await agent.stop()
            self.agents.pop(agent.state.agent_id, None)
            return {"ok": True}

        pending = agent.state.pending
        if command in ("answer", "approve", "deny"):
            if pending is None:
                return {"error": f"agent {agent.state.agent_id} is not waiting"}
            if command == "answer":
                # A non-question wait has no question texts, so an answer would
                # go out as an empty map — which the agent reads as no answer at
                # all. Fail instead of resolving the wait wrongly.
                if pending.wait_kind != AWAITING_QUESTION:
                    return {
                        "error": (
                            f"agent {agent.state.agent_id} is not waiting on a "
                            f"question — use approve or deny"
                        )
                    }
                await agent.send(reply_for_answer(pending, payload["choice"]))
            elif command == "approve":
                await agent.send(reply_for_approval(pending))
            else:
                await agent.send(reply_for_denial(pending, payload.get("reason", "")))
            return {"ok": True}

        return {"error": f"unknown command: {command}"}

    # -- the socket --

    async def serve(self) -> None:
        """Listen on the control socket until cancelled, then stop every agent.

        Refuses to start when another daemon already answers on the socket.
        Unlinking blindly would leave that daemon listening on a path no client
        can reach, holding agents nothing can stop.

        Raises:
            RuntimeError: If a daemon is already serving this socket.
        """
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if await self._socket_is_live(path):
            raise RuntimeError(f"a daemon is already serving {path}")
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._on_client, str(path))
        try:
            async with server:
                await server.serve_forever()
        finally:
            # Orphaned children would keep running with their stdin held by a
            # dead parent, so shutdown has to reach them.
            await asyncio.gather(
                *(agent.stop() for agent in self.agents.values()),
                return_exceptions=True,
            )
            self.agents.clear()
            path.unlink(missing_ok=True)

    @staticmethod
    async def _socket_is_live(path: Path) -> bool:
        """Whether something already answers on ``path``.

        A stale socket file left by a killed daemon refuses the connection, so
        it reads as free — which is what lets the next daemon replace it.
        """
        if not path.exists():
            return False
        try:
            _, writer = await asyncio.open_unix_connection(str(path))
        except OSError:
            return False
        writer.close()
        return True

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """One CLI connection: NDJSON commands in, NDJSON replies out.

        ``attach`` holds the connection open and streams the agent's events;
        every other command is a single round-trip.
        """
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {}
                if payload.get("cmd") == "attach":
                    await self._attach(payload.get("id", ""), writer)
                    return
                reply = await self.handle(payload)
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()

    async def _attach(self, agent_id: str, writer: asyncio.StreamWriter) -> None:
        """Stream one agent's events to a client until it disconnects.

        Replays the buffered ``recent`` events first, so a client that attaches
        mid-turn sees the context it arrived into rather than starting blank.
        """
        agent = self.agents.get(agent_id)
        if agent is None:
            writer.write(
                (json.dumps({"error": f"no such agent: {agent_id}"}) + "\n").encode()
            )
            await writer.drain()
            return
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=WATCHER_QUEUE_LIMIT
        )
        agent.watchers.append(queue)
        try:
            for event in agent.state.recent:
                writer.write((json.dumps(event) + "\n").encode())
            await writer.drain()
            while True:
                event = await queue.get()
                writer.write((json.dumps(event) + "\n").encode())
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            return
        finally:
            if queue in agent.watchers:
                agent.watchers.remove(queue)
