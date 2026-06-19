"""Tests for task lifecycle actions (``maelstrom.task_actions``).

The provider runners (``linear.set_issue_status`` / ``sentry.resolve_issue``)
are monkeypatched, so nothing touches the network. ``run_action`` is the unit
under test for ref-resolution + warn-on-failure; ``move_with_actions`` is
exercised for destination-keyed firing.
"""

import pytest

from maelstrom import task as model
from maelstrom import task_actions
from maelstrom.task import Task
from maelstrom.task_store import InMemoryStore


NOW = "2026-06-08T12:00:00+00:00"


# --- resolve_ref ---


class TestResolveRef:
    def test_linear_parent_resolves(self):
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        assert (
            task_actions.resolve_ref(t, task_actions._LINEAR_REF) == "NORT-12"
        )

    def test_linear_self_id_wins_over_parent(self):
        # A task literally named linear.ABC-1 resolves to itself first.
        t = Task(
            id="linear.ABC-1", title="t", project="p", parent="linear.NORT-12"
        )
        assert task_actions.resolve_ref(t, task_actions._LINEAR_REF) == "ABC-1"

    def test_sentry_resolves_opaque_suffix(self):
        t = Task(id="sentry.abc123", title="t", project="p")
        assert task_actions.resolve_ref(t, task_actions._SENTRY_REF) == "abc123"

    def test_non_ref_resolves_to_none(self):
        t = Task(
            id="2026-06-16.1", title="t", project="p", parent="2026-06-16.2"
        )
        assert task_actions.resolve_ref(t, task_actions._LINEAR_REF) is None


# --- run_action ---


class TestRunAction:
    def test_linear_done_calls_set_issue_status(self, monkeypatch):
        calls = []
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", lambda i, s: calls.append((i, s))
        )
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        task_actions.run_action(t, "linear.done")
        assert calls == [("NORT-12", "done")]

    def test_sentry_resolve_calls_resolve_issue(self, monkeypatch):
        calls = []
        from maelstrom.integrations import sentry

        monkeypatch.setattr(sentry, "resolve_issue", lambda i: calls.append(i))
        t = Task(id="sentry.abc123", title="t", project="p")
        task_actions.run_action(t, "sentry.resolve")
        assert calls == ["abc123"]

    def test_empty_code_is_noop(self, monkeypatch, capsys):
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", _fail("should not be called")
        )
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        task_actions.run_action(t, "")
        assert capsys.readouterr().err == ""

    def test_unknown_code_warns_and_runs_nothing(self, capsys):
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        task_actions.run_action(t, "linear.bogus")
        err = capsys.readouterr().err
        assert "unknown task action" in err
        assert "linear.bogus" in err

    def test_no_matching_ref_warns(self, monkeypatch, capsys):
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", _fail("should not be called")
        )
        t = Task(id="2026-06-16.1", title="t", project="p", parent="2026-06-16.2")
        task_actions.run_action(t, "linear.done")
        assert "no matching" in capsys.readouterr().err

    def test_runner_raising_is_swallowed_with_warning(self, monkeypatch, capsys):
        from maelstrom.integrations import linear

        def boom(issue_id, status):
            raise RuntimeError("api exploded")

        monkeypatch.setattr(linear, "set_issue_status", boom)
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        # Must not raise.
        task_actions.run_action(t, "linear.done")
        err = capsys.readouterr().err
        assert "failed" in err
        assert "api exploded" in err


# --- move_with_actions: destination-keyed firing ---


class TestMoveWithActions:
    def _seed(self, store, **kwargs):
        return model.create(
            store, project="p", title="t", now=NOW, today="2026-06-08", **kwargs
        )

    def test_move_to_done_fires_post_action(self, monkeypatch):
        calls = []
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", lambda i, s: calls.append((i, s))
        )
        store = InMemoryStore()
        t = self._seed(
            store, parent="linear.NORT-12", post_action="linear.done"
        )
        task_actions.move_with_actions(store, "p", t.id, model.STATUS_DONE)
        assert calls == [("NORT-12", "done")]

    def test_move_to_in_progress_fires_pre_action(self, monkeypatch):
        calls = []
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", lambda i, s: calls.append((i, s))
        )
        store = InMemoryStore()
        t = self._seed(
            store, parent="linear.NORT-12", pre_action="linear.in-progress"
        )
        task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_IN_PROGRESS
        )
        assert calls == [("NORT-12", "in-progress")]

    @pytest.mark.parametrize(
        "status",
        [model.STATUS_TODO, model.STATUS_CANCELLED, model.STATUS_BLOCKED],
    )
    def test_other_destinations_fire_nothing(self, monkeypatch, status):
        from maelstrom.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", _fail("should not be called")
        )
        store = InMemoryStore()
        # Start in-progress so a move to todo/cancelled/blocked is a real move.
        t = self._seed(
            store,
            parent="linear.NORT-12",
            pre_action="linear.in-progress",
            post_action="linear.done",
        )
        model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        task_actions.move_with_actions(store, "p", t.id, status)  # must not raise

    def test_returns_moved_task(self, monkeypatch):
        store = InMemoryStore()
        t = self._seed(store)
        moved = task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_DONE
        )
        assert moved.status == model.STATUS_DONE


def _fail(msg):
    def _f(*a, **k):
        raise AssertionError(msg)

    return _f
