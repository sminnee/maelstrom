"""Sort a diff's files into **Diff groups**, for the Changes tab.

The glob rules are in ``docs/reference/configuration.md``, under ``diff_groups:``.
"""

import logging
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from .config import DiffGroup, load_config_or_default
from .protocol import FileGroup

log = logging.getLogger(__name__)

#: The group of each file that no glob matches. It is always last.
OTHER = "Other"


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    if pattern.endswith("/"):
        pattern += "**"
    if "/" not in pattern:
        pattern = "**/" + pattern
    pattern = pattern.removeprefix("/")
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_matches(pattern: str, path: str) -> bool:
    """Whether ``pattern`` matches all of the repo-relative ``path``."""
    return _glob_regex(pattern).fullmatch(path) is not None


def group_files(paths: Sequence[str], groups: Sequence[DiffGroup]) -> list[FileGroup]:
    """Each path in the first group with a glob that matches it, else in Other.

    Groups keep config order, Other is last, and a group with no files is
    dropped. A group named Other matches at its place in the list, sets the
    fallback's fold, and draws last.
    """
    by_name: dict[str, list[str]] = {g.name: [] for g in groups if g.name != OTHER}
    by_name[OTHER] = []
    for path in paths:
        group = next(
            (g for g in groups if any(glob_matches(p, path) for p in g.paths)), None
        )
        by_name[group.name if group else OTHER].append(path)
    collapsed = {OTHER: False} | {g.name: g.collapsed for g in groups}
    return [
        {"name": name, "collapsed": collapsed[name], "paths": files}
        for name, files in by_name.items()
        if files
    ]


def read_diff_groups(worktree_path: Path) -> list[DiffGroup]:
    """The worktree's ``diff_groups:``, or none when its config does not load.

    A broken config must not break the Changes tab: its files go to Other.
    ``from_dict`` raises untyped errors on a config of the wrong shape.
    """
    try:
        return load_config_or_default(worktree_path).diff_groups
    except Exception as exc:  # noqa: BLE001 — a bad config is not a failed diff
        log.warning("No diff groups for %s: %s", worktree_path, exc)
        return []
