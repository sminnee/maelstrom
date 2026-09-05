"""The daemon: N ``claude`` children on this machine, and the socket driving them.

Each agent is a normal ``claude`` process with different I/O plumbing::

    claude -p --input-format stream-json --output-format stream-json --verbose

which is a bidirectional NDJSON pipe over plain stdio. The daemon reads each
child's event stream, derives state through
:func:`maelstrom.agent_model.apply_event`, and writes answers back on the
child's stdin. No MCP server, no WebSocket, no lockfile, no ports.

An agent's live state lives in memory, but its *spawn record* does not: every
agent has an :class:`~maelstrom.agent_model.AgentSpec` on disk saying what it
would take to start it again. ``claude`` keeps the conversation itself — a
driven agent writes a transcript, and ``--resume`` replays it — so a crashed
child, a crashed daemon or a reboot loses the live state and nothing else. See
``docs/dev/agent-daemon.md``.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from .agent_model import (
    AGENT_DETAIL,
    AGENT_EXITED,
    AUTO,
    AWAITING_PLAN_REVIEW,
    AWAITING_QUESTION,
    BACKLOG_END,
    DEFAULT_RESUME_PROMPT,
    EXITED,
    INTERRUPTED_REASON,
    INTERRUPTIBLE,
    MODES,
    SEQ_KEY,
    SPEC_EXITED,
    SPEC_RUNNING,
    SPEC_STOPPED,
    SUB_COMPLETED,
    SUB_RUNNING,
    TRUNCATED,
    AgentSpec,
    AgentState,
    SubagentState,
    TranscriptMeta,
    apply_event,
    build_agent_argv,
    build_agent_detail,
    build_agent_env,
    build_agent_row,
    build_stopped_rows,
    build_subagent_detail,
    build_subagent_rows,
    interrupt_request,
    mark_exited,
    reply_for_answer,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
    set_mode_request,
    subagent_of,
    user_message,
)
from .agent_spec_store import AgentSpecStore, JsonAgentSpecStore
from .agent_transport import STREAM_LIMIT, resolve_socket_path, resolve_spec_dir
from .session_discovery import LiveSessionSet
from .session_view import TaskLookup
from .transcript_store import ClaudeTranscriptStore, TranscriptStore
from .worktree_model import has_claude_transcript

log = logging.getLogger(__name__)

#: How far one attached client may fall behind before it starts losing events.
WATCHER_QUEUE_LIMIT = 1000

#: How long to wait for the child to answer a request the daemon made of it.
#: A child that never answers must fail the command rather than hang the
#: socket: the connection serving it answers nothing until `handle` returns.
REQUEST_TIMEOUT = 10.0

#: How long the pump waits for the child's exit code once its stream has ended.
#: Bounded because a pump that stops for any other reason leaves the child alive,
#: and an unbounded wait there never settles the agent's state.
EXIT_WAIT = 5.0


def _log_pump_failure(agent_id: str) -> Callable[["asyncio.Task[None]"], None]:
    """A done-callback that logs whatever killed ``agent_id``'s pump.

    Nothing awaits the pump task, so without this its exception is swallowed
    and a daemon-side bug leaves no evidence at all.
    """

    def done(task: "asyncio.Task[None]") -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("agent %s: its pump failed", agent_id, exc_info=error)

    return done


def _offer(queue: "asyncio.Queue[dict[str, Any]]", event: dict[str, Any]) -> None:
    """Give ``event`` to a watcher, dropping the oldest when it is full.

    A client that stops reading — a paused pager, a suspended terminal — must
    not grow the daemon's memory without limit. Dropping the oldest event keeps
    the live tail, which is what an attached viewer wants. The drop shows: the
    attach loop sees the seq jump and writes a ``mael_truncated`` marker.
    """
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        queue.put_nowait(event)


def _exit_marker(exit_code: int | None) -> dict[str, Any]:
    """The last event of an attach stream: the agent's process has gone."""
    return {"type": AGENT_EXITED, "exit_code": exit_code}


def _subagent_exit_code(sub: SubagentState) -> int:
    """The exit code a subagent's stream ends with: 0 completed, else 1."""
    return 0 if sub.status == SUB_COMPLETED else 1


def _subagent_refusal(dotted: str, agent_id: str) -> dict[str, Any]:
    """The refusal for a command that drives a subagent: only its parent takes one."""
    return {"error": f"{dotted} is a subagent of {agent_id}; drive {agent_id}"}


