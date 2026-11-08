"""Tests for the scheduler: cron math, the due computation, and the fire step."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import TZPATH, ZoneInfo

import pytest
from domain_fixtures import FakeBuildRuns

from mael_domain import schedule as sched
from mael_domain import task as model
from mael_domain.build_runs import BuildRun
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.zone import host_zone

UTC = timezone.utc
NZ = ZoneInfo("Pacific/Auckland")


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
        assert sched.previous_fire(expr, _dt(now), tz=UTC) == _dt(expected)

    def test_weekday_range_excludes_weekend(self):
        # Sunday 2026-06-21 at 10:00, weekdays-only -> previous Friday 09:00.
        assert sched.previous_fire(
            "0 9 * * 1-5", _dt("2026-06-21T10:00:00"), tz=UTC
        ) == _dt("2026-06-19T09:00:00")


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
        assert sched.next_fire(expr, _dt(now), tz=UTC) == _dt(expected)


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
            sched.previous_fire(expr, _dt("2026-06-18T10:00:00"), tz=UTC)

    def test_list_field(self):
        # "0,30" minutes -> matches both.
        assert sched.previous_fire(
            "0,30 * * * *", _dt("2026-06-18T10:45:00"), tz=UTC
        ) == _dt("2026-06-18T10:30:00")


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


async def _due(store, now, tz=UTC):
    """``(template id, date)`` per firing at ``now``, for templates with no trigger."""
    runs = FakeBuildRuns(None)
    firings, warnings = await sched.due_firings(store, "p", runs, now=now, tz=tz)
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


@pytest.fixture(params=["utc", "zone"])
def at(request):
    """A UTC ISO string as ``now``: in UTC, or in NZ as the CLI passes it.

    Two datetimes with one tzinfo compare by wall clock, so each test runs both.
    """

    def make(s: str) -> datetime:
        now = _dt(s)
        return now if request.param == "utc" else now.astimezone(NZ)

    return make


class TestScheduleZone:
    """Schedules read in NZ."""

    async def _template(self, store, schedule, last_run):
        await _add_template(
            store,
            "p",
            schedule=schedule,
            last_run=last_run,
            created="2026-01-01T00:00:00+00:00",
        )

    async def test_a_boundary_fires_at_its_zone_time(self, at):
        # Mon 2026-11-09 08:00 NZDT is Sun 2026-11-08 19:00 UTC.
        store = InMemoryTaskTable()
        await self._template(store, "0 8 * * 1,5", "2026-11-06T08:00:00+13:00")
        assert await _due(store, at("2026-11-08T18:55:00"), NZ) == []
        assert await _due(store, at("2026-11-08T19:05:00"), NZ) == [
            ("maintenance", "2026-11-09")
        ]

    async def test_a_skipped_hour_boundary_fires_one_hour_later(self, at):
        # NZ springs forward 2026-09-27 02:00 NZST -> 03:00 NZDT (14:00 UTC).
        # 02:30 names 03:30 NZDT (14:30 UTC). At 03:10 NZDT it is still ahead.
        boundary = datetime(2026, 9, 27, 3, 30, tzinfo=NZ)
        assert sched.previous_fire(
            "30 2 * * *", at("2026-09-26T14:10:00"), tz=NZ
        ) == datetime(2026, 9, 26, 2, 30, tzinfo=NZ)
        assert sched.next_fire("30 2 * * *", at("2026-09-26T14:10:00"), tz=NZ) == (
            boundary
        )
        store = InMemoryTaskTable()
        await self._template(store, "30 2 * * *", "2026-09-26T02:30:00+12:00")
        assert await _due(store, at("2026-09-26T14:10:00"), NZ) == []
        assert await _due(store, at("2026-09-26T14:30:00"), NZ) == [
            ("maintenance", "2026-09-27")
        ]

    async def test_a_repeated_hour_boundary_fires_once(self, at):
        # NZ falls back 2026-04-05 03:00 NZDT -> 02:00 NZST. 02:00 NZDT is
        # 13:00 UTC, and the second 02:00 (NZST) is 14:00 UTC.
        store = InMemoryTaskTable()
        await self._template(store, "0 2 * * *", "2026-04-04T02:00:00+13:00")
        result = await sched.fire_due(
            store, "p", FakeBuildRuns(None), now=at("2026-04-04T13:05:00"), tz=NZ
        )
        assert [t.id for t in result.created] == ["maintenance.2026-04-05"]
        assert await _due(store, at("2026-04-04T14:05:00"), NZ) == []

    async def test_a_boundary_missed_in_the_first_pass_fires_in_the_second(self, at):
        # No tick in the first 02:xx. At 02:15 NZST the 02:30 NZDT boundary
        # (13:30 UTC) is past, though its wall minute is later than now's.
        # An ambiguous time never equals one in another zone (PEP 495): compare in UTC.
        boundary = sched.previous_fire("30 2 * * *", at("2026-04-04T14:15:00"), tz=NZ)
        assert boundary.astimezone(UTC) == _dt("2026-04-04T13:30:00")

    async def test_a_frequent_schedule_skips_the_second_pass(self, at):
        store = InMemoryTaskTable()
        await self._template(store, "*/5 * * * *", "2026-04-05T02:55:00+13:00")
        # 02:10 NZST, the second pass of the repeated hour.
        assert await _due(store, at("2026-04-04T14:10:00"), NZ) == []
        # 03:00 NZST, after the repeated hour, fires again.
        assert await _due(store, at("2026-04-04T15:00:00"), NZ) == [
            ("maintenance", "2026-04-05")
        ]

    async def test_an_old_utc_host_watermark_does_not_double_fire(self, at):
        # The old UTC host fired Mon 08:00 UTC (Mon 21:00 NZDT). On Tue 10:00
        # NZDT the nearest boundary, Mon 08:00 NZDT, is before it.
        store = InMemoryTaskTable()
        await self._template(store, "0 8 * * 1,5", "2026-11-09T08:00:00+00:00")
        assert await _due(store, at("2026-11-09T21:00:00"), NZ) == []
        # Fri 2026-11-13 08:05 NZDT fires.
        assert await _due(store, at("2026-11-12T19:05:00"), NZ) == [
            ("maintenance", "2026-11-13")
        ]


class TestHostZone:
    """With no ``schedule_timezone``, the host's zone, never a fixed offset."""

    def test_tz_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TZ", ":Europe/London")
        assert host_zone(tmp_path / "missing").key == "Europe/London"

    def test_the_localtime_link_names_the_zone(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TZ", raising=False)
        target = tmp_path / "zoneinfo" / "Pacific" / "Auckland"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"")
        link = tmp_path / "localtime"
        link.symlink_to(target)
        assert host_zone(link).key == "Pacific/Auckland"

    def test_a_localtime_file_is_read(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TZ", raising=False)
        localtime = tmp_path / "localtime"
        [source] = [
            Path(root, "Pacific", "Auckland")
            for root in TZPATH
            if Path(root, "Pacific", "Auckland").exists()
        ][:1]
        localtime.write_bytes(source.read_bytes())
        zone = host_zone(localtime)
        assert datetime(2026, 1, 1, tzinfo=zone).utcoffset() == timedelta(hours=13)
        assert datetime(2026, 7, 1, tzinfo=zone).utcoffset() == timedelta(hours=12)

    def test_no_localtime_is_utc(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TZ", raising=False)
        assert host_zone(tmp_path / "missing").key == "UTC"


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
        return await sched.fire_due(store, "p", FakeBuildRuns(None), now=now, tz=UTC)

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
    """An NZ wall-clock time on 2026-10-``day``, so the run's date is ``day``.

    NZ is UTC+13 then, so a morning time is on the previous UTC day.
    """
    return datetime(2026, 10, day, hour, 0, tzinfo=NZ)


def _build_run(id, conclusion, completed_at):
    return BuildRun(
        id=id,
        conclusion=conclusion,
        completed_at=completed_at,
        url=f"https://github.com/o/r/actions/runs/{id}",
        head_sha=f"abcdef{id}0000",
    )


class TestFireDueOnTrigger:
    """Templates with a build trigger, fired at 2026-10-08 10:00 NZ."""

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
        return await sched.fire_due(
            store, "p", runs, now=self.NOW.astimezone(UTC), tz=NZ
        )

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
