"""The HTTP face of the orchestrator server: one aiohttp app, every route.

The adapter between :class:`~maelstrom.orchestrator.server.Orchestrator` and
the network. The orchestrator knows nothing about HTTP; this module knows
nothing about the world beyond which table each route reads.
``docs/dev/orchestrator-server.md`` documents what the routes speak.
"""

import asyncio
import json
import logging
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import WSMsgType, web

from .hubs import Lagging
from .protocol import document_row, task_row
from .server import Orchestrator

log = logging.getLogger(__name__)
#: How often an idle notice stream sends a comment, so a proxy keeps it open.
PING_SECS = 15.0
#: How long a stop waits for open streams before it cancels them.
SHUTDOWN_SECS = 1.0
#: How often a transcript socket pings, so a proxy keeps it open.
HEARTBEAT_SECS = 20.0

#: Close codes a transcript socket uses; the browser reads them.
CLOSE_UNKNOWN_ID = 4404
CLOSE_LAGGING = 4409

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
    app.router.add_get("/api/projects", _projects)
    app.router.add_get("/api/worktrees", _worktrees)
    app.router.add_get("/api/tasks", _tasks)
    app.router.add_get("/api/tasks/{project}/{id}", _task)
    app.router.add_get("/api/agents", _agents)
    app.router.add_get("/api/agents/{id}", _agent)
    app.router.add_get("/api/agents/{id}/transcript", _transcript)
    app.router.add_get("/api/agents/{id}/stream", _transcript_stream)
    app.router.add_get("/api/attention", _attention)
    app.router.add_get("/api/documents", _documents)
    app.router.add_get("/api/documents/{id}", _document)
    app.router.add_get("/api/desk", _desk)
    app.router.add_get("/api/events", _events)
    app.router.add_post("/api/agents/{id}/{action}", _agent_command)
    app.router.add_post("/api/tasks/{project}/{id}/launch", _launch)
    app.router.add_post("/api/tasks/{project}/{id}/status", _set_status)
    app.router.add_patch("/api/tasks/{project}/{id}", _update_task)
    app.router.add_post("/api/desk", _desk_add)
    app.router.add_delete("/api/desk/{desk_id:.+}", _desk_remove)
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


# -- commands --


class _BadBody(Exception):
    pass


async def _body(request: web.Request) -> dict[str, Any]:
    """The JSON object a command carries; an empty body is an empty object."""
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise _BadBody("Body is not JSON") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise _BadBody("Body must be a JSON object")
    return body


async def _command(request: web.Request, build: Callable[[dict[str, Any]], dict]):
    """Run the command ``build`` makes of the body, and answer its reply.

    The command dict is what the world socket carried, so ``validate_command``
    and the host-refusal mapping apply unchanged. A refusal answers the code's
    status; a field the validator did not check being missing is the client's
    bug, answered as ``invalid``.
    """
    orch = await _ready(request)
    try:
        command = build(await _body(request))
    except _BadBody as exc:
        return error_response("invalid", str(exc))
    try:
        reply = await orch.handle_command(command)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        # A field of the wrong shape past the validator, or a server fault:
        # the client hears the former, the log keeps the latter.
        log.exception("command %s failed", command.get("type"))
        return error_response("invalid", f"Malformed command: {exc!r}")
    if not reply["ok"]:
        return error_response(reply["error"]["code"], reply["error"]["message"])
    return web.json_response(reply["result"])


#: Each agent action: the command it makes, from the agent id and the body.
_AGENT_ACTIONS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "approve": lambda agent_id, body: {
        "type": "agent.approve",
        "agentId": agent_id,
        "requestId": body.get("requestId"),
    },
    "deny": lambda agent_id, body: {
        "type": "agent.deny",
        "agentId": agent_id,
        "requestId": body.get("requestId"),
        **({"reason": body["reason"]} if "reason" in body else {}),
    },
    "answer": lambda agent_id, body: {
        "type": "agent.answer",
        "agentId": agent_id,
        "requestId": body.get("requestId"),
        **({"answers": body["answers"]} if "answers" in body else {}),
    },
    "say": lambda agent_id, body: {
        "type": "agent.say",
        "agentId": agent_id,
        **({"text": body["text"]} if "text" in body else {}),
    },
    "set-mode": lambda agent_id, body: {
        "type": "agent.setMode",
        "agentId": agent_id,
        **({"mode": body["mode"]} if "mode" in body else {}),
    },
    "stop": lambda agent_id, body: {"type": "agent.stop", "agentId": agent_id},
    "resume": lambda agent_id, body: {
        "type": "agent.resume",
        "agentId": agent_id,
        **({"text": body["text"]} if "text" in body else {}),
    },
}


