"""Adapt the Codex app-server to the orchestrator daemon client boundary."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from .agent_model import AGENT_DETAIL, BACKLOG_END
from .agent_transport import AsyncDaemonClient
from .harness_model import (
    HARNESS_CODEX,
    ModelReference,
    codex_thread_start_options,
    resolve_model_reference,
)


class CodexRpcClient(Protocol):
    """The JSON-RPC operations the Codex daemon adapter needs."""

    async def request(self, method: str, params: dict[str, Any]) -> Any: ...

    def incoming(self) -> AsyncIterator[dict[str, Any]]: ...


@dataclass
class CodexDaemonClient(AsyncDaemonClient):
    """Present Codex threads as agents to the orchestrator server.

    Codex has one connection-wide event stream. This adapter fans that stream
    out by ``threadId`` and keeps the existing daemon-client contract intact.
    Events which have no Claude stream-json equivalent are deliberately kept as
    raw RPC records for the Session view.
    """

    bridge: CodexRpcClient
    _rows: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _queues: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = field(
        default_factory=dict, init=False
    )
    _reader: asyncio.Task[None] | None = field(default=None, init=False)

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply one orchestrator command to a Codex thread."""
        command = str(payload.get("cmd", ""))
        if command == "start":
            return await self._start(payload)
        if command == "list":
            return {"agents": list(self._rows.values()), "usage": None}
        if command == "say":
            return await self._say(payload)
        if command == "interrupt":
            return await self._interrupt(payload)
        if command == "stop":
            return await self._stop(payload)
        return {"ok": False, "error": f"Codex does not support {command!r}."}

    def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """Attach one Session view to its Codex thread."""
        del from_seq, epoch
        return self._attach(agent_id)

    async def restore(self, agents: list[dict[str, Any]]) -> None:
        """Resume the stored Codex threads after an orchestrator restart."""
        for agent in agents:
            if agent.get("harness") != HARNESS_CODEX:
                continue
            thread_id = str(agent.get("id", ""))
            if not thread_id or thread_id in self._rows:
                continue
            try:
                resumed = await self.bridge.request(
                    "thread/resume", {"threadId": thread_id}
                )
                thread = _mapping(resumed, "thread")
            except RuntimeError:
                continue
            self._rows[thread_id] = _row(
                thread_id,
                {
                    "session": agent.get("task_session_id", ""),
                    "cwd": agent.get("cwd", ""),
                    "model": agent.get("model", ""),
                    "mode": agent.get("mode", ""),
                },
                thread,
            )

    async def _start(self, payload: dict[str, Any]) -> dict[str, Any]:
        reference = _codex_reference(
            str(payload.get("model") or "codex:luna"),
            str(payload.get("mode") or "normal"),
        )
        cwd = str(payload["cwd"])
        started = await self.bridge.request(
            "thread/start",
            {
                "cwd": cwd,
                "model": reference.cli_args[1],
                **codex_thread_start_options(str(payload.get("mode") or "normal")),
            },
        )
        thread = _mapping(started, "thread")
        thread_id = str(thread["id"])
        self._rows[thread_id] = _row(thread_id, payload, thread)
        prompt = str(payload.get("prompt") or "")
        if prompt:
            turn = _mapping(
                await self.bridge.request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": prompt}],
                    },
                ),
                "turn",
            )
            self._rows[thread_id]["turn_id"] = str(turn.get("id", ""))
            self._rows[thread_id]["state"] = "processing"
        return {"ok": True, "id": thread_id}

    async def _say(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        if thread_id not in self._rows:
            return {"ok": False, "error": f"no such agent: {thread_id}"}
        turn = _mapping(
            await self.bridge.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {"type": "text", "text": str(payload.get("message", ""))}
                    ],
                },
            ),
            "turn",
        )
        self._rows[thread_id]["turn_id"] = str(turn.get("id", ""))
        self._rows[thread_id]["state"] = "processing"
        return {"ok": True}

    async def _interrupt(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        row = self._rows.get(thread_id)
        if row is None:
            return {"ok": False, "error": f"no such agent: {thread_id}"}
        turn_id = str(row.get("turn_id", ""))
        if turn_id:
            await self.bridge.request(
                "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
            )
        return {"ok": True}

    async def _stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(payload.get("id", ""))
        if thread_id not in self._rows:
            return {"ok": False, "error": f"no such agent: {thread_id}"}
        await self.bridge.request("thread/unsubscribe", {"threadId": thread_id})
        self._rows.pop(thread_id)
        for queue in self._queues.get(thread_id, []):
            queue.put_nowait(None)
        return {"ok": True}

    async def _attach(self, agent_id: str) -> AsyncIterator[dict[str, Any]]:
        row = self._rows.get(agent_id)
        if row is None:
            yield {"error": f"no such agent: {agent_id}"}
            return
        self._ensure_reader()
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._queues.setdefault(agent_id, []).append(queue)
        try:
            yield {
                "type": AGENT_DETAIL,
                "agent": {"request_id": "", "waiting_tool": ""},
            }
            yield {"type": BACKLOG_END, "epoch": f"codex-{agent_id}", "seq": 0}
            while event := await queue.get():
                yield event
        finally:
            self._queues[agent_id].remove(queue)

    def _ensure_reader(self) -> None:
        if self._reader is None:
            self._reader = asyncio.create_task(self._read_events())

    async def _read_events(self) -> None:
        async for message in self.bridge.incoming():
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            thread_id = _thread_id(params)
            if thread_id not in self._rows:
                continue
            method = str(message.get("method", ""))
            if method == "turn/started":
                turn = params.get("turn")
                if isinstance(turn, dict):
                    self._rows[thread_id]["turn_id"] = str(turn.get("id", ""))
                self._rows[thread_id]["state"] = "processing"
            elif method == "turn/completed":
                self._rows[thread_id]["state"] = "idle"
                self._rows[thread_id]["turn_id"] = ""
            event = {"type": "codex_raw", "method": method, "params": params}
            for queue in self._queues.get(thread_id, []):
                queue.put_nowait(event)


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


def _row(
    thread_id: str, payload: dict[str, Any], thread: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "state": "idle",
        "session": str(payload.get("session") or ""),
        "cwd": str(thread.get("cwd") or payload["cwd"]),
        "model": str(payload.get("model") or ""),
        "description": "Codex thread",
        "waiting_on": "",
        "last_message": "",
        "last_note": "",
        "last_note_at": "",
        "cost": "",
        "turn_id": "",
    }


def _thread_id(params: dict[str, Any]) -> str:
    value = params.get("threadId")
    if value is not None:
        return str(value)
    thread = params.get("thread")
    return str(thread.get("id", "")) if isinstance(thread, dict) else ""
