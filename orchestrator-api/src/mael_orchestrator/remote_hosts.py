"""The parent server's link to each **Remote host**.

A remote host is an ordinary orchestrator with ``host_name:`` set. The parent
holds its ``GET /api/events`` stream open: the ``reset`` says the host is
online and names its **Id prefix**, and each ``change`` says which merged kind
to read again. The parent keeps one slice per merged kind.

The same session forwards a browser's request for a host-owned id, and posts a
delegated launch. See ``docs/dev/remote-hosts.md``.
"""

import asyncio
import json
import logging
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aiohttp import web
from yarl import URL

from mael_agent.host_ids import owner_of
from mael_common.util import now_iso
from mael_domain.operations import RUNNING
from mael_domain.protocol import RemoteHostState

from .clients import normal_ip

log = logging.getLogger(__name__)

#: The first and the longest wait before the event stream is tried again.
BACKOFF_SECS = 1.0
BACKOFF_MAX_SECS = 30.0

#: How long the event stream may say nothing before the link counts as down.
#: The remote host pings every 15 s, so a silence this long is a host that
#: went to sleep with the connection still open.
SILENCE_SECS = 40.0

#: How long the read of a host's own ``GET /api/host`` may take. Short, because
#: a reset waits for it before the host goes online.
FACTS_SECS = 5.0

#: How long one JSON call may take. A list read runs on each change notice, so
#: a host that sleeps with its link still open must not hold the merge long.
REQUEST_SECS = 30.0

#: How long a delegated launch may run on the host: it fetches and rebases.
OPERATION_SECS = 300.0
OPERATION_POLL_SECS = 0.5

#: How long a resolved host address is kept. Short, so a changed tailnet IP
#: is seen without a restart.
ADDRESS_SECS = 60.0

#: Each merged kind, and the list route and key the host serves it on. The
#: operations are read in the UI's window: an older host row leaves the merge.
MERGED_KINDS: dict[str, tuple[str, str]] = {
    "operation": ("/api/operations?limit=200", "operations"),
    "agent": ("/api/agents", "agents"),
    "document": ("/api/documents", "documents"),
    "attention": ("/api/attention", "attention"),
    "worktree": ("/api/worktrees", "worktrees"),
    "gateJob": ("/api/gate-jobs", "gateJobs"),
}

#: Request headers a forward drops: the hop's own, and what a browser sends
#: that the host must not take as the caller's. See "Same origin" in
#: ``docs/dev/remote-hosts.md``.
_DROPPED_REQUEST = frozenset(
    {
        "host",
        "origin",
        "referer",
        "authorization",
        "cookie",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "accept-encoding",
    }
)

#: Response headers a forward drops: the body is streamed again, decoded.
_DROPPED_RESPONSE = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-length",
        "content-encoding",
    }
)


