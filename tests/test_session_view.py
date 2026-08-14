"""Tests for maelstrom.session_view: one live session -> its display fields.

The model layer, so these tests inject everything: the registry entries, the task
index, and the project/worktree resolver. No filesystem, no live processes, no
``~/.maelstrom``. That is the point of the layer — ``docs/dev/architecture-patterns.md``
convention 2 keeps model code free of I/O so it can be exercised directly.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from maelstrom import session_view
from maelstrom.session_discovery import LiveSession
from maelstrom.task_index import SqliteTaskIndex, TaskMeta


def _sess(pid=4242, cwd="/w/alpha", session_id=None):
    return LiveSession(pid=pid, cwd=Path(cwd), session_id=session_id)


def _resolver(project=None, worktree=None):
    """A project/worktree resolver that answers the same way for any cwd."""
    return lambda cwd: (project, worktree)


def _empty_index():
    return SqliteTaskIndex(":memory:")


class TestBuildSessionRow:
    def test_pid_and_cwd_come_from_the_process(self):
        row = session_view.build_session_row(
            _sess(), [], _empty_index(), _resolver()
        )
        assert row["pid"] == 4242
        assert row["cwd"] == "/w/alpha"

    def test_every_key_is_present_even_with_nothing_to_report(self):
        # The JSON form of `mael session info` relies on a stable shape.
        row = session_view.build_session_row(
            _sess(), [], _empty_index(), _resolver()
        )
        assert set(row) == {
            "id", "pid", "state", "project", "worktree",
            "task", "cwd", "age", "model",
        }
        assert row["id"] == ""
        assert row["state"] == ""
        assert row["task"] == ""

    def test_project_and_worktree_come_from_the_resolver(self):
        row = session_view.build_session_row(
            _sess(), [], _empty_index(), _resolver("askastro", "delta")
        )
        assert row["project"] == "askastro"
        assert row["worktree"] == "delta"

    def test_state_and_model_are_enriched_from_a_matching_registry_entry(self):
        registry = [{
            "pid": 4242, "cwd": "/w/alpha", "state": "idle",
            "model": "claude-opus", "started_at": _ago(minutes=5),
            "updated_at": _ago(minutes=1),
        }]
        row = session_view.build_session_row(
            _sess(), registry, _empty_index(), _resolver()
        )
        assert row["state"] == "idle"
        assert row["model"] == "claude-opus"
        assert row["age"] == "5m"

    def test_a_registry_entry_for_another_session_does_not_enrich(self):
        registry = [{"pid": 1, "cwd": "/w/other", "state": "idle"}]
        row = session_view.build_session_row(
            _sess(), registry, _empty_index(), _resolver()
        )
        assert row["state"] == ""

    def test_stale_processing_reads_as_idle(self):
        # Claude fires no hook on ESC, so `processing` would stick forever.
        registry = [{
            "pid": 4242, "cwd": "/w/alpha", "state": "processing",
            "updated_at": _ago(minutes=30), "started_at": _ago(minutes=40),
        }]
        row = session_view.build_session_row(
            _sess(), registry, _empty_index(), _resolver()
        )
        assert row["state"] == "idle"

    def test_task_comes_from_the_index_by_session_id(self):
        index = _empty_index()
        index.upsert(
            TaskMeta(
                project="askastro", id="2026-07-03.7",
                status="in-progress", session_id="sid-1",
            )
        )
        row = session_view.build_session_row(
            _sess(session_id="sid-1"), [], index, _resolver()
        )
        assert row["task"] == "2026-07-03.7"

    def test_task_falls_back_to_the_registry_when_the_index_is_cold(self):
        registry = [{"pid": 4242, "cwd": "/w/alpha", "mael_task_id": "2026-07-03.7"}]
        row = session_view.build_session_row(
            _sess(session_id="sid-1"), registry, _empty_index(), _resolver()
        )
        assert row["task"] == "2026-07-03.7"


def _ago(**kwargs) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat()
