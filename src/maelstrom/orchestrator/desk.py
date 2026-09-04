"""The desk: what the canvas draws, as tasks and free agents.

Pure table maths over the desk table :mod:`maelstrom.desk_store` keeps. Every
function returns a new table and never changes the one it is given, so the
server decides when a change is published and saved.
"""

from collections.abc import Container, Iterable
from typing import Literal, cast

from .protocol import DeskEntry
from .world_build import split_task_key

#: The desk as the world holds it, keyed by desk id.
DeskTable = dict[str, DeskEntry]

#: What a desk id can name.
DeskKind = Literal["task", "agent"]

_KINDS: tuple[str, ...] = ("task", "agent")


def desk_id_for_task(task_id: str) -> str:
    """The desk id for a notebook task."""
    return f"task:{task_id}"


def desk_id_for_agent(agent_id: str) -> str:
    """The desk id for a free agent — one with no task."""
    return f"agent:{agent_id}"


def split_desk_id(desk_id: str) -> tuple[DeskKind, str]:
    """What a desk id names: its kind, then the entity's own id.

    Raises:
        ValueError: If ``desk_id`` carries no kind, or a kind the desk has no
            entity for.
    """
    kind, sep, entity_id = desk_id.partition(":")
    if not sep or kind not in _KINDS:
        raise ValueError(f"Not a desk id: {desk_id!r}")
    return cast(DeskKind, kind), entity_id


def add(table: DeskTable, desk_id: str, now: str) -> DeskTable:
    """The table with ``desk_id`` on the desk.

    Adding an entry that is on the desk already keeps the time it first
    arrived, so a second add changes nothing.
    """
    if desk_id in table:
        return dict(table)
    return {**table, desk_id: {"id": desk_id, "addedAt": now}}


def remove(table: DeskTable, desk_id: str) -> DeskTable:
    """The table with ``desk_id`` off the desk.

    Raises:
        KeyError: If ``desk_id`` is not on the desk.
    """
    if desk_id not in table:
        raise KeyError(desk_id)
    return {k: v for k, v in table.items() if k != desk_id}


def prune(
    table: DeskTable, task_ids: Container[str], projects: Iterable[str]
) -> DeskTable:
    """The table with every entry whose task the notebook no longer has dropped.

    Only ``task:`` entries are pruned, and only against ``projects``. A
    project missing from the reading said nothing about its tasks, so its
    entries are kept: project discovery is a filesystem scan, and a project
    that is briefly absent must not cost the user the desk they built for it.

    An ``agent:`` entry is never pruned. An agent stays in the world once
    seen, so the entry always has an entity to draw. See
    :func:`drop_unknown_agents` for the rule that applies to a stored desk.
    """
    covered = set(projects)
    return {
        k: v
        for k, v in table.items()
        if _task_of(k) in task_ids or _project_of(k) not in covered
    }


def drop_unknown_agents(table: DeskTable, agent_ids: Container[str]) -> DeskTable:
    """The table with every ``agent:`` entry naming an unknown agent dropped.

    Only for a desk read from storage. The world's agents do not persist, so a
    stored entry can name an agent that no longer exists; it would draw
    nothing and could never be dismissed. Within one run the opposite rule
    applies — see :func:`prune`.
    """
    return {k: v for k, v in table.items() if _keeps_agent(k, agent_ids)}


def _keeps_agent(desk_id: str, agent_ids: Container[str]) -> bool:
    """Whether an entry survives the load: any non-agent, or a known agent."""
    agent_id = _agent_of(desk_id)
    return agent_id is None or agent_id in agent_ids


def _agent_of(desk_id: str) -> str | None:
    """The agent a desk id names, or ``None`` when it names no agent."""
    try:
        kind, entity_id = split_desk_id(desk_id)
    except ValueError:
        return None
    return entity_id if kind == "agent" else None


def _task_of(desk_id: str) -> str:
    """The task a desk id names, or ``""`` when it names no task.

    ``""`` is never a task id, so an entry that names no task matches no
    reading and falls to the project rule below, which keeps it.
    """
    try:
        kind, entity_id = split_desk_id(desk_id)
    except ValueError:
        return ""
    return entity_id if kind == "task" else ""


def _project_of(desk_id: str) -> str:
    """The project a desk id's task is in, or ``""`` when it names no task."""
    task_id = _task_of(desk_id)
    if not task_id:
        return ""
    try:
        return split_task_key(task_id)[0]
    except ValueError:
        return ""
