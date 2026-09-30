"""A branch's own work: the refs its diff is measured against."""

import subprocess
from pathlib import Path

from .base_store import GitConfigBaseStore
from .worktree import get_current_branch
from .worktree_model import MAIN_BRANCH


def base_refs_for_diff(cwd: Path) -> list[str]:
    """Refs to diff a branch against, best first.

    A stacked branch diffs against its base, so a reader sees only this branch's
    own work rather than the whole stack. ``origin/main`` follows as a fallback for
    a base that has merged and been pruned.
    """
    base = resolve_base_branch(cwd)
    refs = [f"origin/{base}"]
    if base != MAIN_BRANCH:
        refs.append(f"origin/{MAIN_BRANCH}")
    return refs


def resolve_base_branch(cwd: Path) -> str:
    """The branch ``cwd``'s work is stacked on, or ``main`` if it is not stacked.

    Never raises: a worktree whose branch or config cannot be read falls back to
    ``main``, which is what every branch used before stacking existed.
    """
    try:
        return GitConfigBaseStore(cwd).read(get_current_branch(cwd)).branch
    except (OSError, subprocess.SubprocessError):
        return MAIN_BRANCH
