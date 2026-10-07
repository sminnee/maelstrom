"""This build's schema: every ladder, every table, and the open that runs them.

The only module that changes when a subsystem joins the state database. It
gains an import, a ``LADDERS`` entry and a ``TABLES`` entry; nothing below it
moves.
"""

from pathlib import Path

from .db import StateDb
from .migrations.agents import AGENTS
from .migrations.desk import DESK
from .migrations.landings import LANDINGS
from .migrations.spine import SPINE
from .migrations.task_attachments import TASK_ATTACHMENTS
from .migrations.task_export import TASK_EXPORT
from .migrations.tasks import TASKS
from .types import Rung, TableSpec

#: Every subsystem's ladder, by name. A subsystem's schema moves without
#: dragging the others.
LADDERS: dict[str, tuple[Rung, ...]] = {
    "desk": DESK,
    "tasks": TASKS,
    "task_export": TASK_EXPORT,
    "agents": AGENTS,
    "task_attachments": TASK_ATTACHMENTS,
    "landings": LANDINGS,
}

#: Every table a subsystem declares. The spine's own tables are not here: they
#: are the machinery, not rows a caller writes.
TABLES: dict[str, TableSpec] = {
    "desk": TableSpec("desk"),
    "tasks": TableSpec("tasks"),
    # The export queue is the server's own bookkeeping: nothing draws it, and
    # nothing reads the tree it feeds on any code path. So a write to it is not
    # news for a client, even though it shares the task write's transaction.
    "task_export": TableSpec("task_export", notifies=False),
    "agents": TableSpec("agents", notifies=False),
    # The ledger the cost report reads. Nothing in the UI draws it, so a write
    # is not news for a client — as for `agents` itself.
    "agent_milestones": TableSpec("agent_milestones", notifies=False),
    # The server seeds the world from these rows at start and upserts the
    # document itself when it attaches one, so a write is not news for a client.
    "task_attachments": TableSpec("task_attachments", notifies=False),
    # GitHub's data about a pull request, read on the worktree poll. No client
    # draws it, so a write is not news.
    "pull_requests": TableSpec("pull_requests", cached=True, notifies=False),
    # The steps each task has reached. Canonical: see docs/dev/data-architecture.md.
    "task_steps": TableSpec("task_steps", notifies=False),
}


def open_state_db(path: Path | str | None = None) -> StateDb:
    """A :class:`~mael_domain.state_db.db.StateDb` carrying this build's ladders.

    What every caller wants. A :class:`~mael_domain.state_db.db.StateDb` built
    directly knows no schema, which is what keeps the engine below the ladders
    it runs.

    :data:`LADDERS` is read here in the body rather than as a default argument,
    so a test that patches the dict is honoured on the next call.
    """
    return StateDb(path, ladders=LADDERS, tables=TABLES, spine=SPINE)
