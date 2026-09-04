"""The HTTP face of the orchestrator server: one aiohttp app, every route.

The adapter between :class:`~maelstrom.orchestrator.server.Orchestrator` and
the network. The orchestrator knows nothing about HTTP; this module knows
nothing about the world beyond which table each route reads.
``docs/dev/orchestrator-server.md`` documents what the routes speak.
"""

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import web

from .protocol import document_row, task_row
from .server import Orchestrator

#: The largest frame a client may send. Commands are small; this is a guard.
MAX_CLIENT_MESSAGE = 1 << 20
#: How often an idle notice stream sends a comment, so a proxy keeps it open.
PING_SECS = 15.0
#: How long a stop waits for open streams before it cancels them.
SHUTDOWN_SECS = 1.0

#: Where the app keeps the orchestrator it serves.
ORCH = web.AppKey("orch", Orchestrator)

#: The HTTP status for each error code the server answers with.
STATUS_FOR_CODE = {
    "unknown_id": 404,
    "invalid": 400,
    "agent_exited": 409,
    "not_waiting": 409,
    "stale_request": 409,
    "wrong_wait_kind": 409,
    "stale_version": 409,
    "not_implemented": 501,
}

#: The error code for an HTTP status aiohttp raises on its own.
CODE_FOR_STATUS = {404: "unknown_id", 501: "not_implemented"}

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def build_app(orch: Orchestrator) -> web.Application:
    """The app that serves ``orch``: its routes, and its start and stop.

    The orchestrator starts with the app and stops with it, so anything that
    runs the app — the CLI, a test — gets a live world without wiring it.
    """
    app = web.Application(middlewares=[_json_errors])
    app[ORCH] = orch

    async def on_startup(_app: web.Application) -> None:
        await orch.start()

    async def on_cleanup(_app: web.Application) -> None:
        await orch.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", _world_socket)
    app.router.add_get("/api/projects", _projects)
    app.router.add_get("/api/worktrees", _worktrees)
    app.router.add_get("/api/tasks", _tasks)
    app.router.add_get("/api/tasks/{project}/{id}", _task)
    app.router.add_get("/api/agents", _agents)
    app.router.add_get("/api/agents/{id}", _agent)
    app.router.add_get("/api/attention", _attention)
    app.router.add_get("/api/documents", _documents)
    app.router.add_get("/api/documents/{id}", _document)
    app.router.add_get("/api/desk", _desk)
    app.router.add_get("/api/events", _events)
    for method, path in _NOT_IMPLEMENTED:
        app.router.add_route(method, path, _not_implemented)
    return app


#: Routes the UI has controls for and this server does not serve.
_NOT_IMPLEMENTED = (
    ("POST", "/api/documents/{id}/comments"),
    ("POST", "/api/documents/{id}/comments/{cid}/resolve"),
    ("POST", "/api/documents/{id}/approve"),
    ("POST", "/api/documents/{id}/request-changes"),
    ("POST", "/api/tasks"),
    ("POST", "/api/shaping"),
)


# -- errors --


def error_response(code: str, message: str) -> web.Response:
    """The one error shape: ``{"error": {"code", "message"}}`` at the code's status."""
    return web.json_response(
        {"error": {"code": code, "message": message}},
        status=STATUS_FOR_CODE.get(code, 400),
    )


@web.middleware
async def _json_errors(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Every refusal aiohttp raises itself — no route, wrong method — as JSON."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.status < 400:
            raise
        code = CODE_FOR_STATUS.get(exc.status, "invalid")
        body = {"error": {"code": code, "message": exc.reason or exc.text or ""}}
        return web.json_response(body, status=exc.status)


async def _not_implemented(request: web.Request) -> web.Response:
    return error_response(
        "not_implemented", f"{request.method} {request.path} is not implemented yet"
    )


# -- reads --


async def _ready(request: web.Request) -> Orchestrator:
    """The orchestrator, once its first source reads have finished."""
    orch = request.app[ORCH]
    await orch.ready.wait()
    return orch


async def _projects(request: web.Request) -> web.Response:
    orch = await _ready(request)
    return web.json_response({"projects": list(orch.world["projects"].values())})


async def _worktrees(request: web.Request) -> web.Response:
    orch = await _ready(request)
    return web.json_response({"worktrees": list(orch.world["worktrees"].values())})


async def _tasks(request: web.Request) -> web.Response:
    """Every task in every project as a slim row; the ETag answers 304.

    No server-side filter: the client holds every row and filters in memory,
    and one list keeps one cache entry.
    """
    orch = await _ready(request)
    etag = f'"{orch.epoch}-{orch.task_revision}"'
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers={"ETag": etag})
    rows = [task_row(task) for task in orch.world["tasks"].values()]
    response = web.json_response(
        {"tasks": rows, "version": orch.task_version}, headers={"ETag": etag}
    )
    response.enable_compression()
    return response


