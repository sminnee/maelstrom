"""The **Secret store** routes: unlock, set, list and remove.

No route here returns a value, and none ever may: an agent holds the CLI's
Host key. See ``docs/adr/0007-dev-env-secrets-in-a-brokered-store.md``.
"""

import asyncio
from typing import Any

from aiohttp import web

from mael_domain.secrets import (
    SecretError,
    SecretInfo,
    SecretVault,
    StoreLocked,
    UnknownSecret,
    WrongPassphrase,
)

from .errors import BadBody, error_response, read_body

#: The server's one vault.
VAULT = web.AppKey("vault", SecretVault)
#: One unlock at a time: each takes scrypt's memory and a worker thread, and a
#: caller that guesses must not fill the pool the rest of the server uses.
_UNLOCKING = web.AppKey("unlocking", asyncio.Lock)

_CODES: dict[type[Exception], str] = {
    StoreLocked: "secret_store_locked",
    WrongPassphrase: "wrong_passphrase",
    UnknownSecret: "unknown_id",
}


def add_secret_routes(app: web.Application, vault: SecretVault) -> None:
    app[VAULT] = vault
    app[_UNLOCKING] = asyncio.Lock()
    app.router.add_get("/api/secret-store", _state)
    app.router.add_post("/api/secret-store/unlock", _unlock)
    app.router.add_get("/api/secrets", _list)
    app.router.add_put("/api/secrets/{name}", _set)
    app.router.add_delete("/api/secrets/{name}", _remove)


def _refusal(exc: SecretError | BadBody) -> web.Response:
    return error_response(_CODES.get(type(exc), "invalid"), str(exc))


def _state_body(vault: SecretVault) -> dict[str, Any]:
    return {"exists": vault.exists, "locked": vault.locked}


def _row(info: SecretInfo) -> dict[str, Any]:
    return {"name": info.name, "project": info.project, "setAt": info.set_at}


def _text(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str):
        raise BadBody(f"{field} is required")
    return value


def _project(raw: object) -> str | None:
    """The scope a request names: a project, or ``None`` for the global scope."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise BadBody("project must be a string")
    return raw


async def _state(request: web.Request) -> web.Response:
    return web.json_response(_state_body(request.app[VAULT]))


async def _unlock(request: web.Request) -> web.Response:
    """Unlock the store; ``create`` makes it first and sets its passphrase."""
    vault = request.app[VAULT]
    try:
        body = await read_body(request)
        passphrase, create = _text(body, "passphrase"), body.get("create") is True
        async with request.app[_UNLOCKING]:
            await asyncio.to_thread(vault.unlock, passphrase, create=create)
    except (SecretError, BadBody) as exc:
        return _refusal(exc)
    return web.json_response(_state_body(vault))


async def _list(request: web.Request) -> web.Response:
    """Each Secret's name, scope and set date. Works on a locked store."""
    vault = request.app[VAULT]
    secrets = await asyncio.to_thread(vault.list)
    return web.json_response(
        {"locked": vault.locked, "secrets": [_row(s) for s in secrets]}
    )


async def _set(request: web.Request) -> web.Response:
    vault = request.app[VAULT]
    try:
        body = await read_body(request)
        info = await asyncio.to_thread(
            vault.set,
            request.match_info["name"],
            _text(body, "value"),
            _project(body.get("project")),
        )
    except (SecretError, BadBody) as exc:
        return _refusal(exc)
    return web.json_response(_row(info))


async def _remove(request: web.Request) -> web.Response:
    vault = request.app[VAULT]
    try:
        await asyncio.to_thread(
            vault.remove,
            request.match_info["name"],
            _project(request.query.get("project")),
        )
    except SecretError as exc:
        return _refusal(exc)
    return web.json_response({})
