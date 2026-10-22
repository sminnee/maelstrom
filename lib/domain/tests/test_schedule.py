"""Tests for the scheduler: cron math, the due computation, and the fire step."""

from datetime import datetime, timedelta, timezone

import pytest
from domain_fixtures import FakeBuildRuns

from mael_domain import schedule as sched
from mael_domain import task as model
from mael_domain.build_runs import BuildRun
from mael_domain.task_table import InMemoryTaskTable


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
            "0 9 * *",  # too few fields
            "0 9 * * * *",  # too many fields
            "60 * * * *",  # minute out of range
            "* 24 * * *",  # hour out of range
            "* * * * 7",  # dow out of range
            "abc * * * *",  # non-numeric
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


# --- due computation ---


async def _add_template(
    store, project, *, schedule="", last_run="", created, branch="", id="maintenance"
):
    return await model.create(
        store,
        project=project,
        title="Maintenance",
        command="",
        schedule=schedule,
        last_run=last_run,
        branch=branch,
        status=model.STATUS_TEMPLATE,
        id=id,
        now=created,
    )


async def _due(store, now):
    """``(template id, date)`` per firing at ``now``, for templates with no trigger."""
    runs = FakeBuildRuns(None)
    firings, warnings = await sched.due_firings(store, "p", runs, now=now)
    assert warnings == [] and runs.reads == []
    return [(f.tmpl.id, f.date) for f in firings]


class TestDueFirings:
    async def test_no_schedule_never_due(self):
        store = InMemoryTaskTable()
        await _add_template(store, "p", created="2026-06-01T00:00:00+00:00")
        assert await _due(store, _dt("2026-06-18T12:00:00")) == []

    async def test_due_when_boundary_after_watermark(self):
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        assert await _due(store, _dt("2026-06-18T10:00:00")) == [
            ("maintenance", "2026-06-18")
        ]

    async def test_not_due_when_watermark_at_boundary(self):
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-18T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        assert await _due(store, _dt("2026-06-18T10:00:00")) == []

    async def test_created_acts_as_watermark_when_no_last_run(self):
        store = InMemoryTaskTable()
        # Created after today's 09:00 boundary -> not yet due.
        await _add_template(
            store, "p", schedule="0 9 * * *", created="2026-06-18T09:30:00+00:00"
        )
        assert await _due(store, _dt("2026-06-18T10:00:00")) == []
        # Created before it -> due exactly once.
        store2 = InMemoryTaskTable()
        await _add_template(
            store2, "p", schedule="0 9 * * *", created="2026-06-18T08:00:00+00:00"
        )
        assert await _due(store2, _dt("2026-06-18T10:00:00")) == [
            ("maintenance", "2026-06-18")
        ]

    async def test_catch_up_is_one_boundary(self):
        # A week-old watermark still yields a single boundary (today's), never 7.
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-11T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        assert await _due(store, _dt("2026-06-18T10:00:00")) == [
            ("maintenance", "2026-06-18")
        ]

    async def test_due_matches_local_wall_clock_not_utc(self):
        # A "0 9 * * *" template fires against the wall-clock fields of the
        # local-aware `now` the CLI passes in — 09:00 *local*, not 09:00 UTC.
        # Model a +12 zone (e.g. NZ): at 09:30 local it's still 21:30 the
        # previous day in UTC, so a UTC-based scheduler would consider the
        # 09:00 boundary un-reached. It must be due here.
        store = InMemoryTaskTable()
        tz = timezone(timedelta(hours=12))
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+12:00",
            created="2026-06-01T00:00:00+12:00",
        )
        now_local = datetime(2026, 6, 18, 9, 30, tzinfo=tz)
        # Boundary date is the local calendar day, keyed off local 09:00.
        assert await _due(store, now_local) == [("maintenance", "2026-06-18")]


# --- the fire step ---


async def _runs(store, prefix):
    return [
        t.id
        for t in await model.list_tasks(store, project="p")
        if t.id.startswith(f"{prefix}.")
    ]


