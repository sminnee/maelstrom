"""Tests for task lifecycle actions (``mael_domain.task_actions``).

The provider runners (``linear.set_issue_status`` / ``sentry.resolve_issue``)
are monkeypatched, so nothing touches the network. ``run_action`` is the unit
under test for ref-resolution + warn-on-failure; ``move_with_actions`` is
exercised for destination-keyed firing. Every line an action reports reaches
the caller's ``warn``, collected here in a list.
"""

import pytest

from mael_domain import task as model
from mael_domain import task_actions
from mael_domain.task import Task

NOW = "2026-06-08T12:00:00+00:00"


# --- resolve_ref ---


class TestResolveRef:
    def test_linear_parent_resolves(self):
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        assert task_actions.resolve_ref(t, task_actions._LINEAR_REF) == "NORT-12"

    def test_linear_self_id_wins_over_parent(self):
        # A task literally named linear.ABC-1 resolves to itself first.
        t = Task(id="linear.ABC-1", title="t", project="p", parent="linear.NORT-12")
        assert task_actions.resolve_ref(t, task_actions._LINEAR_REF) == "ABC-1"

    def test_sentry_resolves_opaque_suffix(self):
        t = Task(id="sentry.abc123", title="t", project="p")
        assert task_actions.resolve_ref(t, task_actions._SENTRY_REF) == "abc123"

    def test_non_ref_resolves_to_none(self):
        t = Task(id="2026-06-16.1", title="t", project="p", parent="2026-06-16.2")
        assert task_actions.resolve_ref(t, task_actions._LINEAR_REF) is None


# --- run_action ---


class TestRunAction:
    def test_linear_done_calls_set_issue_status(self, monkeypatch):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(
            linear, "set_issue_status", _recorder(calls, "NORT-12: Todo -> Unreleased")
        )
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        task_actions.run_action(t, "linear.done", warn=lines.append)
        assert calls == [("NORT-12", "done")]
        assert lines == [
            "NORT-12: Todo -> Unreleased",
            "action linear.done -> NORT-12 (task x)",
        ]

    def test_sentry_resolve_calls_resolve_issue(self, monkeypatch):
        calls = []
        from mael_domain.integrations import sentry

        monkeypatch.setattr(
            sentry,
            "resolve_issue",
            _recorder(calls, "Resolved: Boom\nStatus: resolved"),
        )
        t = Task(id="sentry.abc123", title="t", project="p")
        lines: list[str] = []
        task_actions.run_action(t, "sentry.resolve", warn=lines.append)
        assert calls == [("abc123",)]
        assert lines == [
            "Resolved: Boom\nStatus: resolved",
            "action sentry.resolve -> abc123 (task sentry.abc123)",
        ]

    def test_empty_code_is_noop(self, monkeypatch):
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        task_actions.run_action(t, "", warn=lines.append)
        assert lines == []

    def test_unknown_code_warns_and_runs_nothing(self):
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        task_actions.run_action(t, "linear.bogus", warn=lines.append)
        assert lines == ["warning: unknown task action 'linear.bogus' on x"]

    def test_no_matching_ref_warns(self, monkeypatch):
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        t = Task(id="2026-06-16.1", title="t", project="p", parent="2026-06-16.2")
        lines: list[str] = []
        task_actions.run_action(t, "linear.done", warn=lines.append)
        assert lines == [
            "warning: action 'linear.done' on 2026-06-16.1: no matching "
            "linear./sentry. ref in id or parent"
        ]

    def test_runner_raising_is_swallowed_with_warning(self, monkeypatch):
        from mael_domain.integrations import linear

        def boom(issue_id, status):
            raise RuntimeError("api exploded")

        monkeypatch.setattr(linear, "set_issue_status", boom)
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        # Must not raise.
        task_actions.run_action(t, "linear.done", warn=lines.append)
        assert lines == ["warning: action 'linear.done' on x failed: api exploded"]


# --- move_with_actions: destination-keyed firing ---


class TestMoveWithActions:
    async def _seed(self, store, **kwargs):
        return await model.create(
            store, project="p", title="t", now=NOW, today="2026-06-08", **kwargs
        )

    async def test_move_to_done_fires_post_action(self, monkeypatch, store):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _recorder(calls))
        t = await self._seed(store, parent="linear.NORT-12", post_action="linear.done")
        await task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_DONE, warn=[].append
        )
        assert calls == [("NORT-12", "done")]

    async def test_move_to_in_progress_fires_pre_action(self, monkeypatch, store):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _recorder(calls))
        t = await self._seed(
            store, parent="linear.NORT-12", pre_action="linear.in-progress"
        )
        await task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_IN_PROGRESS, warn=[].append
        )
        assert calls == [("NORT-12", "in-progress")]

    @pytest.mark.parametrize(
        "status",
        [model.STATUS_TODO, model.STATUS_CANCELLED, model.STATUS_BLOCKED],
    )
    async def test_other_destinations_fire_nothing(self, monkeypatch, status, store):
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        # Start in-progress so a move to todo/cancelled/blocked is a real move.
        t = await self._seed(
            store,
            parent="linear.NORT-12",
            pre_action="linear.in-progress",
            post_action="linear.done",
        )
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        # Must not raise.
        await task_actions.move_with_actions(store, "p", t.id, status, warn=[].append)

    async def test_returns_moved_task(self, monkeypatch, store):
        t = await self._seed(store)
        moved = await task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_DONE, warn=[].append
        )
        assert moved.status == model.STATUS_DONE


def _recorder(calls, result="NORT-12: Todo -> Done"):
    """A runner fake that records its arguments and returns ``result``."""

    def _f(*args):
        calls.append(args)
        return result

    return _f


def _fail(msg):
    def _f(*a, **k):
        raise AssertionError(msg)

    return _f
