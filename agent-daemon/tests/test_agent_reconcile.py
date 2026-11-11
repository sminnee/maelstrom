"""The reconcile model: records against the process table, no I/O.

Every case is literal ``ProcessInfo`` rows and literal records, including the
shape of the night that motivated it: one session, several processes, two
running records.
"""

from mael_common.process_table import ProcessInfo
from mael_daemon.agent_model import SPEC_EXITED, SPEC_STOPPED, AgentSpec
from mael_daemon.agent_reconcile import (
    CRASHED,
    DUPLICATE,
    OWNED,
    RESUMABLE,
    STRAY,
    SUPERSEDED,
    UNKNOWN,
    reconcile,
)

S1 = "11111111-1111-1111-1111-111111111111"
S2 = "22222222-2222-2222-2222-222222222222"

DRIVEN = (
    "claude -p --input-format stream-json --output-format stream-json --verbose "
    "--permission-prompt-tool stdio --forward-subagent-text --replay-user-messages "
    '--settings {"hooks": {"SessionEnd": []}} '
)


def driven(pid: int, session_id: str, *, pgid: int | None = None) -> ProcessInfo:
    """A daemon-driven ``claude`` on ``session_id``, leading its own group."""
    return ProcessInfo(
        pid, pgid if pgid is not None else pid, f"{DRIVEN}--resume {session_id}"
    )


def interactive(pid: int, session_id: str) -> ProcessInfo:
    """A ``claude`` a person started in a terminal, on the same session."""
    return ProcessInfo(pid, pid, f"claude --session-id {session_id}")


def running(agent_id: str, session_id: str, pid: int | None, **over) -> AgentSpec:
    fields = {"started_at": "2026-09-05T10:00:00+00:00", **over}
    return AgentSpec(
        agent_id=agent_id, cwd="/w", session_id=session_id, pid=pid, **fields
    )


def kinds(result) -> dict[str, str]:
    """``agent id -> kind`` for a record's verdict, ``pid -> kind`` for a process's.

    A duplicate names the agent whose session it intrudes on, so it is keyed
    by its own pid here or it would overwrite the owner's verdict.
    """
    return {
        str(v.pid) if v.kind in (DUPLICATE, UNKNOWN) else v.agent_id: v.kind
        for v in result.verdicts
    }


def test_a_held_child_alive_on_its_session_is_owned():
    result = reconcile(
        [running("a1", S1, 100)], [driven(100, S1)], {"a1"}, resume_strays=False
    )
    assert kinds(result) == {"a1": OWNED}
    assert result.kill == ()
    assert result.rewrite == ()
    assert result.resume == ()


def test_a_child_nobody_holds_is_a_stray_killed_and_resumed_on_start():
    """The daemon died; its child did not. The next daemon must not spawn a second."""
    result = reconcile(
        [running("a1", S1, 100)], [driven(100, S1)], set(), resume_strays=True
    )
    assert kinds(result) == {"a1": STRAY}
    assert result.kill == (100,)
    assert [s.agent_id for s in result.resume] == ["a1"]
    assert result.rewrite == ()  # the resume rewrites the record itself


def test_the_cli_kills_a_stray_but_leaves_its_record_running():
    """Without a daemon to hold it, the record must stay for the next start."""
    result = reconcile(
        [running("a1", S1, 100)], [driven(100, S1)], set(), resume_strays=False
    )
    assert kinds(result) == {"a1": STRAY}
    assert result.kill == (100,)
    assert result.resume == ()
    assert result.rewrite == ()


def test_a_second_driven_claude_on_an_owned_session_is_a_duplicate():
    """Tonight's shape: one session, N processes, and the record names one of them."""
    processes = [driven(100, S1), driven(200, S1), driven(300, S1)]
    result = reconcile([running("a1", S1, 100)], processes, {"a1"}, resume_strays=False)
    assert kinds(result) == {"a1": OWNED, "200": DUPLICATE, "300": DUPLICATE}
    assert result.kill == (200, 300)


def test_two_running_records_on_one_session_keep_the_newer():
    """Every relaunch of a task writes another record against its session id."""
    older = running("old", S1, 100, started_at="2026-09-05T09:00:00+00:00")
    newer = running("new", S1, 200, started_at="2026-09-05T10:00:00+00:00")
    result = reconcile(
        [older, newer], [driven(100, S1), driven(200, S1)], set(), resume_strays=True
    )
    assert kinds(result) == {"old": SUPERSEDED, "new": STRAY}
    assert set(result.kill) == {100, 200}
    (stopped,) = [s for s in result.rewrite if s.agent_id == "old"]
    assert stopped.status == SPEC_STOPPED
    assert stopped.pid is None
    # One resume per session, and it is the newer record.
    assert [s.agent_id for s in result.resume] == ["new"]


def test_a_superseded_record_whose_pid_is_dead_kills_nothing():
    """A dead pid may be anyone's by now; only a claude on the session is condemned."""
    older = running("old", S1, 100, started_at="2026-09-05T09:00:00+00:00")
    newer = running("new", S1, 200, started_at="2026-09-05T10:00:00+00:00")
    result = reconcile([older, newer], [driven(200, S1)], {"new"}, resume_strays=False)
    assert kinds(result) == {"old": SUPERSEDED, "new": OWNED}
    assert result.kill == ()


