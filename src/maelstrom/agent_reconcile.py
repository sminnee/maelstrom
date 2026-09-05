"""Reconcile the spawn records with the process table: who owns which ``claude``.

Pure, per ``docs/dev/architecture-patterns.md``: records in, processes in,
verdicts out. The daemon runs it before it resumes anything, and
``mael agent daemon gc | list | reconcile`` run it from the CLI. Nothing here
kills, writes or spawns; :func:`maelstrom.agent_server.apply_reconciliation`
does that with the result.

The question it answers is the one the spawn record used to leave open: is my
predecessor's child still alive? Identity is the session id in the process's
argv, nothing else. A pid the record names that is a driven ``claude`` naming
the record's session is the child; a pid that is anything else has been
reused, and reads as the child having died. See "Strays and gc" in
``docs/dev/agent-daemon.md``.
"""

from dataclasses import dataclass, replace

from .agent_model import SPEC_EXITED, SPEC_RUNNING, SPEC_STOPPED, AgentSpec
from .session_discovery import ProcessInfo, is_driven, session_id_in

#: A record's child, alive and held by this daemon. Nothing to do.
OWNED = "owned"
#: A driven ``claude`` on a session a record already owns, that is not the
#: record's child. Killed.
DUPLICATE = "duplicate"
#: A record's child that outlived the daemon that held it. Killed, and the
#: record resumed once the daemon is starting.
STRAY = "stray"
#: A record whose child the last daemon stopped at its own shutdown. Nothing
#: to kill; resumed once the daemon is starting.
RESUMABLE = "resumable"
#: A running record whose child is gone with no shutdown recorded. Rewritten
#: ``exited``.
CRASHED = "crashed"
#: The older of two running records on one session. Rewritten ``stopped``; its
#: child, if alive, killed.
SUPERSEDED = "superseded"
#: A driven ``claude`` no running record here claims. Reported, never killed
#: from one root: it may belong to another.
UNKNOWN = "unknown"

KINDS = (OWNED, DUPLICATE, STRAY, RESUMABLE, CRASHED, SUPERSEDED, UNKNOWN)


@dataclass(frozen=True)
class Verdict:
    """What one record, or one recordless process, turned out to be.

    ``pid`` and ``pgid`` name the process the verdict is about, when there is
    one. ``agent_id`` is empty for a process no record claims.
    """

    kind: str
    session_id: str
    agent_id: str = ""
    pid: int | None = None
    pgid: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class Reconciliation:
    """The verdicts, and the three things to do about them.

    ``kill`` holds process *group* ids, not pids: every child leads its own
    group, and a child from before that was so sits in its dead daemon's
    group along with that daemon's other strays.
    """

    verdicts: tuple[Verdict, ...]
    kill: tuple[int, ...]
    rewrite: tuple[AgentSpec, ...]
    resume: tuple[AgentSpec, ...]

    def of_kind(self, kind: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.kind == kind]


