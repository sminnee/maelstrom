"""The desk: which tasks the user has put on the canvas.

Pure table maths over the desk table :mod:`maelstrom.desk_store` keeps. Every
function returns a new table and never changes the one it is given, so the
server decides when a change is published and saved.
"""

from collections.abc import Container, Iterable

from .protocol import DeskEntry
from .world_build import split_task_key

#: The desk as the world holds it, keyed by wire task id.
DeskTable = dict[str, DeskEntry]


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

    Only ``projects`` are pruned against. A project missing from the reading
    said nothing about its tasks, so its entries are kept: project discovery
    is a filesystem scan, and a project that is briefly absent must not cost
    the user the desk they built for it.
    """
    covered = set(projects)
    return {
        k: v for k, v in table.items() if k in task_ids or _project_of(k) not in covered
    }


def _project_of(task_id: str) -> str:
    """The project a wire task id names, or ``""`` when it names none."""
    try:
        return split_task_key(task_id)[0]
    except ValueError:
        return ""
