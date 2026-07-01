"""Tests for maelstrom.session_discovery: find transcript → pid → liveness.

The subsystem mirrors Claude Code's own file-based uniqueness rule. Tests point
``CLAUDE_CONFIG_DIR`` at ``tmp_path`` so no real ``~/.claude`` is touched, fake
transcript files by writing ``<id>.jsonl`` under a projects dir, and monkeypatch
liveness / ``lsof`` rather than spawning real processes.
"""

from pathlib import Path

import pytest

from maelstrom import session_discovery
from maelstrom import task as model


@pytest.fixture(autouse=True)
def _claude_root(monkeypatch, tmp_path) -> Path:
    """Point the Claude config root at ``tmp_path`` for every test."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude"


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """Default to an empty ``~/.maelstrom`` registry (no hints).

    Tests that want a registry hint re-patch ``live_sessions`` themselves.
    """
    monkeypatch.setattr(session_discovery.session_store, "live_sessions", lambda: [])


def _write_transcript(claude_root: Path, slug: str, session_id: str) -> Path:
    project_dir = claude_root / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text('{"type":"session"}\n')
    return path


# --- Step 1: find the transcript file ---


class TestTranscriptForSessionId:
    def test_finds_unique_transcript(self, _claude_root):
        path = _write_transcript(_claude_root, "-some-proj", "abc-123")
        assert session_discovery.transcript_for_session_id("abc-123") == path

    def test_none_when_absent(self, _claude_root):
        _write_transcript(_claude_root, "-some-proj", "abc-123")
        assert session_discovery.transcript_for_session_id("other") is None

    def test_none_when_no_projects_dir(self):
        # CLAUDE_CONFIG_DIR points at a tmp dir that has no projects/ yet.
        assert session_discovery.transcript_for_session_id("abc-123") is None

    def test_found_regardless_of_slug(self, _claude_root):
        # The id is globally unique, so a deliberately mis-slugged directory
        # (one that no path-sanitiser would produce) is still matched by id.
        path = _write_transcript(_claude_root, "totally-wrong-slug", "xyz")
        assert session_discovery.transcript_for_session_id("xyz") == path


# --- Step 2: identify the owning pid ---


class TestPidHolding:
    def test_registry_hint_returns_recorded_pid(self, monkeypatch, _claude_root):
        path = _write_transcript(_claude_root, "-work-tree", "sid")
        monkeypatch.setattr(
            session_discovery.session_store,
            "live_sessions",
            lambda: [{"cwd": "/work/tree", "pid": 777}],
        )
        # No lsof should be needed — force it to blow up if called.
        monkeypatch.setattr(
            session_discovery,
            "_lsof_pid",
            lambda p: pytest.fail("lsof should not run when a hint matches"),
        )
        assert session_discovery.pid_holding(path) == 777

    def test_lsof_fallback_when_no_hint(self, monkeypatch, _claude_root):
        path = _write_transcript(_claude_root, "-work-tree", "sid")
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: 999)
        assert session_discovery.pid_holding(path) == 999

    def test_none_when_nothing_holds(self, monkeypatch, _claude_root):
        path = _write_transcript(_claude_root, "-work-tree", "sid")
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: None)
        assert session_discovery.pid_holding(path) is None


# --- Step 3: liveness ---


class TestLiveness:
    def _session(self, monkeypatch, _claude_root, *, pid, alive):
        _write_transcript(_claude_root, "-work-tree", "sid")
        monkeypatch.setattr(
            session_discovery,
            "_lsof_pid",
            lambda p: pid,
        )
        monkeypatch.setattr(session_discovery, "is_process_running", lambda p: alive)
        transcript = session_discovery.transcript_for_session_id("sid")
        assert transcript is not None
        return session_discovery._active_session_from_transcript("sid", transcript)

    def test_alive_pid_is_live(self, monkeypatch, _claude_root):
        s = self._session(monkeypatch, _claude_root, pid=42, alive=True)
        assert s.is_live is True
        assert s.pid == 42

    def test_dead_pid_not_live(self, monkeypatch, _claude_root):
        s = self._session(monkeypatch, _claude_root, pid=42, alive=False)
        assert s.is_live is False
        assert s.pid is None  # a dead holder is reported as no live owner

    def test_no_pid_not_live(self, monkeypatch, _claude_root):
        s = self._session(monkeypatch, _claude_root, pid=None, alive=True)
        assert s.is_live is False
        assert s.pid is None


# --- Public API: by task ---


class TestActiveSessionForTask:
    def _transcript_for_task(self, _claude_root, project, task_id):
        sid = model.session_id_for(project, task_id)
        return _write_transcript(_claude_root, "-wt", sid), sid

    def test_none_when_no_transcript(self):
        assert session_discovery.active_session_for_task("p", "t") is None

    def test_live_session_reported(self, monkeypatch, _claude_root):
        _, sid = self._transcript_for_task(_claude_root, "p", "t")
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: 100)
        monkeypatch.setattr(session_discovery, "is_process_running", lambda p: True)
        s = session_discovery.active_session_for_task("p", "t")
        assert s is not None
        assert s.session_id == sid
        assert s.is_live is True
        assert s.pid == 100

    def test_finished_transcript_not_live(self, monkeypatch, _claude_root):
        # Transcript persists but its holder has exited → safe to re-run.
        self._transcript_for_task(_claude_root, "p", "t")
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: None)
        s = session_discovery.active_session_for_task("p", "t")
        assert s is not None
        assert s.is_live is False

    def test_cwd_from_registry_hint(self, monkeypatch, _claude_root):
        # When the registry records the session, the real worktree cwd (not the
        # slugified project dir) is surfaced for the friendly error message.
        sid = model.session_id_for("p", "t")
        slug = session_discovery.sanitise_path_for_claude(Path("/work/tree"))
        _write_transcript(_claude_root, slug, sid)
        monkeypatch.setattr(
            session_discovery.session_store,
            "live_sessions",
            lambda: [{"cwd": "/work/tree", "pid": 55}],
        )
        monkeypatch.setattr(session_discovery, "is_process_running", lambda p: True)
        s = session_discovery.active_session_for_task("p", "t")
        assert s is not None
        assert s.pid == 55
        assert s.cwd == Path("/work/tree")

    def test_cwd_none_without_registry_hint(self, monkeypatch, _claude_root):
        # No registry entry → cwd is None, not the slugified Claude project dir
        # (which is not a real path and would mislead the error message).
        self._transcript_for_task(_claude_root, "p", "t")
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: 100)
        monkeypatch.setattr(session_discovery, "is_process_running", lambda p: True)
        s = session_discovery.active_session_for_task("p", "t")
        assert s is not None
        assert s.is_live is True
        assert s.cwd is None


# --- Public API: by worktree / by branch ---


class TestByWorktreeAndBranch:
    def _setup_live_task(self, monkeypatch, _claude_root, wt_path):
        sid = model.session_id_for("p", "t")
        slug = session_discovery.sanitise_path_for_claude(wt_path)
        _write_transcript(_claude_root, slug, sid)
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: 100)
        monkeypatch.setattr(session_discovery, "is_process_running", lambda p: True)
        return sid

    def test_by_worktree_matches_by_task(self, monkeypatch, _claude_root):
        wt = Path("/work/tree-alpha")
        sid = self._setup_live_task(monkeypatch, _claude_root, wt)
        by_wt = session_discovery.active_session_for_worktree(wt)
        by_task = session_discovery.active_session_for_task("p", "t")
        assert by_wt is not None and by_task is not None
        assert by_wt.session_id == by_task.session_id == sid

    def test_by_worktree_none_when_only_finished(self, monkeypatch, _claude_root):
        wt = Path("/work/tree-alpha")
        self._setup_live_task(monkeypatch, _claude_root, wt)
        monkeypatch.setattr(session_discovery, "_lsof_pid", lambda p: None)
        assert session_discovery.active_session_for_worktree(wt) is None

    def test_by_branch_resolves_via_worktree(self, monkeypatch, _claude_root):
        wt = Path("/work/tree-alpha")
        sid = self._setup_live_task(monkeypatch, _claude_root, wt)
        # Fake the worktree enumeration: branch → wt path.
        info = type("WT", (), {"path": wt, "branch": "feat/x"})()
        monkeypatch.setattr(session_discovery, "list_worktrees", lambda p: [info])
        s = session_discovery.active_session_for_branch(Path("/proj"), "feat/x")
        assert s is not None
        assert s.session_id == sid

    def test_by_branch_none_when_branch_absent(self, monkeypatch, _claude_root):
        monkeypatch.setattr(session_discovery, "list_worktrees", lambda p: [])
        assert session_discovery.active_session_for_branch(Path("/proj"), "feat/x") is None
