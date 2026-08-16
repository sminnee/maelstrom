"""Storage layer for stack bases.

A branch's base — the branch its work is stacked on — is stored in git config,
following the same storage/model/CLI split as :mod:`maelstrom.env_store` (see
``docs/dev/architecture-patterns.md``). Two keys per branch, plus one per project:

- ``branch.<name>.maelBase`` — the base branch name.
- ``branch.<name>.maelBaseTip`` — ``origin/<base>``'s SHA at the last successful
  rebase, so a base amended during review still replays cleanly.
- ``maelstrom.stackTip`` — the branch new worktrees stack on.

**Why git config.** The base is a property of the *branch*, and worktrees are
recycled, so per-worktree storage loses it. Several tasks share one branch, so a
task field would need a precedence rule for disagreeing siblings, and ``mael add
<branch>`` creates worktrees with no task at all. The tip is a hot machine write
on every sync, which would churn task markdown. Above all, ``git config`` without
``--worktree`` resolves to ``$GIT_COMMON_DIR/config`` from any linked worktree, so
every worktree in a project sees the same bases — the exact property ``gh stack``'s
``.git/gh-stack`` state file lacks, and the reason its local commands are unusable
here. ``git branch -d`` deletes the whole ``[branch]`` section, so cleanup is free.

Two backends are provided:

- :class:`InMemoryBaseStore` — a ``dict``-backed store with no git; model tests run
  against it.
- :class:`GitConfigBaseStore` — the real one. :meth:`all` uses a single
  ``git config --get-regexp`` so batch callers never pay a subprocess per worktree.
"""

import subprocess
from pathlib import Path
from typing import Protocol

from .shell import run_cmd
from .worktree_model import MAIN_BRANCH, BaseRef

# The config keys. Written camelCase for readability in ``.git/config``; git
# lowercases the third component on read, which :meth:`GitConfigBaseStore.all`
# normalises for.
BASE_KEY = "maelBase"
BASE_TIP_KEY = "maelBaseTip"
STACK_TIP_KEY = "maelstrom.stackTip"


class BaseStore(Protocol):
    """A branch -> :class:`BaseRef` store, plus the project's stack tip.

    :meth:`read` never returns ``None``: an unset branch reads as the default
    ``BaseRef()``, so every caller gets a base without a not-set branch of its own.
    """

    def read(self, branch: str) -> BaseRef:
        """Return ``branch``'s base, or the default ``BaseRef()`` if unset."""
        ...

    def write(self, branch: str, base: BaseRef) -> None:
        """Store ``base`` for ``branch``, replacing any previous value.

        A ``base`` with no ``tip`` clears any stored tip: a stale tip would make
        the next rebase replay from the wrong point, which is worse than none.
        """
        ...

    def clear(self, branch: str) -> None:
        """Remove ``branch``'s base, returning it to the default. A no-op if unset."""
        ...

    def all(self) -> dict[str, str]:
        """Return every stored ``branch -> base branch`` pair, in one call."""
        ...

    def read_stack_tip(self) -> str:
        """Return the branch new worktrees stack on. Defaults to ``main``."""
        ...

    def write_stack_tip(self, branch: str) -> None:
        """Point the stack tip at ``branch``."""
        ...


class InMemoryBaseStore:
    """A ``dict``-backed :class:`BaseStore` with no git, for fast unit tests."""

    def __init__(self) -> None:
        self._bases: dict[str, BaseRef] = {}
        self._stack_tip: str = MAIN_BRANCH

    def read(self, branch: str) -> BaseRef:
        return self._bases.get(branch, BaseRef())

    def write(self, branch: str, base: BaseRef) -> None:
        self._bases[branch] = base

    def clear(self, branch: str) -> None:
        self._bases.pop(branch, None)

    def all(self) -> dict[str, str]:
        return {branch: base.branch for branch, base in self._bases.items()}

    def read_stack_tip(self) -> str:
        return self._stack_tip

    def write_stack_tip(self, branch: str) -> None:
        self._stack_tip = branch