@dataclass(eq=False)
class Watcher:
    """One attached client and the stream it asked for. Compared by identity:
    ``_attach`` removes the one it added, never one that looks like it.

    ``subagent`` is the dotted id of the subagent it follows, or ``""`` for the
    agent's own stream. :meth:`Agent.record` offers an event only to the
    watchers of the ring it went to, so a parent's watcher never sees a
    subagent's chatter and a subagent's watcher never sees the parent's.
    """

    subagent: str
    queue: "asyncio.Queue[dict[str, Any]]"


def _truncated(dropped: int) -> dict[str, Any]:
    """The marker for events a client should have seen and cannot."""
    return {"type": TRUNCATED, "dropped": dropped}


def _unreachable(agent: "Agent") -> dict[str, Any]:
    """The refusal for a message the child's stdin would not take."""
    return {"error": f"could not reach agent {agent.state.agent_id}"}


class Agent:
    """One ``claude`` child process, its state, and the clients watching it.

    Owns the child's stdin for the life of the agent. Closing it would end the
    session after one turn, which is what a bare ``claude -p`` does; holding it
    open is what makes the process a long-lived, drivable agent.
    """

    def __init__(
        self,
        agent_id: str,
        cwd: str,
        proc: asyncio.subprocess.Process,
        on_exit: "Callable[[int | None], None] | None" = None,
    ):
        self.state = AgentState(agent_id=agent_id, cwd=cwd)
        #: Names this life of the agent. A resume makes a new ``Agent``, so a
        #: cursor from the old life is not honoured against the new one.
        self.epoch = uuid.uuid4().hex[:8]
        self.proc = proc
        # Called once, with the exit code, when the child's stream ends. The
        # daemon uses it to record the exit, so a crash observed by a daemon
        # that then dies itself is still known to the next one.
        self.on_exit = on_exit
        self.watchers: list[Watcher] = []
        #: Futures waiting on a `control_response` the daemon asked for, by
        #: request id. Only `set-mode` uses one: every other command is
        #: fire-and-forget, because the child's own stream is the evidence.
        self.waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Held, not fire-and-forget: a task only the event loop references can
        # be garbage-collected mid-stream, which would freeze the agent's state
        # while `mael agent list` still showed it running.
        self.pump_task: asyncio.Task[None] | None = None

    async def send(self, message: dict[str, Any]) -> bool:
        """Write one NDJSON message to the child's stdin.

        Returns whether the message went out. A child dying has its stdin
        closed before the stream ends, so a command can arrive after the exit
        guard in :meth:`AgentDaemon.handle` and still reach nothing — the
        caller must refuse rather than report a reply that never went.
        """
        if self.proc.stdin is None or self.proc.stdin.is_closing():
            return False
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()
        return True

    def record(self, message: dict[str, Any]) -> None:
        """Put one message into the agent's stream: reduce it, then fan it out.

        Only what the child will not repeat goes through here — a
        ``control_response`` the daemon wrote. Without it an attached client
        goes on showing a wait that has been answered.

        A ``user`` turn does not: the child replays every one on its own stdout
        marked ``isReplay``, so recording it here would put one turn on the
        stream twice. The orchestrator's normaliser mints a fresh item id
        per copy, so the user's own message would render twice.
        """
        before = self.state
        self.state = apply_event(self.state, message)
        self._settle(message)
        # The stamped copy, off the ring the event went to, so a watcher sees
        # the seq that ring holds.
        dotted = subagent_of(self.state, message)
        ring = self.state.subagents[dotted].recent if dotted else self.state.recent
        stamped = ring[-1]
        self._fan_out(dotted, stamped)
        # A subagent that just ended has watchers waiting on a queue nothing
        # more will fill, so its stream ends the way the parent's does. Only a
        # notification ends one, so only a notification is worth the scan.
        if message.get("subtype") != "task_notification":
            return
        for ended, sub in self.state.subagents.items():
            was = before.subagents.get(ended)
            if was is not None and was.status == SUB_RUNNING != sub.status:
                self._fan_out(ended, _exit_marker(_subagent_exit_code(sub)))

    def _fan_out(self, subagent: str, event: dict[str, Any]) -> None:
        """Offer ``event`` to every watcher of ``subagent``'s stream."""
        for watcher in list(self.watchers):
            if watcher.subagent == subagent:
                _offer(watcher.queue, event)

    def _settle(self, message: dict[str, Any]) -> None:
        """Hand a ``control_response`` to whoever asked the question."""
        if message.get("type") != "control_response":
            return
        response = message.get("response") or {}
        waiter = self.waiting.pop(str(response.get("request_id", "")), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(response)

    def fail_waiters(self) -> None:
        """End every wait on a child that will never answer.

        An exception rather than a cancellation: cancelling a waiter is
        indistinguishable from the daemon cancelling the task that owns it, so
        the caller could not tell a dead child from its own shutdown.
        """
        for waiter in list(self.waiting.values()):
            if not waiter.done():
                waiter.set_exception(ConnectionResetError("the agent has exited"))
        self.waiting.clear()

    async def ask(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Send a request the child must answer, and return its response.

        ``None`` when the request never went out. Raises
        :class:`asyncio.TimeoutError` when the child does not answer in
        :data:`REQUEST_TIMEOUT`.

        The response arrives through :meth:`record`, so it reaches every
        attached client on the way, exactly as an unsolicited one would.
        """
        request_id = str(request["request_id"])
        waiter: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self.waiting[request_id] = waiter
        try:
            if not await self.send(request):
                return None
            self.record(request)
            return await asyncio.wait_for(waiter, REQUEST_TIMEOUT)
        finally:
            self.waiting.pop(request_id, None)

    async def pump(self) -> None:
        """Read the child's stream to its end, then record that the child died.

        The stream ending is the only notice the daemon gets that a child has
        gone, so marking the agent ``exited`` here is what stops a crashed agent
        advertising a wait nobody can answer.

        Every way out of the loop settles the agent's state. An unexpected one
        kills the child on the way, so no orphan is left holding stdin.
        """
        assert self.proc.stdout is not None
        unexpected = True
        try:
            while True:
                try:
                    line = await self.proc.stdout.readline()
                except ValueError:
                    # A line over the limit. The reader resynchronises on the
                    # next one, so this loses the event and nothing more — but
                    # the loss must show: a dropped `control_request` would
                    # leave the agent's state claiming no wait while the child
                    # blocks on one.
                    log.warning(
                        "agent %s: skipping a line over the %d-byte limit",
                        self.state.agent_id,
                        STREAM_LIMIT,
                    )
                    self._fan_out("", _truncated(1))
                    continue
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a non-JSON line is noise, not a state change
                self.record(event)
            unexpected = False

        except asyncio.CancelledError:
            # A cancelled pump is the loop shutting down, not a crash: leave the
            # child to stop(), so its record stays running and a restart
            # resumes it.
            unexpected = False
            raise

        finally:
            # A clean end of stream means the child is already gone; any other
            # way out leaves it running, and an orphan holding stdin is
            # unreachable.
            self._mark_gone(await self._end_child(kill=unexpected))

    async def _end_child(self, kill: bool) -> int | None:
        """Wait for the child to go, and return its exit code.

        ``kill`` ends it up front rather than waiting for it to notice. Either
        way the wait is bounded, so a child that ignores both still lets its
        agent settle — ``None`` then, the exit code being unknown.
        """
        if kill and self.proc.returncode is None:
            self.proc.kill()
        try:
            return await asyncio.wait_for(self.proc.wait(), timeout=EXIT_WAIT)
        except asyncio.TimeoutError:
            return None

    def _mark_gone(self, exit_code: int | None) -> None:
        """Mark the agent gone and release everything waiting on it."""
        self.state = mark_exited(self.state, exit_code)
        self.fail_waiters()
        if self.on_exit is not None:
            self.on_exit(exit_code)
        # Tell every watcher the stream ended because the agent did, so an
        # attach can return rather than wait on a queue nothing will fill.
        # A subagent's watchers too: its events came from this process.
        for watcher in list(self.watchers):
            _offer(watcher.queue, _exit_marker(exit_code))

    async def stop(self) -> None:
        """End the agent: close its stdin, then kill it if it does not exit.

        Closing stdin is what a child is meant to notice, so it gets the same
        bounded wait first and the kill only if it overruns.
        """
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        if await self._end_child(kill=False) is None:
            await self._end_child(kill=True)


def _exited_agent(spec: AgentSpec) -> Agent:
    """An :class:`Agent` for a record whose child is already gone.

    Has no process, so nothing can be sent to it — every command against an
    exited agent is refused anyway, except ``show``, ``stop`` and ``resume``,
    which is exactly what a restored record needs to answer.
    """
    agent = Agent(spec.agent_id, spec.cwd, _DEAD_PROC)
    agent.state = mark_exited(agent.state, spec.exit_code)
    return agent


class _DeadProcess:
    """Stands in for the child of an agent that exited before this daemon ran.

    ``Agent.send`` returns silently on a closing stdin, and ``Agent.stop``
    closes it, so a closed-stdin stand-in makes both safe without a branch.
    """

    stdin = None
    stdout = None
    returncode: int | None = None

    async def wait(self) -> int | None:
        return self.returncode


#: The commands that write to a child or end it. A subagent takes none: its
#: parent does.
DRIVING_COMMANDS = (
    "say",
    "answer",
    "approve",
    "deny",
    "interrupt",
    "set-mode",
    "stop",
    "resume",
)

#: What ``list`` may be asked for. ``running`` is the default and is what the
#: orchestrator reads: live and exited-this-daemon agents, and nothing else.
SCOPE_RUNNING = "running"
SCOPE_STOPPED = "stopped"
SCOPE_ALL = "all"
SCOPES = (SCOPE_RUNNING, SCOPE_STOPPED, SCOPE_ALL)


def _open_task_index() -> TaskLookup:
    """The task index, opened once per listing.

    Imported where it is used: :func:`~maelstrom.task_cli.open_index` opens a
    SQLite database, and a daemon never asked for a stopped listing must not.
    """
    from .task_cli import open_index
    from .task_store import GitFileStore

    return open_index(GitFileStore())


#: One shared instance: it is stateless, and every restored agent wants the same.
_DEAD_PROC: Any = _DeadProcess()


class AgentDaemon:
    """The N agents on this machine, and the control socket the CLI talks to."""

    def __init__(
        self,
        socket_path: str | None = None,
        specs: AgentSpecStore | None = None,
        *,
        has_transcript: Callable[[Path, str], bool] = has_claude_transcript,
        transcripts: TranscriptStore | None = None,
        live: LiveSessionSet | None = None,
        open_task_index: Callable[[], TaskLookup] = _open_task_index,
    ):
        self.socket_path = socket_path or resolve_socket_path()
        self.specs = specs or JsonAgentSpecStore(Path(resolve_spec_dir()))
        self.has_transcript = has_transcript
        self._transcripts = transcripts
        self._live = live
        self._open_task_index = open_task_index
        self.agents: dict[str, Agent] = {}

    @property
    def transcripts(self) -> TranscriptStore:
        """Claude's session transcripts. Built on first use, not at construction.

        A daemon that is never asked for a stopped listing must not pay for the
        store, and the default one resolves ``~`` when it is built.
        """
        if self._transcripts is None:
            self._transcripts = ClaudeTranscriptStore()
        return self._transcripts

    def stopped_rows(self, cwd: str | None) -> list[dict[str, Any]]:
        """Every session that can be resumed, optionally under ``cwd``.

        The three sources are merged in the model layer: Claude's transcripts
        say which sessions exist, the spawn records say how the daemon ran the
        ones it started, and the task index names what each ran for. A session
        that is still live is subtracted, because ``resume`` refuses one.
        """
        cwds = [Path(cwd)] if cwd else None
        metas = self.transcripts.list(cwds)
        specs = _specs_by_session(self.specs.list())
        live = self._live if self._live is not None else LiveSessionSet()
        return build_stopped_rows(
            metas, specs, self._task_ids(metas), live, now=time.time()
        )

    def _task_ids(self, metas: list[TranscriptMeta]) -> dict[str, str]:
        """The task each session ran for, keyed by session id.

        The index is opened once for the whole listing, as
        ``session_view.build_session_row`` does — a per-session open would run
        ``ensure_excludes`` and build a connection hundreds of times.

        A listing is worth more than its task column, so a failure blanks the
        column rather than failing the command. Logged once, because a silent
        blank column gives a user nothing to debug.
        """
        try:
            index = self._open_task_index()
            return {
                meta.session_id: (found.id if found else "")
                for meta in metas
                for found in [index.find_by_session_id(meta.session_id)]
            }
        except Exception:  # noqa: BLE001
            log.exception("could not read the task index; task column left blank")
            return {}

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
        env: dict[str, str] | None = None,
        resume: bool = False,
    ) -> str:
        """Spawn an agent in ``cwd`` and return its id.

        ``env`` is merged over the daemon's own environment, scrubbed by
        :func:`~maelstrom.agent_model.build_agent_env`. A task launch passes
        ``MAEL_TASK_ID`` and its siblings this way; without them the agent's
        skills cannot name the task they run for.

        A session id is minted when the caller gives none, so the spawn record
        always names a session to resume — a child that dies before its
        ``system/init`` would otherwise be unresumable.

        ``resume`` continues the session ``claude`` already has on disk instead
        of claiming a new one. ``agent_id`` keeps the id the orchestrator and
        the user already know, which is what makes a resume invisible to them.
        """
        agent_id = agent_id or uuid.uuid4().hex[:8]
        session_id = session_id or str(uuid.uuid4())
        spec = AgentSpec(
            agent_id=agent_id,
            cwd=cwd,
            session_id=session_id,
            permission_mode=permission_mode,
            model=model,
            env=dict(env or {}),
            prompt=prompt,
            status=SPEC_RUNNING,
        )
        # Written before the spawn, not after: a daemon killed between the two
        # would otherwise leave a running child no record can find.
        self.specs.write(spec)
        argv = build_agent_argv(
            permission_mode=permission_mode,
            session_id=session_id,
            model=model,
            resume=resume,
        )
        # stderr joins stdout so a child that dies early — a bad --model, an
        # expired login — leaves its reason in the event buffer. pump() skips
        # the non-JSON lines, and `mael agent attach` still shows them.
        #
        # One event is one line, and an `assistant` event carries the whole
        # accumulated message — with `--forward-subagent-text` every subagent's
        # text lands on this stream too, so lines run long. Hence the same
        # STREAM_LIMIT the daemon's sockets use.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=STREAM_LIMIT,
            cwd=cwd,
            env=build_agent_env(dict(os.environ), env),
        )
        agent = Agent(agent_id, cwd, proc, on_exit=self._record_exit(agent_id))
        self.agents[agent_id] = agent
        agent.pump_task = asyncio.create_task(agent.pump())
        agent.pump_task.add_done_callback(_log_pump_failure(agent_id))
        if prompt:
            await agent.send(user_message(prompt))
        return agent_id

    def _record_exit(self, agent_id: str) -> Callable[[int | None], None]:
        """A callback that writes ``agent_id``'s exit into its spawn record.

        The record outlives this daemon, so an exit it observed is still known
        after a restart — and a record left ``running`` is genuinely an agent
        that needs bringing back, rather than one that already died.
        """

        def record(exit_code: int | None) -> None:
            spec = self.specs.read(agent_id)
            if spec is None or spec.status == SPEC_STOPPED:
                return  # a deliberate stop is not a crash
            self.specs.write(replace(spec, status=SPEC_EXITED, exit_code=exit_code))

        return record

    async def _resume(self, spec: AgentSpec, text: str | None) -> str:
        """Start ``spec``'s agent again, under its own id.

        A child that never got its opening prompt wrote no transcript, so it is
        started fresh with its original prompt. Otherwise the transcript is
        replayed and the agent gets a turn back — see
        :data:`~maelstrom.agent_model.DEFAULT_RESUME_PROMPT`.

        The transcript on disk is the one fact worth trusting here. A record
        saying no prompt went out cannot be believed: a daemon killed just after
        the send would have written exactly that, and ``--session-id`` on an id
        Claude already knows is refused — so the agent would be unrecoverable
        rather than awkward.
        """
        replay = self.has_transcript(Path(spec.cwd), spec.session_id)
        prompt = text or (DEFAULT_RESUME_PROMPT if replay else spec.prompt)
        return await self.start_agent(
            spec.cwd,
            prompt,
            permission_mode=spec.permission_mode,
            model=spec.model,
            session_id=spec.session_id,
            agent_id=spec.agent_id,
            env=spec.env or None,
            resume=replay,
        )

    async def restore(self) -> None:
        """Bring back the agents the last daemon held.

        A record still marked ``running`` is an agent whose daemon died without
        stopping it, so it is spawned again. An ``exited`` record is loaded as an
        exited agent instead — ``list``, ``show`` and ``resume`` all work on it,
        but nothing respawns it. That is also the loop guard: a resumed child
        that dies again is recorded ``exited``, so the next daemon start leaves
        it alone.
        """
        for spec in self.specs.list():
            if spec.status == SPEC_RUNNING:
                try:
                    await self._resume(spec, None)
                except Exception:  # noqa: BLE001
                    # `restore` runs before the socket binds, so an exception
                    # escaping here loses every agent rather than the one whose
                    # record is bad. A partial record reaches this by design:
                    # `spec_from_dict` defaults rather than raises, because a
                    # resume is worth attempting on one.
                    log.exception("could not resume agent %s", spec.agent_id)
                    self.specs.write(replace(spec, status=SPEC_EXITED))
            elif spec.status != SPEC_STOPPED:
                self.agents[spec.agent_id] = _exited_agent(spec)

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
                    env=payload.get("env") or None,
                    resume=bool(payload.get("resume", False)),
                )
            except OSError as exc:
                # Without this the exception escapes `handle`, the connection
                # closes unanswered, and the CLI blames the daemon for a
                # `claude` that is simply not on PATH.
                return {"error": f"could not start claude: {exc}"}
            return {"ok": True, "id": agent_id}

        if command == "list":
            # Default scope unchanged: the orchestrator never asks for another.
            scope = payload.get("scope", SCOPE_RUNNING)
            if scope not in SCOPES:
                return {"error": f"unknown scope: {scope}"}
            rows = []
            if scope in (SCOPE_RUNNING, SCOPE_ALL):
                for a in self.agents.values():
                    rows.append(build_agent_row(a.state))
                    rows += build_subagent_rows(a.state)
            if scope in (SCOPE_STOPPED, SCOPE_ALL):
                rows += self.stopped_rows(payload.get("cwd") or None)
            return {"agents": rows}

        agent, dotted = self._resolve(payload.get("id", ""))
        if agent is not None and dotted:
            # Only a read: a reply to a subagent's ask is the parent's to give.
            if command == "show":
                return {"agent": build_subagent_detail(agent.state, dotted)}
            if command in DRIVING_COMMANDS:
                return _subagent_refusal(dotted, agent.state.agent_id)
            return {"error": f"unknown command: {command}"}
        if agent is None:
            # A stopped agent is deliberately out of `self.agents`, so a resume
            # of one has only its record to go on. Every other command needs a
            # live agent and still refuses.
            spec = self.specs.read(payload.get("id", ""))
            if command == "resume" and spec is not None:
                if spec.status not in (SPEC_EXITED, SPEC_STOPPED):
                    # A record still `running` belongs to a live child — one
                    # this daemon lost, or one another daemon holds. Same
                    # refusal as the in-memory path, for the same reason.
                    return {"error": f"agent {spec.agent_id} is running"}
                try:
                    await self._resume(spec, payload.get("text") or None)
                except OSError as exc:
                    return {"error": f"could not start claude: {exc}"}
                return {"ok": True, "id": spec.agent_id}
            return {"error": f"no such agent: {payload.get('id', '')}"}

        if command == "resume":
            if agent.state.status != EXITED:
                # Two children on one session id would fight over one transcript.
                return {"error": f"agent {agent.state.agent_id} is running"}
            spec = self.specs.read(agent.state.agent_id)
            if spec is None:
                # Reachable when a `stop` races a `resume`: the agent is still
                # in memory, so "no such agent" would contradict `list`.
                return {"error": f"agent {agent.state.agent_id} has no spawn record"}
            try:
                await self._resume(spec, payload.get("text") or None)
            except OSError as exc:
                return {"error": f"could not start claude: {exc}"}
            return {"ok": True, "id": agent.state.agent_id}

        # `Agent.send` returns silently on a closed stdin, so without this every
        # command against a dead agent would report success. `show`, `stop` and
        # `resume` send nothing, and reading why an agent died is why `show`
        # exists.
        if agent.state.status == EXITED and command not in ("stop", "show"):
            return {"error": f"agent {agent.state.agent_id} has exited"}

        if command == "show":
            return {"agent": build_agent_detail(agent.state)}

        if command == "say":
            # Not recorded: the child replays a user turn itself.
            if not await agent.send(user_message(payload["text"])):
                return _unreachable(agent)
            return {"ok": True}

        if command == "stop":
            # Marked before the stop: the child ending fires `_record_exit`,
            # which would overwrite a still-`running` record as `exited`.
            spec = self.specs.read(agent.state.agent_id)
            if spec is not None:
                self.specs.write(replace(spec, status=SPEC_STOPPED))
            await agent.stop()
            # Popped all the same: the orchestrator reads an id's absence from
            # `list` as an exit. See docs/dev/agent-daemon.md.
            self.agents.pop(agent.state.agent_id, None)
            return {"ok": True}

        if command == "set-mode":
            mode = str(payload.get("mode", ""))
            # Locally first: an unknown mode is the caller's mistake, and the
            # child answers one with an error that says less than this does.
            if mode not in MODES:
                return {"error": f"unknown mode: {mode} — one of {', '.join(MODES)}"}
            error = await self._set_mode(agent, mode)
            return error if error is not None else {"ok": True, "mode": mode}

        pending = agent.state.pending

        if command == "interrupt":
            if agent.state.status not in INTERRUPTIBLE:
                return {"error": f"agent {agent.state.agent_id} is not running a turn"}
            if pending is not None:
                reply = reply_for_denial(pending, INTERRUPTED_REASON)
                if not await agent.send(reply):
                    return _unreachable(agent)
                agent.record(reply)
            request = interrupt_request(str(uuid.uuid4()))
            if not await agent.send(request):
                return _unreachable(agent)
            agent.record(request)
            return {"ok": True}

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
                if "answers" in payload:
                    try:
                        reply = reply_for_answers(pending, payload["answers"] or {})
                    except ValueError as exc:
                        return {"error": str(exc)}
                elif payload.get("choice"):
                    reply = reply_for_answer(pending, payload["choice"])
                else:
                    return {"error": "no answer given"}
            elif command == "approve":
                reply = reply_for_approval(pending)
            else:
                reply = reply_for_denial(pending, payload.get("reason", ""))
            if not await agent.send(reply):
                return _unreachable(agent)
            agent.record(reply)
            # The allow goes first: the child is waiting on that reply.
            if command == "approve" and pending.wait_kind == AWAITING_PLAN_REVIEW:
                error = await self._set_mode(agent, AUTO)
                if error is not None:
                    return {"ok": True, "warning": error["error"]}
                return {"ok": True, "mode": AUTO}
            return {"ok": True}

        return {"error": f"unknown command: {command}"}

    async def _set_mode(self, agent: Agent, mode: str) -> dict | None:
        """Move ``agent`` to ``mode``, returning an error reply or ``None``.

        Nothing may report the mode as changed until the child's reply says
        success -- the child refuses a mode it does not know.
        """
        request = set_mode_request(str(uuid.uuid4()), mode)
        try:
            response = await agent.ask(request)
        except asyncio.TimeoutError:
            return {"error": f"agent {agent.state.agent_id} did not answer"}
        except ConnectionResetError:
            # `pump` fails every waiter when the child dies.
            return {"error": f"agent {agent.state.agent_id} has exited"}
        if response is None:
            return _unreachable(agent)
        if response.get("subtype") != "success":
            detail = response.get("error") or "refused"
            return {"error": f"agent {agent.state.agent_id} refused {mode}: {detail}"}
        # The spawn record is the resume contract, so a mode change that does
        # not reach it is reverted by the next daemon start.
        spec = self.specs.read(agent.state.agent_id)
        if spec is not None:
            self.specs.write(replace(spec, permission_mode=mode))
        return None

    def _resolve(self, agent_id: str) -> tuple[Agent | None, str]:
        """The agent ``agent_id`` names, and the dotted subagent id if it is one.

        ``X`` resolves to ``(X, "")``. ``X.1`` resolves to ``(X, "X.1")`` when
        the model has opened that subagent, and to ``(None, "")`` otherwise —
        a subagent exists once its events have been seen, and a ``list`` is how
        a client learns which have.
        """
        head, dot, _ = agent_id.partition(".")
        agent = self.agents.get(head)
        if agent is None:
            return None, ""
        if not dot:
            return agent, ""
        if agent_id in agent.state.subagents:
            return agent, agent_id
        return None, ""

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
        # Before listening, so the first client to connect sees the restored
        # agents rather than an empty list it would read as "nothing running".
        await self.restore()
        server = await asyncio.start_unix_server(
            self._on_client, str(path), limit=STREAM_LIMIT
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.shutdown()
            path.unlink(missing_ok=True)

    async def shutdown(self) -> None:
        """Stop every child, leaving each record resumable.

        Orphaned children would keep running with their stdin held by a dead
        parent, so shutdown has to reach them. Stopping a child ends its stream,
        which would record an exit — and an exit recorded here is
        indistinguishable from a crash, so the next daemon start would leave the
        agent alone. Dropping the callback first is what keeps the records
        ``running``, which is what makes restarting the daemon to pick up new
        code free.
        """
        for agent in self.agents.values():
            agent.on_exit = None
        await asyncio.gather(
            *(agent.stop() for agent in self.agents.values()),
            return_exceptions=True,
        )
        self.agents.clear()

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
                    await self._attach(
                        payload.get("id", ""),
                        writer,
                        from_seq=_int(payload.get("from")),
                        epoch=str(payload.get("epoch") or ""),
                    )
                    return
                reply = await self.handle(payload)
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        finally:
            writer.close()

    async def _attach(
        self,
        agent_id: str,
        writer: asyncio.StreamWriter,
        *,
        from_seq: int = 0,
        epoch: str = "",
    ) -> None:
        """Stream one agent's events to a client until it disconnects.

        Opens with an :data:`~maelstrom.agent_model.AGENT_DETAIL` frame holding
        :func:`~maelstrom.agent_model.build_agent_detail`. The host knows what
        the agent is waiting on, so it says so, rather than leaving a client to
        infer it from the replayed events. That is what makes a wait answerable
        the moment a client attaches — including a re-attach after a resume.

        Then replays the retained events after ``from_seq``, so a client that
        attaches mid-turn sees the context it arrived into, and one that comes
        back with the cursor it left at gets only what it missed. A cursor from
        another life of the agent — ``epoch`` not this one's — means nothing
        here, so the replay starts from the beginning. When the ring has rolled
        past the cursor, a :data:`~maelstrom.agent_model.TRUNCATED` marker says
        how many events are gone. A :data:`~maelstrom.agent_model.BACKLOG_END`
        marker closes the replay with the epoch and the seq it reached, so
        ``mael agent tail`` knows where history stops and a client knows what
        to come back with.

        The live loop skips anything the replay already carried, so the two
        cannot overlap, and writes a ``TRUNCATED`` marker when a seq jumps: a
        client that fell a whole queue behind is told, not left with a gap it
        cannot see.

        The stream ends with an :data:`~maelstrom.agent_model.AGENT_EXITED`
        marker when the agent's process goes — at once, for an agent that has
        already gone — so a follower returns instead of waiting forever.

        A dotted id attaches to a subagent: the same stream, cut from that
        subagent's ring, with its own seq and its own detail frame. Its epoch
        is the parent's, because its life is the parent's. It ends with the
        exit marker when the subagent's notification comes — ``0`` for
        completed, ``1`` otherwise — or when the parent's process goes.
        """
        agent, dotted = self._resolve(agent_id)
        if agent is None:
            writer.write(
                (json.dumps({"error": f"no such agent: {agent_id}"}) + "\n").encode()
            )
            await writer.drain()
            return
        if epoch != agent.epoch:
            from_seq = 0
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=WATCHER_QUEUE_LIMIT
        )
        watcher = Watcher(dotted, queue)
        agent.watchers.append(watcher)
        try:
            detail, ring, seq, ended, exit_code = _stream_of(agent.state, dotted)
            frame = {"type": AGENT_DETAIL, "agent": detail}
            writer.write((json.dumps(frame) + "\n").encode())
            held = [e for e in ring if e[SEQ_KEY] > from_seq]
            first_held = held[0][SEQ_KEY] if held else seq + 1
            dropped = first_held - (from_seq + 1)
            if dropped > 0:
                writer.write((json.dumps(_truncated(dropped)) + "\n").encode())
            for event in held:
                writer.write((json.dumps(event) + "\n").encode())
            last = seq
            marker = {"type": BACKLOG_END, "epoch": agent.epoch, "seq": last}
            writer.write((json.dumps(marker) + "\n").encode())
            await writer.drain()
            if ended:
                writer.write((json.dumps(_exit_marker(exit_code)) + "\n").encode())
                await writer.drain()
                return
            while True:
                event = await queue.get()
                seq = event.get(SEQ_KEY)
                if isinstance(seq, int):
                    if seq <= last:
                        continue  # the replay carried it already
                    if seq > last + 1:
                        gap = _truncated(seq - last - 1)
                        writer.write((json.dumps(gap) + "\n").encode())
                    last = seq
                writer.write((json.dumps(event) + "\n").encode())
                await writer.drain()
                if event.get("type") == AGENT_EXITED:
                    return
        except (ConnectionError, BrokenPipeError):
            return
        finally:
            if watcher in agent.watchers:
                agent.watchers.remove(watcher)


def _stream_of(
    state: AgentState, dotted: str
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], int, bool, int | None]:
    """What an attach to ``dotted`` (or the agent, for ``""``) replays.

    The detail frame, the ring, the seq the ring reached, whether the stream
    has already ended, and the exit code it ended with. A subagent's stream
    has ended when its notification came or when the parent's process went,
    whichever the state shows.
    """
    if not dotted:
        ended = state.status == EXITED
        return (
            build_agent_detail(state),
            state.recent,
            state.seq,
            ended,
            state.exit_code,
        )
    sub = state.subagents[dotted]
    detail = build_subagent_detail(state, dotted)
    if sub.status != SUB_RUNNING:
        return detail, sub.recent, sub.seq, True, _subagent_exit_code(sub)
    if state.status == EXITED:
        return detail, sub.recent, sub.seq, True, state.exit_code
    return detail, sub.recent, sub.seq, False, None


def _int(value: Any) -> int:
    """A cursor from the wire, or 0 for anything that is not one."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _specs_by_session(specs: list[AgentSpec]) -> dict[str, AgentSpec]:
    """Records keyed by session id, one per session.

    Records are stored per agent id, but a session id can carry several. A task
    keeps its task session id for life, so every relaunch of one task writes
    another record against it.

    A ``stopped`` record wins, because a stop is deliberate and an ``exited``
    one is a crash. Without a rule the winner would be whatever the store
    listed last, and the two backends list in different orders — so the same
    session would resume a different run on disk than in memory.
    """
    best: dict[str, AgentSpec] = {}
    for spec in specs:
        held = best.get(spec.session_id)
        if held is None or (
            spec.status == SPEC_STOPPED and held.status != SPEC_STOPPED
        ):
            best[spec.session_id] = spec
    return best
