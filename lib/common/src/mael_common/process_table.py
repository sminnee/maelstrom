"""The ``claude`` processes on this machine, read from the process table.

A leaf: it shells ``pgrep``, ``ps`` and ``lsof`` and knows nothing of worktrees
or tasks. It is the one reader of the table. The agent daemon reads it to
reconcile its records and to subtract live sessions from the stopped listing;
:mod:`maelstrom.session_discovery` builds its worktree answers on top of it.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .shell import run_cmd_async

# ``mael`` launches ``claude --session-id <uuid>``, and continues an existing
# session with ``claude --resume <uuid>``; recover that uuid from either.
# Matches a canonical uuid, so a bare ``claude`` with no flag — or a
# ``--resume`` with no id, which opens a picker — simply yields ``None``.
_SESSION_ID_RE = re.compile(
    r"--(?:session-id|resume)[=\s]+"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


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


async def command_of(pid: int) -> str | None:
    """``pid``'s full command line, or ``None`` when ``ps`` cannot say.

    One process, asked directly, for a caller that already holds a pid and
    must not search the table for it. ``-ww`` keeps a flag late in a long argv.
    A pid that has gone, or a ``ps`` that will not run, is ``None``: the
    caller refuses rather than guesses.
    """
    try:
        result = await run_cmd_async(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)], quiet=True, check=False
        )
    except (OSError, ValueError):
        return None
    return result.stdout.strip() or None


async def cwds_for_pids(pids: list[int]) -> dict[int, Path]:
    """Each pid's cwd, from one batched ``lsof -a -d cwd``.

    ``-F pn`` prints machine-readable records: ``p<pid>`` starts a process
    block, ``n<path>`` gives its cwd. A pid ``lsof`` reports without a readable
    cwd is absent from the map. ``check=False`` because ``lsof`` exits non-zero
    when some pids have already gone; a missing ``lsof`` yields an empty map.
    """
    args = ["lsof", "-a", "-d", "cwd", "-p", ",".join(str(p) for p in pids), "-F", "pn"]
    try:
        result = await run_cmd_async(args, quiet=True, check=False)
    except (OSError, ValueError):
        return {}
    cwds: dict[int, Path] = {}
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
            cwds[pid] = Path(value)
            pid = None
    return cwds
