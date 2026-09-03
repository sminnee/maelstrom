"""The orchestrator server: the world over one WebSocket.

The adapter layer, and the only asyncio orchestration in the package. It owns
the :class:`~maelstrom.orchestrator.event_log.EventLog`, polls the task and
worktree sources, keeps one attach stream per agent against the agent host,
answers commands, and serves every client the same seq-stamped frames.

The wire format — hello, snapshot or replay, ready, commands and replies — is
documented in ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from ..util import now_iso
from .daemon_bridge import AsyncDaemonClient
from .event_log import RING_SIZE, EventLog
from .protocol import EventFrame, ServerEvent
from .sources import TaskSource, WorktreeSource
from .world_build import diff_kind

log = logging.getLogger(__name__)

#: How often the notebook's version is checked.
TASK_POLL_SECS = 2.0
#: How often ``list-all`` is re-read. It shells out per worktree, so not often.
WORKTREE_POLL_SECS = 15.0
#: How often the agent host's ``list`` is reconciled against the world.
AGENT_POLL_SECS = 2.0


def _reply(command_id: Any, reply: dict[str, Any]) -> str:
    return json.dumps({"reply": {"id": command_id, **reply}})


def _refused(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


class Orchestrator:
    """The world, its sources, and the clients watching it."""

    def __init__(
        self,
        tasks: TaskSource,
        worktrees: WorktreeSource,
        daemon: AsyncDaemonClient,
        *,
        clock: Callable[[], str] = now_iso,
        executor: Executor | None = None,
        ring_size: int = RING_SIZE,
        task_poll: float = TASK_POLL_SECS,
        worktree_poll: float = WORKTREE_POLL_SECS,
        agent_poll: float = AGENT_POLL_SECS,
    ) -> None:
        self.tasks = tasks
        self.worktrees = worktrees
        self.daemon = daemon
        self.clock = clock
        self.executor = executor
        self.log = EventLog(ring_size)
        self._task_poll = task_poll
        self._worktree_poll = worktree_poll
        self._agent_poll = agent_poll
        self._clients: set[ServerConnection] = set()
        # Held while frames are appended and sent, and while a client is
        # handed its snapshot: a frame published between the two would reach
        # the client before the snapshot it is newer than, and be lost.
        self._lock = asyncio.Lock()
        self._task_version: Any = _NEVER
        self._worktree_read = asyncio.Lock()
        self._pollers: list[asyncio.Task[None]] = []
        self._started = asyncio.Event()

    # -- running --

    async def start(self) -> None:
        """Read every source once, then keep them fresh in the background."""
        await self.refresh_tasks()
        await self.refresh_worktrees()
        self._started.set()
        self._pollers = [
            asyncio.create_task(self._poll(self._task_poll, self.refresh_tasks)),
            asyncio.create_task(
                self._poll(self._worktree_poll, self.refresh_worktrees)
            ),
        ]

    async def stop(self) -> None:
        for poller in self._pollers:
            poller.cancel()
        await asyncio.gather(*self._pollers, return_exceptions=True)
        self._pollers = []

    async def serve(self, host: str, port: int) -> None:
        """Serve until cancelled."""
        async with self.serving(host, port):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def serving(self, host: str, port: int) -> AsyncIterator[Server]:
        """Serve for the length of the block. ``port`` 0 picks a free port.

        The socket binds first, so a port in use fails at once. A client that
        connects before the first source reads finish waits for them, so its
        snapshot holds the world rather than an empty one that fills in later.
        """
        async with serve(self.handle_connection, host, port) as server:
            await self.start()
            try:
                yield server
            finally:
                await self.stop()

    async def _poll(self, interval: float, refresh: Callable[[], Any]) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await refresh()
            except Exception:  # noqa: BLE001 — a poller must outlive one bad read
                log.exception("refresh failed")

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run blocking source work off the loop, when an executor was given."""
        if self.executor is None:
            return fn(*args)
        return await asyncio.get_running_loop().run_in_executor(
            self.executor, fn, *args
        )

    # -- keeping the world fresh --

    async def publish(self, events: list[ServerEvent]) -> list[EventFrame]:
        """Append ``events`` to the log and send the frames to every client."""
        if not events:
            return []
        async with self._lock:
            frames = self.log.append(events, self.clock())
            await self._send_all(frames)
        return frames

    async def _send_all(self, frames: list[EventFrame]) -> None:
        """Send ``frames`` in order to every client; drop a client a send fails on.

        The client set is captured once: ``_welcome`` adds to it at await
        points, so pairing results against a second listing could evict the
        wrong client.
        """
        texts = [json.dumps(frame) for frame in frames]
        clients = list(self._clients)

        async def send_all_to(client: ServerConnection) -> None:
            for text in texts:
                await client.send(text)

        results = await asyncio.gather(
            *(send_all_to(client) for client in clients), return_exceptions=True
        )
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, Exception):
                self._clients.discard(client)

    async def refresh_tasks(self, *, force: bool = False) -> None:
        """Re-read the notebook when its version moved, and publish the difference."""
        version = await self._run(self.tasks.version)
        # An unknown version (a notebook with no commits yet) never matches,
        # so the poll degrades to a re-read rather than a permanent stale table.
        if not force and version is not None and version == self._task_version:
            return
        self._task_version = version
        entities = await self._run(self.tasks.read)
        new = {task["id"]: task for task in entities}
        await self.publish(diff_kind("task", self.log.state["world"]["tasks"], new))

    async def refresh_worktrees(self) -> None:
        """Re-read ``list-all``, one read in flight at a time."""
        if self._worktree_read.locked():
            return
        async with self._worktree_read:
            projects, worktrees = await self._run(self.worktrees.read)
        world = self.log.state["world"]
        events = diff_kind("project", world["projects"], {p["id"]: p for p in projects})
        events += diff_kind(
            "worktree", world["worktrees"], {w["id"]: w for w in worktrees}
        )
        await self.publish(events)

    # -- commands --

    async def handle_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run one command and return its reply: ``ok`` with a result, or an error."""
        return _refused("invalid", f"Unsupported command: {command.get('type')}")

    # -- the socket --

    async def handle_connection(self, ws: ServerConnection) -> None:
        """One client: hello, then snapshot or replay, then ready, then commands."""
        try:
            first = await ws.recv()
        except ConnectionClosed:
            return
        hello = _parse(first)
        if hello.get("type") != "hello":
            await ws.send(
                _reply(None, _refused("invalid", "The first message must be a hello"))
            )
            await ws.close()
            return
        await self._started.wait()
        await self._welcome(ws, hello.get("resumeFrom"))
        try:
            async for raw in ws:
                message = _parse(raw)
                if message.get("type") == "hello":
                    await ws.send(
                        _reply(None, _refused("invalid", "Already said hello"))
                    )
                    continue
                command = message.get("command")
                if not isinstance(command, dict):
                    await ws.send(
                        _reply(
                            message.get("id"),
                            _refused("invalid", "No command in message"),
                        )
                    )
                    continue
                reply = await self.handle_command(command)
                await ws.send(_reply(message.get("id"), reply))
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    async def _welcome(self, ws: ServerConnection, resume_from: Any) -> None:
        async with self._lock:
            frames = None
            if isinstance(resume_from, int) and not isinstance(resume_from, bool):
                frames = self.log.replay_from(resume_from)
            if frames is None:
                frames = [self.log.snapshot_frame(self.clock())]
            for frame in frames:
                await ws.send(json.dumps(frame))
            await ws.send(json.dumps({"ready": {"seq": self.log.seq}}))
            self._clients.add(ws)


_NEVER = object()


def _parse(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}
