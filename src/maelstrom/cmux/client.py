"""Transport layer for cmux integration.

Mirrors the storage trio in ``task_store.py`` (Protocol + real + fake):

- ``CmuxClient`` — a Protocol with a single ``run(*args) -> CmuxResult`` method.
- ``SubprocessCmuxClient`` — the real client. Discovers the cmux binary, adds
  ``--socket`` and shells out, wrapping the reply in a ``CmuxResult``.
- ``RecordingCmuxClient`` — the in-memory fake (the cmux analogue of
  ``InMemoryStore``). Records every call and returns scripted results.

Parsing of cmux's ``OK <ref>`` replies lives on ``CmuxResult`` so it sits right
at the transport seam. ``current_client()`` returns ``None`` when no binary is
found or the socket is dead — that ``None`` *is* "not in cmux mode", so no
null-object is needed. The socket path comes from ``CMUX_SOCKET_PATH`` and falls
back to :data:`DEFAULT_SOCKET_PATH` when unset.

All operations are non-fatal; a transport failure surfaces as a ``CmuxResult``
whose ``raw`` is ``None`` (never an exception).
"""

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

# cmux's conventional socket path. Used when ``CMUX_SOCKET_PATH`` is unset or
# empty, so a caller outside a cmux-spawned shell (a launchd tick, a session
# that didn't inherit the var) can still reach a running cmux instead of
# concluding "not in cmux mode". A missing binary or a dead socket still fails
# honestly downstream.
DEFAULT_SOCKET_PATH = "/tmp/cmux.sock"


def resolve_socket_path() -> str:
    """The cmux socket path from the environment, or the conventional default."""
    return os.environ.get("CMUX_SOCKET_PATH") or DEFAULT_SOCKET_PATH


@dataclass(frozen=True)
class CmuxResult:
    """The parsed result of a single cmux command.

    ``raw`` is the raw stdout (stripped), or ``None`` when the transport itself
    failed (no binary, no socket, non-zero exit). Parsing accessors never raise:
    they degrade to ``False`` / ``""`` / ``None`` on a non-OK or ``None`` reply.
    """

    raw: str | None

    @property
    def ok(self) -> bool:
        """True when cmux replied with an ``OK`` line."""
        return bool(self.raw) and self.raw.startswith("OK")

    @property
    def text(self) -> str:
        """The ref payload after ``OK ``; ``""`` for a bare ``OK`` or non-OK."""
        if not self.ok:
            return ""
        assert self.raw is not None  # implied by self.ok
        # "OK <ref>" -> ref; "OK" -> ""
        return self.raw[3:] if len(self.raw) > 2 else ""

    def ref(self, kind: str) -> str | None:
        """First ``{kind}:N`` ref in the payload, or ``None`` (was ``_first_ref``).

        cmux often replies with multiple refs, e.g. new-surface returns
        "surface:5 pane:2 workspace:1"; only the leading surface ref is a valid
        ``--surface`` handle.
        """
        if not self.text:
            return None
        match = re.search(rf"{kind}:\d+", self.text)
        return match.group(0) if match else None


class CmuxClient(Protocol):
    """A transport that runs a cmux command and returns its parsed result."""

    def run(self, *args: str) -> CmuxResult:
        """Run ``cmux <args>`` and return a :class:`CmuxResult`."""
        ...


def _find_cmux_cli() -> str | None:
    """Find the cmux binary.

    Checks PATH first, then falls back to the macOS app bundle location.
    Returns the path to the binary or ``None`` if not found.
    """
    path = shutil.which("cmux")
    if path:
        return path

    app_path = "/Applications/cmux.app/Contents/Resources/bin/cmux"
    if os.path.isfile(app_path):
        return app_path

    return None


