"""This build's schema: every ladder, every table, and the open that runs them.

The only module that changes when a subsystem joins the state database. It
gains an import, a ``LADDERS`` entry and a ``TABLES`` entry; nothing below it
moves.

Run as ``python -m mael_domain.state_db.migrate`` it brings the database up to
this build. The project's install script runs it.
"""

import asyncio
import sys
from pathlib import Path

from ..notebook_root import (
    NOTEBOOK_ROOT_ENV,
    NOTEBOOK_ROOT_UNSET_MESSAGE,
    NotebookRootUnset,
    notebook_root,
)
from ..worktree_model import MAIN_WORKTREE_FOLDER, parse_env_text
from .db import StateDb
from .migrations.agents import AGENTS
from .migrations.desk import DESK
from .migrations.landings import LANDINGS
from .migrations.spine import SPINE
from .migrations.task_attachments import TASK_ATTACHMENTS
from .migrations.task_export import TASK_EXPORT
from .migrations.tasks import TASKS
from .types import Rung, StateDbError, TableSpec

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


async def migrate_state_db(path: Path) -> None:
    """Bring the state database at ``path`` up to this build's schema.

    The only thing that writes a schema.

    Raises:
        StateDbError: If the database is from a newer build.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        await db.migrate()
    finally:
        db.close()


#: What a worktree whose ``.env`` names no root is told.
NO_WORKTREE_ROOT_MESSAGE = (
    f"This worktree's `.env` does not name {NOTEBOOK_ROOT_ENV}, so the state "
    "database was not migrated. Run `mael env reset` to add it."
)


def _pick_root() -> Path | None:
    """The notebook root to migrate, or ``None`` to skip.

    The current directory's ``.env`` wins. The install script inherits its
    caller's environment, and a worktree opened with the bare ``mael`` shim
    inherits ``~/.maelstrom`` — the real notebook, which branch code must not
    migrate. So a worktree with ``_main`` beside it and no root in its ``.env``
    is skipped rather than given the inherited root.

    Raises:
        NotebookRootUnset: If nothing names a root.
    """
    cwd = Path.cwd()
    env_file = cwd / ".env"
    if env_file.is_file():
        root = parse_env_text(env_file.read_text()).get(NOTEBOOK_ROOT_ENV)
        if root:
            return Path(root).expanduser()
    if (
        cwd.name != MAIN_WORKTREE_FOLDER
        and (cwd.parent / MAIN_WORKTREE_FOLDER).is_dir()
    ):
        return None
    return notebook_root()


def main() -> int:
    """Migrate the notebook root's state database, and return the exit code.

    A missing root skips with exit 0, so a fresh install still completes. A
    database from a newer build fails, and ``self-update`` reports it.
    """
    try:
        root = _pick_root()
    except NotebookRootUnset:
        print(NOTEBOOK_ROOT_UNSET_MESSAGE)
        return 0
    if root is None:
        print(NO_WORKTREE_ROOT_MESSAGE)
        return 0
    path = root / "state.db"
    try:
        asyncio.run(migrate_state_db(path))
    except StateDbError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"The state database at {path} is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
