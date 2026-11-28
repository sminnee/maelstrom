"""The **Secret store** model: Secrets, their scopes, and the lock.

A :class:`SecretVault` holds the unlocked store key, in memory only. See
``docs/dev/orchestrator-server.md``, "The Secret store", for the cipher and
the files.
"""

import base64
import binascii
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from mael_common.util import now_iso

from .secret_store import SecretStore

#: The command that unlocks the store. Each refusal of a locked store names it.
UNLOCK_COMMAND = "mael secret unlock"

_HEADER_KEY = "store"
_GLOBAL_KEY = "global"
_PROJECTS_PREFIX = "projects/"
_VERSION = 1
_KEY_BYTES = 32
_NONCE_BYTES = 12
#: What the wrapped store key is bound to.
_HEADER_AAD = b"mael-secret-store-key-v1"

#: A Secret's name is the environment variable that carries it.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A project's name is one path part of a store key.
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: The most scrypt cost a header may ask for: 8 times the default's memory.
_MAX_N = 2**20
_MAX_R = 8
_MAX_P = 4


class SecretError(Exception):
    """A refusal of the Secret store. The message is for the user."""


class StoreLocked(SecretError):
    def __init__(self) -> None:
        super().__init__(
            f"The Secret store is locked. Unlock it with `{UNLOCK_COMMAND}`."
        )


class StoreMissing(SecretError):
    def __init__(self) -> None:
        super().__init__(
            f"There is no Secret store yet. Create it with `{UNLOCK_COMMAND}`."
        )


class StoreExists(SecretError):
    def __init__(self) -> None:
        super().__init__("The Secret store exists already.")


class WrongPassphrase(SecretError):
    def __init__(self) -> None:
        super().__init__("That passphrase does not open the Secret store.")


class UnknownSecret(SecretError):
    def __init__(self, name: str, project: str | None) -> None:
        super().__init__(f"No Secret {name} in {scope_label(project)}.")


class MissingSecrets(SecretError):
    """A resolve that found no value for some names, in the project or globally."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(f"Missing secrets: {', '.join(names)}.")
        self.names = names


class DamagedSecret(SecretError):
    """A sealed value that the store key does not open at its scope and name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"The Secret {name} does not open: its file changed outside the store."
        )


class DamagedStore(SecretError):
    """A header this build cannot read, or sealed values with no header."""


class BadSecret(SecretError):
    """A name, a project, a value or a passphrase the store does not take."""


@dataclass(frozen=True)
class Kdf:
    """The scrypt cost. The default takes about 128 MB and a fraction of a second."""

    n: int = 2**17
    r: int = 8
    p: int = 1


@dataclass(frozen=True)
class SecretInfo:
    """What the store says about a Secret: never its value.

    ``project`` is ``None`` for the global scope.
    """

    name: str
    project: str | None
    set_at: str


def scope_label(project: str | None) -> str:
    """The scope as a person reads it."""
    return "the global scope" if project is None else f"project {project}"


def _scope_key(project: str | None) -> str:
    if project is None:
        return _GLOBAL_KEY
    if not _PROJECT.fullmatch(project):
        raise BadSecret(f"{project!r} is not a project name.")
    return _PROJECTS_PREFIX + project


def _project_of(key: str) -> str | None:
    return None if key == _GLOBAL_KEY else key.removeprefix(_PROJECTS_PREFIX)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _raw(text: str) -> bytes:
    return base64.b64decode(text)


def _encode(text: str, what: str) -> bytes:
    try:
        return text.encode()
    except UnicodeEncodeError:
        raise BadSecret(f"{what} is not valid text.") from None


def _derive(passphrase: str, salt: bytes, kdf: Kdf) -> bytes:
    scrypt = Scrypt(salt=salt, length=_KEY_BYTES, n=kdf.n, r=kdf.r, p=kdf.p)
    return scrypt.derive(_encode(passphrase, "The passphrase"))


@dataclass(frozen=True)
class _Header:
    """The store's header, read and checked."""

    kdf: Kdf
    salt: bytes
    nonce: bytes
    wrapped: bytes

    @classmethod
    def parse(cls, document: dict[str, Any]) -> "_Header":
        try:
            if document["version"] != _VERSION:
                raise DamagedStore(
                    f"The Secret store has version {document['version']}, "
                    f"and this build reads version {_VERSION}."
                )
            kdf = Kdf(**{k: int(document["kdf"][k]) for k in ("n", "r", "p")})
            header = cls(
                kdf,
                _raw(document["kdf"]["salt"]),
                _raw(document["key"]["nonce"]),
                _raw(document["key"]["wrapped"]),
            )
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise DamagedStore("The header of the Secret store is damaged.") from None
        if not (2 <= kdf.n <= _MAX_N and 1 <= kdf.r <= _MAX_R and 1 <= kdf.p <= _MAX_P):
            raise DamagedStore("The header of the Secret store asks for a bad cost.")
        return header


