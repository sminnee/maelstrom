"""Pure scheduling logic for the task notebook.

A *template* is an ordinary task parked in ``template/`` status (see
:data:`mael_domain.task.STATUS_TEMPLATE`) carrying an optional ``schedule`` cron
expression, an optional ``trigger`` and a ``last_run`` watermark. This module owns
the cron math and the "what's due" computation. It never touches git or launches
anything: it reads the injected table and
:class:`~mael_domain.build_runs.BuildRuns`, and :func:`fire_due` writes the runs
to that table. It is unit-testable against an
:class:`~mael_domain.task_table.InMemoryTaskTable` with a frozen ``now``.

The cron parser supports the needed 5-field subset (``m h dom mon dow``): ``*``,
single integers, comma lists, and ``a-b`` ranges (and combinations like
``0,30``). Step (``*/5``) is supported as a top-level field step. Day-of-month
and day-of-week are AND-combined (both must match) for simplicity — the common
schedules ("every weekday at 9", "hourly") never set both, so the distinction
from cron's OR-semantics does not bite.

Every schedule is read in one zone, the *schedule zone*, which the callers pass
as ``tz`` (see :func:`mael_domain.zone.zone_named`). For the DST rules and the
watermark comparison, see docs/dev/scheduled-tasks.md, "The schedule zone".
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from .build_runs import (
    BuildRun,
    BuildRuns,
    BuildTrigger,
    parse_trigger,
    run_section,
)
from .task import (
    STATUS_TEMPLATE,
    Task,
    allocate_run_id,
    duplicate,
    list_tasks,
    update,
)
from .task_table import TaskTable

# --- cron field parsing ---


# (min, max) inclusive bounds for each of the five fields.
_FIELD_BOUNDS = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 6),  # day of week (0 = Sunday)
)


def _parse_field(spec: str, lo: int, hi: int) -> set[int]:
    """Parse one cron field into the set of integer values it matches.

    Accepts ``*``, ``*/step``, single integers, ``a-b`` ranges, ``a-b/step``,
    and comma-separated lists of those. Raises ``ValueError`` on anything else
    or on a value outside ``[lo, hi]``.
    """
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Empty cron field component in {spec!r}")
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                raise ValueError(f"Invalid cron step in {part!r}")
            if step < 1:
                raise ValueError(f"Invalid cron step in {part!r}")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a_s, _, b_s = base.partition("-")
            try:
                start, end = int(a_s), int(b_s)
            except ValueError:
                raise ValueError(f"Invalid cron range in {base!r}")
        else:
            try:
                start = end = int(base)
            except ValueError:
                raise ValueError(f"Invalid cron value in {base!r}")
        if start < lo or end > hi or start > end:
            raise ValueError(f"Cron value out of range [{lo},{hi}] in {part!r}")
        values.update(range(start, end + 1, step))
    return values


def _parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse a 5-field cron expression into per-field value sets.

    Raises ``ValueError`` if the expression does not have exactly five
    whitespace-separated fields or any field is malformed.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"Cron expression must have 5 fields (m h dom mon dow): {expr!r}"
        )
    minute, hour, dom, mon, dow = (
        _parse_field(fields[i], *_FIELD_BOUNDS[i]) for i in range(5)
    )
    return minute, hour, dom, mon, dow


def _matches(dt: datetime, parsed) -> bool:
    """Return whether ``dt`` (minute resolution) satisfies the parsed cron."""
    minute, hour, dom, mon, dow = parsed
    # Python: Monday=0..Sunday=6; cron: Sunday=0..Saturday=6.
    cron_dow = (dt.weekday() + 1) % 7
    return (
        dt.minute in minute
        and dt.hour in hour
        and dt.day in dom
        and dt.month in mon
        and cron_dow in dow
    )


# How far back ``previous_fire`` / forward ``next_fire`` will scan before giving
# up. Minute granularity over ~366 days covers any realistic schedule (e.g. a
# yearly "0 9 1 1 *") with margin while staying a bounded loop.
_MAX_MINUTES = 366 * 24 * 60


# The largest DST shift the scan allows for. A scan starts this far past
# ``now``'s wall minute, so a minute whose instant is on the other side of a
# shift is still visited. One hour covers every zone in use.
_DST_SHIFT = timedelta(hours=1)


def _resolve(wall: datetime, tz: tzinfo) -> datetime:
    """The instant a naive wall-clock minute in ``tz`` names (``fold=0``), in ``tz``.

    The trip through UTC moves a skipped-hour minute to the real time it names.
    """
    return wall.replace(tzinfo=tz, fold=0).astimezone(timezone.utc).astimezone(tz)


def _wall_minute(now: datetime, tz: tzinfo) -> datetime:
    """``now`` as a naive wall-clock minute in ``tz``."""
    return now.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)


def _utc(now: datetime) -> datetime:
    """``now`` in UTC. Two datetimes with one tzinfo compare by wall clock and
    ignore ``fold``, so each instant comparison has ``now`` in another zone."""
    return now.astimezone(timezone.utc)


def previous_fire(expr: str, now: datetime, *, tz: tzinfo) -> datetime | None:
    """Return the most recent fire boundary at or before ``now``, or ``None``.

    Scans wall-clock minutes in ``tz`` backwards, from one DST shift past
    ``now``. A minute whose instant is after ``now`` is skipped: a skipped-hour
    minute resolves one hour later. Returns ``None`` if no boundary is found
    within ~one year (e.g. an unsatisfiable expression).
    """
    parsed = _parse_cron(expr)
    at = _utc(now)
    cursor = _wall_minute(now, tz) + _DST_SHIFT
    for _ in range(_MAX_MINUTES):
        if _matches(cursor, parsed):
            instant = _resolve(cursor, tz)
            if instant <= at:
                return instant
        cursor -= timedelta(minutes=1)
    return None


def next_fire(expr: str, now: datetime, *, tz: tzinfo) -> datetime | None:
    """Return the next fire boundary strictly after ``now``, or ``None``.

    Used for display (e.g. a template listing). Scans wall-clock minutes in
    ``tz`` forward, from one DST shift before ``now``, and skips a minute whose
    instant is at or before ``now``.
    """
    parsed = _parse_cron(expr)
    at = _utc(now)
    cursor = _wall_minute(now, tz) - _DST_SHIFT
    for _ in range(_MAX_MINUTES):
        if _matches(cursor, parsed):
            instant = _resolve(cursor, tz)
            if instant > at:
                return instant
        cursor += timedelta(minutes=1)
    return None


# --- due-template computation ---


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp to an aware datetime, or ``None`` if empty.

    An aware value stays as it is: watermarks compare with boundaries as
    instants, whatever zone wrote them. A naive value reads as host local.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def date_of(dt: datetime) -> str:
    """Return the ISO date (``YYYY-MM-DD``) of a datetime — the run-id key.

    Pass a datetime in the schedule zone, so the key is the day in that zone.
    """
    return dt.date().isoformat()


def _due_boundary(tmpl: Task, now: datetime, tz: tzinfo) -> datetime | None:
    """The cron boundary ``tmpl`` is due at, or ``None`` when it is not due.

    It is due when the most recent boundary at/before ``now`` is *after* its
    watermark (``last_run``, or ``created`` if never run). Only the single
    nearest boundary is considered, so a week offline on a daily template yields
    exactly one run — never a backfill.
    """
    last = _parse_iso(tmpl.last_run) or _parse_iso(tmpl.created)
    prev = previous_fire(tmpl.schedule, now, tz=tz)
    if prev is None or (last is not None and last >= prev):
        return None
    return prev


@dataclass(frozen=True)
class Firing:
    """What one tick does to one template: move its watermark, and maybe fire.

    A ``date`` makes a run keyed by it, and ``section`` is added to the run's
    content. With no ``date``, only the watermark moves.
    """

    tmpl: Task
    watermark: datetime
    date: str = ""
    section: str = ""


def run_to_fire(
    trigger: BuildTrigger, runs: list[BuildRun], since: datetime | None
) -> tuple[BuildRun | None, datetime | None]:
    """The run that fires a template with no cron, and its next watermark.

    Only the newest run after ``since`` counts, so a green run that already
    fixed the build stops an older failure from firing. The watermark moves to
    that run whether or not it fires. ``(None, None)`` means no new run.
    """
    newer = [run for run in runs if since is None or run.completed_at > since]
    if not newer:
        return None, None
    newest = max(newer, key=lambda run: run.completed_at)
    fires = newest.conclusion in trigger.conclusions
    return (newest if fires else None), newest.completed_at


async def due_firings(
    table: TaskTable,
    project: str,
    build_runs: BuildRuns,
    *,
    now: datetime,
    tz: tzinfo,
) -> tuple[list[Firing], list[str]]:
    """One firing per template that fires at ``now`` or moves its watermark.

    A template with a cron fires at its due boundary. With a trigger as well,
    the boundary fires only when the newest completed run has a listed
    conclusion, and is skipped otherwise. A template with a trigger and no cron
    fires on :func:`run_to_fire`. Runs are read once per triggered template that
    is due, and only for those. Every cron and run date is read in ``tz``.

    The second list holds one warning per template the tick could not decide: a
    trigger that does not parse, or a failed read. Its watermark does not move.
    """
    firings: list[Firing] = []
    warnings: list[str] = []
    for tmpl in await list_tasks(table, project=project, status=STATUS_TEMPLATE):
        boundary = None
        if tmpl.schedule:
            boundary = _due_boundary(tmpl, now, tz)
            if boundary is None:
                continue
        if not tmpl.trigger:
            if boundary is not None:
                firings.append(Firing(tmpl, boundary, date_of(boundary)))
            continue
        trigger = parse_trigger(tmpl.trigger)
        if trigger is None:
            warnings.append(f"{tmpl.id}: unknown trigger {tmpl.trigger!r}")
            continue
        runs = await build_runs.completed(project, trigger)
        if runs is None:
            warnings.append(f"{tmpl.id}: could not read the {trigger.workflow} runs")
            continue
        firing = _triggered(tmpl, trigger, runs, boundary, tz)
        if firing is not None:
            firings.append(firing)
    return firings, warnings


def _triggered(
    tmpl: Task,
    trigger: BuildTrigger,
    runs: list[BuildRun],
    boundary: datetime | None,
    tz: tzinfo,
) -> Firing | None:
    """The firing of a template with a trigger; ``boundary`` is its due cron boundary."""
    if boundary is not None:
        newest = runs[0] if runs else None
        if newest is None or newest.conclusion not in trigger.conclusions:
            return Firing(tmpl, boundary)
        return Firing(tmpl, boundary, date_of(boundary), run_section(trigger, newest))
    since = _parse_iso(tmpl.last_run) or _parse_iso(tmpl.created)
    run, watermark = run_to_fire(trigger, runs, since)
    if watermark is None:
        return None
    if run is None:
        return Firing(tmpl, watermark)
    # Keyed by the date in the schedule zone, as a cron boundary is.
    date = date_of(run.completed_at.astimezone(tz))
    return Firing(tmpl, watermark, date, run_section(trigger, run))


@dataclass(frozen=True)
class FireResult:
    """What one tick did to one project: the runs it made, and what it could not decide."""

    created: list[Task]
    warnings: list[str]


async def fire_due(
    table: TaskTable,
    project: str,
    build_runs: BuildRuns,
    *,
    now: datetime,
    tz: tzinfo,
) -> FireResult:
    """Make one run per template that fires at ``now``, and move each watermark.

    Each firing duplicates its template into a date-keyed run and advances the
    template's ``last-run`` in one transaction. A run id that already exists (a
    RunAtLoad and interval double fire, or a second build run on the same day)
    makes nothing, but the watermark still moves past it. A firing from a build
    run adds its ``## Build run`` section to the run's content. A firing with no
    date moves only the watermark.

    The run's ``parent`` is empty, so the run roots its own chain. See
    docs/dev/tasks.md, "Scheduled runs".
    """
    created: list[Task] = []
    firings, warnings = await due_firings(table, project, build_runs, now=now, tz=tz)
    for firing in firings:
        tmpl = firing.tmpl
        last_run = firing.watermark.isoformat()
        if not firing.date:
            await update(table, project, tmpl.id, last_run=last_run)
            continue
        run_id = allocate_run_id(tmpl.id, firing.date)
        if await table.load(project, run_id) is not None:
            await update(table, project, tmpl.id, last_run=last_run)
            continue
        content = None
        if firing.section:
            content = f"{tmpl.content.strip()}\n\n{firing.section}".strip()
        # A rollback leaves the boundary unfired, not half-fired.
        async with table.transact():
            new = await duplicate(
                table,
                project,
                tmpl.id,
                parent="",
                branch=tmpl.branch,
                content=content,
                id=run_id,
            )
            await update(table, project, tmpl.id, last_run=last_run)
        created.append(new)
    return FireResult(created, warnings)
