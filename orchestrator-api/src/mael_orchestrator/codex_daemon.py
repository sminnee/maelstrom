"""Adapt the Codex app-server to the orchestrator daemon client boundary.

See ``docs/dev/codex-app-server-events.md`` for the contract: every command,
every method, and what each becomes.
"""

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from mael_agent.agent_transport import AsyncDaemonClient
from mael_agent.agent_wire import (
    AGENT_DETAIL,
    AGENT_EXITED,
    BACKLOG_END,
    MODES,
    RECENT_LIMIT,
    SEQ_KEY,
    TRUNCATED,
    TS_KEY,
)
from mael_agent.codex_wire import (
    APPROVAL_TOOLS,
    REASON_KEY,
    Approval,
    approval_of,
)
from mael_agent.harness_model import (
    HARNESS_CODEX,
    ModelReference,
    codex_thread_start_options,
    codex_turn_policy,
    resolve_model_reference,
)
from mael_common.util import now_iso
from mael_domain.agent_store import AGENT_ENDED

log = logging.getLogger(__name__)

#: Notifications relayed live and kept out of the ring.
_LIVE_ONLY = frozenset(
    {
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
    }
)

#: The JSON-RPC error code for a server request the adapter cannot answer.
_UNSUPPORTED = -32601

#: Why a refused command has no Codex equivalent.
_NO_EQUIVALENT = {
    "answer": "Codex asks no questions through Maelstrom.",
    "recover": "Codex has no recover command.",
    "show": "Codex threads live in the orchestrator; use its API.",
}


class CodexRpcClient(Protocol):
    """The JSON-RPC operations the Codex daemon adapter needs."""

    async def request(self, method: str, params: dict[str, Any]) -> Any: ...

    def incoming(self) -> AsyncIterator[dict[str, Any]]: ...

    async def respond(self, request_id: str | int, result: Any) -> None: ...

    async def reject(self, request_id: str | int, code: int, message: str) -> None: ...