class GitConfigBaseStore:
    """A :class:`BaseStore` backed by the repository's shared git config.

    ``repo_path`` may be the project root or any linked worktree — plain
    ``git config`` resolves to the shared config either way, which is what makes
    a base set in one worktree visible from all of them.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def _read(self, args: list[str]) -> subprocess.CompletedProcess | None:
        """Run a reading ``git config`` call, or return ``None`` if it could not run.

        A missing directory or a missing ``git`` raises rather than returning a
        code. Callers read bases to *decorate* output — a ``mael list`` row, a
        "Syncing …" line — so an unreadable store must degrade to "no bases", not
        fail the command that asked.
        """
        try:
            return run_cmd(
                ["git", "config", *args],
                cwd=self.repo_path, quiet=True, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def _write(self, args: list[str], *, tolerate_missing: bool = False) -> None:
        """Run a writing ``git config`` call, raising if it did not take effect.

        Writes do not get the read path's tolerance. A silently dropped write is
        the one failure this store must never have: the base tip would keep a
        stale value, and the next rebase would replay from the wrong point with
        nothing reporting why. An unwritable config (read-only checkout, a lock
        held by a concurrent git process) has to surface.

        ``--unset`` returns 5 when the key was not there, which is success for a
        clear, so that code is tolerated when asked for.

        Raises:
            RuntimeError: If the write could not be made.
        """
        try:
            result = run_cmd(
                ["git", "config", *args],
                cwd=self.repo_path, quiet=True, check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"Could not write git config in {self.repo_path}: {e}")
        if result.returncode == 0:
            return
        if tolerate_missing and result.returncode == 5:
            return  # nothing to unset
        raise RuntimeError(
            f"Could not write git config in {self.repo_path}: "
            f"{result.stderr.strip() or f'git config exited {result.returncode}'}"
        )

    def _get(self, key: str) -> str | None:
        result = self._read(["--get", key])
        if result is None or result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    def _set(self, key: str, value: str) -> None:
        self._write([key, value])

    def _unset(self, key: str) -> None:
        self._write(["--unset", key], tolerate_missing=True)

    def read(self, branch: str) -> BaseRef:
        base = self._get(f"branch.{branch}.{BASE_KEY}")
        if base is None:
            return BaseRef()
        return BaseRef(branch=base, tip=self._get(f"branch.{branch}.{BASE_TIP_KEY}"))

    def write(self, branch: str, base: BaseRef) -> None:
        self._set(f"branch.{branch}.{BASE_KEY}", base.branch)
        if base.tip is None:
            self._unset(f"branch.{branch}.{BASE_TIP_KEY}")
        else:
            self._set(f"branch.{branch}.{BASE_TIP_KEY}", base.tip)

    def clear(self, branch: str) -> None:
        """Remove ``branch``'s base. A branch with nothing stored is left alone.

        The read comes first so clearing an already-unstacked branch touches
        nothing: that is the common case on the ``mael add`` path, and it must not
        need a writable store to do nothing.
        """
        if self.read(branch).is_default:
            return
        self._unset(f"branch.{branch}.{BASE_KEY}")
        self._unset(f"branch.{branch}.{BASE_TIP_KEY}")

    def all(self) -> dict[str, str]:
        """Every stored base, from one ``--get-regexp``.

        ``mael list`` and ``sync-all`` want the whole project's bases; asking per
        worktree would put a subprocess on a hot path. Note git lowercases the
        third config component on read, so the returned key is ``maelbase``, not
        ``maelBase`` — matched case-insensitively here. The branch name itself
        keeps its case, and may contain dots and slashes, so the key is split from
        the right.
        """
        result = self._read(["--get-regexp", rf"^branch\..*\.{BASE_KEY}$"])
        if result is None or result.returncode != 0:
            return {}

        bases: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            if not value:
                continue
            head, _, suffix = key.rpartition(".")
            if suffix.lower() != BASE_KEY.lower() or not head.startswith("branch."):
                continue
            branch = head[len("branch."):]
            if branch:
                bases[branch] = value.strip()
        return bases

    def read_stack_tip(self) -> str:
        return self._get(STACK_TIP_KEY) or MAIN_BRANCH

    def write_stack_tip(self, branch: str) -> None:
        self._set(STACK_TIP_KEY, branch)