class TestFireDue:
    """Cron templates: one run per due boundary, and the watermark moves with it."""

    NOW = _dt("2026-06-18T10:00:00")

    async def _fire(self, store, now=NOW):
        return await sched.fire_due(store, "p", FakeBuildRuns(None), now=now)

    async def test_a_due_template_makes_one_parentless_run_and_moves_its_watermark(
        self,
    ):
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-11T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
            branch="chore/maint",
        )
        result = await self._fire(store)
        [run] = result.created
        assert run.id == "maintenance.2026-06-18"
        # The run is parentless: its dot-id names it under the template, but its
        # empty parent lets it root its own chain (follow-ups nest under the run).
        assert run.parent == ""
        assert run.branch == "chore/maint"
        assert await _runs(store, "maintenance") == ["maintenance.2026-06-18"]
        tmpl = await model.load(store, "p", "maintenance")
        assert tmpl.last_run == _dt("2026-06-18T09:00:00").isoformat()

    async def test_a_second_fire_makes_nothing(self):
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        await self._fire(store)
        assert (await self._fire(store)).created == []
        assert await _runs(store, "maintenance") == ["maintenance.2026-06-18"]

    async def test_an_existing_run_id_makes_nothing_but_moves_the_watermark(self):
        # A RunAtLoad and interval double fire: the run exists, and the template's
        # watermark was not yet written.
        store = InMemoryTaskTable()
        await _add_template(
            store,
            "p",
            schedule="0 9 * * *",
            last_run="2026-06-17T09:00:00+00:00",
            created="2026-06-01T00:00:00+00:00",
        )
        await model.duplicate(store, "p", "maintenance", id="maintenance.2026-06-18")
        assert (await self._fire(store)).created == []
        tmpl = await model.load(store, "p", "maintenance")
        assert tmpl.last_run == _dt("2026-06-18T09:00:00").isoformat()

    async def test_each_due_template_fires(self):
        store = InMemoryTaskTable()
        for tmpl_id in ("maint-a", "maint-b"):
            await _add_template(
                store,
                "p",
                schedule="0 9 * * *",
                last_run="2026-06-17T09:00:00+00:00",
                created="2026-06-01T00:00:00+00:00",
                id=tmpl_id,
            )
        result = await self._fire(store)
        assert sorted(t.id for t in result.created) == [
            "maint-a.2026-06-18",
            "maint-b.2026-06-18",
        ]


def _local(day, hour):
    """A local wall-clock time on 2026-10-``day``, so the run's date is ``day``."""
    return datetime(2026, 10, day, hour, 0).astimezone()


def _build_run(id, conclusion, completed_at):
    return BuildRun(
        id=id,
        conclusion=conclusion,
        completed_at=completed_at,
        url=f"https://github.com/o/r/actions/runs/{id}",
        head_sha=f"abcdef{id}0000",
    )


