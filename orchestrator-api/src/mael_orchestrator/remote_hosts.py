"""The parent server's link to each **Remote host**.

A remote host runs ``mael-orchestrator serve`` in **Runner mode**. The parent
holds its ``GET /api/events`` stream open to know whether it is online, opens
worktrees there with ``POST /api/host/worktrees/open``, and reaches its agent
host through :class:`RemoteDaemonClient`, which speaks the same
``AsyncDaemonClient`` interface as the local daemon socket. See
``docs/dev/remote-hosts.md``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from mael_agent.agent_transport import KIND_UNREACHABLE, AsyncDaemonClient
from mael_common.util import now_iso
from mael_domain.protocol import RemoteHostState
from mael_domain.task_launch import LaunchBlocked
from mael_domain.worktree import SyncResult, WorktreeSetup

log = logging.getLogger(__name__)

#: The first and the longest wait before the event stream is tried again.
BACKOFF_SECS = 1.0
BACKOFF_MAX_SECS = 30.0

#: How long the event stream may say nothing before the link counts as down.
#: The remote host pings every 15 s, so a silence this long is a host that
#: went to sleep with the connection still open.
SILENCE_SECS = 40.0

#: How long one request may take. A worktree open fetches and rebases. A
#: ``list`` runs on each agent poll, so a host that sleeps with its link still
#: open must not hold the poll for long.
REQUEST_SECS = 30.0
LIST_SECS = 5.0
OPEN_SECS = 300.0


@dataclass
class HostOpened:
    """What a worktree open on a remote host reports back to the parent."""

    setup: WorktreeSetup
    #: The ``write_env`` step's log lines, written on the remote host.
    env_lines: list[str]
    #: Whether the task's session has a transcript there, so a launch resumes.
    has_transcript: bool


def setup_reply(opened: HostOpened) -> dict[str, Any]:
    """``opened`` as the open route answers it."""
    setup, sync = opened.setup, opened.setup.sync
    return {
        "setup": {
            "path": str(setup.path),
            "name": setup.name,
            "action": setup.action,
            "sync": None
            if sync is None
            else {
                "success": sync.success,
                "branch": sync.branch,
                "message": sync.message,
            },
        },
        "envLines": opened.env_lines,
        "hasTranscript": opened.has_transcript,
    }


def _opened_from(body: dict[str, Any]) -> HostOpened:
    raw = body["setup"]
    sync = raw.get("sync")
    return HostOpened(
        setup=WorktreeSetup(
            path=Path(raw["path"]),
            name=str(raw["name"]),
            action=str(raw["action"]),
            sync=None
            if sync is None
            else SyncResult(
                success=bool(sync["success"]),
                branch=str(sync["branch"]),
                message=str(sync["message"]),
            ),
        ),
        env_lines=[str(line) for line in body.get("envLines") or []],
        has_transcript=bool(body.get("hasTranscript")),
    )


@dataclass
class RemoteHost:
    """One remote host: its base URL, its event link and whether it is online."""

    name: str
    base_url: str
    clock: Callable[[], str] = now_iso
    online: bool = False
    #: When ``online`` last changed. ``""`` before the first change.
    since: str = ""
    backoff: float = BACKOFF_SECS
    _session: aiohttp.ClientSession | None = field(default=None, init=False)

    def session(self) -> aiohttp.ClientSession:
        """The one client session, made on first use so it binds the running loop."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def state(self) -> RemoteHostState:
        """The ``remoteHosts`` entry for this host."""
        return {"name": self.name, "online": self.online, "since": self.since}

    async def run(self, on_flip: Callable[[], None]) -> None:
        """Hold the event stream open; call ``on_flip`` each time ``online`` changes.

        Runs until cancelled. A drop retries with backoff, and only the first
        failure of an outage is logged.
        """
        delay = self.backoff
        failed = False
        while True:
            try:
                timeout = aiohttp.ClientTimeout(sock_read=SILENCE_SECS)
                async with self.session().get(
                    self.url("/api/events"), timeout=timeout
                ) as response:
                    response.raise_for_status()
                    async for line in response.content:
                        if line.startswith(b"event: reset") and not self.online:
                            self._flip(True, on_flip)
                            failed = False
                            delay = self.backoff
                raise ConnectionError("the event stream ended")
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                if not failed:
                    log.warning("remote host %s: %s", self.name, _words(exc))
                failed = True
            if self.online:
                self._flip(False, on_flip)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_SECS)

    def _flip(self, online: bool, on_flip: Callable[[], None]) -> None:
        self.online = online
        self.since = self.clock()
        on_flip()

    async def open_worktree(
        self, project: str, branch: str, base: str, session: str
    ) -> HostOpened:
        """Open ``branch``'s worktree on this host.

        Raises:
            LaunchBlocked: When the host refuses the open or cannot be reached.
        """
        body = {"project": project, "branch": branch, "base": base, "session": session}
        try:
            async with self.session().post(
                self.url("/api/host/worktrees/open"),
                json=body,
                timeout=aiohttp.ClientTimeout(total=OPEN_SECS),
            ) as response:
                reply = await response.json()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            raise LaunchBlocked(
                f"{self.name} did not open the worktree: {exc}"
            ) from exc
        if response.status != 200:
            message = (reply.get("error") or {}).get("message") or response.reason
            raise LaunchBlocked(f"{self.name}: {message}")
        try:
            return _opened_from(reply)
        except (KeyError, TypeError, ValueError) as exc:
            raise LaunchBlocked(f"{self.name} sent an unexpected reply") from exc

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


@dataclass
class RemoteDaemonClient(AsyncDaemonClient):
    """A remote host's agent host, over its two ``/api/host`` routes.

    Errors come back as data, as from the socket client: an offline host or a
    network failure is a reply with ``error`` and ``kind: unreachable``.
    """

    host: RemoteHost

    def _offline(self) -> dict[str, Any]:
        return {"error": f"{self.host.name} is offline", "kind": KIND_UNREACHABLE}

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.host.online:
            return self._offline()
        try:
            async with self.host.session().post(
                self.host.url("/api/host/request"),
                json=local_paths_removed(payload),
                timeout=aiohttp.ClientTimeout(
                    total=LIST_SECS if payload.get("cmd") == "list" else REQUEST_SECS
                ),
            ) as response:
                return await response.json()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            return {
                "error": f"{self.host.name}: {_words(exc)}",
                "kind": KIND_UNREACHABLE,
            }

    async def attach(
        self, agent_id: str, from_seq: int = 0, epoch: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.host.online:
            yield self._offline()
            return
        query = {"from": from_seq, "epoch": epoch} if from_seq > 0 else {}
        url = self.host.url(f"/api/host/agents/{agent_id}/attach")
        if query:
            url = f"{url}?{urlencode(query)}"
        try:
            # The handshake has the list's budget, so a host asleep with its
            # link still open does not park the attach for long.
            ws = await asyncio.wait_for(
                self.host.session().ws_connect(url, heartbeat=20.0), LIST_SECS
            )
            async with ws:
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        break
                    yield message.json()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            yield {
                "error": f"{self.host.name}: {_words(exc)}",
                "kind": KIND_UNREACHABLE,
            }


#: Start and resume fields that name a path on the parent machine. The remote
#: host puts in its own.
LOCAL_PATH_KEYS = ("system_prompt_file", "plugin_dirs")


def _words(exc: BaseException) -> str:
    """``exc`` for a log line or a reply. A timeout's own text is empty."""
    return str(exc) or type(exc).__name__


def local_paths_removed(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in LOCAL_PATH_KEYS}
