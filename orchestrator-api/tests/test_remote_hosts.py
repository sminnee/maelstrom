"""The parent's link to a remote host, against a stub host on loopback."""

import asyncio
from contextlib import asynccontextmanager, suppress

from aiohttp import web

from mael_orchestrator import remote_hosts
from mael_orchestrator.remote_hosts import (
    MERGED_KINDS,
    RemoteHost,
    resolve_addresses,
)


class StubHost:
    """A host whose event stream can be cut and refused, and names it ``prefix``.

    ``send`` writes a ``change`` frame onto the open stream.
    """

    def __init__(self, *, reset: bool = True, prefix: str = "cobalt") -> None:
        self.reset = reset
        self.prefix = prefix
        self.up = True
        self.cut = asyncio.Event()
        self.changes: asyncio.Queue[str] = asyncio.Queue()

    async def events(self, request: web.Request) -> web.StreamResponse:
        if not self.up:
            raise web.HTTPServiceUnavailable()
        response = web.StreamResponse()
        await response.prepare(request)
        if self.reset:
            data = f'{{"epoch": "e1", "idPrefix": "{self.prefix}"}}'
            await response.write(f"event: reset\ndata: {data}\n\n".encode())
        else:
            await response.write(b": ping\n\n")
        cut = asyncio.ensure_future(self.cut.wait())
        while True:
            change = asyncio.ensure_future(self.changes.get())
            await asyncio.wait({cut, change}, return_when=asyncio.FIRST_COMPLETED)
            if not change.done():
                change.cancel()
                return response
            data = f'{{"kind": "{change.result()}"}}'
            await response.write(f"event: change\ndata: {data}\n\n".encode())

    def send(self, kind: str) -> None:
        self.changes.put_nowait(kind)

    async def empty(self, request: web.Request) -> web.Response:
        _route, key = MERGED_ROUTES[request.path]
        return web.json_response({key: []})

    def go_down(self) -> None:
        self.up = False
        self.cut.set()

    def come_up(self) -> None:
        self.cut = asyncio.Event()
        self.up = True


@asynccontextmanager
async def served(stub: StubHost):
    app = web.Application()
    app.router.add_get("/api/events", stub.events)
    for path in MERGED_ROUTES:
        app.router.add_get(path, stub.empty)
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


#: Each merged kind's list route, by path, as the host serves it.
MERGED_ROUTES = {path: (kind, key) for kind, (path, key) in MERGED_KINDS.items()}


async def noop_change(_host, _kinds) -> None:
    return None


@asynccontextmanager
async def linked(stub: StubHost, on_change=noop_change):
    """Run a link named ``cobalt`` to ``stub``; yield it and its flips."""
    flips: list[bool] = []
    async with served(stub) as url:
        host = RemoteHost("cobalt", url, backoff=0.02)
        task = asyncio.create_task(
            host.run(lambda: flips.append(host.online), on_change)
        )
        try:
            yield host, flips
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await host.close()


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def test_a_host_goes_offline_on_a_drop_and_online_again_when_it_answers():
    async def scenario():
        stub = StubHost()
        async with linked(stub) as (_host, flips):
            await until(lambda: flips == [True])
            stub.go_down()
            await until(lambda: flips == [True, False])
            # Several refused retries pass; none flips the host.
            await asyncio.sleep(0.1)
            stub.come_up()
            await until(lambda: flips == [True, False, True])
        return flips

    assert asyncio.run(scenario()) == [True, False, True]


def test_a_stream_that_never_resets_leaves_the_host_offline():
    async def scenario():
        async with linked(StubHost(reset=False)) as (host, flips):
            await asyncio.sleep(0.2)
            return host.online, flips

    assert asyncio.run(scenario()) == (False, [])


def test_a_host_whose_prefix_is_not_its_name_stays_offline():
    """Its ids would not route back to it: ``hosts:`` names it wrongly."""

    async def scenario():
        async with linked(StubHost(prefix="zinc")) as (host, flips):
            await asyncio.sleep(0.2)
            return host.online, flips

    assert asyncio.run(scenario()) == (False, [])


def test_a_reset_reports_every_kind_and_a_change_reports_its_own():
    """A kind the parent does not merge is ignored."""

    async def scenario():
        stub = StubHost()
        reports: list[set[str]] = []

        async def on_change(_host, kinds):
            reports.append(kinds)

        async with linked(stub, on_change):
            await until(lambda: len(reports) == 1)
            stub.send("worktree")
            stub.send("nonsense")
            stub.send("agent")
            await until(lambda: len(reports) == 3)
        return reports

    assert asyncio.run(scenario()) == [
        {"agent", "document", "attention", "worktree", "gateJob"},
        {"worktree"},
        {"agent"},
    ]


def test_an_on_change_error_is_logged_and_the_link_keeps_running(caplog):
    async def scenario():
        stub = StubHost()
        reports: list[set[str]] = []

        async def on_change(_host, kinds):
            reports.append(kinds)
            if len(reports) == 1:
                raise ValueError("a bad row")

        async with linked(stub, on_change) as (host, flips):
            await until(lambda: len(reports) == 1)
            stub.send("agent")
            await until(lambda: len(reports) == 2)
            return reports[1], host.online, flips

    assert asyncio.run(scenario()) == ({"agent"}, True, [True])
    assert "a bad row" in caplog.text


def test_a_hosts_addresses_are_kept_for_a_minute_then_read_again(monkeypatch):
    asked: list[str] = []

    async def resolve(host: str) -> set[str]:
        asked.append(host)
        return {f"100.64.0.{len(asked)}"}

    now = [1000.0]
    monkeypatch.setattr(remote_hosts.time, "monotonic", lambda: now[0])
    host = RemoteHost("laptop", "https://laptop.ts.net:2772", resolve=resolve)

    async def scenario():
        first = await host.addresses()
        now[0] += 59
        kept = await host.addresses()
        now[0] += 2
        return first, kept, await host.addresses()

    first, kept, again = asyncio.run(scenario())
    assert (first, kept, again) == ({"100.64.0.1"}, {"100.64.0.1"}, {"100.64.0.2"})
    assert asked == ["laptop.ts.net", "laptop.ts.net"]


def test_resolving_finds_a_name_and_gives_nothing_for_an_unknown_one():
    async def scenario():
        return await resolve_addresses("localhost"), await resolve_addresses(
            "nohost.invalid"
        )

    local, unknown = asyncio.run(scenario())
    assert "127.0.0.1" in local
    assert unknown == set()
