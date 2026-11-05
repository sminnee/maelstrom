"""Each worktree's **Language servers**: started on demand, fed the rev, stopped when idle.

The service layer over :mod:`mael_orchestrator.lsp_client`. See
``docs/dev/code-navigation.md``, "The language server".
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mael_domain.language_servers import LanguageServer, reads, server_for
from mael_domain.protocol import Definition, DefinitionTarget
from mael_domain.worktree_changes import (
    check_paths,
    differing_paths,
    in_rev,
    new_commit,
    rev_text,
)

from .lsp_client import LspClient, LspError

log = logging.getLogger(__name__)

#: A server with no request for this long stops.
IDLE_SECS = 600.0
#: The first request waits while the server reads the project.
FIRST_REQUEST_SECS = 30.0
REQUEST_SECS = 10.0

#: The overlay key of the working tree, which has no commit.
_WORKING_TREE = ""


@dataclass
class _Server:
    client: LspClient
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The commit the open files hold, or ``None`` before the first sync.
    commit: str | None = None
    #: Each open file and its version.
    opened: dict[str, int] = field(default_factory=dict)
    answered: bool = False
    idle: asyncio.Task[None] | None = None
    #: Set when the worktree closes: a request that waited on the lock gives up.
    closed: bool = False


class LanguageServers:
    """One server per worktree and server name, out of ``servers``."""

    def __init__(
        self,
        servers: Sequence[LanguageServer] = (),
        *,
        idle_secs: float = IDLE_SECS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.servers = list(servers)
        self._idle_secs = idle_secs
        self._sleep = sleep
        self._running: dict[tuple[str, str], _Server] = {}

    async def definition(
        self,
        worktree_id: str,
        path: Path,
        rev: str,
        file: str,
        line: int,
        character: int,
    ) -> Definition:
        """Where the name at 1-based ``line`` and UTF-16 ``character`` of ``file`` is defined.

        Raises :class:`~mael_domain.worktree_changes.BadPath`,
        :class:`~mael_domain.worktree_changes.UnknownRev` and
        :class:`~mael_orchestrator.lsp_client.LspError`.
        """
        check_paths(file)
        commit = await new_commit(path, rev)
        found = server_for(path, file, self.servers)
        if found is None:
            return {"rev": rev, "targets": [], "reason": "no_server"}
        spec, command = found
        key = (worktree_id, spec.name)
        server = self._running.get(key)
        if server is None:
            server = self._running[key] = _Server(LspClient(command, path))
        async with server.lock:
            if server.closed:
                raise LspError(f"{spec.name} stopped: its worktree closed")
            if server.idle is not None:
                server.idle.cancel()
            try:
                result = await self._ask(
                    server, spec, path, commit, file, line, character
                )
            finally:
                server.idle = asyncio.create_task(self._stop_when_idle(server))
        targets = [await self._target(path, commit, loc) for loc in _locations(result)]
        return {
            "rev": rev,
            "targets": targets,
            "reason": "ok" if targets else "no_definition",
        }

    async def _ask(
        self,
        server: _Server,
        spec: LanguageServer,
        path: Path,
        commit: str | None,
        file: str,
        line: int,
        character: int,
    ) -> Any:
        client = server.client
        if not client.running:
            server.commit, server.opened, server.answered = None, {}, False
            await client.start(FIRST_REQUEST_SECS)
        await self._sync_overlay(server, spec, path, commit, file)
        result = await client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": (path / file).as_uri()},
                "position": {"line": line - 1, "character": character},
            },
            REQUEST_SECS if server.answered else FIRST_REQUEST_SECS,
        )
        server.answered = True
        return result

    async def _sync_overlay(
        self,
        server: _Server,
        spec: LanguageServer,
        path: Path,
        commit: str | None,
        file: str,
    ) -> None:
        """Open each file the server must read at ``commit``'s text, and close the rest.

        A file whose working-tree text is the rev's needs no overlay: the
        server reads the disk. The working tree's own text can change between
        requests, so under no commit the open file is sent again each time.
        """
        needed = {p for p in await differing_paths(path, commit) if reads(spec, p)} | {
            file
        }
        key = commit or _WORKING_TREE
        if server.commit != key or commit is None:
            stale = set(server.opened)
        else:
            stale = set(server.opened) - needed
        for name in sorted(stale):
            await server.client.notify(
                "textDocument/didClose",
                {"textDocument": {"uri": (path / name).as_uri()}},
            )
            del server.opened[name]
        for name in sorted(needed - set(server.opened)):
            text = await rev_text(path, commit, name)
            if text is None:
                continue
            version = server.opened[name] = server.opened.get(name, 0) + 1
            await server.client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": (path / name).as_uri(),
                        "languageId": spec.name,
                        "version": version,
                        "text": text,
                    }
                },
            )
        server.commit = key

    async def _target(
        self, path: Path, commit: str | None, location: dict[str, Any]
    ) -> DefinitionTarget:
        uri = location.get("targetUri") or location.get("uri") or ""
        span = (
            location.get("targetSelectionRange")
            or location.get("targetRange")
            or location.get("range")
            or {}
        )
        target = Path(unquote(urlparse(uri).path))
        root = path.resolve()
        try:
            relative = target.resolve().relative_to(root).as_posix()
        except ValueError:
            relative = None
        start = span.get("start", {}).get("line", 0) + 1
        end = span.get("end", {}).get("line", start - 1) + 1
        return {
            "path": relative if relative is not None else str(target),
            "range": {"start": start, "end": end},
            "inRev": relative is not None and await in_rev(path, commit, relative),
        }

    async def _stop_when_idle(self, server: _Server) -> None:
        # The entry stays: the next request starts the client again.
        await self._sleep(self._idle_secs)
        async with server.lock:
            await server.client.stop()

    async def close_worktree(self, worktree_id: str) -> None:
        """Stop every server of ``worktree_id``."""
        for key in [k for k in self._running if k[0] == worktree_id]:
            await self._stop(key)

    async def stop_all(self) -> None:
        for key in list(self._running):
            await self._stop(key)

    async def _stop(self, key: tuple[str, str]) -> None:
        server = self._running.pop(key, None)
        if server is None:
            return
        server.closed = True
        if server.idle is not None:
            server.idle.cancel()
        async with server.lock:
            await server.client.stop()


def _locations(result: Any) -> list[dict[str, Any]]:
    """A ``definition`` result as a list: it may be null, one Location, or a list."""
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    return [loc for loc in result if isinstance(loc, dict)]
