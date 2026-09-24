"""Answer "is there a **live** Claude session for this task / branch / worktree?"

The authoritative, fast signal is the live ``claude`` CLI processes themselves
and their working directories. A running ``claude`` session's cwd *is* the
worktree it was launched in. :mod:`mael_common.process_table` reads the
``claude`` processes with their command lines, and one batched ``lsof -a -d
cwd`` gives every live session's real worktree path in ~0.03s. The command line
carries the ``--session-id`` ``mael`` launched it with, the durable link back
to the task.

This deliberately does **not** consult transcript files to decide liveness,
and a session registry was tried and removed:

- A running ``claude`` CLI does not hold its transcript file-descriptor open
  (it appends-and-closes), so ``lsof`` on transcripts reports nothing for live
  sessions and false-positives on editor tabs — an empirically wrong signal,
  and slow (a system-wide ``lsof`` sweep per worktree made ``mael list`` take
  ~49s).
- A registry (``~/.maelstrom/sessions/*.json``), written per session, missed
  the current session and let its ``state`` go stale, so it could never be the
  liveness authority. It has been removed.

``pgrep`` finds most sessions, not all of them: the ``claude`` that runs ``mael``
can be missing from its own sweep. So a caller that already holds a pid —
``CLAUDE_PID``, or one a user typed — uses :func:`session_for_pid`, which asks
the process directly instead of searching the sweep. The process stays the
authority either way; only the search for it is incomplete.

Callers work through :class:`LiveSessionSet`, which sweeps once on first use,
then answers per-worktree questions (``count_for`` / ``active_for`` / ``all_for``)
off that shared list — each session attributing itself to a worktree via
:attr:`LiveSession.worktree`. It sits above
:func:`mael_domain.task.session_id_for`.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePath

from mael_common.process_table import (
    ProcessTableUnavailable,
    command_of,
    cwds_for_pids,
    list_claude_processes,
    session_id_in,
)


@dataclass
class LiveSession:
    """A running Claude CLI process, its working directory, and its session-id.

    ``cwd`` is where the session runs; :attr:`worktree` is the worktree that owns
    it. ``session_id`` is the ``--session-id`` uuid ``mael`` launched it with
    (``None`` for a bare ``claude`` started outside ``mael``), the task-precise
    key the run-guard and ``session list`` correlate on. Every
    :class:`LiveSession` a lookup returns was live *at scan time* — there is no
    ``is_live`` flag because a non-live process is simply absent from the sweep.
    Callers acting on a returned session (e.g. the run-guard) accept the small
    TOCTOU window in which the pid may exit before they use it.
    """

    pid: int
    cwd: Path
    session_id: str | None = None

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


async def all_live_sessions() -> list[LiveSession]:
    """Every running Claude CLI session, its cwd, and its session-id.

    Read from :mod:`mael_common.process_table`: the ``claude`` processes and
    their command lines, then one batched ``lsof`` for their cwds. Only a
    process whose executable is ``claude`` counts, the test ``pgrep -x claude``
    makes, so a helper that merely carries a driven agent's flags is not a
    session. A process without ``--session-id`` or ``--resume <uuid>`` (a bare
    ``claude``) yields ``session_id=None``, and one whose cwd cannot be read is
    skipped.

    A process table that cannot be read at all reads as no sessions, as it does
    for the daemon's stopped listing: a caller asking "is anything live?"
    gets "no" rather than a crash.
    """
    try:
        processes = await list_claude_processes()
    except ProcessTableUnavailable:
        return []
    commands = {p.pid: p.command for p in processes if _is_claude_command(p.command)}
    if not commands:
        return []
    return [
        LiveSession(pid=pid, cwd=cwd, session_id=session_id_in(commands[pid]))
        for pid, cwd in (await cwds_for_pids(list(commands))).items()
    ]


async def session_for_pid(pid: int) -> LiveSession | None:
    """The live session for ``pid``, built from the process rather than a sweep.

    ``all_live_sessions`` finds sessions with ``pgrep``, which does not report
    every live ``claude``: on some setups the ``claude`` that is running ``mael``
    is itself missing from the sweep, so a session cannot look itself up. A pid
    the caller already trusts — ``CLAUDE_PID``, or one a user typed — needs no
    search, so this reads the process directly.

    ``None`` for a pid this cannot describe as a session. That is either a pid
    whose command is not ``claude``, or one whose cwd ``lsof`` cannot read.
    Neither may be guessed at: ``mael session end`` signals the pid it resolves,
    so a mistyped pid must not reach an unrelated process, and a missing cwd
    would otherwise be reported as the caller's own.
    """
    command = await command_of(pid)
    if command is None or not _is_claude_command(command):
        return None
    cwd = (await cwds_for_pids([pid])).get(pid)
    if cwd is None:
        return None
    return LiveSession(pid=pid, cwd=cwd, session_id=session_id_in(command))


def _is_claude_command(command: str) -> bool:
    """Whether ``command`` is a ``claude`` CLI process, from its ``ps`` line.

    Only the executable counts, so a command that merely mentions ``claude`` in
    its arguments — ``mael`` itself does, constantly — is not a session. This is
    the same test ``pgrep -x claude`` makes, applied to one process the sweep did
    not return.
    """
    head = command.split(maxsplit=1)[0] if command.strip() else ""
    return PurePath(head).name == "claude"


def _sweep_blocking() -> list[LiveSession]:
    """Take the sweep from synchronous code, whatever loop the caller holds.

    :func:`all_live_sessions` is a coroutine, so a synchronous caller needs a
    loop to drive it. ``asyncio.run`` is that loop, and it raises when one is
    already running — which happens whenever the orchestrator server runs a
    source with no executor to offload to. So a caller on a running loop gets
    a short-lived thread with a loop of its own instead of an exception.

    That thread blocks the caller for the length of the sweep, exactly as the
    old synchronous code did. Nothing regresses; it is simply the fallback for
    a caller that has not yet moved to ``await``ing
    :meth:`LiveSessionSet.sweep`, which is the path that never blocks.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(all_live_sessions())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(all_live_sessions())).result()


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

    async def sweep(self) -> "LiveSessionSet":
        """Take the sweep now, off the caller's event loop, and return self.

        The async entry point. An ``await``ing caller — the orchestrator
        server, a daemon handler — sweeps through this so ``pgrep``/``lsof``/
        ``ps`` never block its loop, then reads the cached answers off the
        synchronous accessors below. Sweeping twice is a no-op.
        """
        if self._sessions is None:
            self._sessions = await all_live_sessions()
        return self

    @property
    def sessions(self) -> list[LiveSession]:
        """The swept live sessions, taking the sweep on first access.

        Prefer ``await``ing :meth:`sweep`; this property is what the many
        synchronous CLI callers keep using. It sweeps on a private loop, so it
        works in a script, in a worker thread, and — through a short-lived
        thread of its own — on a caller that already holds a running loop.
        After the first access it only reads the cache and costs nothing.
        """
        if self._sessions is None:
            self._sessions = _sweep_blocking()
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

    def for_session_id(self, session_id: str) -> LiveSession | None:
        """The live session whose ``session_id`` matches, or ``None``.

        Task-precise: keys on *this task's own* deterministic session-id rather
        than on worktree occupancy, so the ``mael task run`` guard blocks only a
        genuine relaunch of the same task — a sibling sharing the worktree (one
        PR per parent) no longer trips it. The worktree-granular
        ``active_for``/``all_for``/``count_for`` stay for ``mael close``,
        ``mael list``'s SESSION count, and ``reconcile``.
        """
        return next((s for s in self.sessions if s.session_id == session_id), None)

    def resolve(self, handle: str) -> LiveSession:
        """The live session a user-typed ``handle`` names.

        A handle is a **pid**, a full session-id uuid, or a unique prefix of one
        (four characters or more, so a typo cannot silently hit a session). An
        all-digit handle is always read as a pid: a uuid holds dashes and hex, and
        a pid is what a user reads off ``mael session list``.

        A pid is accepted because it is the only handle that always resolves. A
        session started outside ``mael`` carries no ``--session-id``, and a session
        that has run ``/clear`` holds a new live id that its command line never
        learns about.

        Raises ``KeyError`` when nothing matches, and ``ValueError`` naming the
        candidates when a prefix matches more than one session. The CLI layer turns
        both into a ``ClickException``.
        """
        if handle.isdigit():
            match = next((s for s in self.sessions if s.pid == int(handle)), None)
            if match is None:
                raise KeyError(f"No live session with pid {handle}")
            return match

        exact = self.for_session_id(handle)
        if exact is not None:
            return exact

        if len(handle) >= 4:
            matches = [
                s
                for s in self.sessions
                if s.session_id and s.session_id.startswith(handle)
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                ids = ", ".join(sorted(str(s.session_id) for s in matches))
                raise ValueError(f"Session id prefix '{handle}' is ambiguous: {ids}")

        raise KeyError(f"No live session matching '{handle}'")
