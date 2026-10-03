"""Canonical Agent records."""

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from mael_agent.harness_model import HARNESS_CLAUDE, TRANSPORT_CLI, TRANSPORT_DAEMON

from .state_db.db import StateDb

#: The statuses an Agent record carries. ``running`` from the start until
#: ``stop``, ``ended`` from then on. A record written before this field existed
#: carries neither and reads as ``running``, which is what it meant.
AGENT_RUNNING = "running"
AGENT_ENDED = "ended"

#: A batch of Agent records. Named here because each store's own ``list``
#: method shadows the builtin inside its class body.
Records = list[dict[str, Any]]


class AgentStore(Protocol):
    """The canonical records for Agents Maelstrom has started.

    One record is one session of one task, so the store is also the relation
    between the two: :meth:`for_task` and :meth:`for_session` read it from
    either end.
    """

    async def save(self, agent: dict[str, Any]) -> None: ...

    async def list(self) -> list[dict[str, Any]]: ...

    async def read(self, agent_id: str) -> dict[str, Any] | None: ...

    async def changed_since(self, since: int) -> tuple[Records, int]:
        """The records written after revision ``since``, and the revision to
        ask from next time."""
        ...

    async def for_task(self, task: str) -> Records:
        """The records of one task, newest first.

        ``task`` is the task row id, ``<project>/<task_id>``. A blank names no
        task, so it answers nothing rather than every adopted agent.
        """
        ...

    async def for_session(self, session_id: str) -> Records:
        """The records that ran one session, newest first.

        A resume starts a new agent on the session the last one held, so one
        session id can span several records. A blank answers nothing.
        """
        ...

    async def retask(self, old: str, new: str) -> None:
        """Move every record of task ``old`` to task ``new``.

        What a task rename and a project rename call, so the sessions follow
        the task to its new row id.
        """
        ...


