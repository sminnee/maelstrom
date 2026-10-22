"""A worktree's **Dirty files**, read from ``git status``.

The one parser of ``git status --porcelain``. ``mael git status``, the close
check, and the orchestrator's Changes tab all read through it.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from mael_common.shell import run_cmd, run_cmd_async

from .worktree_model import MAELSTROM_MANAGED_FILES

# --no-optional-locks: a read must not take index.lock from under an agent's
# commit in the same worktree. -z: paths come unquoted, and a rename's old path
# is its own entry. --untracked-files=all: each file in a new directory, not
# the directory.
_STATUS = [
    "git",
    "--no-optional-locks",
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
]


@dataclass(frozen=True)
class DirtyFile:
    """One dirty file and git's two status letters for it.

    ``index`` is the staged change and ``worktree`` the unstaged one: a space
    for none, ``?`` for both when the file is untracked.
    """

    path: str
    index: str
    worktree: str

    @property
    def staged(self) -> bool:
        return self.index not in " ?"

    @property
    def unstaged(self) -> bool:
        return self.worktree not in " ?"

    @property
    def untracked(self) -> bool:
        return self.index == "?"


def read_dirty_files(path: Path) -> list[DirtyFile]:
    """The dirty files in the worktree at ``path``, in git's order.

    A path that is not a directory, or a git that fails, reads as clean.
    """
    if not path.is_dir():
        return []
    return _parse(run_cmd(_STATUS, cwd=path, quiet=True, check=False))


async def read_dirty_files_async(path: Path) -> list[DirtyFile]:
    """:func:`read_dirty_files`, without blocking the calling thread."""
    if not path.is_dir():
        return []
    return _parse(await run_cmd_async(_STATUS, cwd=path, quiet=True, check=False))


def _parse(result: subprocess.CompletedProcess) -> list[DirtyFile]:
    if result.returncode != 0:
        return []
    files: list[DirtyFile] = []
    entries = iter(result.stdout.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            # The old path follows as its own entry.
            next(entries, None)
        if name not in MAELSTROM_MANAGED_FILES:
            files.append(DirtyFile(name, code[0], code[1]))
    return files
