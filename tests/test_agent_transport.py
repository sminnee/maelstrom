"""The NDJSON round trip between the CLI and the daemon, over a real socket.

One reply per connection, one line each. The line can be large: a stopped
listing carries every resumable session on the machine, which is hundreds of
rows.

The socket is a ``socketpair``, not a bound path — see "Tests in an agent
sandbox" in ``CONTRIBUTING.md``.
"""

import asyncio
import errno
import json
import os
import socket

import pytest

from maelstrom.agent_transport import (
    KIND_DENIED,
    KIND_UNREACHABLE,
    STREAM_LIMIT,
    UNREACHABLE_MARKER,
    SocketAsyncDaemonClient,
    SocketDaemonClient,
    request_over_socket,
)

#: How long the stand-in daemon may take to answer. A transport that stops
#: writing must fail the test, not hang the suite.
SERVE_TIMEOUT = 5.0


@pytest.fixture()
def connected_pair(monkeypatch):
    """Answer the next connection from a stand-in daemon on a socketpair.

    Call ``connected_pair(reply)`` to route :func:`open_connection` to one end
    of a fresh pair, and serve ``reply`` on the other. Returns the serving task
    so a test can await the exchange finishing.
    """

    def install(reply: dict):
        client_end, daemon_end = socket.socketpair(socket.AF_UNIX)

        async def fake_open(socket_path: str, *, limit: int = STREAM_LIMIT):
            return await asyncio.open_unix_connection(sock=client_end, limit=limit)

        monkeypatch.setattr("maelstrom.agent_transport.open_connection", fake_open)

        async def serve():
            reader, writer = await asyncio.open_unix_connection(sock=daemon_end)
            await reader.readline()
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
            writer.close()

        return asyncio.ensure_future(serve())

    return install


def test_a_reply_larger_than_the_default_stream_limit_round_trips(connected_pair):
    """``asyncio`` reads 64 KiB per line by default, and a listing exceeds it.

    ``mael agent list --stopped`` returns one row per resumable session. On a
    working machine that is hundreds of rows and well over 64 KiB, so a default
    reader raises ``LimitOverrunError`` and the command dies in a traceback.
    """
    rows = [{"id": f"s{i}", "label": "x" * 200} for i in range(1000)]

    async def run():
        server = connected_pair({"agents": rows})
        try:
            return await request_over_socket("unused.sock", {"cmd": "list"})
        finally:
            await asyncio.wait_for(server, timeout=SERVE_TIMEOUT)

    reply = asyncio.run(run())
    assert "error" not in reply
    assert len(reply["agents"]) == 1000


def test_a_large_reply_round_trips_through_the_blocking_client(connected_pair):
    """``SocketDaemonClient`` is what every CLI command actually uses."""
    rows = [{"id": f"s{i}", "label": "x" * 200} for i in range(1000)]

    async def run():
        server = connected_pair({"agents": rows})
        try:
            client = SocketDaemonClient("unused.sock")
            return await asyncio.get_running_loop().run_in_executor(
                None, client.request, {"cmd": "list"}
            )
        finally:
            await asyncio.wait_for(server, timeout=SERVE_TIMEOUT)

    assert len(asyncio.run(run())["agents"]) == 1000


class TestNothingStartsADaemon:
    """No client ever spawns a daemon.

    The everyday daemon was replaced four times by a worktree's test code,
    because the worktree's orchestrator noticed the socket was gone first and
    started one. The starting command chose the code, so whoever noticed first
    won. Now a missing daemon is an error that names the root and the command
    that starts one.
    """

    def test_a_request_to_a_missing_socket_returns_an_error_reply(self, tmp_path):
        """Unreachability is data, not an exception: the CLI, the launcher and
        the bridge all read `reply["error"]`."""
        missing = str(tmp_path / "gone" / "agent-daemon.sock")
        reply = asyncio.run(request_over_socket(missing, {"cmd": "list"}))
        assert "No agent daemon on" in reply["error"]
        assert str(tmp_path / "gone") in reply["error"]
        assert "mael self-env start" in reply["error"]
        assert "mael env start" in reply["error"]

    def test_a_request_to_a_missing_socket_spawns_nothing(self, tmp_path, monkeypatch):
        spawned = []
        monkeypatch.setattr(
            "subprocess.Popen", lambda *a, **k: spawned.append(a) or None
        )
        missing = str(tmp_path / "gone" / "agent-daemon.sock")
        asyncio.run(request_over_socket(missing, {"cmd": "list"}))
        assert spawned == []

    def test_the_blocking_client_spawns_nothing_either(self, tmp_path, monkeypatch):
        """`SocketDaemonClient` is what every CLI command actually uses."""
        spawned = []
        monkeypatch.setattr(
            "subprocess.Popen", lambda *a, **k: spawned.append(a) or None
        )
        missing = str(tmp_path / "gone" / "agent-daemon.sock")
        reply = SocketDaemonClient(missing).request({"cmd": "list"})
        assert "No agent daemon on" in reply["error"]
        assert spawned == []


