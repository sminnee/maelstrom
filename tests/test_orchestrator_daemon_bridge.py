"""The real agent-host client, against a throwaway socket server.

The scripted fake drives every server test; this is the one place the socket
framing and the three error replies the server's code mapping relies on are
exercised for real.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from maelstrom.orchestrator.daemon_bridge import SocketAsyncDaemonClient


async def _serve(handler, body):
    """Run ``body`` against a server whose connections ``handler`` answers."""
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        path = str(Path(tmp) / "d.sock")
        server = await asyncio.start_unix_server(handler, path)
        try:
            return await body(SocketAsyncDaemonClient(path, autostart=False))
        finally:
            server.close()
            await server.wait_closed()


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
