"""Pure scheduling logic for the task notebook.

A *template* is an ordinary task parked in ``template/`` status (see
:data:`maelstrom.task.STATUS_TEMPLATE`) carrying an optional ``schedule`` cron
expression and a ``last_run`` watermark. This module owns the cron math and the
"what's due" computation; it never touches git or launches anything — it only
reads the injected store and returns plain data, so it is unit-testable against
an :class:`~maelstrom.task_store.InMemoryStore` with a frozen ``now``.

The cron parser supports the needed 5-field subset (``m h dom mon dow``): ``*``,
single integers, comma lists, and ``a-b`` ranges (and combinations like
``0,30``). Step (``*/5``) is supported as a top-level field step. Day-of-month
and day-of-week are AND-combined (both must match) for simplicity — the common
schedules ("every weekday at 9", "hourly") never set both, so the distinction
from cron's OR-semantics does not bite.
"""

from datetime import datetime, timedelta, timezone

from .task import STATUS_TEMPLATE, Task, list_tasks
from .task_store import TaskStore


# --- cron field parsing ---


# (min, max) inclusive bounds for each of the five fields.
_FIELD_BOUNDS = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0 = Sunday)
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


def previous_fire(expr: str, now: datetime) -> datetime | None:
    """Return the most recent fire boundary at or before ``now``, or ``None``.

    Scans minute-by-minute backwards from ``now`` (truncated to the minute).
    Returns ``None`` if no boundary is found within ~one year (e.g. an
    unsatisfiable expression).
    """
    parsed = _parse_cron(expr)
    cursor = now.replace(second=0, microsecond=0)
    for _ in range(_MAX_MINUTES):
        if _matches(cursor, parsed):
            return cursor
        cursor -= timedelta(minutes=1)
    return None


def next_fire(expr: str, now: datetime) -> datetime | None:
    """Return the next fire boundary strictly after ``now``, or ``None``.

    Used for display (e.g. a template listing). Scans minute-by-minute forward.
    """
    parsed = _parse_cron(expr)
    cursor = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_MINUTES):
        if _matches(cursor, parsed):
            return cursor
        cursor += timedelta(minutes=1)
    return None


# --- due-template computation ---


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp to an aware UTC datetime, or ``None`` if empty."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def date_of(dt: datetime) -> str:
    """Return the ISO date (``YYYY-MM-DD``) of a datetime — the run-id key."""
    return dt.date().isoformat()


def due_templates(
    store: TaskStore, project: str, *, now: datetime
) -> list[tuple[Task, str]]:
    """Return ``(template, boundary_date)`` for every template due at ``now``.

    A template is due when it has a ``schedule`` and the most recent fire
    boundary at/before ``now`` is *after* its watermark (``last_run``, or
    ``created`` if never run). Only the single nearest boundary is considered, so
    a week offline on a daily template yields exactly one run — never a backfill.
    """
    out: list[tuple[Task, str]] = []
    for tmpl in list_tasks(store, project=project, status=STATUS_TEMPLATE):
        if not tmpl.schedule:
            continue
        last = _parse_iso(tmpl.last_run) or _parse_iso(tmpl.created)
        prev = previous_fire(tmpl.schedule, now)
        if prev is None:
            continue
        if last is None or last < prev:
            out.append((tmpl, date_of(prev)))
    return out
