"""The real agent-host client, against a stand-in daemon on a socketpair.

The scripted fake drives every server test; this is the one place the socket
framing and the three error replies the server's code mapping relies on are
exercised for real.

Each connection is one ``socketpair``, served by ``handler`` as a task on the
caller's loop.
"""

import asyncio
import json
import socket
from unittest.mock import patch

from maelstrom.agent_transport import STREAM_LIMIT
from maelstrom.orchestrator.daemon_bridge import SocketAsyncDaemonClient


async def _serve(handler, body):
    """Run ``body`` against a stand-in daemon whose connections ``handler`` answers."""
    serving: list[asyncio.Task] = []

    async def fake_open(socket_path: str, *, limit: int = STREAM_LIMIT):
        client_end, daemon_end = socket.socketpair(socket.AF_UNIX)
        reader, writer = await asyncio.open_unix_connection(
            sock=daemon_end, limit=limit
        )
        serving.append(asyncio.ensure_future(handler(reader, writer)))
        return await asyncio.open_unix_connection(sock=client_end, limit=limit)

    # ``request`` reaches the socket through ``agent_transport``; ``attach``
    # goes through the bridge's own ``_connect``, which imported the helper by
    # name. Both bindings need the stand-in.
    with (
        patch("maelstrom.agent_transport.open_connection", fake_open),
        patch("maelstrom.orchestrator.daemon_bridge.open_connection", fake_open),
    ):
        try:
            return await body(SocketAsyncDaemonClient("unused.sock", autostart=False))
        finally:
            for task in serving:
                task.cancel()
            await asyncio.gather(*serving, return_exceptions=True)


def test_a_reply_line_comes_back_as_the_reply():
    async def handler(reader, writer):
        line = await reader.readline()
        payload = json.loads(line)
        writer.write((json.dumps({"echo": payload["cmd"]}) + "\n").encode())
        await writer.drain()
        writer.close()

    reply = asyncio.run(_serve(handler, lambda c: c.request({"cmd": "list"})))
    assert reply == {"echo": "list"}


def test_a_closed_connection_and_a_malformed_line_are_error_replies():
    async def close_silently(reader, writer):
        await reader.readline()
        writer.close()

    async def garbage(reader, writer):
        await reader.readline()
        writer.write(b"not json\n")
        await writer.drain()
        writer.close()

    closed = asyncio.run(_serve(close_silently, lambda c: c.request({"cmd": "x"})))
    assert "without replying" in closed["error"]
    malformed = asyncio.run(_serve(garbage, lambda c: c.request({"cmd": "x"})))
    assert "malformed" in malformed["error"]


def test_an_unreachable_socket_is_an_error_reply_not_an_exception():
    client = SocketAsyncDaemonClient("/nonexistent/d.sock", autostart=False)
    reply = asyncio.run(client.request({"cmd": "list"}))
    assert "not reachable" in reply["error"]


def test_attach_streams_lines_until_the_server_closes():
    async def handler(reader, writer):
        await reader.readline()
        for event in ({"type": "a"}, {"type": "b"}):
            writer.write((json.dumps(event) + "\n").encode())
        await writer.drain()
        writer.close()

    async def body(client):
        return [event async for event in client.attach("a1")]

    assert asyncio.run(_serve(handler, body)) == [{"type": "a"}, {"type": "b"}]


def test_attach_sends_the_cursor_only_when_it_has_one():
    """An older daemon knows no cursor, so a plain attach stays a plain attach."""
    requests = []

    async def handler(reader, writer):
        requests.append(json.loads(await reader.readline()))
        writer.close()

    async def body(client):
        async for _ in client.attach("a1"):
            pass
        async for _ in client.attach("a1", 350, "9b2e7c41"):
            pass
        return requests

    assert asyncio.run(_serve(handler, body)) == [
        {"cmd": "attach", "id": "a1"},
        {"cmd": "attach", "id": "a1", "from": 350, "epoch": "9b2e7c41"},
    ]
