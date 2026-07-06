"""Tests for maelstrom.session_discovery: live claude processes → cwd → worktree.

Liveness is the set of running ``claude`` CLI processes and their cwds, obtained
via one ``pgrep -x claude`` plus one batched ``lsof -a -d cwd``. Tests fake those
two external calls by monkeypatching :func:`maelstrom.session_discovery.run_cmd`
rather than spawning real processes, and stub ``list_worktrees`` for the
worktree-prefix tiebreak.
"""

import subprocess
from pathlib import Path

from maelstrom import session_discovery


def _completed(stdout: str) -> subprocess.CompletedProcess:
    """A CompletedProcess carrying ``stdout`` (the only field callers read)."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fake_run_cmd(pgrep_out: str, lsof_out: str, ps_out: str = ""):
    """A ``run_cmd`` stand-in that answers pgrep, lsof, and ps from fixed output.

    Dispatches on the argv's first token so a single stub serves all three calls
    ``all_live_sessions`` makes. ``ps_out`` defaults to empty (no session-ids),
    which keeps the pre-session-id tests unchanged.
    """
    def run(cmd, *args, **kwargs):
        if cmd[0] == "pgrep":
            return _completed(pgrep_out)
        if cmd[0] == "lsof":
            return _completed(lsof_out)
        if cmd[0] == "ps":
            return _completed(ps_out)
        raise AssertionError(f"unexpected command: {cmd}")
    return run


def _ps_records(pairs: list[tuple[int, str]]) -> str:
    """Render ``(pid, command-line)`` pairs as ``ps -o pid=,command=`` output."""
    return "\n".join(f"  {pid} {cmd}" for pid, cmd in pairs) + "\n"


def _lsof_records(pairs: list[tuple[int, str]]) -> str:
    """Render ``(pid, cwd)`` pairs as ``lsof -F pn`` output."""
    lines = []
    for pid, cwd in pairs:
        lines.append(f"p{pid}")
        lines.append(f"n{cwd}")
    return "\n".join(lines) + "\n"


class TestAllLiveSessions:
    def test_empty_when_no_claude(self, monkeypatch):
        # pgrep exits 1 / prints nothing when nothing matches.
        monkeypatch.setattr(
            session_discovery, "run_cmd", _fake_run_cmd("", "")
        )
        assert session_discovery.all_live_sessions() == []

    def test_parses_pid_and_cwd(self, monkeypatch):
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd("42\n99\n", _lsof_records([(42, "/w/alpha"), (99, "/w/echo")])),
        )
        sessions = session_discovery.all_live_sessions()
        assert sessions == [
            session_discovery.LiveSession(pid=42, cwd=Path("/w/alpha")),
            session_discovery.LiveSession(pid=99, cwd=Path("/w/echo")),
        ]

    def test_skips_pid_without_cwd(self, monkeypatch):
        # lsof reports pid 42's cwd but not pid 99's.
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd("42\n99\n", _lsof_records([(42, "/w/alpha")])),
        )
        sessions = session_discovery.all_live_sessions()
        assert sessions == [
            session_discovery.LiveSession(pid=42, cwd=Path("/w/alpha"))
        ]

    def test_pgrep_missing_binary_is_empty(self, monkeypatch):
        def raise_oserror(cmd, *a, **k):
            raise OSError("pgrep not found")
        monkeypatch.setattr(session_discovery, "run_cmd", raise_oserror)
        assert session_discovery.all_live_sessions() == []

    def test_lsof_missing_binary_is_empty(self, monkeypatch):
        def run(cmd, *a, **k):
            if cmd[0] == "pgrep":
                return _completed("42\n")
            raise OSError("lsof not found")
        monkeypatch.setattr(session_discovery, "run_cmd", run)
        assert session_discovery.all_live_sessions() == []

    def test_ignores_non_numeric_pgrep_lines(self, monkeypatch):
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd("garbage\n42\n", _lsof_records([(42, "/w/alpha")])),
        )
        sessions = session_discovery.all_live_sessions()
        assert sessions == [
            session_discovery.LiveSession(pid=42, cwd=Path("/w/alpha"))
        ]

    def test_captures_session_id_from_ps(self, monkeypatch):
        sid = "97894d02-f335-5ea3-9d9f-050330a4902b"
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd(
                "47519\n",
                _lsof_records([(47519, "/w/delta")]),
                _ps_records([(47519, f"claude --session-id {sid} --foo bar")]),
            ),
        )
        sessions = session_discovery.all_live_sessions()
        assert sessions == [
            session_discovery.LiveSession(
                pid=47519, cwd=Path("/w/delta"), session_id=sid
            )
        ]

    def test_session_id_none_when_flag_absent(self, monkeypatch):
        # A bare `claude` launched outside mael has no --session-id.
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd(
                "42\n",
                _lsof_records([(42, "/w/alpha")]),
                _ps_records([(42, "claude --resume")]),
            ),
        )
        sessions = session_discovery.all_live_sessions()
        assert sessions[0].session_id is None

    def test_session_id_missing_ps_is_none(self, monkeypatch):
        # A box without `ps` still sweeps; only the session-ids are lost.
        def run(cmd, *a, **k):
            if cmd[0] == "pgrep":
                return _completed("42\n")
            if cmd[0] == "lsof":
                return _completed(_lsof_records([(42, "/w/alpha")]))
            raise OSError("ps not found")
        monkeypatch.setattr(session_discovery, "run_cmd", run)
        sessions = session_discovery.all_live_sessions()
        assert sessions == [
            session_discovery.LiveSession(pid=42, cwd=Path("/w/alpha"))
        ]

    def test_session_id_matched_per_pid(self, monkeypatch):
        # Two live sessions, each with its own --session-id; ps lines may arrive
        # in any order, so matching is by pid, not position.
        a = "97894d02-f335-5ea3-9d9f-050330a4902b"
        b = "94063899-7207-57ac-9629-4cc8d130667f"
        monkeypatch.setattr(
            session_discovery,
            "run_cmd",
            _fake_run_cmd(
                "42\n99\n",
                _lsof_records([(42, "/w/alpha"), (99, "/w/echo")]),
                _ps_records([
                    (99, f"claude --session-id {b}"),
                    (42, f"claude --session-id {a}"),
                ]),
            ),
        )
        sessions = session_discovery.all_live_sessions()
        by_pid = {s.pid: s.session_id for s in sessions}
        assert by_pid == {42: a, 99: b}


def _make_worktree(root: Path, name: str, *, bare: bool = False) -> Path:
    """Create a worktree dir under ``root`` with a ``.git`` marker.

    A linked worktree carries a ``.git`` *file* (the gitdir pointer); the main
    checkout carries a ``.git`` *dir*. :attr:`LiveSession.worktree` only checks
    existence, so ``bare`` picks which kind to lay down.
    """
    wt = root / name
    wt.mkdir(parents=True, exist_ok=True)
    if bare:
        (wt / ".git").mkdir()
    else:
        (wt / ".git").write_text("gitdir: /somewhere\n")
    return wt


class TestLiveSessionWorktree:
    def test_cwd_at_worktree_root(self, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        sess = session_discovery.LiveSession(pid=1, cwd=alpha)
        assert sess.worktree == alpha

    def test_cwd_in_subdir_walks_up(self, tmp_path):
        # A session cd'd into a subdir attributes to the worktree root.
        alpha = _make_worktree(tmp_path, "alpha")
        (alpha / "src").mkdir()
        sess = session_discovery.LiveSession(pid=1, cwd=alpha / "src")
        assert sess.worktree == alpha

    def test_nested_worktree_wins_over_parent(self, tmp_path):
        # A nested worktree has its own .git, so the nearest-.git walk stops
        # there rather than attributing to the parent worktree.
        main = _make_worktree(tmp_path, "_main", bare=True)
        nested = _make_worktree(main, "nested")
        sess = session_discovery.LiveSession(pid=1, cwd=nested)
        assert sess.worktree == nested

    def test_none_when_no_git_ancestor(self, tmp_path):
        loose = tmp_path / "loose"
        loose.mkdir()
        sess = session_discovery.LiveSession(pid=1, cwd=loose)
        assert sess.worktree is None


class TestLiveSessionSet:
    def test_all_for_returns_every_match(self, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        echo = _make_worktree(tmp_path, "echo")
        (alpha / "src").mkdir()
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=alpha),
            session_discovery.LiveSession(pid=2, cwd=echo),
            session_discovery.LiveSession(pid=3, cwd=alpha / "src"),
        ]
        result = session_discovery.LiveSessionSet(sessions).all_for(alpha)
        assert [s.pid for s in result] == [1, 3]

    def test_active_for_returns_first_match(self, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        echo = _make_worktree(tmp_path, "echo")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=echo),
            session_discovery.LiveSession(pid=2, cwd=alpha),
        ]
        s = session_discovery.LiveSessionSet(sessions).active_for(alpha)
        assert s is not None and s.pid == 2

    def test_active_for_none_when_no_match(self, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        echo = _make_worktree(tmp_path, "echo")
        sessions = [session_discovery.LiveSession(pid=1, cwd=echo)]
        assert session_discovery.LiveSessionSet(sessions).active_for(alpha) is None

    def test_count_for(self, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        echo = _make_worktree(tmp_path, "echo")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=alpha),
            session_discovery.LiveSession(pid=2, cwd=alpha),
            session_discovery.LiveSession(pid=3, cwd=echo),
        ]
        assert session_discovery.LiveSessionSet(sessions).count_for(alpha) == 2

    def test_nested_worktree_not_attributed_to_parent(self, tmp_path):
        main = _make_worktree(tmp_path, "_main", bare=True)
        nested = _make_worktree(main, "nested")
        sessions = [session_discovery.LiveSession(pid=1, cwd=nested)]
        live = session_discovery.LiveSessionSet(sessions)
        assert live.count_for(main) == 0
        assert [s.pid for s in live.all_for(nested)] == [1]

    def test_for_session_id_finds_match(self):
        a = "97894d02-f335-5ea3-9d9f-050330a4902b"
        b = "94063899-7207-57ac-9629-4cc8d130667f"
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=Path("/w/a"), session_id=a),
            session_discovery.LiveSession(pid=2, cwd=Path("/w/b"), session_id=b),
        ]
        live = session_discovery.LiveSessionSet(sessions)
        found_a = live.for_session_id(a)
        found_b = live.for_session_id(b)
        assert found_a is not None and found_a.pid == 1
        assert found_b is not None and found_b.pid == 2

    def test_for_session_id_none_when_no_match(self):
        sessions = [
            session_discovery.LiveSession(
                pid=1, cwd=Path("/w/a"),
                session_id="97894d02-f335-5ea3-9d9f-050330a4902b",
            ),
        ]
        live = session_discovery.LiveSessionSet(sessions)
        assert live.for_session_id("94063899-7207-57ac-9629-4cc8d130667f") is None

    def test_for_session_id_ignores_none_ids(self):
        # A session without a session-id must never match a None lookup key
        # (there is no None key), and must not be returned by any real lookup.
        sessions = [session_discovery.LiveSession(pid=1, cwd=Path("/w/a"))]
        live = session_discovery.LiveSessionSet(sessions)
        assert live.for_session_id("anything") is None

    def test_sweeps_lazily_when_no_sessions_passed(self, monkeypatch, tmp_path):
        alpha = _make_worktree(tmp_path, "alpha")
        calls = []
        monkeypatch.setattr(
            session_discovery,
            "all_live_sessions",
            lambda: calls.append(1)
            or [session_discovery.LiveSession(pid=1, cwd=alpha)],
        )
        live = session_discovery.LiveSessionSet()
        assert live.count_for(alpha) == 1
        assert live.all_for(alpha)[0].pid == 1
        assert calls == [1]  # swept once, then reused
