"""The NDJSON round trip between the CLI and the daemon, over a real socket.

One reply per connection, one line each. The line can be large: a stopped
listing carries every resumable session on the machine, which is hundreds of
rows.
"""

import asyncio
import json

import pytest

from maelstrom.agent_transport import SocketDaemonClient, request_over_socket


@pytest.fixture()
def socket_path(tmp_path) -> str:
    return str(tmp_path / "agent-daemon.sock")


async def _serve(socket_path: str, reply: dict):
    """A daemon stand-in that answers one command with ``reply``."""

    async def on_client(reader, writer):
        await reader.readline()
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        writer.close()

    return await asyncio.start_unix_server(on_client, socket_path)


def _round_trip(socket_path: str, reply: dict) -> dict:
    async def run():
        server = await _serve(socket_path, reply)
        try:
            return await request_over_socket(
                socket_path, {"cmd": "list"}, autostart=False
            )
        finally:
            server.close()
            await server.wait_closed()

    return asyncio.run(run())


def test_a_reply_larger_than_the_default_stream_limit_round_trips(socket_path):
    """``asyncio`` reads 64 KiB per line by default, and a listing exceeds it.

    ``mael agent list --stopped`` returns one row per resumable session. On a
    working machine that is hundreds of rows and well over 64 KiB, so a default
    reader raises ``LimitOverrunError`` and the command dies in a traceback.
    """
    rows = [{"id": f"s{i}", "label": "x" * 200} for i in range(1000)]
    reply = _round_trip(socket_path, {"agents": rows})
    assert "error" not in reply
    assert len(reply["agents"]) == 1000


def test_a_large_reply_round_trips_through_the_blocking_client(socket_path):
    """``SocketDaemonClient`` is what every CLI command actually uses."""
    rows = [{"id": f"s{i}", "label": "x" * 200} for i in range(1000)]

    async def run():
        server = await _serve(socket_path, {"agents": rows})
        try:
            client = SocketDaemonClient(socket_path, autostart=False)
            return await asyncio.get_running_loop().run_in_executor(
                None, client.request, {"cmd": "list"}
            )
        finally:
            server.close()
            await server.wait_closed()

    assert len(asyncio.run(run())["agents"]) == 1000
