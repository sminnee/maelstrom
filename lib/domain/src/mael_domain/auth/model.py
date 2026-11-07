"""Who may use the orchestrator server. See ``CONTEXT.md``, "Passkey" and "API key".

Pure functions and records. :mod:`.store` keeps the records and :mod:`.keys`
keeps the key files; the server's gate in ``mael_orchestrator.auth`` applies
them to a request.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

#: What a key permits. See ``CONTEXT.md``, "Host key" and "Feedback key".
Scope = Literal["host", "live-feedback"]
HOST: Scope = "host"
LIVE_FEEDBACK: Scope = "live-feedback"
SCOPES: tuple[Scope, ...] = (HOST, LIVE_FEEDBACK)

#: The one route the feedback key opens.
LIVE_FEEDBACK_PATH = "/api/live-feedback"

#: Every API key starts with this, so a key in a log or a paste is plain to see.
KEY_PREFIX = "mael_"

#: How long a sign-in lasts.
SIGN_IN_DAYS = 30

PasskeyState = Literal["pending", "verified"]

#: The verify code's letters: no 0/O, 1/I/L, so a person reads it back right.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8


@dataclass
class Passkey:
    """One device of the server's one user.

    ``id`` is the WebAuthn credential id, base64url. A pending passkey has a
    ``verify_code``; ``mael auth verify`` with that code makes it verified.
    """

    id: str
    public_key: str
    sign_count: int
    label: str
    created: str
    state: PasskeyState
    verify_code: str


@dataclass
class ApiKey:
    """A key a non-browser client carries. Only its hash is kept."""

    hash: str
    scope: Scope
    created: str


@dataclass
class SignIn:
    """A browser's sign-in, by the hash of its cookie token."""

    token_hash: str
    passkey_id: str
    expires: str


def hash_secret(secret: str) -> str:
    """The SHA-256 hex digest of ``secret``: what the store keeps of a key or token."""
    return hashlib.sha256(secret.encode()).hexdigest()


def new_key() -> str:
    """A new API key: the prefix and 32 random bytes."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def new_token() -> str:
    """A new sign-in token for a cookie."""
    return secrets.token_urlsafe(32)


def new_verify_code() -> str:
    """A new verify code, shown as ``XXXX-XXXX``."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalise_code(code: str) -> str:
    """``code`` as :func:`new_verify_code` writes it, whatever case or dashes it came in."""
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == _CODE_LENGTH else raw


def sign_in_expiry(now: datetime) -> str:
    """When a sign-in made at ``now`` ends."""
    return (now + timedelta(days=SIGN_IN_DAYS)).isoformat()


def scope_permits(scope: Scope, method: str, path: str) -> bool:
    """Whether a key of ``scope`` may make the request ``method path``."""
    if scope == HOST:
        return True
    return method == "GET" and path == LIVE_FEEDBACK_PATH
