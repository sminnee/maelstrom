"""The HTTP face of the orchestrator server: one aiohttp app, every route.

The adapter between :class:`~maelstrom.orchestrator.server.Orchestrator` and
the network. The orchestrator knows nothing about HTTP; this module knows
nothing about the world. ``docs/dev/orchestrator-server.md`` documents the
wire protocol the routes speak.
"""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiohttp import web

from .server import Orchestrator

#: The largest frame a client may send. Commands are small; this is a guard.
MAX_CLIENT_MESSAGE = 1 << 20

#: Where the app keeps the orchestrator it serves.
ORCH = web.AppKey("orch", Orchestrator)


def build_app(orch: Orchestrator) -> web.Application:
    """The app that serves ``orch``: its routes, and its start and stop.

    The orchestrator starts with the app and stops with it, so anything that
    runs the app — the CLI, a test — gets a live world without wiring it.
    """
    app = web.Application()
    app[ORCH] = orch

    async def on_startup(_app: web.Application) -> None:
        await orch.start()

    async def on_cleanup(_app: web.Application) -> None:
        await orch.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", _world_socket)
    return app


async def _world_socket(request: web.Request) -> web.WebSocketResponse:
    """The world over one WebSocket: hello, snapshot or replay, ready, commands."""
    ws = web.WebSocketResponse(max_msg_size=MAX_CLIENT_MESSAGE)
    await ws.prepare(request)
    orch = request.app[ORCH]
    await orch.handle_connection(ws)
    return ws


@asynccontextmanager
async def serving(app: web.Application, host: str, port: int) -> AsyncIterator[int]:
    """Serve ``app`` for the length of the block, yielding the bound port.

    ``port`` 0 picks a free one. The socket binds before the orchestrator
    starts, so a port in use fails at once rather than after the first source
    reads. A client that connects before those reads finish waits for them.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    runner = web.AppRunner(app)
    started = False
    try:
        sock.bind((host, port))
        await runner.setup()
        await web.SockSite(runner, sock).start()
        started = True
        yield sock.getsockname()[1]
    finally:
        # Until the site owns the socket, nothing else closes it on a failure.
        if not started:
            sock.close()
        await runner.cleanup()


async def serve_app(app: web.Application, host: str, port: int) -> None:
    """Serve until cancelled."""
    async with serving(app, host, port):
        await asyncio.Event().wait()
