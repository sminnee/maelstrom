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

from mael_domain.language_servers import (
    LanguageServer,
    language_id,
    reads,
    server_for,
)
from mael_domain.protocol import (
    CodeTarget,
    Definition,
    DefinitionReason,
    LineRange,
)
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
#: The most references one answer holds. Each distinct path of them costs one
#: ``documentSymbol`` request.
MAX_REFERENCES = 100
#: The window pinned when no function or class holds a line.
WINDOW_ABOVE = 5
WINDOW_BELOW = 25
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

        Each target's ``range`` is its **Fragment**, else a window round it.
        When the click is on the definition itself, ``references`` lists each
        use in the rev, each with its own Fragment.

        Raises :class:`~mael_domain.worktree_changes.BadPath`,
        :class:`~mael_domain.worktree_changes.UnknownRev` and
        :class:`~mael_orchestrator.lsp_client.LspError`.
        """
        check_paths(file)
        commit = await new_commit(path, rev)
        found = server_for(path, file, self.servers)
        if found is None:
            return {
                "rev": rev,
                "targets": [],
                "references": None,
                "reason": "no_server",
            }
        spec = found.server
        key = (worktree_id, spec.name)
        server = self._running.get(key)
        if server is None:
            client = LspClient(found.command, path, found.options)
            server = self._running[key] = _Server(client)
        async with server.lock:
            if server.closed:
                raise LspError(f"{spec.name} stopped: its worktree closed")
            if server.idle is not None:
                server.idle.cancel()
            try:
                return await self._ask(
                    server, spec, path, rev, commit, file, line, character
                )
            finally:
                server.idle = asyncio.create_task(self._stop_when_idle(server))

    async def _ask(
        self,
        server: _Server,
        spec: LanguageServer,
        path: Path,
        rev: str,
        commit: str | None,
        file: str,
        line: int,
        character: int,
    ) -> Definition:
        """Every request of one click, under one lock hold: one **Rev overlay** for all."""
        client = server.client
        if not client.running:
            server.commit, server.opened, server.answered = None, {}, False
            await client.start(FIRST_REQUEST_SECS)
        await self._sync_overlay(server, spec, path, commit, file)
        if spec.waits_for_load:
            await client.loaded(FIRST_REQUEST_SECS)
        position = {
            "textDocument": {"uri": (path / file).as_uri()},
            "position": {"line": line - 1, "character": character},
        }
        found = _locations(
            await client.request(
                "textDocument/definition",
                position,
                REQUEST_SECS if server.answered else FIRST_REQUEST_SECS,
            )
        )
        server.answered = True
        tracked: dict[str, bool] = {}
        targets = [await _location(path, commit, loc, tracked) for loc in found]
        references: list[CodeTarget] | None = None
        more = 0
        if any(_holds(loc, path / file, line - 1, character) for loc in found):
            # The definition was a round trip, so a load that the overlay's
            # didOpen began has sent its begin by now.
            if spec.waits_for_load:
                await client.loaded(FIRST_REQUEST_SECS)
            used = await client.request(
                "textDocument/references",
                {**position, "context": {"includeDeclaration": False}},
                FIRST_REQUEST_SECS,
            )
            mapped = [
                await _location(path, commit, loc, tracked) for loc in _locations(used)
            ]
            # One per line: the menu goes to a line, so two uses on it are one row.
            by_line = {
                (r["path"], r["range"]["start"]): r for r in mapped if r["inRev"]
            }
            references = [by_line[k] for k in sorted(by_line)]
            more = max(0, len(references) - MAX_REFERENCES)
            references = references[:MAX_REFERENCES]
        symbols: dict[str, list[dict[str, Any]] | None] = {}
        for target in [*targets, *(references or [])]:
            if target["inRev"] and target["path"] not in symbols:
                # tsserver answers [] for a file it has not opened.
                if reads(spec, target["path"]):
                    await self._open(server, path, commit, target["path"])
                symbols[target["path"]] = await self._symbols(
                    client, path, target["path"]
                )
        if not targets:
            reason: DefinitionReason = "no_definition"
        elif references == []:
            reason = "no_references"
        else:
            reason = "ok"
        answer: Definition = {
            "rev": rev,
            "targets": [_place(spec, t, symbols.get(t["path"])) for t in targets],
            "references": None,
            "reason": reason,
        }
        if references is not None:
            answer["references"] = [
                _place(spec, r, symbols.get(r["path"])) for r in references
            ]
            if more:
                answer["moreReferences"] = more
        return answer

    async def _symbols(
        self, client: LspClient, path: Path, file: str
    ) -> list[dict[str, Any]] | None:
        """The symbol tree of ``file``, or ``None`` when the server fails to give it."""
        try:
            result = await client.request(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": (path / file).as_uri()}},
                REQUEST_SECS,
            )
        except LspError as exc:
            log.warning("documentSymbol for %s failed: %s", file, exc)
            return None
        return [s for s in result or [] if isinstance(s, dict)]

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
            await self._open(server, path, commit, name)
        server.commit = key

    async def _open(
        self, server: _Server, path: Path, commit: str | None, name: str
    ) -> None:
        """Open ``name`` at ``commit``'s text, unless it is open or not in the rev."""
        if name in server.opened:
            return
        text = await rev_text(path, commit, name)
        if text is None:
            return
        server.opened[name] = 1
        await server.client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": (path / name).as_uri(),
                    "languageId": language_id(name),
                    "version": 1,
                    "text": text,
                }
            },
        )

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