@dataclass
class _Thread:
    """One Codex thread, as the agent the orchestrator sees."""

    row: dict[str, Any]
    #: The events an attach replays, each stamped with its seq and time.
    ring: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=RECENT_LIMIT)
    )
    seq: int = 0
    queues: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    #: Open approval requests, by request id, oldest first.
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class CodexDaemonClient(AsyncDaemonClient):
    """Present Codex threads as agents to the orchestrator server."""

    bridge: CodexRpcClient
    #: What stamps each event. Injected so a test can pin it.
    clock: Callable[[], str] = now_iso
    _threads: dict[str, _Thread] = field(default_factory=dict, init=False)
    _reader: asyncio.Task[None] | None = field(default=None, init=False)
    #: Whether the connection dropped since the threads were subscribed.
    _stale: bool = field(default=False, init=False)
    #: One epoch per adapter life: a cursor from another life replays all.
    _epoch: str = field(default_factory=lambda: uuid.uuid4().hex[:8], init=False)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one orchestrator command to a Codex thread.

        A bridge failure is the reply's error, never an exception: the server
        reads ``error`` and reports it as it does a Claude daemon refusal.
        """
        command = str(payload.get("cmd", ""))
        if command == "list":
            # The pid is the proxy's: every thread runs behind that one process.
            pid = getattr(self.bridge, "pid", None)
            return {
                "agents": [{**t.row, "pid": pid} for t in self._threads.values()],
                "usage": None,
            }
        if command in _NO_EQUIVALENT:
            return _error(_NO_EQUIVALENT[command])
        handler = {
            "start": self._start,
            "resume": self._resume,
            "say": self._say,
            "run": self._run,
            "interrupt": self._interrupt,
            "stop": self._stop,
            "set-mode": self._set_mode,
            "approve": self._approve,
            "deny": self._deny,
        }.get(command)
        if handler is None:
            return _error(f"Codex does not support {command!r}.")
        try:
            await self._resubscribe()
            return await handler(payload)
        except RuntimeError as error:
            return _error(str(error))

    def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """Attach one Session view to its Codex thread."""
        return self._attach(agent_id, from_seq if epoch == self._epoch else 0)

    async def restore(self, agents: list[dict[str, Any]]) -> None:
        """Resume the live Codex threads after an orchestrator restart."""
        for agent in agents:
            if agent.get("harness") != HARNESS_CODEX:
                continue
            if agent.get("status") == AGENT_ENDED:
                continue
            thread_id = str(agent.get("id", ""))
            if not thread_id or thread_id in self._threads:
                continue
            try:
                resumed = await self.bridge.request(
                    "thread/resume", {"threadId": thread_id}
                )
                thread = _mapping(resumed, "thread")
            except RuntimeError as error:
                log.warning("Codex thread %s not restored: %s", thread_id, error)
                continue
            self._adopt(
                thread_id,
                thread,
                session=str(agent.get("task_session_id") or ""),
                cwd=str(agent.get("cwd") or ""),
                model=str(agent.get("model") or ""),
                mode=str(agent.get("mode") or "normal"),
            )

    # -- commands ----------------------------------------------------------

    async def _start(self, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "normal")
        reference = _codex_reference(str(payload.get("model") or "codex:luna"), mode)
        params: dict[str, Any] = {
            "cwd": str(payload["cwd"]),
            "model": reference.cli_args[1],
            **codex_thread_start_options(mode),
            **_thread_settings(payload),
        }
        thread = _mapping(await self.bridge.request("thread/start", params), "thread")
        thread_id = str(thread["id"])
        self._threads[thread_id] = _Thread(
            _row(
                thread_id,
                session=str(payload.get("session") or ""),
                cwd=str(thread.get("cwd") or payload["cwd"]),
                model=str(payload.get("model") or ""),
                mode=mode,
            )
        )
        self._ensure_reader()
        if prompt := str(payload.get("prompt") or ""):
            try:
                await self._turn(thread_id, prompt, [])
            except RuntimeError:
                # No record is written for a failed start, so no thread stays.
                self._threads.pop(thread_id)
                await self.bridge.request("thread/unsubscribe", {"threadId": thread_id})
                raise
        return {"ok": True, "id": thread_id}

    async def _resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bring a stopped thread back under its own id."""
        thread_id = str(payload.get("id", ""))
        if thread_id not in self._threads:
            params: dict[str, Any] = {
                "threadId": thread_id,
                **_thread_settings(payload),
            }
            if payload.get("cwd"):
                # A relaunch may open another worktree than the thread ran in.
                params["cwd"] = str(payload["cwd"])
            resumed = await self.bridge.request("thread/resume", params)
            thread = _mapping(resumed, "thread")
            self._adopt(
                thread_id,
                thread,
                session=str(payload.get("session") or ""),
                cwd=str(thread.get("cwd") or ""),
                model=str(thread.get("model") or ""),
                mode=str(payload.get("mode") or "normal"),
            )
        if text := str(payload.get("text") or ""):
            await self._turn(thread_id, text, [])
        return {"ok": True, "id": thread_id}

    async def _say(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        if thread_id not in self._threads:
            return _no_agent(thread_id)
        text = str(payload.get("text", ""))
        if text.split(" ", 1)[0] == "/compact":
            # Codex compacts on its own method, and takes no instructions.
            await self.bridge.request("thread/compact/start", {"threadId": thread_id})
            return {"ok": True}
        await self._turn(thread_id, text, _images(payload.get("attachments")))
        return {"ok": True}

    async def _run(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        if thread_id not in self._threads:
            return _no_agent(thread_id)
        command = str(payload.get("command", "")).strip()
        if not command:
            return _error("no command given")
        await self.bridge.request(
            "thread/shellCommand", {"threadId": thread_id, "command": command}
        )
        return {"ok": True}

    async def _interrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        thread = self._threads.get(thread_id)
        if thread is None:
            return _no_agent(thread_id)
        turn_id = str(thread.row.get("turn_id") or "")
        if not turn_id:
            return _error(f"agent {thread_id} is not running a turn")
        # An unanswered approval leaves the turn blocked for ever.
        for request_id in list(thread.pending):
            await self._decide(thread_id, request_id, False, "Interrupted by user")
        await self.bridge.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
        )
        return {"ok": True}

    async def _stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        thread = self._threads.pop(thread_id, None)
        if thread is None:
            return _no_agent(thread_id)
        await self.bridge.request("thread/unsubscribe", {"threadId": thread_id})
        for queue in thread.queues:
            queue.put_nowait({"type": AGENT_EXITED, "exit_code": 0})
        return {"ok": True}

    async def _set_mode(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Store the mode; the next turn starts under its policy."""
        thread_id = str(payload.get("id", ""))
        thread = self._threads.get(thread_id)
        if thread is None:
            return _no_agent(thread_id)
        mode = str(payload.get("mode", ""))
        if mode not in MODES:
            return _error(f"unknown mode: {mode} — one of {', '.join(MODES)}")
        thread.row["mode"] = mode
        # In Codex's own words, so the normaliser moves the agent's mode.
        self._record(
            thread_id,
            {
                "type": "codex_raw",
                "method": "thread/settings/updated",
                "params": {
                    "threadId": thread_id,
                    "threadSettings": codex_turn_policy(mode),
                },
            },
        )
        return {"ok": True, "mode": mode}

    async def _approve(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._answer(payload, True)

    async def _deny(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._answer(payload, False)

    async def _answer(self, payload: dict[str, Any], allowed: bool) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        thread = self._threads.get(thread_id)
        if thread is None:
            return _no_agent(thread_id)
        request_id = str(payload.get("request") or "")
        if not request_id:
            # As the Claude daemon: one open request needs no name.
            if len(thread.pending) > 1:
                return _error(
                    f"agent {thread_id} is waiting on {len(thread.pending)} "
                    "requests; name one with `request`"
                )
            request_id = next(iter(thread.pending), "")
        if request_id not in thread.pending:
            return _error(f"agent {thread_id} is not waiting on request {request_id}")
        remember = allowed and payload.get("remember") is True
        if remember and thread.pending[request_id]["approval"].remember is None:
            return _error(f"request {request_id} offers no remember")
        reason = str(payload.get("reason") or "Denied by user")
        await self._decide(thread_id, request_id, allowed, reason, remember=remember)
        return {"ok": True}

    async def _decide(
        self,
        thread_id: str,
        request_id: str,
        allowed: bool,
        reason: str,
        *,
        remember: bool = False,
    ) -> None:
        """Answer one approval, and record the reply beside the request."""
        thread = self._threads[thread_id]
        pending = thread.pending[request_id]
        approval: Approval = pending["approval"]
        if not allowed:
            decision: Any = approval.decline
        elif remember:
            decision = approval.remember
        else:
            decision = "accept"
        result = {"decision": decision}
        await self.bridge.respond(pending["rpc_id"], result)
        thread.pending.pop(request_id)
        row = thread.row
        still = next(iter(thread.pending.values()), None)
        if still is not None:
            row["state"] = "awaiting-permission"
        else:
            row["state"] = "processing" if row.get("turn_id") else "idle"
        row["waiting_on"] = still["approval"].description if still else ""
        reply: dict[str, Any] = {
            "type": "codex_raw",
            "method": pending["method"],
            "id": pending["rpc_id"],
            "result": result,
        }
        if not allowed:
            reply[REASON_KEY] = reason
        self._record(thread_id, reply)

    async def _turn(self, thread_id: str, text: str, images: list[str]) -> None:
        thread = self._threads[thread_id]
        inputs: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        inputs += [{"type": "localImage", "path": path} for path in images]
        started = await self.bridge.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": inputs,
                **codex_turn_policy(str(thread.row.get("mode") or "normal")),
            },
        )
        turn = _mapping(started, "turn")
        thread.row["turn_id"] = str(turn.get("id", ""))
        thread.row["state"] = "processing"
        thread.row["last_message_at"] = self.clock()

    # -- the event stream --------------------------------------------------

    async def _attach(
        self, agent_id: str, from_seq: int
    ) -> AsyncIterator[dict[str, Any]]:
        thread = self._threads.get(agent_id)
        if thread is None:
            yield {"error": f"no such agent: {agent_id}"}
            return
        await self._resubscribe()
        self._ensure_reader()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        thread.queues.append(queue)
        try:
            pending = next(iter(thread.pending.values()), None)
            yield {"type": AGENT_DETAIL, "agent": _detail_fields(pending)}
            held = [e for e in thread.ring if e[SEQ_KEY] > from_seq]
            first = held[0][SEQ_KEY] if held else thread.seq + 1
            if (dropped := first - (from_seq + 1)) > 0:
                yield {"type": TRUNCATED, "dropped": dropped}
            for event in held:
                yield event
            last = thread.seq
            yield {"type": BACKLOG_END, "epoch": self._epoch, "seq": last}
            while event := await queue.get():
                seq = event.get(SEQ_KEY)
                if isinstance(seq, int) and seq <= last:
                    continue  # the replay carried it already
                yield event
                if event.get("type") == AGENT_EXITED:
                    return
        finally:
            if queue in thread.queues:
                thread.queues.remove(queue)

    def _record(self, thread_id: str, event: dict[str, Any], ts: str = "") -> None:
        """Stamp ``event``, keep it in the ring, and send it to every watcher."""
        thread = self._threads.get(thread_id)
        if thread is None:
            return
        thread.seq += 1
        event = {**event, SEQ_KEY: thread.seq, TS_KEY: ts or self.clock()}
        thread.ring.append(event)
        for queue in thread.queues:
            queue.put_nowait(event)

    def _ensure_reader(self) -> None:
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_events())

    async def _resubscribe(self) -> None:
        """Subscribe this connection to every thread again, after a drop.

        Stays stale until every thread is back, so a failed resume is retried
        on the next request.
        """
        if not self._stale:
            return
        resubscribed = True
        for thread_id, thread in list(self._threads.items()):
            try:
                resumed = await self.bridge.request(
                    "thread/resume", {"threadId": thread_id, "excludeTurns": True}
                )
                status = _mapping(resumed, "thread").get("status")
            except RuntimeError as error:
                log.warning("Codex thread %s not resubscribed: %s", thread_id, error)
                resubscribed = False
                continue
            # The old connection's requests died with it.
            thread.pending.clear()
            thread.row["waiting_on"] = ""
            if _status_type(status) != "active":
                thread.row["state"] = "idle"
                thread.row["turn_id"] = ""
        self._stale = not resubscribed
        self._ensure_reader()

    async def _read_events(self) -> None:
        try:
            async for message in self.bridge.incoming():
                await self._route(message)
        except RuntimeError as error:
            log.warning("Codex app-server connection dropped: %s", error)
        # Each watch ends without an exit marker: the threads still live.
        self._stale = True
        self._reader = None
        for thread in self._threads.values():
            for queue in thread.queues:
                queue.put_nowait(None)

    async def _route(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        request_id = message.get("id")
        thread_id = _thread_id(params)
        thread = self._threads.get(thread_id)
        if request_id is not None and (thread is None or method not in APPROVAL_TOOLS):
            # Unanswered, the turn would hang. A known thread shows it raw.
            await self.bridge.reject(
                request_id, _UNSUPPORTED, f"Maelstrom does not support {method}."
            )
            self._record(
                thread_id,
                {
                    "type": "codex_raw",
                    "method": method,
                    "id": request_id,
                    "params": params,
                },
            )
            return
        if thread is None:
            return
        row = thread.row
        if request_id is not None and (approval := approval_of(method, params)):
            thread.pending[str(request_id)] = {
                "rpc_id": request_id,
                "method": method,
                "approval": approval,
            }
            row["turn_id"] = str(params.get("turnId") or row.get("turn_id") or "")
            row["state"] = "awaiting-permission"
            row["waiting_on"] = approval.description
            self._record(
                thread_id,
                {
                    "type": "codex_raw",
                    "method": method,
                    "id": request_id,
                    "params": params,
                },
            )
            return
        if method in ("serverRequest/resolved", "turn/completed"):
            # Codex settled the request itself, or the turn took it away.
            resolved = str(params.get("requestId", ""))
            for open_id in list(thread.pending):
                if method == "turn/completed" or open_id == resolved:
                    thread.pending.pop(open_id)
        _follow_row(row, method, params, self.clock)
        event = {"type": "codex_raw", "method": method, "params": params}
        if method in _LIVE_ONLY:
            for queue in thread.queues:
                queue.put_nowait(event)
            return
        self._record(thread_id, event)

    def _adopt(
        self,
        thread_id: str,
        thread: dict[str, Any],
        *,
        session: str,
        cwd: str,
        model: str,
        mode: str,
    ) -> None:
        """Hold a resumed thread, its ring rebuilt from the turns Codex kept."""
        held = _Thread(
            _row(
                thread_id,
                session=session,
                cwd=str(thread.get("cwd") or cwd),
                model=model,
                mode=mode,
            )
        )
        self._threads[thread_id] = held
        turns = thread.get("turns")
        for turn in turns if isinstance(turns, list) else []:
            if isinstance(turn, dict):
                self._replay_turn(thread_id, turn)
        if _status_type(thread.get("status")) == "active" and held.row["turn_id"]:
            held.row["state"] = "processing"

    def _replay_turn(self, thread_id: str, turn: dict[str, Any]) -> None:
        """One stored turn, as the notifications that drew it live."""
        started = _iso(turn.get("startedAt"))
        completed = _iso(turn.get("completedAt"))
        items = turn.get("items")
        for item in items if isinstance(items, list) else []:
            self._record(
                thread_id,
                {
                    "type": "codex_raw",
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn.get("id"),
                        "item": item,
                    },
                },
                started,
            )
        if turn.get("status") == "inProgress":
            self._threads[thread_id].row["turn_id"] = str(turn.get("id") or "")
            return
        summary = {k: v for k, v in turn.items() if k != "items"}
        self._record(
            thread_id,
            {
                "type": "codex_raw",
                "method": "turn/completed",
                "params": {"threadId": thread_id, "turn": summary},
            },
            completed or started,
        )


def _follow_row(
    row: dict[str, Any], method: str, params: dict[str, Any], clock: Callable[[], str]
) -> None:
    """Move the ``list`` row as the notification moves the agent."""
    if method == "turn/started":
        turn = params.get("turn")
        if isinstance(turn, dict):
            row["turn_id"] = str(turn.get("id", ""))
        row["state"] = "processing"
    elif method == "turn/completed":
        row["state"] = "idle"
        row["turn_id"] = ""
        row["waiting_on"] = ""
    elif method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage")
        if isinstance(usage, dict):
            row["tokens"] = _total(usage.get("total")) or row["tokens"]
            row["context_tokens"] = _total(usage.get("last")) or row["context_tokens"]
    elif method == "thread/started":
        thread = params.get("thread")
        if isinstance(thread, dict) and thread.get("model"):
            row["model"] = row["model"] or f"codex:{thread['model']}"
    elif method == "item/completed":
        item = params.get("item")
        if isinstance(item, dict) and item.get("type") == "agentMessage":
            text = str(item.get("text") or "").strip()
            row["last_message"] = text.splitlines()[0][:60] if text else ""
            row["last_message_at"] = clock()


def _thread_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """The instructions and environment a thread starts or resumes with."""
    settings: dict[str, Any] = {}
    prompt_file = str(payload.get("system_prompt_file") or "")
    if prompt_file:
        try:
            settings["developerInstructions"] = Path(prompt_file).read_text()
        except OSError as error:
            raise RuntimeError(f"could not read {prompt_file}: {error}") from error
    env = payload.get("env")
    if isinstance(env, dict) and env:
        settings["config"] = {
            "shell_environment_policy": {
                "set": {str(k): str(v) for k, v in env.items()}
            }
        }
    return settings


def _images(attachments: Any) -> list[str]:
    if not isinstance(attachments, list):
        return []
    return [
        str(item["path"])
        for item in attachments
        if isinstance(item, dict) and item.get("path")
    ]


def _codex_reference(value: str, mode: str) -> ModelReference:
    if ":" not in value:
        value = f"codex:{value}"
    reference = resolve_model_reference(value, mode)
    if reference.harness != HARNESS_CODEX:
        raise ValueError(f"Codex daemon needs a codex model, got {value!r}.")
    return reference


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get(name), dict):
        raise RuntimeError(f"Codex {name} response was invalid: {value!r}")
    return value[name]


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def _no_agent(thread_id: str) -> dict[str, Any]:
    return _error(f"no such agent: {thread_id}")


def _row(
    thread_id: str, *, session: str, cwd: str, model: str, mode: str
) -> dict[str, Any]:
    """A ``list`` row, with every key an ``AgentRow`` has."""
    return {
        "id": thread_id,
        "parent": "",
        "description": "",
        "state": "idle",
        "session": session,
        "cwd": cwd,
        "pid": None,
        "model": model,
        "mode": mode,
        "waiting_on": "",
        "last_message": "",
        "last_message_at": "",
        "last_note": "",
        "last_note_at": "",
        # Codex reports no price.
        "cost": "",
        "tokens": 0,
        "subagent_tokens": {},
        "context_tokens": 0,
        "background": [],
        "turn_id": "",
    }


def _total(value: Any) -> int:
    figure = value.get("totalTokens") if isinstance(value, dict) else None
    return figure if isinstance(figure, int) and not isinstance(figure, bool) else 0


def _status_type(status: Any) -> str:
    return str(status.get("type") or "") if isinstance(status, dict) else ""


def _iso(seconds: Any) -> str:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        return ""
    stamp = datetime.fromtimestamp(seconds, UTC).isoformat()
    return stamp.replace("+00:00", "Z")


def _thread_id(params: dict[str, Any]) -> str:
    value = params.get("threadId")
    if value is not None:
        return str(value)
    thread = params.get("thread")
    return str(thread.get("id", "")) if isinstance(thread, dict) else ""


def _detail_fields(pending: dict[str, Any] | None) -> dict[str, Any]:
    if pending is None:
        return {"request_id": "", "waiting_tool": ""}
    approval: Approval = pending["approval"]
    return {
        "request_id": str(pending["rpc_id"]),
        "waiting_tool": approval.tool,
        "waiting_input": approval.input,
        "waiting_on": approval.description,
    }
