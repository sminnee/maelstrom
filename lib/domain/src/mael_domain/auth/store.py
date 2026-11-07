"""Where passkeys, API keys and sign-ins are kept.

:mod:`.model` decides what each record is; this module only stores them. One
API key per scope: a new key of a scope replaces the old one.
"""

import copy
import dataclasses
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, TypeVar

from ..state_db.db import StateDb
from ..state_db.types import Write
from .model import SCOPES, ApiKey, Passkey, Scope, SignIn

T = TypeVar("T")

PASSKEYS = "passkeys"
API_KEYS = "api_keys"
SIGN_INS = "sign_ins"


class AuthStore(ABC):
    """Passkeys, the API key of each scope, and sign-ins."""

    @abstractmethod
    async def list_passkeys(self) -> list[Passkey]:
        """Every passkey, oldest first."""

    @abstractmethod
    async def read_passkey(self, id: str) -> Passkey | None:
        """The passkey with credential id ``id``, or ``None``."""

    @abstractmethod
    async def save_passkey(self, passkey: Passkey) -> None:
        """Store ``passkey``, replacing the row with its id."""

    @abstractmethod
    async def delete_passkey(self, id: str) -> None:
        """Remove the passkey ``id`` and each of its sign-ins."""

    @abstractmethod
    async def read_api_key(self, scope: Scope) -> ApiKey | None:
        """The API key of ``scope``, or ``None``."""

    @abstractmethod
    async def save_api_key(self, key: ApiKey) -> None:
        """Store ``key`` as the one key of its scope."""

    @abstractmethod
    async def read_sign_in(self, token_hash: str) -> SignIn | None:
        """The sign-in whose token hashes to ``token_hash``, or ``None``."""

    @abstractmethod
    async def save_sign_in(self, sign_in: SignIn) -> None:
        """Store ``sign_in``."""

    @abstractmethod
    async def delete_sign_in(self, token_hash: str) -> None:
        """Remove one sign-in. Removing one that is not there does nothing."""

    async def find_api_key(self, key_hash: str) -> ApiKey | None:
        """The API key whose hash is ``key_hash``, or ``None``."""
        for scope in SCOPES:
            key = await self.read_api_key(scope)
            if key is not None and key.hash == key_hash:
                return key
        return None


class InMemoryAuthStore(AuthStore):
    """Auth records for a test. ``api_keys`` seeds the key of each scope."""

    def __init__(self, api_keys: Iterable[ApiKey] = ()) -> None:
        self._passkeys: dict[str, Passkey] = {}
        self._keys: dict[str, ApiKey] = {k.scope: k for k in api_keys}
        self._sign_ins: dict[str, SignIn] = {}

    async def list_passkeys(self) -> list[Passkey]:
        return sorted(
            (copy.copy(p) for p in self._passkeys.values()), key=lambda p: p.created
        )

    async def read_passkey(self, id: str) -> Passkey | None:
        found = self._passkeys.get(id)
        return copy.copy(found) if found else None

    async def save_passkey(self, passkey: Passkey) -> None:
        self._passkeys[passkey.id] = copy.copy(passkey)

    async def delete_passkey(self, id: str) -> None:
        self._passkeys.pop(id, None)
        for token_hash, sign_in in list(self._sign_ins.items()):
            if sign_in.passkey_id == id:
                del self._sign_ins[token_hash]

    async def read_api_key(self, scope: Scope) -> ApiKey | None:
        found = self._keys.get(scope)
        return copy.copy(found) if found else None

    async def save_api_key(self, key: ApiKey) -> None:
        self._keys[key.scope] = copy.copy(key)

    async def read_sign_in(self, token_hash: str) -> SignIn | None:
        found = self._sign_ins.get(token_hash)
        return copy.copy(found) if found else None

    async def save_sign_in(self, sign_in: SignIn) -> None:
        self._sign_ins[sign_in.token_hash] = copy.copy(sign_in)

    async def delete_sign_in(self, token_hash: str) -> None:
        self._sign_ins.pop(token_hash, None)


class SqliteAuthStore(AuthStore):
    """Auth records in the state database. Each record is a JSON ``body``."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def list_passkeys(self) -> list[Passkey]:
        found = [_load(Passkey, r) for r in await self._db.read_all(PASSKEYS)]
        return sorted(found, key=lambda p: p.created)

    async def read_passkey(self, id: str) -> Passkey | None:
        row = await self._db.read(PASSKEYS, id)
        return _load(Passkey, row) if row is not None else None

    async def save_passkey(self, passkey: Passkey) -> None:
        await self._db.upsert(PASSKEYS, passkey.id, body=_dump(passkey))

    async def delete_passkey(self, id: str) -> None:
        sign_ins = await self._db.read_where(SIGN_INS, "passkey", id)
        await self._db.write_all(
            [Write(SIGN_INS, row["id"]) for row in sign_ins] + [Write(PASSKEYS, id)]
        )

    async def read_api_key(self, scope: Scope) -> ApiKey | None:
        row = await self._db.read(API_KEYS, scope)
        return _load(ApiKey, row) if row is not None else None

    async def save_api_key(self, key: ApiKey) -> None:
        await self._db.upsert(API_KEYS, key.scope, body=_dump(key))

    async def read_sign_in(self, token_hash: str) -> SignIn | None:
        row = await self._db.read(SIGN_INS, token_hash)
        return _load(SignIn, row) if row is not None else None

    async def save_sign_in(self, sign_in: SignIn) -> None:
        await self._db.upsert(
            SIGN_INS,
            sign_in.token_hash,
            passkey=sign_in.passkey_id,
            body=_dump(sign_in),
        )

    async def delete_sign_in(self, token_hash: str) -> None:
        await self._db.delete(SIGN_INS, token_hash)


def _dump(record: Any) -> str:
    return json.dumps(dataclasses.asdict(record))


def _load(cls: type[T], row: Any) -> T:
    return cls(**json.loads(row["body"]))
