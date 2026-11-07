"""The auth gate, end to end over a real port.

A bare client stands for a stranger on the tailnet. A browser is a client
with a cookie jar and an ``Origin``, and a :class:`SoftAuthenticator` for its
passkey. The host is a client with the host key.
"""

from contextlib import asynccontextmanager

import aiohttp
import pytest
from soft_authenticator import SoftAuthenticator
from test_orchestrator_server import (
    FEEDBACK_KEY,
    HOST_KEY,
    WORKTREE_PATH,
    Api,
    Harness,
    run,
)

from mael_domain.auth.model import SignIn, hash_secret
from mael_orchestrator.auth import MAX_PENDING
from mael_orchestrator.routes import serving

DEV_SERVER = "https://box.ts.net:3040"


@pytest.fixture
def harness(store):
    harness = Harness(store)
    harness.party.ui_origins = frozenset({DEV_SERVER})
    return harness


class Server:
    """The served app, and a client of each kind on it."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"

    @asynccontextmanager
    async def client(self, key: str | None = None, origin: str | None = None):
        """A client with a cookie jar, carrying ``key`` and ``origin`` if given.

        ``origin`` defaults to the server's own, as a page it serves sends.
        """
        headers = {"Origin": origin or self.url}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        async with aiohttp.ClientSession(
            base_url=self.url,
            headers=headers,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        ) as session:
            yield Api(session)


@asynccontextmanager
async def served(harness: Harness):
    async with serving(harness.app(), [("127.0.0.1", None)], 0) as port:
        yield Server(port)


async def sign_up(api: Api, device: SoftAuthenticator, origin: str) -> dict:
    options = (await api.post("/api/auth/register/options")).body
    reply = await api.post(
        "/api/auth/register",
        {"label": "Laptop", "credential": device.register(options, origin)},
    )
    assert reply.status == 200, reply.body
    return reply.body


async def sign_in(api: Api, device: SoftAuthenticator, origin: str, **kw):
    options = (await api.post("/api/auth/login/options")).body
    return await api.post(
        "/api/auth/login", {"credential": device.sign(options, origin, **kw)}
    )


async def verified_browser(server: Server, device: SoftAuthenticator):
    async with server.client() as browser, server.client(HOST_KEY) as host:
        code = (await sign_up(browser, device, server.url))["code"]
        assert (await host.post("/api/auth/verify", {"code": code})).status == 200


# -- keys --


def test_a_request_with_no_credential_is_refused(harness):
    async def scenario():
        async with served(harness) as server, server.client() as stranger:
            return (
                await stranger.get("/api/tasks"),
                await stranger.get("/api/auth/status"),
            )

    tasks, status = run(scenario())
    assert (tasks.status, tasks.body["error"]["code"]) == (401, "auth_required")
    assert status.body == {"state": "anonymous"}


def test_an_unknown_key_is_refused(harness):
    async def scenario():
        async with served(harness) as server, server.client("mael_wrong") as client:
            return await client.get("/api/tasks")

    assert run(scenario()).status == 401


def test_the_host_key_opens_each_route(harness):
    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            return (await host.get("/api/tasks")).status, (
                await host.get("/api/auth/passkeys")
            ).body

    assert run(scenario()) == (200, {"passkeys": []})


def test_the_feedback_key_opens_the_socket_and_nothing_else(harness):
    async def scenario():
        async with served(harness) as server, server.client() as client:
            tasks = await client.session.get(
                "/api/tasks", headers={"Authorization": f"Bearer {FEEDBACK_KEY}"}
            )
            query = {"path": WORKTREE_PATH, "service": "web", "key": FEEDBACK_KEY}
            async with client.session.ws_connect(
                "/api/live-feedback", params=query
            ) as ws:
                await ws.send_json({"type": "exposeParams", "params": {}})
                opened = not ws.closed
            return tasks.status, (await tasks.json())["error"]["code"], opened

    assert run(scenario()) == (403, "forbidden", True)


def test_a_key_in_the_query_opens_only_a_socket(harness):
    """A key in a URL ends up in logs, so a plain request may not carry one."""

    async def scenario():
        async with served(harness) as server, server.client() as client:
            return (await client.get(f"/api/tasks?key={HOST_KEY}")).status

    assert run(scenario()) == 401


# -- passkeys --


def test_a_new_passkey_waits_for_the_host_to_verify_its_code(harness):
    device = SoftAuthenticator()

    async def scenario():
        async with (
            served(harness) as server,
            server.client() as browser,
            server.client(HOST_KEY) as host,
        ):
            signed_up = await sign_up(browser, device, server.url)
            before = await browser.get("/api/tasks")
            listed = (await host.get("/api/auth/passkeys")).body["passkeys"]
            verified = await host.post("/api/auth/verify", {"code": signed_up["code"]})
            return (
                signed_up,
                before,
                listed,
                verified.status,
                (await browser.get("/api/auth/status")).body,
                (await browser.get("/api/tasks")).status,
            )

    signed_up, before, listed, verified, status, after = run(scenario())
    assert signed_up["state"] == "pending"
    assert (before.status, before.body["error"]["code"]) == (403, "passkey_pending")
    assert [(p["id"], p["state"], p["code"]) for p in listed] == [
        (device.id, "pending", signed_up["code"])
    ]
    assert verified == 200
    assert status == {"state": "signed_in", "label": "Laptop"}
    assert after == 200


def test_a_wrong_code_verifies_nothing(harness):
    async def scenario():
        async with (
            served(harness) as server,
            server.client() as browser,
            server.client(HOST_KEY) as host,
        ):
            await sign_up(browser, SoftAuthenticator(), server.url)
            return (await host.post("/api/auth/verify", {"code": "AAAA-AAAA"})).status

    assert run(scenario()) == 404


def test_a_signed_in_browser_may_not_admit_a_passkey(harness):
    """Only a person with a shell on the host may admit a device."""
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                assert (await sign_in(browser, device, server.url)).status == 200
                return (
                    (await browser.get("/api/auth/passkeys")).status,
                    (await browser.post("/api/auth/verify", {"code": "x"})).status,
                )

    assert run(scenario()) == (403, 403)


def test_a_verified_passkey_signs_in_on_a_new_browser(harness):
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                login = await sign_in(browser, device, server.url)
                tasks = await browser.get("/api/tasks")
                await browser.post("/api/auth/logout")
                return (
                    login.body,
                    tasks.status,
                    (await browser.get("/api/tasks")).status,
                )

    assert run(scenario()) == ({"state": "signed_in", "label": "Laptop"}, 200, 401)


def test_a_forged_assertion_signs_nobody_in(harness):
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                login = await sign_in(browser, device, server.url, tamper=True)
                return login.status, (await browser.get("/api/tasks")).status

    assert run(scenario()) == (401, 401)


def test_a_passkey_made_on_a_foreign_page_is_refused(harness):
    """Another dev server on the host shares the RP ID, but not the origin."""

    async def scenario():
        async with served(harness) as server, server.client() as browser:
            options = (await browser.post("/api/auth/register/options")).body
            credential = SoftAuthenticator().register(
                options, "https://box.ts.net:3041"
            )
            return await browser.post(
                "/api/auth/register", {"label": "x", "credential": credential}
            )

    assert run(scenario()).status == 400


def test_a_revoked_passkey_ends_its_sign_ins(harness):
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser, server.client(HOST_KEY) as host:
                await sign_in(browser, device, server.url)
                revoked = await host.delete(f"/api/auth/passkeys/{device.id}")
                return revoked.status, (await browser.get("/api/tasks")).status

    assert run(scenario()) == (200, 401)


def test_an_expired_sign_in_is_refused_and_forgotten(harness):
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            await harness.auth.save_sign_in(
                SignIn(hash_secret("old-token"), device.id, "2026-01-01T00:00:00+00:00")
            )
            async with server.client() as browser:
                browser.session.cookie_jar.update_cookies(
                    {f"mael_login_{server.port}": "old-token"}
                )
                tasks = await browser.get("/api/tasks")
            return tasks.status, await harness.auth.read_sign_in(
                hash_secret("old-token")
            )

    assert run(scenario()) == (401, None)


def test_a_challenge_answers_one_ceremony(harness):
    """A replayed assertion is what a challenge exists to stop."""
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                options = (await browser.post("/api/auth/login/options")).body
                credential = device.sign(options, server.url)
                first = await browser.post(
                    "/api/auth/login", {"credential": credential}
                )
                again = await browser.post(
                    "/api/auth/login", {"credential": credential}
                )
                return first.status, again.status

    assert run(scenario()) == (200, 400)


def test_a_sign_in_on_one_server_keeps_the_other_s(harness, store):
    """Cookies ignore the port: one name for both would let each overwrite the other."""
    device = SoftAuthenticator()
    other = Harness(store)

    async def scenario():
        async with served(harness) as server, served(other) as neighbour:
            await verified_browser(server, device)
            await verified_browser(neighbour, device)
            # One jar, as one browser: it sends each cookie to every port.
            async with server.client() as browser:
                await sign_in(browser, device, server.url)
                options = await browser.session.post(
                    f"{neighbour.url}/api/auth/login/options"
                )
                await browser.session.post(
                    f"{neighbour.url}/api/auth/login",
                    json={
                        "credential": device.sign(await options.json(), neighbour.url)
                    },
                    headers={"Origin": neighbour.url},
                )
                here = (await browser.get("/api/tasks")).status
                there = (await browser.session.get(f"{neighbour.url}/api/tasks")).status
                return here, there

    assert run(scenario()) == (200, 200)


def test_a_sign_up_past_the_cap_replaces_the_oldest_pending_passkey(harness):
    """Anyone may sign up, so the pending passkeys must not grow without end."""
    devices = [SoftAuthenticator() for _ in range(MAX_PENDING + 1)]

    async def scenario():
        async with served(harness) as server:
            for device in devices:
                async with server.client() as browser:
                    await sign_up(browser, device, server.url)
        return [p.id for p in await harness.auth.list_passkeys()]

    assert run(scenario()) == [d.id for d in devices[1:]]


def test_a_label_reaches_the_host_without_control_characters(harness):
    """`mael auth list` prints the label a stranger chose."""

    async def scenario():
        async with served(harness) as server, server.client() as browser:
            options = (await browser.post("/api/auth/register/options")).body
            credential = SoftAuthenticator().register(options, server.url)
            reply = await browser.post(
                "/api/auth/register",
                {"label": "Lap\x1b[2Jtop\n", "credential": credential},
            )
            return reply.body["label"]

    assert run(scenario()) == "Lap[2Jtop"


# -- origins --


def test_a_cookie_from_a_foreign_origin_may_not_write_or_open_a_socket(harness):
    """Another dev server on the host is same-site, so the cookie goes with it."""
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                await sign_in(browser, device, server.url)
                foreign = {"Origin": "https://box.ts.net:3041"}
                write = await browser.session.post(
                    "/api/desk", json={}, headers=foreign
                )
                with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                    await browser.session.ws_connect(
                        "/api/agents/ag1/stream", headers=foreign
                    )
                # A UI origin may write, and an `<img>` GET carries no origin.
                ui = await browser.session.post(
                    "/api/desk", json={}, headers={"Origin": DEV_SERVER}
                )
                image = await browser.session.get(
                    "/api/files/nothing", skip_auto_headers=["Origin"]
                )
                return write.status, refused.value.status, ui.status, image.status

    # The handlers answer past the gate: the desk refuses an empty body, and
    # there is no such file.
    assert run(scenario()) == (403, 403, 400, 404)


def test_a_header_that_is_not_a_bearer_does_not_skip_the_origin_check(harness):
    """Only a key replaces the cookie. Any other header leaves the cookie in charge."""
    device = SoftAuthenticator()

    async def scenario():
        async with served(harness) as server:
            await verified_browser(server, device)
            async with server.client() as browser:
                await sign_in(browser, device, server.url)
                write = await browser.session.post(
                    "/api/desk",
                    json={},
                    headers={
                        "Origin": "https://box.ts.net:3041",
                        "Authorization": "Basic eDp5",
                    },
                )
                return write.status

    assert run(scenario()) == 403


@pytest.mark.parametrize(
    ("origin", "allowed"), [(DEV_SERVER, True), ("https://box.ts.net:3041", False)]
)
def test_only_a_ui_origin_may_read_the_change_stream(harness, origin, allowed):
    """The dev server's page dials the stream across origins, with its cookie."""

    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as client:
            async with client.session.get(
                "/api/events", headers={"Origin": origin}
            ) as response:
                return {
                    "allow": response.headers.get("Access-Control-Allow-Origin"),
                    "credentials": response.headers.get(
                        "Access-Control-Allow-Credentials"
                    ),
                    "vary": response.headers.get("Vary"),
                }

    expected = (
        # Without Vary a cache could serve one origin's answer to another.
        {"allow": origin, "credentials": "true", "vary": "Origin"}
        if allowed
        else {"allow": None, "credentials": None, "vary": None}
    )
    assert run(scenario()) == expected
