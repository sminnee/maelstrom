"""The refresher contract: what to fetch, how often, and what a refusal costs.

A refresher keeps one cached table current. It owns its cadence, its budget and
what it does when the upstream refuses; a reader never triggers one. See
``docs/dev/data-architecture.md``, "Cached".

Three guards the orchestrator server writes by hand today are one idea: the
GitHub rate-limit stand-off becomes :func:`refused`, the worktree poll's
``only_when_watched`` becomes :meth:`Refresher.wanted`, and the desk-narrowed
branch set becomes :meth:`Refresher.scope`.

``_kept_fresh`` stays outside the contract. It spaces reads an arrival
triggers, deliberately excluding the poll's own, where :func:`due` asks one
question of both.

No SQL and no loop. A refresher's caller awaits :meth:`Refresher.fetch` and then
writes the result: **fetch outside, write inside**, so no network call ever
happens with the write lock held.

Two clocks, because the two facts want different ones. A stand-off is measured
in monotonic loop time, so an NTP step cannot extend or end it. A ``since`` a
user reads is wall time. The server already keeps both; this makes the split
explicit rather than incidental.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Protocol

#: A narrowed fetch's ids, or ``None`` to ask about everything.
Scope = set[str] | None


class RefreshRefused(Exception):
    """The upstream would not answer.

    ``stand_off`` is how many seconds to wait before asking again. ``None``
    leaves the refresher's own :attr:`Refresher.stand_off_secs` to decide, which
    is the ordinary case: a rate limit is the refresher's budget, not the
    caller's.
    """

    def __init__(self, detail: str, *, stand_off: float | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.stand_off = stand_off


@dataclass(frozen=True)
class Fetched:
    """What one fetch came back with.

    ``complete`` is the most important thing here. A narrowed fetch never
    deletes: it did not ask about the rows it left out, so treating it as
    complete would delete every row outside the scope the moment the table
    persists. Today the worktree read is safe only because the table is rebuilt
    whole on every poll.
    """

    rows: dict[str, dict[str, Any]]
    complete: bool = True


class Refresher(Protocol):
    """What keeps one cached table current.

    :meth:`fetch` may block or return an awaitable, matching what
    ``Orchestrator._run`` already takes: a source converted to ``async`` keeps
    working through the one call site, and a source still blocking keeps its
    thread.
    """

    #: Names this refresher in its health row and in a log line.
    name: str
    #: Seconds between attempts.
    cadence: float
    #: Seconds to stand off after a refusal that names no period of its own.
    stand_off_secs: float

    def wanted(self) -> bool:
        """Whether anyone is watching. A read nobody hears buys nothing."""
        ...

    def scope(self) -> Scope:
        """The ids worth asking about, or ``None`` for all of them."""
        ...

    def fetch(self, scope: Scope) -> "Fetched | Awaitable[Fetched]":
        """Ask the upstream. Raises :class:`RefreshRefused` when it will not answer."""
        ...


@dataclass(frozen=True)
class Health:
    """What is known about one refresher, as its row holds it.

    ``reachable`` and ``since`` copy ``protocol.Host``'s semantics deliberately,
    including its rule: ``since`` moves only when ``reachable`` moves, so a
    second consecutive refusal does not reset the down-clock. That is exactly
    what a refusal must follow.

    ``protocol.Host`` is not reused: it is a wire TypedDict where this is a
    storage row.

    ``last_attempt`` and ``stand_off_until`` hold monotonic loop times; ``since``
    and ``last_success`` hold ISO wall times. Both are stored as text, because a
    row column is text and the two clocks must not be confused for one another
    by sharing a number type.
    """

    name: str
    reachable: bool
    since: str
    last_attempt: str = ""
    last_success: str = ""
    stand_off_until: str = ""
    detail: str = ""


def due(health: Health, refresher: Refresher, now: float) -> bool:
    """Whether ``refresher`` should run.

    Three reasons not to, and only those: it is standing off, nobody is
    watching, or the cadence has not passed. ``now`` is monotonic loop time, so
    an NTP step can neither extend a stand-off nor end one early.
    """
    if _standing_off(health, now):
        return False
    if not refresher.wanted():
        return False
    if not health.last_attempt:
        return True
    return now - _reading(health.last_attempt, default=0.0) >= refresher.cadence


def _standing_off(health: Health, now: float) -> bool:
    """Whether a refusal's stand-off still holds."""
    if not health.stand_off_until:
        return False
    return now < _reading(health.stand_off_until, default=0.0)


def _reading(value: str, *, default: float) -> float:
    """``value`` as a clock reading, or ``default`` when it is not one.

    A row a different build wrote, or one edited by hand, must not raise out of
    :func:`due`: the poller catches and logs, so it would log once a tick
    forever. Both defaults mean "ask now", which is the safe way to be wrong.
    """
    try:
        return float(value)
    except ValueError:
        return default


def refused(
    health: Health, exc: RefreshRefused, refresher: Refresher, now: float, at: str
) -> Health:
    """``health`` after a refusal, with the stand-off the refusal asked for.

    Both clocks: ``now`` is monotonic and the stand-off is measured from it;
    ``at`` is the ISO wall time a user reads.

    ``since`` moves only if this is the first refusal in a run of them. A
    refresher refused every minute for an hour has been down for an hour, not
    for a minute.
    """
    stand_off = exc.stand_off if exc.stand_off is not None else refresher.stand_off_secs
    return Health(
        name=health.name,
        reachable=False,
        since=at if health.reachable else health.since,
        last_attempt=str(now),
        last_success=health.last_success,
        stand_off_until=str(now + stand_off),
        detail=exc.detail,
    )


def succeeded(health: Health, now: float, at: str) -> Health:
    """``health`` after a fetch that answered, with the stand-off cleared.

    ``since`` moves only on the recovery itself, for the same reason a refusal
    leaves it alone: it says when the current state began.
    """
    return Health(
        name=health.name,
        reachable=True,
        since=at if not health.reachable else health.since,
        last_attempt=str(now),
        last_success=at,
        stand_off_until="",
        detail="",
    )


def health_row(health: Health) -> dict[str, Any]:
    """``health`` as the columns ``refresher_health`` holds.

    ``reachable`` becomes an integer, because SQLite has no boolean; the
    reverse is :func:`health_from_row`.
    """
    return {
        "reachable": int(health.reachable),
        "since": health.since,
        "last_attempt": health.last_attempt,
        "last_success": health.last_success,
        "stand_off_until": health.stand_off_until,
        "detail": health.detail,
    }


def health_from_row(name: str, row: Any) -> Health:
    """``row`` as a :class:`Health`, or an unknown one when there is no row.

    A refresher with no row has never run, which :func:`due` reads as due —
    so a first start asks, rather than waiting out a cadence it never served.
    """
    if row is None:
        return Health(name=name, reachable=True, since="")
    return Health(
        name=name,
        reachable=bool(row["reachable"]),
        since=row["since"],
        last_attempt=row["last_attempt"],
        last_success=row["last_success"],
        stand_off_until=row["stand_off_until"],
        detail=row["detail"],
    )


def health_entity(health: Health) -> dict[str, Any]:
    """``health`` as a wire entity.

    A plain dict rather than a TypedDict: adding ``"refresher"`` to
    ``ENTITY_KINDS`` is a wire change, and no refresher ships yet. The
    TypedDict lands beside ``Host`` when the first one does.
    """
    return {
        "id": health.name,
        "reachable": health.reachable,
        "since": health.since,
        "lastSuccess": health.last_success,
        "detail": health.detail,
    }
