"""The auth gate: who may reach the API, and the passkey routes.

See ``docs/dev/orchestrator-server.md``, "Authentication".
"""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aiohttp import hdrs, web
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from mael_domain.auth.model import (
    SIGN_IN_DAYS,
    Passkey,
    SignIn,
    hash_secret,
    new_token,
    new_verify_code,
    normalise_code,
    scope_permits,
    sign_in_expiry,
)
from mael_domain.auth.store import AuthStore

from .errors import BadBody, error_response, read_body

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

#: How long a WebAuthn challenge stays good.
CHALLENGE_SECS = 300.0
#: How many challenges may wait at once. Anyone may ask for one.
MAX_CHALLENGES = 100
#: How many passkeys may wait for the host at once. Anyone may sign up, so a
#: new one past the cap replaces the oldest.
MAX_PENDING = 5

#: The ceremony routes, open to anyone: a browser with no sign-in needs them.
_OPEN_PATHS = frozenset(
    {
        "/api/auth/status",
        "/api/auth/register/options",
        "/api/auth/register",
        "/api/auth/login/options",
        "/api/auth/login",
        "/api/auth/logout",
    }
)
#: The passkey admin routes. Only the host key opens them.
_HOST_ONLY_PREFIXES = ("/api/auth/passkeys", "/api/auth/verify")

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class AuthParty:
    """The WebAuthn relying party: the store, the UI origins, and open challenges.

    ``user_name`` is the name a passkey picker shows for this server, so two
    dev orchestrators on one host can be told apart. ``ui_origins`` are the
    origins, besides the server's own, that a signed-in page may be served
    from.
    """

    store: AuthStore
    user_name: str
    ui_origins: frozenset[str] = frozenset()
    #: Challenges issued and not yet used, with the monotonic time each ends.
    _challenges: dict[str, float] = field(default_factory=dict)

    def issue(self, challenge: bytes) -> None:
        """Remember ``challenge`` for :data:`CHALLENGE_SECS`."""
        now = time.monotonic()
        for old, ends in list(self._challenges.items()):
            if ends < now:
                del self._challenges[old]
        while len(self._challenges) >= MAX_CHALLENGES:
            del self._challenges[next(iter(self._challenges))]
        self._challenges[bytes_to_base64url(challenge)] = now + CHALLENGE_SECS

    def redeem(self, credential: dict[str, Any]) -> bytes | None:
        """The challenge ``credential`` answers, once, if this party issued it."""
        try:
            client_data = json.loads(
                base64url_to_bytes(credential["response"]["clientDataJSON"])
            )
            challenge = str(client_data["challenge"])
        except (KeyError, TypeError, ValueError):
            return None
        ends = self._challenges.pop(challenge, None)
        if ends is None or ends < time.monotonic():
            return None
        return base64url_to_bytes(challenge)

    @property
    def user_id(self) -> bytes:
        """A user handle of this server alone.

        A shared handle would make an authenticator replace one dev
        orchestrator's passkey with the next one's, since they share the RP ID.
        """
        return hashlib.sha256(self.user_name.encode()).digest()[:16]


AUTH = web.AppKey("auth", AuthParty)


def rp_id(request: web.Request) -> str:
    """The RP ID: the host name the browser dialled.

    A browser at ``localhost`` and one at the tailnet name each get passkeys
    bound to the name they see. The browser itself refuses an RP ID that is not
    its origin's host, so the request's own host is the only one that works.
    """
    return request.url.host or "localhost"


def cookie_name(request: web.Request) -> str:
    """The sign-in cookie of the server that took ``request``.

    The port is in the name: a cookie is scoped to the host, not the port, and
    each dev orchestrator on the host has its own sign-ins.
    """
    sockname = (
        request.transport.get_extra_info("sockname") if request.transport else None
    )
    port = sockname[1] if sockname else request.url.port
    return f"mael_login_{port}"


def allowed_origins(request: web.Request) -> list[str]:
    """The UI origins: the server's own origin and each configured one."""
    party = request.app[AUTH]
    own = f"{request.scheme}://{request.host}"
    return [own, *sorted(party.ui_origins - {own})]


def origin_allowed(request: web.Request) -> bool:
    origin = request.headers.get(hdrs.ORIGIN)
    return origin is not None and origin in allowed_origins(request)


def _is_websocket(request: web.Request) -> bool:
    return request.headers.get(hdrs.UPGRADE, "").lower() == "websocket"


