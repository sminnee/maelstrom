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


# --- run_action ---


class TestRunAction:
    def test_set_task_status_E3_the_id_wins_over_the_parent(self, monkeypatch):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _recorder(calls))
        # The id and the Parent each name an issue.
        t = Task(id="linear.ABC-1", title="t", project="p", parent="linear.NORT-12")
        task_actions.run_action(t, "linear.done", warn=[].append)
        assert calls == [("ABC-1", "done")]

    def test_set_task_status_E5_the_caller_gets_each_line(self, monkeypatch):
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

    def test_set_task_status_E3_a_sentry_ref_in_the_id(self, monkeypatch):
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

    def test_set_task_status_E3_no_action_runs_nothing(self, monkeypatch):
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        task_actions.run_action(t, "", warn=lines.append)
        assert lines == []

    def test_set_task_status_E4_an_unknown_action_warns(self):
        t = Task(id="x", title="t", project="p", parent="linear.NORT-12")
        lines: list[str] = []
        task_actions.run_action(t, "linear.bogus", warn=lines.append)
        assert lines == ["warning: unknown task action 'linear.bogus' on x"]

    def test_set_task_status_E4_no_issue_ref_warns(self, monkeypatch):
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        t = Task(id="2026-06-16.1", title="t", project="p", parent="2026-06-16.2")
        lines: list[str] = []
        task_actions.run_action(t, "linear.done", warn=lines.append)
        assert lines == [
            "warning: action 'linear.done' on 2026-06-16.1: no matching "
            "linear./sentry. ref in id or parent"
        ]

    def test_set_task_status_E4_an_action_that_raises_warns(self, monkeypatch):
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
        return await model.create(store, project="p", title="t", now=NOW, **kwargs)

    @pytest.mark.parametrize("status", [model.STATUS_BUILT, model.STATUS_DONE])
    async def test_set_task_status_E3_the_end_of_the_build_runs_its_action(
        self, monkeypatch, store, status
    ):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _recorder(calls))
        t = await self._seed(store, parent="linear.NORT-12", post_action="linear.done")
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        await task_actions.move_with_actions(store, "p", t.id, status, warn=[].append)
        assert calls == [("NORT-12", "done")]

    async def test_set_task_status_E3_a_merge_after_the_build_runs_no_action(
        self, monkeypatch, store
    ):
        from mael_domain.integrations import linear

        t = await self._seed(store, parent="linear.NORT-12", post_action="linear.done")
        await model.move(store, "p", t.id, model.STATUS_BUILT, now=NOW)
        monkeypatch.setattr(linear, "set_issue_status", _fail("should not be called"))
        await task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_DONE, warn=[].append
        )

    async def test_set_task_status_E3_in_progress_runs_its_own(
        self, monkeypatch, store
    ):
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
        [model.STATUS_TODO, model.STATUS_CANCELLED],
    )
    async def test_set_task_status_E3_other_moves_run_nothing(
        self, monkeypatch, status, store
    ):
        calls = []
        from mael_domain.integrations import linear

        monkeypatch.setattr(linear, "set_issue_status", _recorder(calls))
        # Start in-progress so a move to todo/cancelled is a real move.
        t = await self._seed(
            store,
            parent="linear.NORT-12",
            pre_action="linear.in-progress",
            post_action="linear.done",
        )
        await model.move(store, "p", t.id, model.STATUS_IN_PROGRESS, now=NOW)
        lines: list[str] = []
        await task_actions.move_with_actions(
            store, "p", t.id, status, warn=lines.append
        )
        assert calls == []
        assert lines == []

    async def test_set_task_status_E1_the_task_is_done(self, monkeypatch, store):
        t = await self._seed(store)
        moved = await task_actions.move_with_actions(
            store, "p", t.id, model.STATUS_DONE, warn=[].append
        )
        assert moved.status == model.STATUS_MERGED


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
