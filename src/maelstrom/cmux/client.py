"""Transport layer for cmux integration.

Mirrors the storage trio in ``task_store.py`` (Protocol + real + fake):

- ``CmuxClient`` — a Protocol with a single ``run(*args) -> CmuxResult`` method.
- ``SubprocessCmuxClient`` — the real client. Discovers the cmux binary, adds
  ``--socket`` and shells out, wrapping the reply in a ``CmuxResult``.
- ``RecordingCmuxClient`` — the in-memory fake (the cmux analogue of
  ``InMemoryStore``). Records every call and returns scripted results.

Parsing of cmux's ``OK <ref>`` replies lives on ``CmuxResult`` so it sits right
at the transport seam. ``current_client()`` returns ``None`` when not running
inside cmux (``CMUX_SOCKET_PATH`` unset) or when no binary is found — that
``None`` *is* "not in cmux mode", so no null-object is needed.

All operations are non-fatal; a transport failure surfaces as a ``CmuxResult``
whose ``raw`` is ``None`` (never an exception).
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


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


def current_client() -> CmuxClient | None:
    """Return a real :class:`CmuxClient`, or ``None`` when not in cmux mode.

    ``None`` is returned when ``CMUX_SOCKET_PATH`` is unset/empty or no cmux
    binary can be found — i.e. ``None`` *is* "not in cmux mode".
    """
    socket_path = os.environ.get("CMUX_SOCKET_PATH")
    if not socket_path:
        return None
    cli = _find_cmux_cli()
    if cli is None:
        return None
    return SubprocessCmuxClient(cli, socket_path)


def is_cmux_mode() -> bool:
    """Return True if running inside cmux with a usable client."""
    return current_client() is not None
