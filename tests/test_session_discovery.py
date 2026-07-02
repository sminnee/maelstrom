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


def _fake_run_cmd(pgrep_out: str, lsof_out: str):
    """A ``run_cmd`` stand-in that answers pgrep and lsof from fixed output.

    Dispatches on the argv's first token so a single stub serves both calls
    ``all_live_sessions`` makes.
    """
    def run(cmd, *args, **kwargs):
        if cmd[0] == "pgrep":
            return _completed(pgrep_out)
        if cmd[0] == "lsof":
            return _completed(lsof_out)
        raise AssertionError(f"unexpected command: {cmd}")
    return run


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


def _wt(path: str, branch: str = "b"):
    """A stand-in worktree with the fields the tiebreak reads."""
    from maelstrom.worktree import WorktreeInfo

    return WorktreeInfo(path=Path(path), branch=branch, commit="deadbeef")


def _patch_worktrees(monkeypatch, *paths):
    """Stub ``list_worktrees`` to return a fixed set of worktrees.

    ``list_worktrees(cwd)`` in reality returns every worktree of ``cwd``'s repo
    (always including the one ``cwd`` sits in), so tests must provide the
    candidate paths for the longest-prefix attribution to resolve against.
    """
    worktrees = [_wt(p) for p in paths]
    monkeypatch.setattr(session_discovery, "list_worktrees", lambda p: worktrees)


class TestLiveSessionCountForWorktree:
    def test_counts_matching_cwds(self, monkeypatch):
        _patch_worktrees(monkeypatch, "/w/alpha", "/w/echo")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=Path("/w/alpha")),
            session_discovery.LiveSession(pid=2, cwd=Path("/w/alpha")),
            session_discovery.LiveSession(pid=3, cwd=Path("/w/echo")),
        ]
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/alpha"), sessions
        ) == 2

    def test_zero_when_none_match(self, monkeypatch):
        _patch_worktrees(monkeypatch, "/w/alpha", "/w/echo")
        sessions = [session_discovery.LiveSession(pid=1, cwd=Path("/w/echo"))]
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/alpha"), sessions
        ) == 0

    def test_counts_nested_cwd(self, monkeypatch):
        # A session cwd'd into a subdir of the worktree still counts.
        _patch_worktrees(monkeypatch, "/w/alpha")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=Path("/w/alpha/src"))
        ]
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/alpha"), sessions
        ) == 1

    def test_nested_worktree_tiebreak(self, monkeypatch):
        # /w/_main and /w/_main/nested are both worktrees; a session in the
        # nested one must count only for /w/_main/nested, not /w/_main.
        _patch_worktrees(monkeypatch, "/w/_main", "/w/_main/nested")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=Path("/w/_main/nested"))
        ]
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/_main"), sessions
        ) == 0
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/_main/nested"), sessions
        ) == 1

    def test_worktree_list_memoised_across_rows(self, monkeypatch):
        # The shared cache must collapse repeated list_worktrees calls: one
        # sweep over two worktree rows should shell git once per distinct cwd.
        calls = []
        monkeypatch.setattr(
            session_discovery,
            "list_worktrees",
            lambda p: calls.append(p) or [_wt("/w/alpha"), _wt("/w/echo")],
        )
        sessions = [session_discovery.LiveSession(pid=1, cwd=Path("/w/alpha"))]
        cache: dict = {}
        session_discovery.live_session_count_for_worktree(
            Path("/w/alpha"), sessions, cache
        )
        session_discovery.live_session_count_for_worktree(
            Path("/w/echo"), sessions, cache
        )
        assert calls == [Path("/w/alpha")]  # one call for the one distinct cwd

    def test_sweeps_when_no_sessions_passed(self, monkeypatch):
        _patch_worktrees(monkeypatch, "/w/alpha")
        monkeypatch.setattr(
            session_discovery,
            "all_live_sessions",
            lambda: [session_discovery.LiveSession(pid=1, cwd=Path("/w/alpha"))],
        )
        assert session_discovery.live_session_count_for_worktree(
            Path("/w/alpha")
        ) == 1


class TestActiveSessionForWorktree:
    def test_returns_first_match(self, monkeypatch):
        _patch_worktrees(monkeypatch, "/w/alpha", "/w/echo")
        sessions = [
            session_discovery.LiveSession(pid=1, cwd=Path("/w/echo")),
            session_discovery.LiveSession(pid=2, cwd=Path("/w/alpha")),
        ]
        s = session_discovery.active_session_for_worktree(
            Path("/w/alpha"), sessions
        )
        assert s is not None
        assert s.pid == 2

    def test_none_when_no_match(self, monkeypatch):
        _patch_worktrees(monkeypatch, "/w/alpha", "/w/echo")
        sessions = [session_discovery.LiveSession(pid=1, cwd=Path("/w/echo"))]
        assert session_discovery.active_session_for_worktree(
            Path("/w/alpha"), sessions
        ) is None
