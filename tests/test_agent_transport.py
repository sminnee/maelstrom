"""The NDJSON round trip between the CLI and the daemon, over a real socket.

One reply per connection, one line each. The line can be large: a stopped
listing carries every resumable session on the machine, which is hundreds of
rows.

The socket is a ``socketpair``, not a bound path — see "Tests in an agent
sandbox" in ``CONTRIBUTING.md``.
"""

import asyncio
import json
import socket

import pytest

from maelstrom.agent_transport import (
    STREAM_LIMIT,
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
            return await request_over_socket(
                "unused.sock", {"cmd": "list"}, autostart=False
            )
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
            client = SocketDaemonClient("unused.sock", autostart=False)
            return await asyncio.get_running_loop().run_in_executor(
                None, client.request, {"cmd": "list"}
            )
        finally:
            await asyncio.wait_for(server, timeout=SERVE_TIMEOUT)

    assert len(asyncio.run(run())["agents"]) == 1000