def _target_path(location: dict[str, Any]) -> Path:
    uri = location.get("targetUri") or location.get("uri") or ""
    return Path(unquote(urlparse(uri).path))


def _name_span(location: dict[str, Any]) -> dict[str, Any]:
    """The range of the name itself: a link's selection range, else the range."""
    return (
        location.get("targetSelectionRange")
        or location.get("targetRange")
        or location.get("range")
        or {}
    )


def _holds(location: dict[str, Any], file: Path, line: int, character: int) -> bool:
    """``location`` names the 0-based ``line`` and ``character`` of ``file``: the click is on it."""
    if _target_path(location).resolve() != file.resolve():
        return False
    span = _name_span(location)
    start, end = span.get("start", {}), span.get("end", {})
    at = (line, character)
    return (
        (start.get("line", -1), start.get("character", 0))
        <= at
        <= (end.get("line", -1), end.get("character", 0))
    )


async def _location(
    path: Path, commit: str | None, location: dict[str, Any], tracked: dict[str, bool]
) -> CodeTarget:
    """``location`` as a path in the worktree, with the name's own lines as its range.

    ``tracked`` holds each path's ``inRev`` already found: uses cluster in a few
    files, and each fresh path costs a ``git`` call.
    """
    target = _target_path(location)
    span = _name_span(location)
    try:
        relative = target.resolve().relative_to(path.resolve()).as_posix()
    except ValueError:
        relative = None
    start = span.get("start", {}).get("line", 0) + 1
    end = span.get("end", {}).get("line", start - 1) + 1
    return {
        "path": relative if relative is not None else str(target),
        "range": {"start": start, "end": end},
        "inRev": relative is not None
        and await _tracked(path, commit, relative, tracked),
    }


async def _tracked(
    path: Path, commit: str | None, relative: str, tracked: dict[str, bool]
) -> bool:
    if relative not in tracked:
        tracked[relative] = await in_rev(path, commit, relative)
    return tracked[relative]


def _place(
    spec: LanguageServer, target: CodeTarget, symbols: list[dict[str, Any]] | None
) -> CodeTarget:
    """Set the lines to pin for ``target``: its **Fragment**, else the window.

    ``symbols`` is the ``documentSymbol`` result of the target's file; ``None``
    when there is none. A target outside the rev keeps the name's lines.
    """
    if not target["inRev"]:
        return target
    anchor = target["range"]["start"]
    enclosing = _enclosing(spec, symbols or [], anchor - 1)
    placed: CodeTarget = {
        "path": target["path"],
        "inRev": True,
        "range": {
            "start": max(1, anchor - WINDOW_ABOVE),
            "end": anchor + WINDOW_BELOW,
        },
    }
    if enclosing is not None:
        placed["range"], placed["symbol"] = enclosing
    if anchor != placed["range"]["start"]:
        placed["anchor"] = anchor
    return placed


def _enclosing(
    spec: LanguageServer, symbols: list[dict[str, Any]], line: int
) -> tuple[LineRange, str] | None:
    """The innermost symbol of a **Fragment** kind of ``spec`` that holds 0-based ``line``.

    Reads both ``documentSymbol`` shapes: ``DocumentSymbol`` trees, and flat
    ``SymbolInformation`` with a ``containerName``. Returns its 1-based,
    inclusive lines and its dotted name.
    """
    best: tuple[int, LineRange, str] | None = None
    for name, span in _fragments(spec, symbols, ""):
        start = span.get("start", {}).get("line", 0)
        end = span.get("end", {}).get("line", start)
        if start <= line <= end and (best is None or end - start <= best[0]):
            best = (end - start, {"start": start + 1, "end": end + 1}, name)
    return None if best is None else (best[1], best[2])


def _fragments(
    spec: LanguageServer,
    symbols: list[dict[str, Any]],
    outer: str,
    depth: int = 0,
) -> list[tuple[str, dict[str, Any]]]:
    """Each symbol of a **Fragment** kind, in tree order, with its dotted name and range.

    A kind of ``spec.top_level_kinds`` counts only with no enclosing symbol: a
    ``const`` inside a function would pin one line.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    for symbol in symbols:
        name = str(symbol.get("name", ""))
        if "location" in symbol:
            container = symbol.get("containerName")
            dotted = f"{container}.{name}" if container else name
            span = symbol["location"].get("range", {})
            top_level = not container
        else:
            dotted = f"{outer}.{name}" if outer else name
            span = symbol.get("range", {})
            top_level = depth == 0
        kind = symbol.get("kind")
        counts = kind in spec.fragment_kinds or (
            top_level and kind in spec.top_level_kinds
        )
        if counts:
            found.append((dotted, span))
        children = symbol.get("children")
        if isinstance(children, list):
            found += _fragments(
                spec,
                [c for c in children if isinstance(c, dict)],
                dotted if counts else outer,
                depth + 1,
            )
    return found
