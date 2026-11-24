"""A software WebAuthn authenticator: an ES256 key and a credential id.

It answers the options the server sends as a browser's authenticator would,
with ``none`` attestation, so a test runs the passkey ceremonies with no real
browser.
"""

import base64
import hashlib
import json
import secrets

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

#: User present, user verified.
_UP_UV = 0x01 | 0x04
#: Attested credential data follows.
_AT = 0x40


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class SoftAuthenticator:
    """One passkey on one software device."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.sign_count = 0
        self.user_handle = ""

    @property
    def id(self) -> str:
        return b64url(self.credential_id)

    def _client_data(self, kind: str, options: dict, origin: str) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": options["challenge"],
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()

    def _auth_data(self, rp_id: str, flags: int, extra: bytes = b"") -> bytes:
        self.sign_count += 1
        return (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
            + extra
        )

    def register(self, options: dict, origin: str) -> dict:
        """The credential ``navigator.credentials.create`` returns, as JSON."""
        numbers = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        attested = (
            bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cose_key
        )
        auth_data = self._auth_data(options["rp"]["id"], _UP_UV | _AT, attested)
        self.user_handle = options["user"]["id"]
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(
                    self._client_data("webauthn.create", options, origin)
                ),
                "attestationObject": b64url(attestation),
            },
            "clientExtensionResults": {},
        }

    def sign(self, options: dict, origin: str, *, tamper: bool = False) -> dict:
        """The credential ``navigator.credentials.get`` returns, as JSON.

        ``tamper`` signs other bytes than the ones sent, as a forged
        assertion would.
        """
        client_data = self._client_data("webauthn.get", options, origin)
        auth_data = self._auth_data(options["rpId"], _UP_UV)
        signed = auth_data + hashlib.sha256(client_data).digest()
        if tamper:
            signed += b"x"
        signature = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        return {
            "id": self.id,
            "rawId": self.id,
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": self.user_handle,
            },
            "clientExtensionResults": {},
        }