class TestFireDueOnTrigger:
    """Templates with a build trigger, fired at 2026-10-08 10:00 local."""

    NOW = _local(8, 10)

    async def _template(self, store, trigger, *, schedule="", last_run=""):
        return await model.create(
            store,
            project="p",
            title="Fix the nightly build",
            content="Fix it.",
            schedule=schedule,
            trigger=trigger,
            last_run=last_run,
            status=model.STATUS_TEMPLATE,
            id="nightly",
            now="2026-10-01T00:00:00+00:00",
        )

    async def _tick(self, store, runs):
        return await sched.fire_due(store, "p", runs, now=self.NOW)

    async def _watermark(self, store):
        return datetime.fromisoformat(
            (await model.load(store, "p", "nightly")).last_run
        )

    async def test_a_failed_run_fires_once(self):
        store = InMemoryTaskTable()
        await self._template(store, "gh-action/nightly.yml")
        runs = FakeBuildRuns([_build_run(2, "failure", _local(7, 3))])
        await self._tick(store, runs)
        run = await model.load(store, "p", "nightly.2026-10-07")
        assert run.content == (
            "Fix it.\n\n## Build run\n\n"
            "nightly.yml on main: failure. "
            "https://github.com/o/r/actions/runs/2 — commit abcdef2.\n"
            "Read the log: `mael gh check-log 2 --failed-only`."
        )
        assert run.trigger == ""
        assert await self._watermark(store) == _local(7, 3)
        assert (await self._tick(store, runs)).created == []
        assert await _runs(store, "nightly") == ["nightly.2026-10-07"]

    async def test_a_second_failure_that_day_moves_the_watermark(self):
        store = InMemoryTaskTable()
        await self._template(store, "gh-action/nightly.yml")
        first = _build_run(2, "failure", _local(7, 3))
        await self._tick(store, FakeBuildRuns([first]))
        second = _build_run(3, "failure", _local(7, 5))
        await self._tick(store, FakeBuildRuns([second, first]))
        assert await _runs(store, "nightly") == ["nightly.2026-10-07"]
        assert await self._watermark(store) == _local(7, 5)

    async def test_a_newer_green_run_moves_the_watermark_only(self):
        store = InMemoryTaskTable()
        await self._template(store, "gh-action/nightly.yml")
        runs = [
            _build_run(3, "success", _local(7, 4)),
            _build_run(2, "failure", _local(7, 3)),
        ]
        await self._tick(store, FakeBuildRuns(runs))
        assert await _runs(store, "nightly") == []
        assert await self._watermark(store) == _local(7, 4)

    @pytest.mark.parametrize(
        ("trigger", "conclusion", "fires"),
        [
            ("gh-action/nightly.yml", "cancelled", False),
            ("gh-action/nightly.yml", "timed_out", True),
            ("gh-action/nightly.yml success", "success", True),
        ],
    )
    async def test_only_a_listed_conclusion_fires(self, trigger, conclusion, fires):
        store = InMemoryTaskTable()
        await self._template(store, trigger)
        await self._tick(
            store, FakeBuildRuns([_build_run(2, conclusion, _local(7, 3))])
        )
        expected = ["nightly.2026-10-07"] if fires else []
        assert await _runs(store, "nightly") == expected

    async def test_a_green_run_section_has_no_log_command(self):
        store = InMemoryTaskTable()
        await self._template(store, "gh-action/nightly.yml success")
        await self._tick(store, FakeBuildRuns([_build_run(2, "success", _local(7, 3))]))
        run = await model.load(store, "p", "nightly.2026-10-07")
        assert run.content.endswith(
            "nightly.yml on main: success. "
            "https://github.com/o/r/actions/runs/2 — commit abcdef2."
        )

    @pytest.mark.parametrize(
        ("trigger", "schedule", "warning"),
        [
            ("gh-action/nightly.yml", "", "could not read the nightly.yml runs"),
            (
                "gh-action/nightly.yml",
                "0 9 * * *",
                "could not read the nightly.yml runs",
            ),
            ("nightly.yml", "", "unknown trigger 'nightly.yml'"),
        ],
        ids=["read-fails", "read-fails-with-cron", "bad-trigger"],
    )
    async def test_an_undecided_template_warns_and_keeps_its_watermark(
        self, trigger, schedule, warning
    ):
        store = InMemoryTaskTable()
        last_run = _local(7, 9).isoformat()
        await self._template(store, trigger, schedule=schedule, last_run=last_run)
        result = await self._tick(store, FakeBuildRuns(None))
        assert result.warnings == [f"nightly: {warning}"]
        assert result.created == []
        assert (await model.load(store, "p", "nightly")).last_run == last_run

    async def test_a_cron_boundary_with_a_green_build_is_skipped(self):
        store = InMemoryTaskTable()
        await self._template(
            store,
            "gh-action/nightly.yml",
            schedule="0 9 * * *",
            last_run=_local(7, 9).isoformat(),
        )
        await self._tick(
            store, FakeBuildRuns([_build_run(3, "success", _local(7, 20))])
        )
        assert await _runs(store, "nightly") == []
        assert await self._watermark(store) == _local(8, 9)

    async def test_a_cron_boundary_with_a_failed_build_fires(self):
        store = InMemoryTaskTable()
        await self._template(
            store,
            "gh-action/nightly.yml",
            schedule="0 9 * * *",
            last_run=_local(7, 9).isoformat(),
        )
        await self._tick(
            store, FakeBuildRuns([_build_run(3, "failure", _local(6, 20))])
        )
        run = await model.load(store, "p", "nightly.2026-10-08")
        assert "https://github.com/o/r/actions/runs/3" in run.content
        assert await self._watermark(store) == _local(8, 9)
