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

Callers work through :class:`LiveSessionSet`, which sweeps once on first use,
then answers per-worktree questions (``count_for`` / ``active_for`` / ``all_for``)
off that shared list — each session attributing itself to a worktree via
:attr:`LiveSession.worktree`. It sits above
:func:`maelstrom.task.session_id_for` and beside :mod:`maelstrom.session_store`,
with no import cycle: ``session_store`` never imports this module.
"""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .shell import run_cmd


@dataclass
class LiveSession:
    """A running Claude CLI process and its working directory.

    ``cwd`` is where the session runs; :attr:`worktree` is the worktree that owns
    it. Every :class:`LiveSession` a lookup returns was live *at scan time* —
    there is no ``is_live`` flag because a non-live process is simply absent from
    the sweep. Callers acting on a returned session (e.g. the run-guard) accept
    the small TOCTOU window in which the pid may exit before they use it.
    """

    pid: int
    cwd: Path

    @cached_property
    def worktree(self) -> Path | None:
        """The worktree that owns this session's cwd, or ``None``.

        A worktree root is the nearest ancestor of ``cwd`` (including ``cwd``
        itself) that carries a ``.git`` entry — a file for a linked worktree, a
        directory for the main checkout. ``mael`` launches ``claude`` with its
        cwd *at* the worktree root, so this is usually ``cwd`` itself; the walk
        only matters when a session cd'd into a subdirectory. A nested worktree
        has its own ``.git``, so it wins over its parent without a prefix
        tiebreak. Cheaper and more robust than shelling ``git worktree list``.
        """
        for path in (self.cwd, *self.cwd.parents):
            if (path / ".git").exists():
                return path
        return None


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


class LiveSessionSet:
    """One live-``claude`` sweep plus the per-worktree questions asked of it.

    Construct once, then ask ``count_for`` / ``active_for`` / ``all_for`` per
    worktree. The sweep (:func:`all_live_sessions`) runs lazily on first access
    and is cached, so a batch caller (``mael list`` over many rows, ``reconcile``
    over many branches) shells ``pgrep``/``lsof`` once for the whole pass.
    Attribution is each session's own :attr:`LiveSession.worktree`.

    Pass ``sessions`` to reuse a sweep taken elsewhere (or to inject a fixture);
    omit it to sweep on first use.
    """

    def __init__(self, sessions: list[LiveSession] | None = None) -> None:
        self._sessions = sessions

    @property
    def sessions(self) -> list[LiveSession]:
        """The swept live sessions, taking the sweep on first access."""
        if self._sessions is None:
            self._sessions = all_live_sessions()
        return self._sessions

    def all_for(self, worktree_path: Path) -> list[LiveSession]:
        """Every live session owned by ``worktree_path``.

        Used by ``mael close`` to stop a worktree's sessions before tearing it
        down.
        """
        return [s for s in self.sessions if s.worktree == worktree_path]

    def active_for(self, worktree_path: Path) -> LiveSession | None:
        """The first live session in ``worktree_path``, or ``None``.

        Drives the ``mael task run`` duplicate-launch guard and ``reconcile``.
        """
        return next((s for s in self.sessions if s.worktree == worktree_path), None)

    def count_for(self, worktree_path: Path) -> int:
        """How many live sessions run in ``worktree_path``.

        Drives the ``SESSION`` column of ``mael list`` / ``mael list-all``.
        """
        return sum(1 for s in self.sessions if s.worktree == worktree_path)