class SubprocessCmuxClient:
    """The real :class:`CmuxClient`: shells out to the cmux binary.

    Was the free functions ``cmux_cmd`` + ``_find_cmux_cli``. Holds the binary
    path and socket so ``run`` is a thin ``subprocess.run`` wrapper.
    """

    def __init__(self, cli_path: str, socket_path: str) -> None:
        self._cli_path = cli_path
        self._socket_path = socket_path

    def run(self, *args: str) -> CmuxResult:
        """Run a cmux command with ``--socket`` and parse the text response.

        Returns a :class:`CmuxResult` whose ``raw`` is the stripped stdout, or
        ``None`` on any transport failure (the command is non-fatal).
        """
        cmd = [self._cli_path, "--socket", self._socket_path, *args]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return CmuxResult(result.stdout.strip())
        except (FileNotFoundError, subprocess.CalledProcessError):
            return CmuxResult(None)


# A scripted-response source: either a dict keyed by the exact args tuple, or a
# callable taking the args and returning the raw stdout (or None).
Responses = dict[tuple[str, ...], str | None] | Callable[..., str | None]


@dataclass
class RecordingCmuxClient:
    """The in-memory fake :class:`CmuxClient` (the cmux ``InMemoryStore`` analogue).

    Records every ``run`` call in ``calls`` and returns a scripted result.
    ``responses`` is either a ``dict`` keyed by the exact args tuple or a callable
    ``fn(*args) -> str | None``; anything not matched returns ``CmuxResult(None)``.
    """

    responses: Responses | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, *args: str) -> CmuxResult:
        self.calls.append(args)
        raw = self._lookup(args)
        return CmuxResult(raw)

    def _lookup(self, args: tuple[str, ...]) -> str | None:
        if self.responses is None:
            return None
        if callable(self.responses):
            return self.responses(*args)
        return self.responses.get(args)


def _socket_is_live(client: CmuxClient) -> bool:
    """True if the cmux socket answers a ``ping``.

    ``ping`` is the cheapest verb: any reply (even a bare ``OK``) proves the
    socket is up. A dead socket makes ``run`` return ``CmuxResult(None)`` — so
    ``raw is not None`` distinguishes a live daemon from a stale/missing one.
    """
    return client.run("ping").raw is not None


def current_client() -> CmuxClient | None:
    """Return a real :class:`CmuxClient`, or ``None`` when not in cmux mode.

    The socket path comes from ``CMUX_SOCKET_PATH``, falling back to the
    conventional :data:`DEFAULT_SOCKET_PATH` when it is unset/empty — so a caller
    that did not inherit the var can still reach a running cmux. ``None`` is
    returned when no cmux binary can be found or the socket is dead (no daemon
    answering) — i.e. ``None`` *is* "not in cmux mode". The liveness probe is one
    extra subprocess per call; launches are rare enough that the honesty is worth
    it.
    """
    cli = _find_cmux_cli()
    if cli is None:
        return None
    client = SubprocessCmuxClient(cli, resolve_socket_path())
    if not _socket_is_live(client):
        return None
    return client


def ensure_cmux_running(*, timeout_s: float = 8.0) -> bool:
    """Ensure the cmux app is up, launching it if needed. True once reachable.

    1. If a freshly-built client already answers ``ping`` → up (True).
    2. Else, if the app is installed, launch it via ``open`` (a plain
       subprocess — **never** ``replace_process``) and poll ``ping`` until it
       answers or ``timeout_s`` elapses.

    Returns False if there is no binary, the app is not installed, or it never
    answers within the timeout. The socket path defaults to
    :data:`DEFAULT_SOCKET_PATH` when ``CMUX_SOCKET_PATH`` is unset. Works from a
    GUI-session LaunchAgent because ``open`` brings the app up in that session.
    """
    cli = _find_cmux_cli()
    if cli is None:
        return False
    client = SubprocessCmuxClient(cli, resolve_socket_path())
    if _socket_is_live(client):
        return True

    try:
        subprocess.run(
            ["open", "-b", "com.cmuxterm.app"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False  # app not installed / open failed

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _socket_is_live(client):
            return True
        time.sleep(0.25)
    return False


def is_cmux_mode() -> bool:
    """Return True if running inside cmux with a usable client.

    Not a cheap env check: since ``current_client()`` now ping-probes the
    socket, each call fires one ``cmux ping`` subprocess. Callers that run per
    status update (e.g. ``set_status``) or per layout verb pay that probe —
    acceptable because these paths are interactive/rare, but do not call this in
    a hot loop.
    """
    return current_client() is not None
