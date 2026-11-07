"""The auth ladder: passkeys, API keys and sign-ins.

Canonical: the orchestrator server alone writes them. See
:mod:`mael_domain.auth.store` for the model.
"""

from ..types import Migration, Rung

AUTH: tuple[Rung, ...] = (
    Migration(
        (
            # `id` is the WebAuthn credential id, base64url.
            "CREATE TABLE passkeys ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX passkeys_revision ON passkeys (revision)",
            # `id` is the key's scope: one key per scope. The body holds the
            # key's SHA-256; the key itself is never stored.
            "CREATE TABLE api_keys ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX api_keys_revision ON api_keys (revision)",
            # `id` is the cookie token's SHA-256. `passkey` is a column so a
            # revoke deletes the passkey's sign-ins with one indexed read.
            "CREATE TABLE sign_ins ("
            "id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "passkey TEXT NOT NULL DEFAULT '', "
            "body TEXT NOT NULL DEFAULT '{}')",
            "CREATE INDEX sign_ins_revision ON sign_ins (revision)",
            "CREATE INDEX sign_ins_passkey ON sign_ins (passkey)",
        )
    ),
)
