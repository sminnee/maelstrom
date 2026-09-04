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
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .agent_model import (
    AGENT_DETAIL,
    AGENT_EXITED,
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
    TRUNCATED,
    AgentSpec,
    AgentState,
    apply_event,
    build_agent_argv,
    build_agent_detail,
    build_agent_env,
    build_agent_row,
    interrupt_request,
    mark_exited,
    reply_for_answer,
    reply_for_answers,
    reply_for_approval,
    reply_for_denial,
    set_mode_request,
    user_message,
)
from .agent_spec_store import AgentSpecStore, JsonAgentSpecStore
from .agent_transport import resolve_socket_path, resolve_spec_dir
from .worktree_model import has_claude_transcript

log = logging.getLogger(__name__)

#: How far one attached client may fall behind before it starts losing events.
WATCHER_QUEUE_LIMIT = 1000

#: How long to wait for the child to answer a request the daemon made of it.
#: A child that never answers must fail the command rather than hang the
#: socket: the connection serving it answers nothing until `handle` returns.
REQUEST_TIMEOUT = 10.0


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
        self.watchers: list[asyncio.Queue[dict[str, Any]]] = []
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

        A ``user`` turn does not: the child replays every one on its own
        stdout, marked ``isReplay``, so recording it here would put one turn on
        the stream twice. The orchestrator's normaliser mints a fresh item id
        per copy, so the user's own message would render twice.
        """
        self.state = apply_event(self.state, message)
        self._settle(message)
        # The stamped copy, so a watcher sees the seq the ring holds.
        stamped = self.state.recent[-1]
        for queue in list(self.watchers):
            _offer(queue, stamped)

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
                self.record(event)

        finally:
            exit_code = await self.proc.wait()
            self.state = mark_exited(self.state, exit_code)
            self.fail_waiters()
            if self.on_exit is not None:
                self.on_exit(exit_code)
            # Tell every watcher the stream ended because the agent did, so an
            # attach can return rather than wait on a queue nothing will fill.
            for queue in list(self.watchers):
                _offer(queue, _exit_marker(exit_code))

    async def stop(self) -> None:
        """End the agent: close its stdin, then kill it if it does not exit."""
        if self.proc.stdin is not None and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.proc.kill()


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
    ):
        self.socket_path = socket_path or resolve_socket_path()
        self.specs = specs or JsonAgentSpecStore(Path(resolve_spec_dir()))
        self.has_transcript = has_transcript
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
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=build_agent_env(dict(os.environ), env),
        )
        agent = Agent(agent_id, cwd, proc, on_exit=self._record_exit(agent_id))
        self.agents[agent_id] = agent
        agent.pump_task = asyncio.create_task(agent.pump())
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
            if spec is None:
                return  # `stop` deleted it; a deliberate stop is not a crash
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
            else:
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
            return {"agents": [build_agent_row(a.state) for a in self.agents.values()]}

        agent = self.agents.get(payload.get("id", ""))
        if agent is None:
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
            # Deleted first: a stop is deliberate, so no later daemon start
            # should read the record and bring the agent back.
            self.specs.delete(agent.state.agent_id)
            await agent.stop()
            self.agents.pop(agent.state.agent_id, None)
            return {"ok": True}

        if command == "set-mode":
            mode = str(payload.get("mode", ""))
            # Locally first: an unknown mode is the caller's mistake, and the
            # child answers one with an error that says less than this does.
            if mode not in MODES:
                return {"error": f"unknown mode: {mode} — one of {', '.join(MODES)}"}
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
                # The child refused, so nothing may report the mode as changed.
                detail = response.get("error") or "refused"
                return {
                    "error": f"agent {agent.state.agent_id} refused {mode}: {detail}"
                }
            # The spawn record is the resume contract, so a mode change that
            # does not reach it is reverted by the next daemon start.
            spec = self.specs.read(agent.state.agent_id)
            if spec is not None:
                self.specs.write(replace(spec, permission_mode=mode))
            return {"ok": True, "mode": mode}

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
        # Before listening, so the first client to connect sees the restored
        # agents rather than an empty list it would read as "nothing running".
        await self.restore()
        server = await asyncio.start_unix_server(self._on_client, str(path))
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
        """
        agent = self.agents.get(agent_id)
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
        agent.watchers.append(queue)
        try:
            detail = {"type": AGENT_DETAIL, "agent": build_agent_detail(agent.state)}
            writer.write((json.dumps(detail) + "\n").encode())
            held = [e for e in agent.state.recent if e[SEQ_KEY] > from_seq]
            first_held = held[0][SEQ_KEY] if held else agent.state.seq + 1
            dropped = first_held - (from_seq + 1)
            if dropped > 0:
                writer.write((json.dumps(_truncated(dropped)) + "\n").encode())
            for event in held:
                writer.write((json.dumps(event) + "\n").encode())
            last = agent.state.seq
            marker = {"type": BACKLOG_END, "epoch": agent.epoch, "seq": last}
            writer.write((json.dumps(marker) + "\n").encode())
            await writer.drain()
            if agent.state.status == EXITED:
                writer.write(
                    (json.dumps(_exit_marker(agent.state.exit_code)) + "\n").encode()
                )
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
            if queue in agent.watchers:
                agent.watchers.remove(queue)


def _int(value: Any) -> int:
    """A cursor from the wire, or 0 for anything that is not one."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
