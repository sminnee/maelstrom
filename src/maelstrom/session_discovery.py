"""Answer "is there a **live** Claude session for this task / branch / worktree?"

The authoritative, fast signal is the live ``claude`` CLI processes themselves
and their working directories. A running ``claude`` session's cwd *is* the
worktree it was launched in, so one ``pgrep -x claude`` plus one batched
``lsof -a -d cwd`` gives every live session's real worktree path in ~0.03s. A
third batched call — ``ps -o command=`` — reads each process's command line so
we can recover the ``--session-id`` ``mael`` launched it with, the durable link
back to the task even when the registry has no file for the session.

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

``pgrep`` finds most sessions, not all of them: the ``claude`` that runs ``mael``
can be missing from its own sweep. So a caller that already holds a pid —
``CLAUDE_PID``, or one a user typed — uses :func:`session_for_pid`, which asks
the process directly instead of searching the sweep. The process stays the
authority either way; only the search for it is incomplete.

Callers work through :class:`LiveSessionSet`, which sweeps once on first use,
then answers per-worktree questions (``count_for`` / ``active_for`` / ``all_for``)
off that shared list — each session attributing itself to a worktree via
:attr:`LiveSession.worktree`. It sits above
:func:`maelstrom.task.session_id_for` and beside :mod:`maelstrom.session_store`,
with no import cycle: ``session_store`` never imports this module.
"""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePath

from .shell import run_cmd_async

