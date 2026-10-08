"""Comms, and the tasks that feed each one.

See ``CONTEXT.md``, "Comm". A comm row is kept by
:mod:`mael_domain.comm_store`. The link is the task's ``comms`` field, so a
link change is a task write.
"""

from mael_common.util import now_iso

from . import task as task_model
from .comm_store import Comm, CommStore
from .task import Task
from .task_table import TaskTable


async def _load(store: CommStore, id: str) -> Comm:
    comm = await store.read(id)
    if comm is None:
        raise KeyError(f"Comm not found: {id}")
    return comm


def _title(title: str) -> str:
    if not title.strip():
        raise ValueError("A comm needs a title")
    return title.strip()


async def new(
    store: CommStore,
    title: str,
    content: str = "",
    recipients: list[str] | None = None,
    now: str | None = None,
) -> Comm:
    """Create a comm. Its id is the next ``c<n>``.

    Raises ``ValueError`` for a blank title.
    """
    title = _title(title)
    async with store.transact():
        comm = Comm(
            id=await store.next_id(),
            title=title,
            content=content,
            recipients=list(recipients or []),
            created_at=now if now is not None else now_iso(),
        )
        await store.save(comm)
    return comm


async def edit(
    store: CommStore,
    id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    recipients: list[str] | None = None,
) -> Comm:
    """Change the given fields.

    Raises ``KeyError`` for an unknown id and ``ValueError`` for a blank title.
    """
    comm = await _load(store, id)
    if title is not None:
        comm.title = _title(title)
    if content is not None:
        comm.content = content
    if recipients is not None:
        comm.recipients = list(recipients)
    await store.save(comm)
    return comm


async def close(store: CommStore, id: str, now: str | None = None) -> Comm:
    """Close a comm. Closing a closed comm keeps the first close time."""
    comm = await _load(store, id)
    if comm.closed_at:
        return comm
    comm.closed_at = now if now is not None else now_iso()
    await store.save(comm)
    return comm


async def reopen(store: CommStore, id: str) -> Comm:
    """Open a closed comm again."""
    comm = await _load(store, id)
    comm.closed_at = ""
    await store.save(comm)
    return comm


async def _relink(
    table: TaskTable, comm_id: str, project: str, task_ids: list[str], add: bool
) -> list[Task]:
    changed: list[Task] = []
    async with table.transact():
        for id in task_ids:
            task = await task_model.load(table, project, id)
            if add:
                comms = task.comms if comm_id in task.comms else [*task.comms, comm_id]
            else:
                comms = [c for c in task.comms if c != comm_id]
            if comms != task.comms:
                task = await task_model.update(table, project, id, comms=comms)
            changed.append(task)
    return changed


async def link(
    table: TaskTable, comm_id: str, project: str, task_ids: list[str]
) -> list[Task]:
    """Add ``comm_id`` to each task's comms. All or nothing.

    Raises ``KeyError`` for an unknown task. The comm id is not checked: the
    caller does that.
    """
    return await _relink(table, comm_id, project, task_ids, add=True)


async def unlink(
    table: TaskTable, comm_id: str, project: str, task_ids: list[str]
) -> list[Task]:
    """Remove ``comm_id`` from each task's comms. All or nothing."""
    return await _relink(table, comm_id, project, task_ids, add=False)
