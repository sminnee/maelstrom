"""Answer "is there a **live** Claude session for this task / branch / worktree?"

The authoritative, fast signal is the live ``claude`` CLI processes themselves
and their working directories. A running ``claude`` session's cwd *is* the
worktree it was launched in, so one ``pgrep -x claude`` plus one batched
``lsof -a -d cwd`` gives every live session's real worktree path in ~0.03s.

This deliberately does **not** consult transcript files or the ``~/.maelstrom``
session registry to decide liveness:

- A running ``claude`` CLI does not hold its transcript file-descriptor open
  (it appends-and-closes), so ``lsof`` on transcripts reports nothing for live
  sessions and false-positives on editor tabs — an empirically wrong signal,
  and slow (a system-wide ``lsof`` sweep per worktree made ``mael list`` take
  ~49s).
- The registry (``~/.maelstrom/sessions/*.json``) misses the current session
  and its ``state`` goes stale, so it cannot be the liveness authority. It
  survives only as *optional enrichment* for ``mael session list``.

Every caller filters the single :func:`all_live_sessions` list. It sits above
:func:`maelstrom.task.session_id_for` and beside :mod:`maelstrom.session_store`,
with no import cycle: ``session_store`` never imports this module.
"""

from dataclasses import dataclass
from pathlib import Path

from .shell import run_cmd
from .worktree import list_worktrees


@dataclass
class LiveSession:
    """A running Claude CLI process and its working directory.

    ``cwd`` is the real worktree path the session was launched in (its process
    cwd). Every :class:`LiveSession` a lookup returns was live *at scan time* —
    there is no ``is_live`` flag because a non-live process is simply absent from
    the sweep. Callers acting on a returned session (e.g. the run-guard) accept
    the small TOCTOU window in which the pid may exit before they use it.
    """

    pid: int
    cwd: Path


def all_live_sessions() -> list[LiveSession]:
    """Every running Claude CLI session and its cwd, in one pgrep + one lsof.

    1. ``pgrep -x claude`` → the pids of the real CLI. ``-x`` matches the exact
       command name, so ``bun`` MCP-channel helpers and ``Code Helper`` are
       excluded — only the CLI itself.
    2. ``lsof -a -d cwd -p <pids> -F pn`` → one call returning each pid's cwd as
       ``-F`` records (``p<pid>`` / ``n<path>``). A pid whose cwd can't be read
       is skipped.

    Both external calls tolerate a missing binary or non-zero exit and yield an
    empty list rather than raising, so a box with no ``claude`` running (or
    without ``pgrep``/``lsof``) reports ``[]``.
    """
    pids = _claude_pids()
    if not pids:
        return []
    return _cwds_for_pids(pids)


def _claude_pids() -> list[int]:
    """Pids of the running ``claude`` CLI, via ``pgrep -x claude``.

    ``check=False`` because ``pgrep`` exits 1 when nothing matches — that is a
    normal "no sessions" result, not an error. A missing ``pgrep`` binary or any
    other failure also yields ``[]``.
    """
    try:
        result = run_cmd(["pgrep", "-x", "claude"], quiet=True, check=False)
    except (OSError, ValueError):
        return []
    pids: list[int] = []
    for line in result.stdout.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _cwds_for_pids(pids: list[int]) -> list[LiveSession]:
    """Resolve each pid's cwd with one batched ``lsof -a -d cwd``.

    ``-F pn`` prints machine-readable records: ``p<pid>`` starts a process
    block, ``n<path>`` gives its cwd. We pair them into :class:`LiveSession`s,
    skipping any pid ``lsof`` reports without a readable cwd. ``check=False``
    because ``lsof`` exits non-zero when some pids have already gone.
    """
    args = ["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-F", "pn"]
    try:
        result = run_cmd(args, quiet=True, check=False)
    except (OSError, ValueError):
        return []
    sessions: list[LiveSession] = []
    pid: int | None = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None:
            sessions.append(LiveSession(pid=pid, cwd=Path(value)))
            pid = None
    return sessions


def _cwd_under(cwd: Path, worktree_path: Path) -> bool:
    """True if ``cwd`` is ``worktree_path`` itself or nested beneath it."""
    return cwd == worktree_path or worktree_path in cwd.parents


# Per-sweep memo of ``list_worktrees(cwd)`` results, keyed on the session cwd.
# The longest-prefix tiebreak needs the cwd's own repo's worktree paths; without
# a cache, ``mael list`` would re-shell ``git worktree list`` once per session
# *per worktree row* (O(worktrees × sessions) subprocesses). A batch caller
# passes one dict so every session's worktree list is fetched at most once.
_WorktreeCache = dict[Path, list[Path]]


def _owning_worktree(cwd: Path, cache: _WorktreeCache) -> Path | None:
    """The most-specific worktree path that owns ``cwd``, or ``None``.

    Resolves against the git worktree list of ``cwd``'s own repo (memoised in
    ``cache``): the *longest* worktree path that is a prefix of ``cwd`` wins, so
    a nested worktree and its parent don't both claim the same cwd. ``None`` when
    ``cwd`` sits under no known worktree.
    """
    paths = cache.get(cwd)
    if paths is None:
        paths = [wt.path for wt in list_worktrees(cwd)]
        cache[cwd] = paths
    best: Path | None = None
    for path in paths:
        if _cwd_under(cwd, path) and (best is None or len(str(path)) > len(str(best))):
            best = path
    return best


def live_session_count_for_worktree(
    worktree_path: Path,
    sessions: list[LiveSession] | None = None,
    cache: _WorktreeCache | None = None,
) -> int:
    """How many live Claude sessions are running in ``worktree_path``.

    Counts the sessions whose cwd resolves to ``worktree_path`` (longest-prefix
    tiebreak, so a nested worktree and its parent don't both claim a session).
    Drives the ``SESSION`` column of ``mael list`` / ``mael list-all``.

    ``sessions`` lets a batch caller (``mael list`` over many worktrees) sweep
    :func:`all_live_sessions` once and share it; ``None`` sweeps per call.
    ``cache`` is a shared worktree-list memo the batch caller threads through
    every row so ``git worktree list`` runs at most once per distinct cwd.
    """
    if sessions is None:
        sessions = all_live_sessions()
    if cache is None:
        cache = {}
    return sum(
        1 for s in sessions if _owning_worktree(s.cwd, cache) == worktree_path
    )


def active_session_for_worktree(
    worktree_path: Path,
    sessions: list[LiveSession] | None = None,
    cache: _WorktreeCache | None = None,
) -> LiveSession | None:
    """The live Claude session running in ``worktree_path``, or ``None``.

    Returns the first live session whose cwd resolves to ``worktree_path``
    (longest-prefix tiebreak). ``None`` when nothing live runs there. Used by the
    ``mael task run`` duplicate-launch guard. ``cache`` memoises the worktree-list
    lookup when a batch caller (reconcile) resolves many branches in one sweep.
    """
    if sessions is None:
        sessions = all_live_sessions()
    if cache is None:
        cache = {}
    for session in sessions:
        if _owning_worktree(session.cwd, cache) == worktree_path:
            return session
    return None
