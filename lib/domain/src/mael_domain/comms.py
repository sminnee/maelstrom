"""Comms, the tasks that feed each one, and the updates each one sends.

See ``docs/specs/tasks/CONTEXT.md``, "Comm" and "Comm update". A comm row is kept by
:mod:`mael_domain.comm_store`. The link is the task's ``comms`` field, so a
link change is a task write.
"""

import asyncio
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from mael_common.util import now_iso

from . import comm_codename
from . import task as task_model
from .comm_store import (
    DRAFT,
    SENT,
    Comm,
    CommItem,
    CommStore,
    CommUpdate,
    CommUpdateStore,
)
from .landing_store import STEPS, StepEvent, TaskStepStore
from .task import Task
from .task_metadata_generator import TaskMetadataGenerator
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


async def _codenames(store: CommStore, but: str = "") -> set[str]:
    """The codenames in use, except the one of comm ``but``."""
    return {c.codename for c in await store.list() if c.codename and c.id != but}


async def _make_codename(
    store: CommStore,
    title: str,
    content: str,
    generator: TaskMetadataGenerator | None,
) -> str:
    """Ask the Task metadata generator for a codename, on a thread: the call blocks."""
    return await asyncio.to_thread(
        comm_codename.generate_codename,
        title,
        content,
        await _codenames(store),
        generator,
    )


async def new(
    store: CommStore,
    title: str,
    content: str = "",
    recipients: list[str] | None = None,
    now: str | None = None,
    category: str = "",
    project: str = "",
    generator: TaskMetadataGenerator | None = None,
) -> Comm:
    """Create a comm. Its id is the next ``c<n>``, and it gets a codename.

    The generator runs before the write. The codename is checked again
    inside it, so a comm made at the same time cannot take the same one: the
    second comm then keeps a blank codename. ``generator`` defaults to
    ``default_generator()``.

    Raises ``ValueError`` for a blank title.
    """
    title = _title(title)
    codename = await _make_codename(store, title, content, generator)
    async with store.transact():
        if codename and comm_codename.is_taken(codename, await _codenames(store)):
            codename = ""
        comm = Comm(
            id=await store.next_id(),
            title=title,
            content=content,
            recipients=list(recipients or []),
            created_at=now if now is not None else now_iso(),
            category=category.strip(),
            project=project,
            codename=codename,
        )
        await store.save(comm)
    return comm


async def fill_codenames(
    store: CommStore, generator: TaskMetadataGenerator | None = None
) -> list[Comm]:
    """Give a codename to each comm that has none. Returns the comms named.

    A comm the model cannot name stays blank, to try again at the next call.
    """
    named: list[Comm] = []
    for comm in await store.list():
        if comm.codename:
            continue
        codename = await _make_codename(store, comm.title, comm.content, generator)
        if not codename:
            continue
        async with store.transact():
            current = await store.read(comm.id)
            if current is None or current.codename:
                continue
            if comm_codename.is_taken(codename, await _codenames(store)):
                continue
            current.codename = codename
            await store.save(current)
        named.append(current)
    return named


async def edit(
    store: CommStore,
    id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    recipients: list[str] | None = None,
    category: str | None = None,
    project: str | None = None,
    codename: str | None = None,
) -> Comm:
    """Change the given fields. ``""`` clears ``category``, ``project`` or ``codename``.

    A codename is put in Title Case. Raises ``KeyError`` for an unknown id, and
    ``ValueError`` for a blank title, a malformed codename, or a codename
    another comm uses.
    """
    async with store.transact():
        comm = await _load(store, id)
        if codename is not None and codename.strip():
            name = comm_codename.normalise(codename)
            if not name:
                raise ValueError(
                    f"Not a codename: {codename!r}. Use one or two words, letters only."
                )
            if comm_codename.is_taken(name, await _codenames(store, but=id)):
                raise ValueError(f"Codename {name!r} is in use by another comm")
            comm.codename = name
        elif codename is not None:
            comm.codename = ""
        if title is not None:
            comm.title = _title(title)
        if content is not None:
            comm.content = content
        if recipients is not None:
            comm.recipients = list(recipients)
        if category is not None:
            comm.category = category.strip()
        if project is not None:
            comm.project = project
        await store.save(comm)
    return comm