async def _task(request: web.Request) -> web.Response:
    orch = await _ready(request)
    task_id = f"{request.match_info['project']}/{request.match_info['id']}"
    task = orch.world["tasks"].get(task_id)
    if task is None:
        return error_response("unknown_id", f"No task {task_id}")
    return web.json_response(task)


async def _agents(request: web.Request) -> web.Response:
    orch = await _ready(request)
    return web.json_response({"agents": list(orch.world["agents"].values())})


async def _agent(request: web.Request) -> web.Response:
    """One agent, plus ``pendingRequest``: the item it waits on, so a decision renders alone."""
    orch = await _ready(request)
    agent_id = request.match_info["id"]
    agent = orch.world["agents"].get(agent_id)
    if agent is None:
        return error_response("unknown_id", f"No agent {agent_id}")
    return web.json_response(
        {**agent, "pendingRequest": orch.pending_request(agent_id)}
    )


async def _attention(request: web.Request) -> web.Response:
    orch = await _ready(request)
    items = list(orch.world["attention"].values())
    if request.query.get("open"):
        items = [item for item in items if item["clearedAt"] is None]
    return web.json_response({"attention": items})


async def _documents(request: web.Request) -> web.Response:
    orch = await _ready(request)
    rows = [document_row(doc) for doc in orch.world["documents"].values()]
    return web.json_response({"documents": rows})


async def _document(request: web.Request) -> web.Response:
    orch = await _ready(request)
    document_id = request.match_info["id"]
    doc = orch.world["documents"].get(document_id)
    if doc is None:
        return error_response("unknown_id", f"No document {document_id}")
    return web.json_response(doc)


async def _desk(request: web.Request) -> web.Response:
    orch = await _ready(request)
    return web.json_response({"desk": list(orch.world["desk"].values())})


# -- the notice stream --


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _events(request: web.Request) -> web.StreamResponse:
    """Change notices as server-sent events: one ``reset``, then a ``change`` per kind.

    The subscription opens before the reset is written, so nothing published
    in between is lost: a notice the client hears before it has refetched is
    one more refetch, never a missed change.
    """
    orch = await _ready(request)
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)
    with orch.notices.subscribe() as subscriber:
        await response.write(_sse("reset", {"epoch": orch.epoch}))
        while True:
            try:
                batch = await asyncio.wait_for(subscriber.next(), PING_SECS)
            except asyncio.TimeoutError:
                await response.write(b": ping\n\n")
                continue
            for kind, ids in batch.items():
                await response.write(_sse("change", {"kind": kind, "ids": sorted(ids)}))


# -- the world socket --


async def _world_socket(request: web.Request) -> web.WebSocketResponse:
    """The world over one WebSocket: hello, snapshot or replay, ready, commands."""
    ws = web.WebSocketResponse(max_msg_size=MAX_CLIENT_MESSAGE)
    await ws.prepare(request)
    orch = request.app[ORCH]
    await orch.handle_connection(ws)
    return ws


# -- running --


@asynccontextmanager
async def serving(app: web.Application, host: str, port: int) -> AsyncIterator[int]:
    """Serve ``app`` for the length of the block, yielding the bound port.

    ``port`` 0 picks a free one. The socket binds before the orchestrator
    starts, so a port in use fails at once rather than after the first source
    reads. A client that connects before those reads finish waits for them.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # A client that goes away cancels its handler, so a notice stream ends
    # with its reader rather than at the next ping.
    runner = web.AppRunner(
        app, handler_cancellation=True, shutdown_timeout=SHUTDOWN_SECS
    )
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
