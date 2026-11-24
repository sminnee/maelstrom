"""The auth ladder: passkeys, API keys and sign-ins.

Canonical: the orchestrator server alone writes them. See
:mod:`mael_domain.auth.store` for the model. No table holds a secret: a key or
a cookie token is kept only as its SHA-256.
"""

from ..types import Migration, Rung

AUTH: tuple[Rung, ...] = (
    Migration(
        (
            # `id` is the WebAuthn credential id, base64url. The body holds
            # the public key, the label and, while pending, the verify code.
            "CREATE TABLE auth_passkeys ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX auth_passkeys_revision ON auth_passkeys (revision)",
            # `id` is the key's SHA-256. The Host key and each Delegate key
            # are rows here. `scope` is a column so the server finds the Host
            # key, or every Delegate key, with one indexed read.
            "CREATE TABLE auth_api_keys ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "scope TEXT NOT NULL DEFAULT '', "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX auth_api_keys_revision ON auth_api_keys (revision)",
            "CREATE INDEX auth_api_keys_scope ON auth_api_keys (scope)",
            # `id` is the cookie token's SHA-256. `passkey` is a column so a
            # revoke deletes the passkey's sign-ins with one indexed read.
            "CREATE TABLE auth_sign_ins ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "passkey TEXT NOT NULL DEFAULT '', "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX auth_sign_ins_revision ON auth_sign_ins (revision)",
            "CREATE INDEX auth_sign_ins_passkey ON auth_sign_ins (passkey)",
        )
    ),
)