def reconcile(
    records: list[AgentSpec],
    processes: list[ProcessInfo],
    held: set[str],
    *,
    resume_strays: bool,
) -> Reconciliation:
    """Match ``records`` against ``processes`` and say what to do.

    ``held`` is the agent ids the calling daemon holds in memory; the CLI
    passes none when the daemon is down. ``resume_strays`` is on only when a
    daemon is starting: the CLI kills a stray and leaves its record
    ``running``, so the next start resumes it.

    The rules, in order:

    1. One running record per session. Where a session has several, the
       newest ``started_at`` wins and the rest are SUPERSEDED.
    2. A surviving record whose pid is a driven ``claude`` naming its session
       is OWNED when held, else STRAY.
    3. Any other driven process naming a surviving record's session is a
       DUPLICATE.
    4. A surviving record with no such process, and not held, is RESUMABLE
       when the last daemon recorded a shutdown (``last_status`` set) or
       never recorded a pid at all; otherwise CRASHED. A held record with a
       dead pid gets no verdict: its pump owns it.
    5. A driven process matching no surviving record's session is UNKNOWN.
    """
    driven = [p for p in processes if is_driven(p.command)]
    by_pid = {p.pid: p for p in processes}
    verdicts: list[Verdict] = []
    kill: list[int] = []
    rewrite: list[AgentSpec] = []
    resume: list[AgentSpec] = []

    def condemn(process: ProcessInfo) -> None:
        if process.pgid not in kill:
            kill.append(process.pgid)

    #: Pids a verdict has already placed, so no process is judged twice.
    claimed: set[int] = set()

    # Rule 1: one running record per session.
    survivors: dict[str, AgentSpec] = {}
    for spec in sorted(
        (r for r in records if r.status == SPEC_RUNNING),
        key=lambda r: r.started_at,
    ):
        loser = survivors.get(spec.session_id)
        survivors[spec.session_id] = spec
        if loser is None:
            continue
        child = _child_of(loser, by_pid)
        verdicts.append(
            Verdict(
                SUPERSEDED,
                loser.session_id,
                loser.agent_id,
                loser.pid,
                child.pgid if child else None,
                f"a newer running record ({spec.agent_id}) owns this session",
            )
        )
        rewrite.append(replace(loser, status=SPEC_STOPPED, pid=None))
        if child is not None:
            claimed.add(child.pid)
            condemn(child)

    for session_id, spec in survivors.items():
        child = _child_of(spec, by_pid)
        # Rules 2 and 4: the record's own child.
        if child is not None:
            claimed.add(child.pid)
            if spec.agent_id in held:
                verdicts.append(
                    Verdict(OWNED, session_id, spec.agent_id, child.pid, child.pgid)
                )
            else:
                verdicts.append(
                    Verdict(
                        STRAY,
                        session_id,
                        spec.agent_id,
                        child.pid,
                        child.pgid,
                        "its daemon is gone",
                    )
                )
                condemn(child)
                if resume_strays:
                    resume.append(spec)
        elif spec.agent_id not in held:
            if spec.last_status or spec.pid is None:
                verdicts.append(
                    Verdict(
                        RESUMABLE,
                        session_id,
                        spec.agent_id,
                        spec.pid,
                        None,
                        "stopped by the last daemon's shutdown"
                        if spec.last_status
                        else "no pid recorded",
                    )
                )
                if resume_strays:
                    resume.append(spec)
            else:
                verdicts.append(
                    Verdict(
                        CRASHED,
                        session_id,
                        spec.agent_id,
                        spec.pid,
                        None,
                        f"pid {spec.pid} is not this session's claude",
                    )
                )
                rewrite.append(
                    replace(spec, status=SPEC_EXITED, exit_code=None, pid=None)
                )
        # Rule 3: every other driven process on this session.
        for process in driven:
            if process.pid in claimed or session_id_in(process.command) != session_id:
                continue
            claimed.add(process.pid)
            verdicts.append(
                Verdict(
                    DUPLICATE,
                    session_id,
                    spec.agent_id,
                    process.pid,
                    process.pgid,
                    f"a second driven claude on a session {spec.agent_id} owns",
                )
            )
            condemn(process)

    # Rule 5: driven processes nobody here claims.
    for process in driven:
        if process.pid in claimed:
            continue
        verdicts.append(
            Verdict(
                UNKNOWN,
                session_id_in(process.command) or "",
                "",
                process.pid,
                process.pgid,
                "no running record here names its session",
            )
        )

    return Reconciliation(
        verdicts=tuple(verdicts),
        kill=tuple(kill),
        rewrite=tuple(rewrite),
        resume=tuple(resume),
    )


def _child_of(spec: AgentSpec, by_pid: dict[int, ProcessInfo]) -> ProcessInfo | None:
    """The process ``spec`` names, when it is a driven ``claude`` on its session.

    Anything else at that pid is a reused number, and reads as the child
    being gone.
    """
    if spec.pid is None:
        return None
    process = by_pid.get(spec.pid)
    if process is None or not is_driven(process.command):
        return None
    if session_id_in(process.command) != spec.session_id:
        return None
    return process
