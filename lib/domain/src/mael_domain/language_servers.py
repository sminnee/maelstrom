"""The **Language servers** maelstrom knows, and which one reads a file.

The list is hardcoded: ``docs/dev/code-navigation.md``, "The language server".
"""

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .diff_groups import glob_matches


@dataclass(frozen=True)
class LanguageServer:
    """One server: its name, the command that starts it on stdio, and the files it reads."""

    name: str
    #: The executable and its arguments. A bare name resolves in the
    #: worktree's ``.venv/bin`` first, then on ``PATH``.
    command: tuple[str, ...]
    #: Repo-relative globs, in the ``diff_groups:`` syntax.
    globs: tuple[str, ...]


LANGUAGE_SERVERS: tuple[LanguageServer, ...] = (
    LanguageServer("pyright", ("pyright-langserver", "--stdio"), globs=("**/*.py",)),
)


def server_for(
    worktree_path: Path,
    file: str,
    servers: Sequence[LanguageServer] = LANGUAGE_SERVERS,
) -> tuple[LanguageServer, list[str]] | None:
    """The first server whose globs match ``file``, and its resolved command.

    ``None`` when no glob matches, or when the server is not installed: then
    the file is not navigable.
    """
    for server in servers:
        if not reads(server, file):
            continue
        executable = _resolve(worktree_path, server.command[0])
        if executable:
            return server, [executable, *server.command[1:]]
    return None


def reads(server: LanguageServer, file: str) -> bool:
    return any(glob_matches(g, file) for g in server.globs)


def installed(
    worktree_path: Path, servers: Sequence[LanguageServer]
) -> list[LanguageServer]:
    """The servers of ``servers`` whose command resolves for ``worktree_path``."""
    return [s for s in servers if _resolve(worktree_path, s.command[0])]


def _resolve(worktree_path: Path, name: str) -> str | None:
    if "/" not in name:
        venv = worktree_path / ".venv" / "bin" / name
        if os.access(venv, os.X_OK):
            return str(venv)
    return shutil.which(name)