async def _agent_command(request: web.Request) -> web.StreamResponse:
    action = _AGENT_ACTIONS.get(request.match_info["action"])
    if action is None:
        raise web.HTTPNotFound(reason=f"No agent action {request.match_info['action']}")
    agent_id = request.match_info["id"]
    return await _command(request, lambda body: action(agent_id, body))


def _task_id(request: web.Request) -> str:
    return f"{request.match_info['project']}/{request.match_info['id']}"


async def _launch(request: web.Request) -> web.StreamResponse:
    """Launch waits for the host's reply, as the socket command did."""
    task_id = _task_id(request)
    return await _command(
        request,
        lambda body: {
            "type": "agent.launch",
            "taskId": task_id,
            **({"model": body["model"]} if body.get("model") else {}),
        },
    )


async def _set_status(request: web.Request) -> web.StreamResponse:
    task_id = _task_id(request)
    return await _command(
        request,
        lambda body: {
            "type": "task.setStatus",
            "taskId": task_id,
            "status": body.get("status"),
        },
    )


async def _update_task(request: web.Request) -> web.StreamResponse:
    task_id = _task_id(request)
    return await _command(
        request,
        lambda body: {"type": "task.update", "taskId": task_id, "fields": body},
    )


async def _desk_add(request: web.Request) -> web.StreamResponse:
    return await _command(
        request, lambda body: {"type": "desk.add", "id": body.get("id")}
    )


async def _desk_remove(request: web.Request) -> web.StreamResponse:
    # aiohttp has already decoded the match: a desk id arrives URL-encoded and lands plain.
    desk_id = request.match_info["desk_id"]
    return await _command(request, lambda body: {"type": "desk.remove", "id": desk_id})


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


# -- transcripts --


async def _transcript(request: web.Request) -> web.Response:
    """One agent's transcript as it stands, with the seq a socket can resume from."""
    orch = await _ready(request)
    agent_id = request.match_info["id"]
    if agent_id not in orch.world["agents"]:
        return error_response("unknown_id", f"No agent {agent_id}")
    # A subagent is followed on demand; this read is the demand.
    await orch.ensure_attached(agent_id)
    return web.json_response(
        {"agentId": agent_id, **orch.transcript_snapshot(agent_id)}
    )


async def _transcript_stream(request: web.Request) -> web.WebSocketResponse:
    """One agent's transcript over a WebSocket: a snapshot or a replay, then live frames.

    The subscribe and the snapshot are one synchronous step, so no frame lands
    between them. A reader that falls a queue behind is closed ``4409`` and
    comes back with ``from``.
    """
    orch = await _ready(request)
    agent_id = request.match_info["id"]
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_SECS)
    await ws.prepare(request)
    if agent_id not in orch.world["agents"]:
        await ws.close(code=CLOSE_UNKNOWN_ID, message=b"unknown_id")
        return ws
    await orch.ensure_attached(agent_id)
    from_seq = _int_or_none(request.query.get("from"))
    with orch.transcripts.subscribe(agent_id) as subscriber:
        log = orch.transcript_log(agent_id)
        replay = log.replay_from(from_seq) if from_seq is not None else None
        if replay is None:
            opening: dict[str, Any] = {"type": "transcript.snapshot", **log.snapshot()}
        else:
            opening = {"type": "transcript.replay", "seq": log.seq, "frames": replay}
        await ws.send_json(opening)
        closed = asyncio.create_task(_until_closed(ws))
        try:
            while not ws.closed:
                nxt = asyncio.create_task(subscriber.next())
                done, _ = await asyncio.wait(
                    {nxt, closed}, return_when=asyncio.FIRST_COMPLETED
                )
                if nxt not in done:
                    nxt.cancel()
                    break
                frame = nxt.result()
                if isinstance(frame, Lagging):
                    # The reader must be out of ``receive()`` first: a close
                    # that races a receive drops the transport before the
                    # client's acknowledgement, and the client sees 1006.
                    closed.cancel()
                    await asyncio.gather(closed, return_exceptions=True)
                    await ws.close(code=CLOSE_LAGGING, message=b"lagging")
                    break
                await ws.send_json(frame)
        finally:
            closed.cancel()
    return ws


async def _until_closed(ws: web.WebSocketResponse) -> None:
    """Read the socket until the client closes it. Nothing it sends means anything."""
    async for message in ws:
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.ERROR):
            return


def _int_or_none(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


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
