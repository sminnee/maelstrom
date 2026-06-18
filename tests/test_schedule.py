"""Tests for the pure scheduler (cron math + due-template computation)."""

from datetime import datetime, timezone

import pytest

from maelstrom import schedule as sched
from maelstrom import task as model
from maelstrom.task_store import InMemoryStore


def _dt(s: str) -> datetime:
    """Parse an ISO string to an aware UTC datetime."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# --- cron parsing / boundary computation ---


class TestPreviousFire:
    @pytest.mark.parametrize(
        "expr,now,expected",
        [
            # Daily at 09:00 — boundary is today 09:00 if we're past it.
            ("0 9 * * *", "2026-06-18T10:30:00", "2026-06-18T09:00:00"),
            # Before today's boundary -> yesterday's.
            ("0 9 * * *", "2026-06-18T08:00:00", "2026-06-17T09:00:00"),
            # Exactly on the boundary -> that boundary (at/before).
            ("0 9 * * *", "2026-06-18T09:00:00", "2026-06-18T09:00:00"),
            # Hourly on the hour.
            ("0 * * * *", "2026-06-18T10:30:00", "2026-06-18T10:00:00"),
            # Every 5 minutes.
            ("*/5 * * * *", "2026-06-18T10:32:00", "2026-06-18T10:30:00"),
            # Weekdays at 09:00 — 2026-06-20 is a Saturday, so fall back to Fri.
            ("0 9 * * 1-5", "2026-06-20T12:00:00", "2026-06-19T09:00:00"),
        ],
    )
    def test_boundaries(self, expr, now, expected):
        assert sched.previous_fire(expr, _dt(now)) == _dt(expected)

    def test_weekday_range_excludes_weekend(self):
        # Sunday 2026-06-21 at 10:00, weekdays-only -> previous Friday 09:00.
        assert sched.previous_fire("0 9 * * 1-5", _dt("2026-06-21T10:00:00")) == _dt(
            "2026-06-19T09:00:00"
        )


class TestNextFire:
    @pytest.mark.parametrize(
        "expr,now,expected",
        [
            ("0 9 * * *", "2026-06-18T10:30:00", "2026-06-19T09:00:00"),
            ("0 9 * * *", "2026-06-18T08:00:00", "2026-06-18T09:00:00"),
            ("0 * * * *", "2026-06-18T10:30:00", "2026-06-18T11:00:00"),
            ("*/5 * * * *", "2026-06-18T10:31:00", "2026-06-18T10:35:00"),
            # Strictly after: exactly on a boundary yields the next one.
            ("0 9 * * *", "2026-06-18T09:00:00", "2026-06-19T09:00:00"),
        ],
    )
    def test_boundaries(self, expr, now, expected):
        assert sched.next_fire(expr, _dt(now)) == _dt(expected)


class TestCronParseErrors:
    @pytest.mark.parametrize(
        "expr",
        [
            "0 9 * *",          # too few fields
            "0 9 * * * *",      # too many fields
            "60 * * * *",       # minute out of range
            "* 24 * * *",       # hour out of range
            "* * * * 7",        # dow out of range
            "abc * * * *",      # non-numeric
        ],
    )
    def test_invalid_raises(self, expr):
        with pytest.raises(ValueError):
            sched.previous_fire(expr, _dt("2026-06-18T10:00:00"))

    def test_list_field(self):
        # "0,30" minutes -> matches both.
        assert sched.previous_fire("0,30 * * * *", _dt("2026-06-18T10:45:00")) == _dt(
            "2026-06-18T10:30:00"
        )


# --- due-template computation ---


def _add_template(store, project, *, schedule="", last_run="", created):
    return model.create(
        store,
        project=project,
        title="Maintenance",
        command="",
        schedule=schedule,
        last_run=last_run,
        status=model.STATUS_TEMPLATE,
        id="maintenance",
        now=created,
    )


class TestDueTemplates:
    def test_no_schedule_never_due(self):
        store = InMemoryStore()
        _add_template(store, "p", created="2026-06-01T00:00:00+00:00")
        assert sched.due_templates(store, "p", now=_dt("2026-06-18T12:00:00")) == []

    def test_due_when_boundary_after_watermark(self):
        store = InMemoryStore()
        _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        due = sched.due_templates(store, "p", now=_dt("2026-06-18T10:00:00"))
        assert len(due) == 1
        tmpl, date = due[0]
        assert tmpl.id == "maintenance"
        assert date == "2026-06-18"

    def test_not_due_when_watermark_at_boundary(self):
        store = InMemoryStore()
        _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        assert sched.due_templates(store, "p", now=_dt("2026-06-18T10:00:00")) == []

    def test_created_acts_as_watermark_when_no_last_run(self):
        store = InMemoryStore()
        # Created after today's 09:00 boundary -> not yet due.
        _add_template(
            store, "p", schedule="0 9 * * *", created="2026-06-18T09:30:00+00:00"
        )
        assert sched.due_templates(store, "p", now=_dt("2026-06-18T10:00:00")) == []
        # Created before it -> due exactly once.
        store2 = InMemoryStore()
        _add_template(
            store2, "p", schedule="0 9 * * *", created="2026-06-18T08:00:00+00:00"
        )
        due = sched.due_templates(store2, "p", now=_dt("2026-06-18T10:00:00"))
        assert [d[1] for d in due] == ["2026-06-18"]

    def test_catch_up_is_one_boundary(self):
        # A week-old watermark still yields a single boundary (today's), never 7.
        store = InMemoryStore()
        _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-11T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        due = sched.due_templates(store, "p", now=_dt("2026-06-18T10:00:00"))
        assert len(due) == 1
        assert due[0][1] == "2026-06-18"
