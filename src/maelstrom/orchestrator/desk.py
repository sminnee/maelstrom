"""The desk: which tasks the user has put on the canvas.

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


def add(table: DeskTable, task_id: str, now: str) -> DeskTable:
    """The table with ``task_id`` on the desk.

    Adding a task that is on the desk already keeps the time it first
    arrived, so a second add changes nothing.
    """
    if task_id in table:
        return dict(table)
    return {**table, task_id: {"id": task_id, "addedAt": now}}


def remove(table: DeskTable, task_id: str) -> DeskTable:
    """The table with ``task_id`` off the desk.

    Raises:
        KeyError: If ``task_id`` is not on the desk.
    """
    if task_id not in table:
        raise KeyError(task_id)
    return {k: v for k, v in table.items() if k != task_id}


def prune(
    table: DeskTable, task_ids: Container[str], projects: Iterable[str]
) -> DeskTable:
    """The table with every entry whose task the notebook no longer has dropped.

    Only ``task:`` entries are pruned, and only against ``projects``. A
    project missing from the reading said nothing about its tasks, so its
    entries are kept: project discovery is a filesystem scan, and a project
    that is briefly absent must not cost the user the desk they built for it.

    An ``agent:`` entry is never pruned. Nothing removes an agent from the
    world, so the entry always has an entity to draw, and dropping it would
    defeat the sticky rule that keeps a stopped agent on the canvas.
    """
    covered = set(projects)
    return {
        k: v
        for k, v in table.items()
        if _task_of(k) in task_ids or _project_of(k) not in covered
    }


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
    """The project a desk id's task is in, or ``""`` when it names no task.

    An id that names no task falls outside every covered project, so
    :func:`prune` keeps it.
    """
    task_id = _task_of(desk_id)
    if not task_id:
        return ""
    try:
        return split_task_key(task_id)[0]
    except ValueError:
        return ""