def test_a_running_record_with_a_dead_pid_and_no_shutdown_is_resumable():
    """A daemon that was killed wrote no shutdown flag, and its agents still come back.

    A ``running`` record means nobody stopped the agent. A child that dies
    under a live daemon is recorded ``exited`` by its pump, so it never
    reaches this branch.
    """
    result = reconcile(
        [running("a1", S1, 100, last_status="processing")],
        [],
        set(),
        resume_strays=True,
    )
    assert kinds(result) == {"a1": RESUMABLE}
    assert result.rewrite == ()
    assert [s.agent_id for s in result.resume] == ["a1"]


def test_a_record_resumed_three_times_with_no_clean_shutdown_is_crashed():
    """The guard for a daemon that dies at each start.

    Without it, each start would respawn every agent for ever.
    """
    below = reconcile(
        [running("a1", S1, 100, unclean_resumes=2)], [], set(), resume_strays=True
    )
    assert kinds(below) == {"a1": RESUMABLE}

    result = reconcile(
        [running("a1", S1, 100, unclean_resumes=3)], [], set(), resume_strays=True
    )
    assert kinds(result) == {"a1": CRASHED}
    (spec,) = result.rewrite
    assert spec.status == SPEC_EXITED
    assert spec.exit_code is None
    assert spec.pid is None
    assert result.resume == ()


def test_a_reused_pid_that_is_not_this_sessions_claude_is_not_the_child():
    """Identity is the session id in argv, so a reused number cannot pass as the child."""
    result = reconcile(
        [running("a1", S1, 100)],
        [interactive(100, S2), driven(100, S2)],
        set(),
        resume_strays=True,
    )
    assert kinds(result)["a1"] == RESUMABLE
    assert result.kill == ()


def test_a_child_is_known_by_its_argv_session_after_a_clear():
    """A `/clear` moves the record's session; the live child's argv keeps the old id.

    Matched on the record's session, the child would read as gone: the gc
    would leave it alive and the restore would start a second `claude`.
    """
    cleared = running("a1", S2, 100, argv_session_id=S1)
    held = reconcile([cleared], [driven(100, S1)], {"a1"}, resume_strays=True)
    assert kinds(held) == {"a1": OWNED}

    result = reconcile(
        [cleared], [driven(100, S1), driven(200, S2)], set(), resume_strays=True
    )
    assert kinds(result) == {"a1": STRAY, "200": DUPLICATE}
    assert set(result.kill) == {100, 200}
    assert [s.agent_id for s in result.resume] == ["a1"]


def test_a_child_of_a_record_with_no_pid_is_a_duplicate():
    """A record with no pid has no child to own, so a child on its session dies."""
    result = reconcile(
        [running("a1", S1, None)], [driven(100, S1)], set(), resume_strays=True
    )
    assert kinds(result) == {"a1": RESUMABLE, "100": DUPLICATE}
    assert result.kill == (100,)


def test_a_held_record_with_a_dead_pid_gets_no_verdict():
    """Its pump owns it: the exit will be recorded when the stream ends."""
    result = reconcile([running("a1", S1, 100)], [], {"a1"}, resume_strays=False)
    assert result.verdicts == ()


def test_a_driven_claude_no_record_here_claims_is_unknown_and_left_alone():
    """It may belong to another root; one root does not kill what it cannot place."""
    result = reconcile([], [driven(100, S2)], set(), resume_strays=True)
    assert kinds(result) == {"100": UNKNOWN}
    assert result.kill == ()
    (verdict,) = result.verdicts
    assert verdict.session_id == S2


def test_a_driven_claude_on_an_exited_records_session_is_unknown_not_a_duplicate():
    """Only a running record claims a session. An exited one owns nothing now,
    and a task relaunched under another root reuses its task session id."""
    exited = running("a1", S1, None, status=SPEC_EXITED)
    result = reconcile([exited], [driven(100, S1)], set(), resume_strays=True)
    assert kinds(result) == {"100": UNKNOWN}
    assert result.kill == ()


def test_an_interactive_claude_is_never_a_verdict():
    """A person's session on a task's id is not the daemon's to kill or to count."""
    result = reconcile(
        [running("a1", S1, 100)],
        [interactive(200, S1), driven(100, S1)],
        {"a1"},
        resume_strays=False,
    )
    assert kinds(result) == {"a1": OWNED}


def test_stopped_and_exited_records_are_not_reconciled():
    records = [
        running("s", S1, None, status=SPEC_STOPPED),
        running("e", S2, None, status=SPEC_EXITED),
    ]
    result = reconcile(records, [], set(), resume_strays=True)
    assert result.verdicts == ()
    assert result.resume == ()


def test_kill_holds_process_groups_once_each():
    """A child from before its own session sits in its dead daemon's group."""
    processes = [driven(100, S1, pgid=50), driven(200, S1, pgid=50)]
    result = reconcile([running("a1", S1, 100)], processes, set(), resume_strays=False)
    assert kinds(result) == {"a1": STRAY, "200": DUPLICATE}
    assert result.kill == (50,)