def default_project(comms: list[Comm], category: str) -> str:
    """The project a new comm in ``category`` defaults to.

    It is the most common non-blank project among the comms in that category.
    A tie goes to the project of the newest comm. Blank when no comm in the
    category has a project, or when ``category`` is blank.
    """
    if not category:
        return ""
    used = [c for c in comms if c.category == category and c.project]
    counts = Counter(c.project for c in used)
    newest: dict[str, str] = {}
    for c in used:
        newest[c.project] = max(newest.get(c.project, ""), c.created_at)
    return max(counts, key=lambda p: (counts[p], newest[p]), default="")


def categories(comms: list[Comm]) -> list[str]:
    """The distinct non-blank categories, sorted."""
    return sorted({c.category for c in comms if c.category})


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


@dataclass(frozen=True)
class LinkedTask:
    """What the comm update functions read of a task: which it is, its title, its comms."""

    project: str
    task_id: str
    title: str
    comms: tuple[str, ...]


def queue(
    comm: Comm,
    tasks: Iterable[LinkedTask],
    steps: Iterable[StepEvent],
    updates: Iterable[CommUpdate],
) -> list[StepEvent]:
    """The steps ``comm`` has not yet reported, in ``steps`` order.

    See ``docs/specs/tasks/CONTEXT.md``, "Comm update", and ``docs/dev/data-architecture.md`` for
    why the queue is set by membership and not by record time.
    """
    if comm.closed_at:
        return []
    feeding = {(t.project, t.task_id) for t in tasks if comm.id in t.comms}
    held = {
        (i.project, i.task_id, i.step)
        for u in updates
        if u.comm_id == comm.id
        for i in u.items
    }
    return [
        e
        for e in steps
        if (e.project, e.task_id) in feeding
        and (e.project, e.task_id, e.step) not in held
    ]


def update_content(items: list[CommItem], tasks: Iterable[LinkedTask]) -> str:
    """The text of a new update: one bullet per task, at its furthest step.

    A task records ``done`` and ``merged`` in one sync as a rule, and two
    bullets for one task are noise.
    """
    titles = {(t.project, t.task_id): t.title for t in tasks}
    furthest: dict[tuple[str, str], str] = {}
    for item in items:
        key = (item.project, item.task_id)
        if key not in furthest or STEPS.index(item.step) > STEPS.index(furthest[key]):
            furthest[key] = item.step
    return "\n".join(
        f"- {titles.get(key, key[1])} — {step}" for key, step in furthest.items()
    )


async def draft_updates(
    comm_store: CommStore,
    update_store: CommUpdateStore,
    step_store: TaskStepStore,
    tasks: list[LinkedTask],
    now: str | None = None,
) -> list[CommUpdate]:
    """**Draft updates**: a new update for each open comm with a queue.

    An unsent draft stays as it is; the new update is added beside it.
    Returns the updates written, in comm order.
    """
    now = now if now is not None else now_iso()
    steps = await step_store.list()
    made: list[CommUpdate] = []
    for comm in await comm_store.list():
        updates = await update_store.for_comm(comm.id)
        queued = queue(comm, tasks, steps, updates)
        if not queued:
            continue
        items = [CommItem(e.project, e.task_id, e.step) for e in queued]
        update = CommUpdate(
            id=f"{comm.id}.{_next_update(updates)}",
            comm_id=comm.id,
            created_at=now,
            items=items,
            content=update_content(items, tasks),
        )
        await update_store.save(update)
        made.append(update)
    return made


def _next_update(updates: list[CommUpdate]) -> int:
    return max((int(u.id.rsplit(".", 1)[1]) for u in updates), default=0) + 1


async def _load_draft(store: CommUpdateStore, id: str) -> CommUpdate:
    update = await store.read(id)
    if update is None:
        raise KeyError(f"Comm update not found: {id}")
    if update.state != DRAFT:
        raise ValueError(f"Comm update {id} is already sent")
    return update


async def edit_update(store: CommUpdateStore, id: str, content: str) -> CommUpdate:
    """Replace a draft's content.

    Raises ``KeyError`` for an unknown id and ``ValueError`` for a sent update.
    """
    update = await _load_draft(store, id)
    update.content = content
    await store.save(update)
    return update


async def send_update(
    store: CommUpdateStore, id: str, now: str | None = None
) -> CommUpdate:
    """Mark a draft sent. See ``docs/specs/tasks/CONTEXT.md``, "Comm update".

    Raises ``KeyError`` for an unknown id and ``ValueError`` for a sent update.
    """
    update = await _load_draft(store, id)
    update.state = SENT
    update.sent_at = now if now is not None else now_iso()
    await store.save(update)
    return update
