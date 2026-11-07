"""The parent's link to a remote host, against a stub runner on loopback."""

import asyncio
from contextlib import asynccontextmanager

from aiohttp import web

from mael_orchestrator.remote_hosts import RemoteDaemonClient, RemoteHost


class StubRunner:
    """A runner whose event stream can be cut and refused, and which records requests."""

    def __init__(self, *, reset: bool = True) -> None:
        self.reset = reset
        self.up = True
        self.cut = asyncio.Event()
        self.requests: list[dict] = []

    async def events(self, request: web.Request) -> web.StreamResponse:
        if not self.up:
            raise web.HTTPServiceUnavailable()
        response = web.StreamResponse()
        await response.prepare(request)
        if self.reset:
            await response.write(b"event: reset\ndata: {}\n\n")
        else:
            await response.write(b": ping\n\n")
        await self.cut.wait()
        return response

    async def host_request(self, request: web.Request) -> web.Response:
        self.requests.append(await request.json())
        return web.json_response({"ok": True})

    def go_down(self) -> None:
        self.up = False
        self.cut.set()

    def come_up(self) -> None:
        self.cut = asyncio.Event()
        self.up = True


@asynccontextmanager
async def served(stub: StubRunner):
    app = web.Application()
    app.router.add_get("/api/events", stub.events)
    app.router.add_post("/api/host/request", stub.host_request)
    runner = web.AppRunner(app, shutdown_timeout=0.1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        stub.cut.set()
        await runner.cleanup()


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def test_a_host_goes_offline_on_a_drop_and_online_again_when_it_answers():
    async def scenario():
        stub = StubRunner()
        flips: list[bool] = []
        async with served(stub) as url:
            host = RemoteHost("cobalt", url, backoff=0.02)
            task = asyncio.create_task(host.run(lambda: flips.append(host.online)))
            try:
                await until(lambda: flips == [True])
                stub.go_down()
                await until(lambda: flips == [True, False])
                # Several refused retries pass; none flips the host.
                await asyncio.sleep(0.1)
                stub.come_up()
                await until(lambda: flips == [True, False, True])
            finally:
                task.cancel()
                await host.close()
        return flips

    assert asyncio.run(scenario()) == [True, False, True]


def test_a_stream_that_never_resets_leaves_the_host_offline():
    async def scenario():
        stub = StubRunner(reset=False)
        flips: list[bool] = []
        async with served(stub) as url:
            host = RemoteHost("cobalt", url, backoff=0.02)
            task = asyncio.create_task(host.run(lambda: flips.append(host.online)))
            await asyncio.sleep(0.2)
            task.cancel()
            await host.close()
        return host.online, flips

    assert asyncio.run(scenario()) == (False, [])


def test_a_request_leaves_out_the_parent_paths():
    async def scenario():
        stub = StubRunner()
        async with served(stub) as url:
            host = RemoteHost("cobalt", url, online=True)
            reply = await RemoteDaemonClient(host).request(
                {
                    "cmd": "start",
                    "cwd": "/cobalt/w",
                    "system_prompt_file": "/monolith/prompt.md",
                    "plugin_dirs": ["/monolith/plugins"],
                }
            )
            await host.close()
        return reply, stub.requests

    reply, requests = asyncio.run(scenario())
    assert reply == {"ok": True}
    assert requests == [{"cmd": "start", "cwd": "/cobalt/w"}]


def test_a_request_to_a_host_that_does_not_answer_is_unreachable():
    async def scenario():
        # Port 9 (discard) has no listener on loopback.
        host = RemoteHost("cobalt", "http://127.0.0.1:9", online=True)
        client = RemoteDaemonClient(host)
        request = await client.request({"cmd": "list"})
        frames = [frame async for frame in client.attach("r1")]
        await host.close()
        return request, frames

    request, frames = asyncio.run(scenario())
    assert request["kind"] == "unreachable"
    assert request["error"].startswith("cobalt: ")
    assert [f["kind"] for f in frames] == ["unreachable"]
