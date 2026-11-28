"""The secret routes, over a real port: what the server adds to the Secret store."""

import json

import pytest
from test_orchestrator_auth import Server, served
from test_orchestrator_server import HOST_KEY, Harness, run

from mael_domain.secret_store import InMemorySecretStore
from mael_domain.secrets import Kdf, SecretVault
from mael_orchestrator.routes import build_app, serving

pytestmark = pytest.mark.binds_socket

PASSPHRASE = "correct horse battery"
CANARY = "canary-123"
NOW = "2026-11-28T09:00:00+00:00"


class SecretHarness(Harness):
    """A harness whose app has a cheap vault over a store in memory."""

    def app(self, web_root=None):
        vault = SecretVault(
            InMemorySecretStore(), kdf=Kdf(n=2, r=1, p=1), clock=lambda: NOW
        )
        return build_app(self.orch, self.party, web_root=web_root, vault=vault)


@pytest.fixture
def harness(store):
    return SecretHarness(store)


async def unlock(host, **over):
    body = {"passphrase": PASSPHRASE, **over}
    return await host.post("/api/secret-store/unlock", body)


async def put(host, name: str, body: dict):
    async with host.session.put(f"/api/secrets/{name}", json=body) as response:
        return response.status, await response.json()


def test_the_host_sets_lists_and_removes_a_secret(harness):
    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            created = await unlock(host, create=True)
            status, row = await put(
                host, "API_KEY", {"value": CANARY, "project": "northwind"}
            )
            listed = await host.get("/api/secrets")
            removed = await host.delete("/api/secrets/API_KEY?project=northwind")
            after = await host.get("/api/secrets")
            return created, status, row, listed, removed, after

    created, status, row, listed, removed, after = run(scenario())
    assert created.body == {"exists": True, "locked": False}
    assert status == 200
    assert row == {"name": "API_KEY", "project": "northwind", "setAt": NOW}
    assert listed.body == {"locked": False, "secrets": [row]}
    assert removed.status == 200
    assert after.body["secrets"] == []


def test_no_secret_route_answers_with_a_value(harness):
    """Each secret route the app has, by each method, with a Secret in the store."""

    async def scenario():
        app = harness.app()
        paths = sorted(
            {
                route.resource.canonical.replace("{name}", "API_KEY")
                for route in app.router.routes()
                if route.resource is not None
                and route.resource.canonical.startswith("/api/secret")
            }
        )
        async with serving(app, [("127.0.0.1", None)], 0) as port:
            async with Server(port).client(HOST_KEY) as host:
                await unlock(host, create=True)
                await put(host, "API_KEY", {"value": CANARY})
                heard = []
                for path in paths:
                    for method in ("GET", "POST", "PUT", "PATCH"):
                        body = {"passphrase": PASSPHRASE, "value": CANARY}
                        async with host.session.request(
                            method, path, json=body
                        ) as response:
                            heard.append(await response.text())
                return paths, heard

    paths, heard = run(scenario())
    assert paths == [
        "/api/secret-store",
        "/api/secret-store/unlock",
        "/api/secrets",
        "/api/secrets/API_KEY",
    ]
    assert CANARY not in json.dumps(heard)


def test_the_store_is_locked_at_each_server_start(store):
    """The app's own vault, over the notebook root's files: no test vault."""
    harness = Harness(store)

    async def first_start():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            await unlock(host, create=True)
            return await put(host, "API_KEY", {"value": CANARY})

    async def second_start():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            state = await host.get("/api/secret-store")
            status, refusal = await put(host, "API_KEY", {"value": "other"})
            listed = await host.get("/api/secrets")
            return state, status, refusal, listed

    assert run(first_start())[0] == 200
    state, status, refusal, listed = run(second_start())
    assert state.body == {"exists": True, "locked": True}
    assert (status, refusal["error"]["code"]) == (409, "secret_store_locked")
    assert (listed.status, listed.body["locked"]) == (200, True)


def test_a_wrong_passphrase_is_refused(harness):
    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            await unlock(host, create=True)
            return await unlock(host, passphrase="wrong")

    refusal = run(scenario())
    assert (refusal.status, refusal.body["error"]["code"]) == (403, "wrong_passphrase")


def test_a_body_without_its_text_is_refused(harness):
    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            await unlock(host, create=True)
            return (
                await host.post("/api/secret-store/unlock", {"passphrase": 7}),
                await put(host, "API_KEY", {}),
            )

    unlock_reply, (status, body) = run(scenario())
    assert (unlock_reply.status, unlock_reply.body["error"]["code"]) == (400, "invalid")
    assert (status, body["error"]["code"]) == (400, "invalid")


def test_removing_an_unknown_secret_is_not_found(harness):
    async def scenario():
        async with served(harness) as server, server.client(HOST_KEY) as host:
            await unlock(host, create=True)
            return await host.delete("/api/secrets/ABSENT")

    reply = run(scenario())
    assert (reply.status, reply.body["error"]["code"]) == (404, "unknown_id")