async def _signed_in(request: web.Request) -> tuple[SignIn, Passkey] | None:
    """The request's sign-in and its passkey, when its cookie names a live one."""
    token = request.cookies.get(cookie_name(request))
    if not token:
        return None
    store = request.app[AUTH].store
    sign_in = await store.read_sign_in(hash_secret(token))
    if sign_in is None:
        return None
    if datetime.fromisoformat(sign_in.expires) <= datetime.now(timezone.utc):
        await store.delete_sign_in(sign_in.token_hash)
        return None
    passkey = await store.read_passkey(sign_in.passkey_id)
    if passkey is None:
        return None
    return sign_in, passkey


async def _with_key(request: web.Request, handler: Handler, key: str):
    found = await request.app[AUTH].store.find_api_key(hash_secret(key))
    if found is None:
        return error_response("auth_required", "The API key is not known")
    if not scope_permits(found.scope, request.method, request.path):
        return error_response(
            "forbidden", f"The {found.scope} key may not use {request.path}"
        )
    return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Refuse each ``/api`` request that carries no credential its route accepts."""
    path = request.path
    if not path.startswith("/api/"):
        return await handler(request)
    bearer = request.headers.get(hdrs.AUTHORIZATION, "")
    if bearer.lower().startswith("bearer "):
        return await _with_key(request, handler, bearer[7:].strip())
    if _is_websocket(request) and request.query.get("key"):
        return await _with_key(request, handler, request.query["key"])

    # The cookie. Other dev servers on this host are same-site, so
    # SameSite=Lax lets them send it; the Origin is what tells them apart.
    unsafe = request.method not in _SAFE_METHODS or _is_websocket(request)
    if (
        request.cookies.get(cookie_name(request))
        and unsafe
        and not origin_allowed(request)
    ):
        return error_response("forbidden", "This origin may not use the sign-in")
    if path in _OPEN_PATHS:
        return await handler(request)
    found = await _signed_in(request)
    if found is None:
        return error_response("auth_required", "Sign in with a passkey")
    if path.startswith(_HOST_ONLY_PREFIXES):
        return error_response("forbidden", "Only the host key may manage passkeys")
    if found[1].state != "verified":
        return error_response("passkey_pending", "Run `mael auth verify` on the host")
    return await handler(request)


# -- routes --


def add_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/auth/status", _status)
    app.router.add_post("/api/auth/register/options", _register_options)
    app.router.add_post("/api/auth/register", _register)
    app.router.add_post("/api/auth/login/options", _login_options)
    app.router.add_post("/api/auth/login", _login)
    app.router.add_post("/api/auth/logout", _logout)
    app.router.add_get("/api/auth/passkeys", _passkeys)
    app.router.add_post("/api/auth/verify", _verify)
    app.router.add_delete("/api/auth/passkeys/{id}", _revoke)


def _status_body(passkey: Passkey | None) -> dict[str, Any]:
    if passkey is None:
        return {"state": "anonymous"}
    if passkey.state == "pending":
        return {"state": "pending", "code": passkey.verify_code, "label": passkey.label}
    return {"state": "signed_in", "label": passkey.label}


async def _status(request: web.Request) -> web.Response:
    found = await _signed_in(request)
    return web.json_response(_status_body(found[1] if found else None))


async def _credential(request: web.Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """The body, and the WebAuthn credential it carries. Raises :class:`BadBody`."""
    body = await read_body(request)
    credential = body.get("credential")
    if not isinstance(credential, dict):
        raise BadBody("credential is required")
    return body, credential


def _label(raw: object) -> str:
    """A passkey's name, safe to print on the host's terminal.

    An anonymous caller chooses it, and ``mael auth list`` prints it, so a
    control sequence must not reach the screen.
    """
    label = "".join(ch for ch in str(raw or "") if ch.isprintable()).strip()
    return label[:80] or "Passkey"


async def _register_options(request: web.Request) -> web.Response:
    party = request.app[AUTH]
    existing = await party.store.list_passkeys()
    options = generate_registration_options(
        rp_id=rp_id(request),
        rp_name="Maelstrom",
        user_name=party.user_name,
        user_id=party.user_id,
        user_display_name=party.user_name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.id)) for p in existing
        ],
    )
    party.issue(options.challenge)
    return web.json_response(json.loads(options_to_json(options)))


async def _register(request: web.Request) -> web.Response:
    party = request.app[AUTH]
    try:
        body, credential = await _credential(request)
    except BadBody as exc:
        return error_response("invalid", str(exc))
    challenge = party.redeem(credential)
    if challenge is None:
        return error_response("invalid", "The challenge is unknown or has expired")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id(request),
            expected_origin=allowed_origins(request),
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        return error_response("invalid", f"The passkey was not accepted: {exc}")
    pending = [p for p in await party.store.list_passkeys() if p.state == "pending"]
    for oldest in pending[: max(0, len(pending) - MAX_PENDING + 1)]:
        await party.store.delete_passkey(oldest.id)
    passkey = Passkey(
        id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        label=_label(body.get("label")),
        created=datetime.now(timezone.utc).isoformat(),
        state="pending",
        verify_code=new_verify_code(),
    )
    await party.store.save_passkey(passkey)
    response = web.json_response(_status_body(passkey))
    await _sign_in(request, response, passkey)
    return response


async def _login_options(request: web.Request) -> web.Response:
    party = request.app[AUTH]
    options = generate_authentication_options(
        rp_id=rp_id(request), user_verification=UserVerificationRequirement.PREFERRED
    )
    party.issue(options.challenge)
    return web.json_response(json.loads(options_to_json(options)))


async def _login(request: web.Request) -> web.Response:
    party = request.app[AUTH]
    try:
        _, credential = await _credential(request)
    except BadBody as exc:
        return error_response("invalid", str(exc))
    passkey = await party.store.read_passkey(str(credential.get("id", "")))
    if passkey is None:
        return error_response(
            "auth_required", "This passkey is not known to this server"
        )
    challenge = party.redeem(credential)
    if challenge is None:
        return error_response("invalid", "The challenge is unknown or has expired")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id(request),
            expected_origin=allowed_origins(request),
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        return error_response("auth_required", f"The passkey was not accepted: {exc}")
    passkey.sign_count = verified.new_sign_count
    await party.store.save_passkey(passkey)
    response = web.json_response(_status_body(passkey))
    await _sign_in(request, response, passkey)
    return response


async def _sign_in(
    request: web.Request, response: web.Response, passkey: Passkey
) -> None:
    token = new_token()
    await request.app[AUTH].store.save_sign_in(
        SignIn(
            token_hash=hash_secret(token),
            passkey_id=passkey.id,
            expires=sign_in_expiry(datetime.now(timezone.utc)),
        )
    )
    response.set_cookie(
        cookie_name(request),
        token,
        max_age=SIGN_IN_DAYS * 24 * 3600,
        path="/",
        httponly=True,
        samesite="Lax",
        secure=request.secure,
    )


async def _logout(request: web.Request) -> web.Response:
    name = cookie_name(request)
    token = request.cookies.get(name)
    if token:
        await request.app[AUTH].store.delete_sign_in(hash_secret(token))
    response = web.json_response({"state": "anonymous"})
    response.del_cookie(name, path="/")
    return response


def _passkey_row(passkey: Passkey) -> dict[str, Any]:
    row = {
        "id": passkey.id,
        "label": passkey.label,
        "state": passkey.state,
        "created": passkey.created,
    }
    if passkey.state == "pending":
        row["code"] = passkey.verify_code
    return row


async def _passkeys(request: web.Request) -> web.Response:
    passkeys = await request.app[AUTH].store.list_passkeys()
    return web.json_response({"passkeys": [_passkey_row(p) for p in passkeys]})


async def _verify(request: web.Request) -> web.Response:
    store = request.app[AUTH].store
    try:
        body = await read_body(request)
    except BadBody as exc:
        return error_response("invalid", str(exc))
    code = normalise_code(str(body.get("code", "")))
    for passkey in await store.list_passkeys():
        if passkey.state == "pending" and passkey.verify_code == code:
            passkey.state = "verified"
            passkey.verify_code = ""
            await store.save_passkey(passkey)
            return web.json_response(_passkey_row(passkey))
    return error_response("unknown_id", f"No pending passkey has code {code}")


async def _revoke(request: web.Request) -> web.Response:
    store = request.app[AUTH].store
    id = request.match_info["id"]
    if await store.read_passkey(id) is None:
        return error_response("unknown_id", f"No passkey {id}")
    await store.delete_passkey(id)
    return web.json_response({"id": id})