@pytest.fixture()
def deny_connect(monkeypatch):
    """Fail the next connect with `code`, raised as a bare `OSError`.

    Raising `OSError(EPERM, ...)` rather than `PermissionError` directly is the
    point: CPython maps the errno to the subclass, and that mapping is what
    picks the message. A test raising the subclass would assume it.
    """

    def deny(code):
        async def refuse(*args, **kwargs):
            raise OSError(code, os.strerror(code))

        monkeypatch.setattr("maelstrom.agent_transport.open_connection", refuse)

    return deny


class TestADeniedConnectIsNotAnAbsentDaemon:
    """A sandbox denies the connect; the daemon is alive on the other side."""

    @pytest.mark.parametrize("code", [errno.EPERM, errno.EACCES])
    def test_a_denied_connect_names_the_denial_and_drops_the_marker(
        self, tmp_path, monkeypatch, deny_connect, code
    ):
        """`agent_cli` reads the marker as "no daemon holds these agents"."""
        socket_path = str(tmp_path / "agent-daemon.sock")
        deny_connect(code)
        reply = asyncio.run(request_over_socket(socket_path, {"cmd": "list"}))
        assert "permission denied" in reply["error"].lower()
        assert socket_path in reply["error"]
        assert UNREACHABLE_MARKER not in reply["error"]

    def test_an_absent_socket_still_reads_as_an_absent_daemon(self, tmp_path):
        """The reframing must not swallow the case it split away from."""
        missing = str(tmp_path / "gone" / "agent-daemon.sock")
        reply = asyncio.run(request_over_socket(missing, {"cmd": "list"}))
        assert UNREACHABLE_MARKER in reply["error"]


class TestEveryConnectSiteTellsTheCasesApart:
    """All three connect sites map a failure the same way.

    `request_over_socket` is one of three. `AsyncDaemonClient.attach` and the
    orchestrator's `SocketAsyncDaemonClient.attach` open their own connections,
    so a denial reported correctly by one and as an absent daemon by another is
    the same trap in a different place.
    """

    def test_the_async_attach_names_a_denial(self, tmp_path, deny_connect):
        socket_path = str(tmp_path / "agent-daemon.sock")
        deny_connect(errno.EPERM)

        async def first_line():
            async for line in SocketAsyncDaemonClient(socket_path).attach("a1"):
                return line
            return {}

        reply = asyncio.run(first_line())
        assert "permission denied" in reply["error"].lower()
        assert UNREACHABLE_MARKER not in reply["error"]

    def test_the_async_attach_still_names_an_absent_daemon(self, tmp_path):
        missing = str(tmp_path / "gone" / "agent-daemon.sock")

        async def first_line():
            async for line in SocketAsyncDaemonClient(missing).attach("a1"):
                return line
            return {}

        reply = asyncio.run(first_line())
        assert UNREACHABLE_MARKER in reply["error"]


class TestTheReplyCarriesTheKind:
    """Callers branch on `kind`, not on the wording of `error`.

    `agent_cli` used to decide "does a daemon hold these agents?" by looking
    for a substring in the message. Rewording any transport error could put
    that substring where it did not belong, and `gc` would kill live agents.
    """

    def test_a_denial_is_kind_denied(self, tmp_path, deny_connect):
        deny_connect(errno.EPERM)
        reply = asyncio.run(
            request_over_socket(str(tmp_path / "agent-daemon.sock"), {"cmd": "list"})
        )
        assert reply["kind"] == KIND_DENIED

    def test_an_absent_daemon_is_kind_unreachable(self, tmp_path):
        missing = str(tmp_path / "gone" / "agent-daemon.sock")
        reply = asyncio.run(request_over_socket(missing, {"cmd": "list"}))
        assert reply["kind"] == KIND_UNREACHABLE

    def test_the_async_attach_carries_the_kind_too(self, tmp_path, deny_connect):
        deny_connect(errno.EPERM)

        async def first_line():
            async for line in SocketAsyncDaemonClient(
                str(tmp_path / "agent-daemon.sock")
            ).attach("a1"):
                return line
            return {}

        assert asyncio.run(first_line())["kind"] == KIND_DENIED

    def test_a_daemon_error_carries_no_kind(self, connected_pair):
        """Only a connect failure has a kind; a daemon's own error is its own."""

        async def run():
            server = connected_pair({"error": "no such agent: a1"})
            try:
                return await request_over_socket("unused.sock", {"cmd": "show"})
            finally:
                await asyncio.wait_for(server, timeout=SERVE_TIMEOUT)

        reply = asyncio.run(run())
        assert reply["error"] == "no such agent: a1"
        assert "kind" not in reply