class SqliteAgentStore(AgentStore):
    """Store the Agents Maelstrom has started."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def save(self, agent: dict[str, Any]) -> None:
        """Write the record, with both ends of the relation as columns.

        The columns are copied from the body on every save, so an index can
        never name a task the record no longer does.
        """
        await self._db.upsert(
            "agents",
            str(agent["id"]),
            body=json.dumps(agent, sort_keys=True),
            task=str(agent.get("task") or ""),
            session_id=str(agent.get("session_id") or ""),
        )

    async def list(self) -> list[dict[str, Any]]:
        agents: list[dict[str, Any]] = []
        for row in await self._db.read_all("agents"):
            if (agent := _decoded(row)) is not None:
                agents.append(agent)
        return agents

    async def read(self, agent_id: str) -> dict[str, Any] | None:
        """One agent's record, or ``None``. Keyed, so it costs one row.

        The table holds every agent Maelstrom ever started, so a caller asking
        about a single id must not pay the lifetime agent count to find out.
        """
        row = await self._db.read("agents", agent_id)
        return _decoded(row) if row is not None else None

    async def changed_since(self, since: int) -> tuple[Records, int]:
        """The records written after revision ``since``, and the revision read.

        A poller's read: it costs the records that moved, not the lifetime
        count. The revision is read first, so a record written between the two
        reads comes back again next time rather than never.
        """
        revision = await self._db.revision()
        rows = await self._db.changed_since("agents", since)
        return [a for row in rows if (a := _decoded(row)) is not None], revision

    async def for_task(self, task: str) -> Records:
        return await self._where("task", task)

    async def for_session(self, session_id: str) -> Records:
        return await self._where("session_id", session_id)

    async def retask(self, old: str, new: str) -> None:
        async with self._db.transact():
            for agent in await self.for_task(old):
                await self.save({**agent, "task": new})

    async def _where(self, column: str, value: str) -> Records:
        """The records whose indexed ``column`` is ``value``, newest first."""
        if not value:
            return []
        rows = await self._db.read_where("agents", column, value)
        return _newest_first([a for row in rows if (a := _decoded(row)) is not None])


class InMemoryAgentStore(AgentStore):
    """An :class:`AgentStore` with no database, for a test and a bare server.

    Records are copied on the way in and on the way out, so a caller cannot
    change stored state through a shared reference. A save that changes nothing
    moves no revision, as on the database.
    """

    def __init__(self, records: Records | None = None) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._revisions: dict[str, int] = {}
        self._revision = 0
        for record in records or []:
            self._write(record)

    def _write(self, agent: dict[str, Any]) -> None:
        agent_id = str(agent["id"])
        if self._records.get(agent_id) == agent:
            return
        self._revision += 1
        self._records[agent_id] = dict(agent)
        self._revisions[agent_id] = self._revision

    @property
    def records(self) -> dict[str, dict[str, Any]]:
        """Every stored record by agent id, copied, for a test to assert on."""
        return {agent_id: dict(agent) for agent_id, agent in self._records.items()}

    async def save(self, agent: dict[str, Any]) -> None:
        self._write(agent)

    async def list(self) -> list[dict[str, Any]]:
        return [dict(self._records[agent_id]) for agent_id in sorted(self._records)]

    async def read(self, agent_id: str) -> dict[str, Any] | None:
        agent = self._records.get(agent_id)
        return dict(agent) if agent is not None else None

    async def changed_since(self, since: int) -> tuple[Records, int]:
        moved = sorted(
            (revision, agent_id)
            for agent_id, revision in self._revisions.items()
            if revision > since
        )
        return [dict(self._records[agent_id]) for _, agent_id in moved], self._revision

    async def for_task(self, task: str) -> Records:
        return await self._where("task", task)

    async def for_session(self, session_id: str) -> Records:
        return await self._where("session_id", session_id)

    async def retask(self, old: str, new: str) -> None:
        for agent in await self.for_task(old):
            self._write({**agent, "task": new})

    async def _where(self, field: str, value: str) -> Records:
        if not value:
            return []
        return _newest_first(
            [agent for agent in await self.list() if agent.get(field) == value]
        )


def _newest_first(records: Records) -> Records:
    """``records`` by start, newest first.

    Compared as instants, not as text: two stamps in different zones sort
    wrongly as strings. A record with no readable start is the oldest, which is
    what :func:`register_agent` says an empty one means.
    """
    return sorted(records, key=_started, reverse=True)


def _started(agent: dict[str, Any]) -> datetime:
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    try:
        at = datetime.fromisoformat(str(agent.get("started_at") or ""))
    except ValueError:
        return oldest
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _decoded(row: Any) -> dict[str, Any] | None:
    """One stored row as a record, or ``None`` when it is not one.

    A store bug must drop the bad row rather than corrupt the read, so a body
    that is not JSON, or whose ``id`` disagrees with the row's, is no record.
    """
    try:
        agent = json.loads(row["body"])
    except json.JSONDecodeError:
        return None
    if isinstance(agent, dict) and agent.get("id") == row["id"]:
        return agent
    return None


def new_agent_record(
    agent_id: str,
    *,
    harness: str,
    session_id: str,
    task: str,
    cwd: str,
    model: str,
    mode: str,
    started_at: str,
    transport: str = TRANSPORT_DAEMON,
) -> dict[str, Any]:
    """One running Agent record, however the agent arrived.

    The single constructor for the shape, so a launched agent and an adopted one
    cannot end up describable by different fields — which is the
    indistinguishability the adoption path exists to give.

    The record outlives the agent, so it says whether the agent is still there
    and when each end of its life was.

    ``task`` is the task row id, ``<project>/<task_id>``, and empty for an
    agent on no task. ``session_id`` is the session the agent started on. The
    two make the record the link between a task and its sessions.

    ``transport`` is named only for a ``cli`` session, which no daemon holds —
    see :func:`is_cli_record`.
    """
    record = {
        "id": agent_id,
        "harness": harness,
        "session_id": session_id,
        "task": task,
        "cwd": cwd,
        "model": model,
        "mode": mode or "normal",
        "status": AGENT_RUNNING,
        "started_at": started_at,
        "ended_at": "",
    }
    if transport != TRANSPORT_DAEMON:
        record["transport"] = transport
    return record


def is_cli_record(agent: dict[str, Any]) -> bool:
    """Whether ``agent`` records a ``cli`` session rather than a driven agent.

    No daemon holds such a session, so nothing lists it and nothing ends its
    record: the process table says whether it is live. A reader that asks a
    daemon about every running record must leave these out.
    """
    return agent.get("transport") == TRANSPORT_CLI


async def task_of_session(store: AgentStore, session_id: str) -> str:
    """The task row id a session ran for, or ``""`` when it ran for none.

    The newest record that names a task answers. An adopted record of the same
    session names none, and must not hide the launch's own.
    """
    for record in await store.for_session(session_id):
        if task := str(record.get("task") or ""):
            return task
    return ""


async def register_agent(
    store: AgentStore,
    agent_id: str,
    row: dict[str, Any],
    task: str,
    *,
    harness: str = HARNESS_CLAUDE,
    started_at: str = "",
) -> dict[str, Any]:
    """Adopt a live agent that has no Agent record: ``row`` is its daemon ``list`` entry.

    Returned as well as saved, because the router holds its live set in memory
    and would otherwise read the store back to get it.

    ``harness`` defaults to ``claude`` for ``mael agent register``, which
    reaches only the Claude agent daemon. ``started_at`` is when the adoption
    happened, not when the agent did: nobody recorded the real start. An empty
    one reads as arbitrarily old, so the record gets no grace period.
    """
    agent = new_agent_record(
        agent_id,
        harness=harness,
        session_id=str(row.get("session", "")),
        task=task,
        cwd=str(row.get("cwd", "")),
        model=str(row.get("model", "")),
        mode=str(row.get("mode") or "normal"),
        started_at=started_at,
    )
    await store.save(agent)
    return agent


class MilestoneStore(Protocol):
    """The ledger of what each agent had spent at each stage of the work."""

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]: ...

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]: ...


class SqliteMilestoneStore:
    """Store one snapshot per stage an agent reaches, with its delta.

    The delta is computed on write, against the agent's previous snapshot: the
    caller has the totals and no reason to read the ledger back.
    """

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]:
        """Write one snapshot, its delta included, and return it.

        A stage reached twice writes two rows rather than replacing one: the
        second run spent real tokens, and collapsing them would hide the spend.

        The row comes back because the caller has no other way to learn what
        the stage cost: the delta is computed here, against a previous
        snapshot only this store holds.
        """
        previous = await self.list(str(milestone["agent_id"]))
        row = _snapshot(
            milestone, previous[-1] if previous else None, len(previous) + 1
        )
        await self._db.upsert(
            "agent_milestones",
            row["id"],
            **{key: value for key, value in row.items() if key != "id"},
        )
        return _milestone_row(row)

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]:
        """Every snapshot, oldest first. One agent's with ``agent_id``, else all.

        The order is the order the stages were reached in, because the row id
        carries the ordinal and both reads sort by id.

        One agent's rows go through ``read_where``, which uses the table's
        ``agent_id`` index. :meth:`record` reads this on every write, so a scan
        here would make recording a milestone cost the whole ledger's history.
        """
        rows = (
            await self._db.read_where("agent_milestones", "agent_id", agent_id)
            if agent_id
            else await self._db.read_all("agent_milestones")
        )
        return [_milestone_row(row) for row in rows]


class InMemoryMilestoneStore:
    """A :class:`MilestoneStore` with no database, for a test and a bare server.

    Holds the rows :class:`SqliteMilestoneStore` would have written, in the
    same shape, so a reader cannot tell the two apart.
    """

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def record(self, milestone: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(milestone["agent_id"])
        previous = [row for row in self._rows if row["agent_id"] == agent_id]
        row = _snapshot(
            milestone, previous[-1] if previous else None, len(previous) + 1
        )
        self._rows.append(row)
        # Through `_milestone_row`, as `list` reads back and as the SQLite
        # backend returns: the two must stay indistinguishable.
        return _milestone_row(row)

    async def list(self, agent_id: str = "") -> list[dict[str, Any]]:
        # Through `_milestone_row`, as the SQLite backend reads back: a caller
        # must not be able to tell the two apart. `recognised` is the field
        # that would otherwise differ, stored as an int and read as a bool.
        return [
            _milestone_row(row)
            for row in self._rows
            if not agent_id or row["agent_id"] == agent_id
        ]


def _snapshot(
    milestone: dict[str, Any], previous: dict[str, Any] | None, ordinal: int
) -> dict[str, Any]:
    """One ledger row: the cumulative figures, and the delta since ``previous``.

    The shape both backends store and :func:`_milestone_row` reads back, built
    here so the two cannot drift apart.
    """
    agent_id = str(milestone["agent_id"])
    own = _count(milestone.get("own_tokens"))
    sub = _count(milestone.get("subagent_tokens"))
    cost = float(milestone.get("cost_usd") or 0.0)
    return {
        "id": _milestone_id(agent_id, ordinal),
        "agent_id": agent_id,
        "name": str(milestone.get("name") or ""),
        "at": str(milestone.get("at") or ""),
        "recognised": 1 if milestone.get("recognised", True) else 0,
        "own_total": own,
        "sub_total": sub,
        "cost_usd": cost,
        "own_delta": _delta(own, _count_of(previous, "own_total")),
        "sub_delta": _delta(sub, _count_of(previous, "sub_total")),
        "cost_delta": max(cost - float(previous["cost_usd"] if previous else 0.0), 0.0),
    }


def _milestone_row(row: Any) -> dict[str, Any]:
    """One stored snapshot, in the shape the model and the report read."""
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "name": row["name"],
        "at": row["at"],
        "recognised": bool(row["recognised"]),
        "own_total": row["own_total"],
        "sub_total": row["sub_total"],
        "cost_usd": row["cost_usd"],
        "own_delta": row["own_delta"],
        "sub_delta": row["sub_delta"],
        "cost_delta": row["cost_delta"],
    }


def _milestone_id(agent_id: str, ordinal: int) -> str:
    """The row id: the agent and a zero-padded ordinal.

    Padded because ``read_all`` sorts by id as text, and ``10`` must not sort
    before ``2``.
    """
    return f"{agent_id}#{ordinal:04d}"


def _count(value: Any) -> int:
    """A token figure off a caller's dict. Anything that is not a count is 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _count_of(row: dict[str, Any] | None, key: str) -> int:
    return _count(row[key]) if row else 0


def _delta(now: int, before: int) -> int:
    """What was spent between two readings.

    Never negative: a daemon restart puts a running total back to 0, and a
    negative delta would read as a stage that gave tokens back.
    """
    return max(now - before, 0)