class SecretVault:
    """The Secret store, and the unlocked key while this object lives. Thread-safe."""

    def __init__(
        self,
        store: SecretStore,
        *,
        kdf: Kdf = Kdf(),
        clock: Callable[[], str] = now_iso,
    ) -> None:
        self._store = store
        self._kdf = kdf
        self._clock = clock
        self._key: bytes | None = None
        self._mutex = threading.Lock()

    @property
    def exists(self) -> bool:
        """Whether the store has a passphrase yet."""
        return self._store.read(_HEADER_KEY) is not None

    @property
    def locked(self) -> bool:
        return self._key is None

    def unlock(self, passphrase: str, *, create: bool = False) -> None:
        """Unlock the store with ``passphrase`` until this object dies.

        ``create`` makes the store and sets its passphrase. It is explicit, so
        a mistyped passphrase never makes a store.

        Raises:
            StoreExists: ``create`` on a store that exists.
            StoreMissing: No ``create``, and no store.
            DamagedStore: A header this build cannot read, or a ``create``
                beside sealed values that a lost header left behind.
            WrongPassphrase: The passphrase does not open the store. The lock
                state does not change.
        """
        if not passphrase:
            raise BadSecret("A passphrase must not be empty.")
        with self._mutex:
            document = self._store.read(_HEADER_KEY)
            if create:
                if document is not None:
                    raise StoreExists()
                if self._scope_keys():
                    raise DamagedStore(
                        "The Secret store has sealed values and no header, so "
                        "nothing opens them. Restore its `store.json`, or "
                        "delete the store's folder and set each Secret again."
                    )
                self._key = self._create(passphrase)
                return
            if document is None:
                raise StoreMissing()
            header = _Header.parse(document)
        # Outside the mutex: scrypt is slow, and a list must not wait on it.
        wrapping = _derive(passphrase, header.salt, header.kdf)
        try:
            key = AESGCM(wrapping).decrypt(header.nonce, header.wrapped, _HEADER_AAD)
        except InvalidTag:
            raise WrongPassphrase() from None
        with self._mutex:
            self._key = key

    def _create(self, passphrase: str) -> bytes:
        salt, nonce, key = (
            os.urandom(16),
            os.urandom(_NONCE_BYTES),
            os.urandom(_KEY_BYTES),
        )
        wrapping = _derive(passphrase, salt, self._kdf)
        self._store.write(
            _HEADER_KEY,
            {
                "version": _VERSION,
                "kdf": {
                    "salt": _b64(salt),
                    "n": self._kdf.n,
                    "r": self._kdf.r,
                    "p": self._kdf.p,
                },
                "key": {
                    "nonce": _b64(nonce),
                    "wrapped": _b64(AESGCM(wrapping).encrypt(nonce, key, _HEADER_AAD)),
                },
            },
        )
        return key

    def _cipher(self) -> AESGCM:
        if self._key is None:
            raise StoreLocked()
        return AESGCM(self._key)

    def _sealed(self, key: str) -> dict[str, Any]:
        document = self._store.read(key)
        return document["secrets"] if document else {}

    def _scope_keys(self) -> list[str]:
        """The store keys that are a scope. A stray file in the folder is none."""
        return [
            key
            for key in self._store.keys()
            if key == _GLOBAL_KEY
            or (
                key.startswith(_PROJECTS_PREFIX)
                and _PROJECT.fullmatch(key.removeprefix(_PROJECTS_PREFIX))
            )
        ]

    def set(self, name: str, value: str, project: str | None = None) -> SecretInfo:
        """Store ``value`` as the Secret ``name`` of ``project``, or of the global scope.

        A Secret of that name and scope gets the new value.
        """
        if not _NAME.fullmatch(name):
            raise BadSecret(
                f"{name!r} is not a Secret name: use letters, digits and `_`."
            )
        if not value:
            raise BadSecret("A Secret's value must not be empty.")
        key = _scope_key(project)
        plain = _encode(value, "The value")
        with self._mutex:
            cipher = self._cipher()
            nonce = os.urandom(_NONCE_BYTES)
            sealed = self._sealed(key)
            sealed[name] = {
                "nonce": _b64(nonce),
                "value": _b64(cipher.encrypt(nonce, plain, _aad(key, name))),
                "set_at": self._clock(),
            }
            self._store.write(key, {"version": _VERSION, "secrets": sealed})
            return SecretInfo(name, project, sealed[name]["set_at"])

    def list(self) -> list[SecretInfo]:
        """Each Secret of each scope, by scope then name. Works on a locked store."""
        with self._mutex:
            return [
                SecretInfo(name, _project_of(key), entry["set_at"])
                for key in self._scope_keys()
                for name, entry in sorted(self._sealed(key).items())
            ]

    def remove(self, name: str, project: str | None = None) -> None:
        """Remove the Secret ``name`` of ``project``, or of the global scope."""
        key = _scope_key(project)
        with self._mutex:
            self._cipher()
            sealed = self._sealed(key)
            if name not in sealed:
                raise UnknownSecret(name, project)
            del sealed[name]
            self._store.write(key, {"version": _VERSION, "secrets": sealed})

    def resolve(self, names: Iterable[str], project: str | None) -> dict[str, str]:
        """The value of each of ``names`` for ``project``.

        A value of the project wins over a global one.

        Raises:
            MissingSecrets: Some names have no value. It lists them all.
            DamagedSecret: A sealed value was moved or changed in its file.
        """
        with self._mutex:
            cipher = self._cipher()
            scopes = [_GLOBAL_KEY]
            if project is not None:
                scopes.insert(0, _scope_key(project))
            sealed = {key: self._sealed(key) for key in scopes}
            values: dict[str, str] = {}
            missing: list[str] = []
            for name in names:
                key = next((k for k in scopes if name in sealed[k]), None)
                if key is None:
                    missing.append(name)
                    continue
                entry = sealed[key][name]
                try:
                    values[name] = cipher.decrypt(
                        _raw(entry["nonce"]), _raw(entry["value"]), _aad(key, name)
                    ).decode()
                except InvalidTag:
                    raise DamagedSecret(name) from None
            if missing:
                raise MissingSecrets(missing)
            return values


def _aad(scope_key: str, name: str) -> bytes:
    return f"{scope_key}\0{name}".encode()