# ``mael`` launches ``claude --session-id <uuid>``, and continues an existing
# session with ``claude --resume <uuid>``; recover that uuid from either.
# Matches a canonical uuid, so a bare ``claude`` with no flag — or a
# ``--resume`` with no id, which opens a picker — simply yields ``None``.
_SESSION_ID_RE = re.compile(
    r"--(?:session-id|resume)[=\s]+"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
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

    1. ``pgrep -x claude`` → the pids of the real CLI. ``-x`` matches the exact
       command name, so ``bun`` MCP-channel helpers and ``Code Helper`` are
       excluded — only the CLI itself.
    2. ``lsof -a -d cwd -p <pids> -F pn`` → one call returning each pid's cwd as
       ``-F`` records (``p<pid>`` / ``n<path>``). A pid whose cwd can't be read
       is skipped.
    3. ``ps -o pid=,command= -p <pids>`` → one call returning each pid's command
       line, from which we parse the ``--session-id`` uuid. A process without
       the flag (a bare ``claude``) just yields ``session_id=None``.

    All three external calls tolerate a missing binary or non-zero exit and yield
    an empty result rather than raising, so a box with no ``claude`` running (or
    without ``pgrep``/``lsof``/``ps``) reports ``[]`` — and a missing ``ps`` only
    costs the session-ids, not the sweep.
    """
    pids = await _claude_pids()
    if not pids:
        return []
    sessions = await _cwds_for_pids(pids)
    session_ids = await _session_ids_for_pids(pids)
    for s in sessions:
        s.session_id = session_ids.get(s.pid)
    return sessions


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
    command = (await _commands_for_pids([pid])).get(pid)
    if command is None or not _is_claude_command(command):
        return None
    sessions = await _cwds_for_pids([pid])
    found = next((s for s in sessions if s.pid == pid), None)
    if found is None:
        return None
    match = _SESSION_ID_RE.search(command)
    found.session_id = match.group(1) if match else None
    return found


async def _claude_pids() -> list[int]:
    """Pids of the running ``claude`` CLI, via ``pgrep -x claude``.

    ``check=False`` because ``pgrep`` exits 1 when nothing matches — that is a
    normal "no sessions" result, not an error. A missing ``pgrep`` binary or any
    other failure also yields ``[]``.
    """
    try:
        result = await run_cmd_async(["pgrep", "-x", "claude"], quiet=True, check=False)
    except (OSError, ValueError):
        return []
    pids: list[int] = []
    for line in result.stdout.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


async def _cwds_for_pids(pids: list[int]) -> list[LiveSession]:
    """Resolve each pid's cwd with one batched ``lsof -a -d cwd``.

    ``-F pn`` prints machine-readable records: ``p<pid>`` starts a process
    block, ``n<path>`` gives its cwd. We pair them into :class:`LiveSession`s,
    skipping any pid ``lsof`` reports without a readable cwd. ``check=False``
    because ``lsof`` exits non-zero when some pids have already gone.
    """
    args = ["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-F", "pn"]
    try:
        result = await run_cmd_async(args, quiet=True, check=False)
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


async def _commands_for_pids(pids: list[int]) -> dict[int, str]:
    """Map ``pid -> command line`` with one batched ``ps``.

    ``ps -ww -o pid=,command= -p <pids>`` gives one ``<pid> <cmd…>`` line per
    live pid. ``-ww`` prints the command line at unlimited width, so a flag late
    in the args is never clipped by column truncation. A pid that has already
    gone is absent from the map. ``check=False`` because ``ps`` exits non-zero
    when some pids have gone; a missing ``ps`` binary yields an empty map.
    """
    args = ["ps", "-ww", "-o", "pid=,command=", "-p", ",".join(str(p) for p in pids)]
    try:
        result = await run_cmd_async(args, quiet=True, check=False)
    except (OSError, ValueError):
        return {}
    mapping: dict[int, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        try:
            pid = int(head)
        except ValueError:
            continue
        mapping[pid] = rest
    return mapping


def _is_claude_command(command: str) -> bool:
    """Whether ``command`` is a ``claude`` CLI process, from its ``ps`` line.

    Only the executable counts, so a command that merely mentions ``claude`` in
    its arguments — ``mael`` itself does, constantly — is not a session. This is
    the same test ``pgrep -x claude`` makes, applied to one process the sweep did
    not return.
    """
    head = command.split(maxsplit=1)[0] if command.strip() else ""
    return PurePath(head).name == "claude"


async def _session_ids_for_pids(pids: list[int]) -> dict[int, str]:
    """Map ``pid -> session-id``, read from each pid's command line.

    The id comes from the ``--session-id`` flag the launcher passes. A pid whose
    line lacks the flag is simply absent from the map (→ ``session_id=None``);
    a bare ``claude`` the user started carries no id.
    """
    mapping: dict[int, str] = {}
    for pid, command in (await _commands_for_pids(pids)).items():
        m = _SESSION_ID_RE.search(command)
        if m:
            mapping[pid] = m.group(1)
    return mapping


# --- the process table, for the agent daemon's reconcile -------------------


@dataclass(frozen=True)
class ProcessInfo:
    """One process from the table: its pid, its process group, and its argv.

    ``command`` is the full command line at unlimited width. A child launched
    from cmux carries a long ``--settings {…}`` JSON in its argv, so a clipped
    line would hide the ``--session-id`` that comes after it.
    """

    pid: int
    pgid: int
    command: str


class ProcessTableUnavailable(RuntimeError):
    """``ps`` or ``pgrep`` could not read the process table at all.

    Distinct from "no matching process": inside an agent sandbox both tools
    fail outright, and a reconcile that read that as "every child is dead"
    would rewrite every record as crashed.
    """


def session_id_in(command: str) -> str | None:
    """The ``--session-id`` or ``--resume`` uuid in ``command``, or ``None``."""
    match = _SESSION_ID_RE.search(command)
    return match.group(1) if match else None


#: The two flags only a daemon-driven ``claude`` carries. Together they are
#: the mark of a driven agent; an interactive session has neither.
_DRIVEN_FLAGS = ("--input-format stream-json", "--permission-prompt-tool stdio")


def is_driven(command: str) -> bool:
    """Whether ``command`` is a daemon-driven ``claude``, from its argv."""
    return all(flag in command for flag in _DRIVEN_FLAGS)


async def list_claude_processes() -> list[ProcessInfo]:
    """Every ``claude`` process on the machine, with its group and its argv.

    The union of ``pgrep -x claude`` (the CLI by name) and
    ``pgrep -f -- '--permission-prompt-tool stdio'`` (a driven agent by its
    flag, whatever the executable is called), then one ``ps -ww`` for the
    group ids and the full command lines.

    Raises:
        ProcessTableUnavailable: If ``pgrep`` or ``ps`` failed for any reason
            other than "nothing matched". ``pgrep`` exits 1 for no match and
            something else when it cannot read the table; ``ps`` exits 1 when
            some of the pids have already gone, which is a normal race, and
            cannot run at all where the table is closed.
    """
    pids: set[int] = set()
    for argv in (
        ["pgrep", "-x", "claude"],
        ["pgrep", "-f", "--", "--permission-prompt-tool stdio"],
    ):
        try:
            result = await run_cmd_async(argv, quiet=True, check=False)
        except (OSError, ValueError) as exc:
            raise ProcessTableUnavailable(f"pgrep failed: {exc}") from exc
        if result.returncode not in (0, 1):
            raise ProcessTableUnavailable(
                f"pgrep exited {result.returncode}: {result.stderr.strip()}"
            )
        for token in result.stdout.split():
            try:
                pids.add(int(token))
            except ValueError:
                continue
    if not pids:
        return []
    args = ["ps", "-ww", "-o", "pid=,pgid=,command=", "-p", ",".join(map(str, pids))]
    try:
        result = await run_cmd_async(args, quiet=True, check=False)
    except (OSError, ValueError) as exc:
        raise ProcessTableUnavailable(f"ps failed: {exc}") from exc
    if result.returncode not in (0, 1):
        raise ProcessTableUnavailable(
            f"ps exited {result.returncode}: {result.stderr.strip()}"
        )
    return parse_process_table(result.stdout)


def parse_process_table(text: str) -> list[ProcessInfo]:
    """``ps -o pid=,pgid=,command=`` output as :class:`ProcessInfo` rows.

    A line that does not start with two integers is not a process and is
    skipped.
    """
    rows: list[ProcessInfo] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, pgid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        rows.append(
            ProcessInfo(pid=pid, pgid=pgid, command=parts[2] if len(parts) > 2 else "")
        )
    return rows


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
