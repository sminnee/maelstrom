"""The **Language servers** maelstrom knows, and which one reads a file.

The list is hardcoded: ``docs/dev/code-navigation.md``, "The language server".
"""

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .diff_groups import glob_matches

#: The LSP symbol kinds that make a **Fragment**: Class, Method, Constructor, Function.
DEFAULT_FRAGMENT_KINDS = frozenset({5, 6, 9, 12})


@dataclass(frozen=True)
class ServerInstall:
    """How ``mael self check-host`` installs a server on each platform."""

    darwin: str
    linux: str
    #: Another host tool the install command runs, such as ``node`` for npm.
    needs: str | None = None


@dataclass(frozen=True)
class LocalRuntime:
    """A runtime a server takes from the project, sent as an ``initializationOptions`` value."""

    #: Relative to a folder of the worktree. The nearest folder above the file
    #: that starts the server, and that holds this path, wins.
    path: str
    #: Where the absolute path goes in ``initializationOptions``.
    option: tuple[str, ...]


@dataclass(frozen=True)
class LanguageServer:
    """One server: its name, the command that starts it on stdio, and the files it reads."""

    name: str
    #: The executable and its arguments. A bare name resolves in the worktree's
    #: ``.venv/bin``, then its ``node_modules/.bin``, then on ``PATH``.
    command: tuple[str, ...]
    #: Repo-relative globs, in the ``diff_groups:`` syntax.
    globs: tuple[str, ...]
    #: The LSP symbol kinds that make a **Fragment**.
    fragment_kinds: frozenset[int] = DEFAULT_FRAGMENT_KINDS
    #: Kinds that make a **Fragment** only at the top level of a file.
    top_level_kinds: frozenset[int] = frozenset()
    #: The server loads its project in the background after the first
    #: ``didOpen``, and reports it as work-done progress: wait for it to end.
    waits_for_load: bool = False
    local_runtime: LocalRuntime | None = None
    install: ServerInstall | None = None


LANGUAGE_SERVERS: tuple[LanguageServer, ...] = (
    LanguageServer(
        "pyright",
        ("pyright-langserver", "--stdio"),
        globs=("**/*.py",),
        install=ServerInstall("uv tool install pyright", "uv tool install pyright"),
    ),
    LanguageServer(
        "typescript",
        ("typescript-language-server", "--stdio"),
        globs=("**/*.ts", "**/*.tsx"),
        # Variable and Constant: ``export const Foo = () => …``.
        top_level_kinds=frozenset({13, 14}),
        waits_for_load=True,
        # The project's pinned TypeScript, as VS Code's ``typescript.tsdk``.
        local_runtime=LocalRuntime(
            "node_modules/typescript/lib/tsserver.js", ("tsserver", "path")
        ),
        install=ServerInstall(
            "npm install -g typescript-language-server typescript",
            "sudo npm install -g typescript-language-server typescript",
            needs="node",
        ),
    ),
)

_LANGUAGE_IDS = {".py": "python", ".ts": "typescript", ".tsx": "typescriptreact"}


@dataclass(frozen=True)
class Resolved:
    """The server that reads a file, the command that starts it, and its options."""

    server: LanguageServer
    command: list[str]
    #: The ``initializationOptions`` to start it with.
    options: dict[str, Any]


def server_for(
    worktree_path: Path,
    file: str,
    servers: Sequence[LanguageServer] = LANGUAGE_SERVERS,
) -> Resolved | None:
    """The first server whose globs match ``file``, with its command and options.

    ``None`` when no glob matches, or when the server is not installed: then
    the file is not navigable.
    """
    for server in servers:
        if not reads(server, file):
            continue
        executable = _resolve(worktree_path, server.command[0])
        if executable:
            command = [executable, *server.command[1:]]
            return Resolved(server, command, _options(worktree_path, server, file))
    return None


def installed(
    worktree_path: Path, servers: Sequence[LanguageServer]
) -> list[LanguageServer]:
    """The servers of ``servers`` whose command resolves for ``worktree_path``."""
    return [s for s in servers if _resolve(worktree_path, s.command[0])]


def reads(server: LanguageServer, file: str) -> bool:
    return any(glob_matches(g, file) for g in server.globs)


def language_id(file: str) -> str:
    """The LSP ``languageId`` of ``file``, from its suffix."""
    return _LANGUAGE_IDS.get(PurePosixPath(file).suffix, "plaintext")


def _options(worktree_path: Path, server: LanguageServer, file: str) -> dict[str, Any]:
    runtime = server.local_runtime
    if runtime is None:
        return {}
    worktree = worktree_path.absolute()
    for parent in PurePosixPath(file).parents:
        found = worktree / parent / runtime.path
        if found.is_file():
            options: dict[str, Any] = {runtime.option[-1]: str(found)}
            for key in reversed(runtime.option[:-1]):
                options = {key: options}
            return options
    return {}


def _resolve(worktree_path: Path, name: str) -> str | None:
    if "/" not in name:
        for local in (
            worktree_path / ".venv/bin" / name,
            worktree_path / "node_modules/.bin" / name,
        ):
            if os.access(local, os.X_OK):
                return str(local)
    return shutil.which(name)