async def resolve_addresses(host: str) -> set[str]:
    """Every IP ``host`` resolves to. Empty when it does not resolve."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
    except OSError:
        return set()
    return {ip for info in infos if (ip := normal_ip(str(info[4][0]))) is not None}


class HostUnreachable(Exception):
    """The host is offline, did not answer, or refused a call."""


def owns(host: str, kind: str, entity: dict[str, Any]) -> bool:
    """Whether ``entity`` of ``kind`` belongs to ``host``'s own work.

    An agent, worktree or gate job carries the prefix in its id. A document
    or an attention item belongs to its agent. A host's attached documents
    and its merged rows from a host of its own are not its work.
    """
    if kind in ("document", "attention"):
        return owner_of(str(entity.get("agentId") or "")) == host
    return owner_of(str(entity.get("id") or "")) == host


@dataclass
class RemoteHost:
    """One remote host: its link, whether it is online, and its merged slices."""

    name: str
    base_url: str
    #: The host's **Host key**, from its ``hosts:`` entry. ``""`` sends none.
    token: str = ""
    clock: Callable[[], str] = now_iso
    online: bool = False
    #: When ``online`` last changed. ``""`` before the first change.
    since: str = ""
    #: Why the last try failed, while offline. ``""`` while online.
    reason: str = ""
    #: The host's own ``host.cmux`` and ``host.ssh``, from its ``GET
    #: /api/host``. ``None`` until the first read.
    cmux: bool | None = None
    ssh: bool | None = None
    backoff: float = BACKOFF_SECS
    #: Per merged kind, what the host's list gave last time, by id.
    slices: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Resolves a host name to its IPs. A test gives a fake one.
    resolve: Callable[[str], Awaitable[set[str]]] = resolve_addresses
    _session: aiohttp.ClientSession | None = field(default=None, init=False)
    _misnamed: bool = field(default=False, init=False)
    _facts_failed: bool = field(default=False, init=False)
    #: The last resolved addresses, and the monotonic time they were read.
    _addresses: tuple[float, set[str]] | None = field(default=None, init=False)

    def session(self) -> aiohttp.ClientSession:
        """The one client session, made on first use so it binds the running loop.

        Every call to the host goes through it, a forward too, so the host's
        key goes on its headers once. A forward drops the browser's own
        ``Authorization`` first. See "Auth" in ``docs/dev/remote-hosts.md``.
        """
        if self._session is None or self._session.closed:
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def addresses(self) -> set[str]:
        """The IPs of ``base_url``'s host, read again after ``ADDRESS_SECS``.

        A browser whose peer IP is one of these is on this host: a
        **Remote client**.
        """
        now = time.monotonic()
        if self._addresses is not None and now - self._addresses[0] < ADDRESS_SECS:
            return self._addresses[1]
        host = URL(self.base_url).host or ""
        found = await self.resolve(host) if host else set()
        self._addresses = (now, found)
        return found

    def state(self) -> RemoteHostState:
        """The ``remoteHosts`` entry for this host."""
        return {
            "name": self.name,
            "online": self.online,
            "since": self.since,
            "reason": self.reason,
            "cmux": self.cmux,
            "ssh": self.ssh,
        }

    async def run(
        self,
        on_state: Callable[[], None],
        on_change: Callable[["RemoteHost", set[str]], Awaitable[None]],
    ) -> None:
        """Hold the event stream open, and report each new state and each change.

        ``on_state`` runs each time :meth:`state` changes, other than ``since``.
        ``on_change`` runs with the merged kinds to read again: every kind on a
        ``reset``, and the notice's kind on a ``change``. Runs until cancelled.
        A drop retries with backoff, and only the first failure of an outage is
        logged.
        """
        delay = self.backoff
        failed = False
        while True:
            reason = ""
            try:
                timeout = aiohttp.ClientTimeout(sock_read=SILENCE_SECS)
                async with self.session().get(
                    self.url("/api/events"), timeout=timeout
                ) as response:
                    response.raise_for_status()
                    async for name, data in _sse_events(response.content):
                        if name == "reset":
                            reason = self._misnaming(data)
                            if reason:
                                break
                            cmux, ssh = await self._read_facts()
                            self._update(on_state, online=True, cmux=cmux, ssh=ssh)
                            failed = False
                            delay = self.backoff
                            await self._report(on_change, set(MERGED_KINDS))
                        elif name == "change" and self.online:
                            kind = str(data.get("kind") or "")
                            if kind in MERGED_KINDS:
                                await self._report(on_change, {kind})
                            elif kind == "host":
                                cmux, ssh = await self._read_facts()
                                self._update(on_state, online=True, cmux=cmux, ssh=ssh)
                raise ConnectionError("the event stream ended")
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                # A misnamed host's own words win over the end of the stream.
                reason = reason or _words(exc)
                if not failed:
                    log.warning("remote host %s: %s", self.name, _words(exc))
                failed = True
            self._update(on_state, online=False, reason=reason)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_SECS)

    async def _report(
        self,
        on_change: Callable[["RemoteHost", set[str]], Awaitable[None]],
        kinds: set[str],
    ) -> None:
        """Run ``on_change``. A merge that fails is logged; the link stays up.

        A bad row from the host must not end the link, as one bad read must
        not end a local poller.
        """
        try:
            await on_change(self, kinds)
        except Exception:
            log.exception("remote host %s: merge of %s failed", self.name, kinds)

    def _misnaming(self, reset: dict[str, Any]) -> str:
        """Why the host's id prefix is not the name ``hosts:`` gives it, or ``""``.

        A mismatch keeps the host offline: its ids would not route back to it.
        """
        prefix = str(reset.get("idPrefix") or "")
        if prefix == self.name:
            self._misnamed = False
            return ""
        reason = f"names itself {prefix!r}: set its host_name to {self.name}"
        if not self._misnamed:
            log.warning("remote host %s %s", self.name, reason)
        self._misnamed = True
        return reason

    async def _read_facts(self) -> tuple[bool | None, bool | None]:
        """The host's own ``cmux`` and ``ssh``. A failed read keeps the last ones."""
        try:
            async with self.session().get(
                self.url("/api/host"),
                timeout=aiohttp.ClientTimeout(total=FACTS_SECS),
            ) as response:
                response.raise_for_status()
                reply = await response.json()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            if not self._facts_failed:
                log.warning("remote host %s: no host read: %s", self.name, _words(exc))
            self._facts_failed = True
            return self.cmux, self.ssh
        self._facts_failed = False
        host = reply.get("host") if isinstance(reply, dict) else None
        if not isinstance(host, dict):
            return self.cmux, self.ssh
        cmux, ssh = host.get("cmux"), host.get("ssh")
        return (
            cmux if isinstance(cmux, bool) else self.cmux,
            ssh if isinstance(ssh, bool) else self.ssh,
        )

    def _update(
        self,
        on_state: Callable[[], None],
        *,
        online: bool,
        reason: str = "",
        cmux: bool | None = None,
        ssh: bool | None = None,
    ) -> None:
        """Take a new state, and run ``on_state`` when it differs from the last.

        A ``None`` fact keeps the last one. ``since`` moves only when ``online`` does.
        """
        cmux = self.cmux if cmux is None else cmux
        ssh = self.ssh if ssh is None else ssh
        if (self.online, self.reason, self.cmux, self.ssh) == (
            online,
            reason,
            cmux,
            ssh,
        ):
            return
        if self.online != online:
            self.since = self.clock()
        self.online, self.reason, self.cmux, self.ssh = online, reason, cmux, ssh
        on_state()

    async def get_json(self, path: str) -> dict[str, Any]:
        """``GET path`` on the host.

        Raises:
            HostUnreachable: When the host is offline, silent or refuses.
        """
        return await self._call("GET", path)

    async def post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST path`` on the host. Raises :class:`HostUnreachable` as :meth:`get_json`."""
        return await self._call("POST", path, body)

    async def delete_json(self, path: str) -> dict[str, Any]:
        """``DELETE path`` on the host. Raises :class:`HostUnreachable` as :meth:`get_json`."""
        return await self._call("DELETE", path)

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.online:
            raise HostUnreachable(f"{self.name} is offline")
        try:
            async with self.session().request(
                method,
                self.url(path),
                json=body,
                timeout=aiohttp.ClientTimeout(total=REQUEST_SECS),
            ) as response:
                reply = await response.json()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as exc:
            raise HostUnreachable(f"{self.name}: {_words(exc)}") from exc
        if response.status >= 400:
            error = reply.get("error") if isinstance(reply, dict) else None
            message = (error or {}).get("message") or response.reason
            raise HostUnreachable(f"{self.name}: {message}")
        return reply

    async def await_operation(self, operation_id: str) -> dict[str, Any]:
        """The host's operation ``operation_id`` once it has ended.

        Raises:
            HostUnreachable: When the host goes away, or the wait runs out.
        """
        deadline = asyncio.get_running_loop().time() + OPERATION_SECS
        while asyncio.get_running_loop().time() < deadline:
            reply = await self.get_json("/api/operations")
            found = next(
                (o for o in reply.get("operations", []) if o["id"] == operation_id),
                None,
            )
            if found is None:
                raise HostUnreachable(f"{self.name} lost operation {operation_id}")
            if found["state"] != RUNNING:
                return found
            await asyncio.sleep(OPERATION_POLL_SECS)
        raise HostUnreachable(f"{self.name}: operation {operation_id} ran too long")

    async def forward(self, request: web.Request) -> web.StreamResponse:
        """Send ``request`` to the same path on the host, and stream its reply back.

        The method, the path, the query and the body go unchanged. The
        browser's ``Origin`` and credentials do not. ``X-Forwarded-For`` gets
        the browser's IP appended, so the host's **Audit record** names it.
        """
        if not self.online:
            raise HostUnreachable(f"{self.name} is offline")
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._forward_socket(request)
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _DROPPED_REQUEST and k.lower() != "x-forwarded-for"
        }
        forwarded_for = ", ".join(
            v for v in (request.headers.get("X-Forwarded-For"), request.remote) if v
        )
        if forwarded_for:
            headers["X-Forwarded-For"] = forwarded_for
        body = await request.read()
        try:
            async with self.session().request(
                request.method,
                self.url(request.path_qs),
                headers=headers,
                data=body or None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(sock_connect=5.0, sock_read=60.0),
            ) as upstream:
                response = web.StreamResponse(
                    status=upstream.status,
                    reason=upstream.reason,
                    headers={
                        k: v
                        for k, v in upstream.headers.items()
                        if k.lower() not in _DROPPED_RESPONSE
                    },
                )
                await response.prepare(request)
                try:
                    async for chunk in upstream.content.iter_any():
                        await response.write(chunk)
                    await response.write_eof()
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                    # The status is sent, so no error reply can follow: the
                    # browser sees the body end short.
                    log.warning(
                        "remote host %s: forward cut: %s", self.name, _words(exc)
                    )
                return response
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            raise HostUnreachable(f"{self.name}: {_words(exc)}") from exc

    async def _forward_socket(self, request: web.Request) -> web.WebSocketResponse:
        """Join the browser's socket to one on the host, frame for frame."""
        try:
            upstream = await asyncio.wait_for(
                self.session().ws_connect(self.url(request.path_qs), heartbeat=20.0),
                REQUEST_SECS,
            )
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
            raise HostUnreachable(f"{self.name}: {_words(exc)}") from exc
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)

        async def pump(
            source: Any, sink: web.WebSocketResponse | aiohttp.ClientWebSocketResponse
        ) -> None:
            async for message in source:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await sink.send_str(message.data)
                elif message.type == aiohttp.WSMsgType.BINARY:
                    await sink.send_bytes(message.data)
                else:
                    break

        down = asyncio.ensure_future(pump(upstream, ws))
        up = asyncio.ensure_future(pump(ws, upstream))
        try:
            await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (down, up):
                task.cancel()
            await asyncio.gather(down, up, return_exceptions=True)
            await upstream.close()
            await ws.close()
        return ws

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


async def _sse_events(
    content: aiohttp.StreamReader,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Each ``(event, data)`` on a server-sent event stream. Pings are skipped."""
    name: str | None = None
    data: list[str] = []
    async for raw in content:
        line = raw.decode().rstrip("\r\n")
        if line == "":
            if name is not None:
                try:
                    parsed = json.loads("\n".join(data)) if data else {}
                except ValueError:
                    parsed = {}
                yield name, parsed if isinstance(parsed, dict) else {}
            name, data = None, []
            continue
        if line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value.lstrip()
        if key == "event":
            name = value
        elif key == "data":
            data.append(value)


def _words(exc: BaseException) -> str:
    """``exc`` for a log line or a reply. A timeout's own text is empty."""
    return str(exc) or type(exc).__name__
