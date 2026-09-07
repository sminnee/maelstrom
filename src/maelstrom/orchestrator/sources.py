"""Where the orchestrator server reads tasks and worktrees from.

Storage layer. Each source is a Protocol with a real implementation over the
notebook or ``list-all`` and an in-memory one for tests. Both return wire
entities built by :mod:`.world_build`, so the server holds one shape of the
world and diffs readings of it.

The two sources reach the loop differently. :class:`ListAllWorktreeSource`
awaits its git and ``gh`` calls, so it runs on the loop and never stalls the
socket. Every :class:`NotebookTaskSource` method blocks, so the server runs it
in its executor — a pool of one thread; see ``docs/dev/orchestrator-server.md``
for why one. A source may block or answer with an awaitable, and the server
takes either, so neither kind needs help from the caller.
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .. import task as model
from .. import task_actions
from ..agent_model import build_start_payload
from ..branch_name import TaskNames, infer_task_names
from ..github_model import PrStatus, RateLimited, pr_from_row
from ..list_all import build_list_all_data
from ..session_discovery import LiveSessionSet
from ..task_index import TaskIndex
from ..task_launch import LaunchBlocked, check_not_live, check_synced, plan_launch
from ..task_store import TaskStore
from ..worktree import WorktreeSetup
from ..worktree_model import has_claude_transcript
from .protocol import Project, Task, Worktree
from .validate import EDITABLE
from .world_build import (
    project_entity,
    split_task_key,
    task_entity,
    task_key,
    worktree_entity,
)

#: Opens a worktree on a branch: ``(project, branch, base) -> WorktreeSetup``.
#: ``base`` is what seeds the branch's stored base the first time; ``""`` leaves
#: it to the launcher. Named without a task, so work that has none — a free
#: agent — opens a worktree through the same injected collaborator.
OpenWorktree = Callable[[str, str, str], WorktreeSetup]

#: Closes one worktree: ``(project, nato, path) -> None``. The server passes
#: what the world already holds, so the closer resolves nothing itself. Raises
#: :class:`CloseBlocked` with the reason when it will not close — the message
#: the user reads on the button. Awaited: the close stops the worktree's agents
#: over the agent host's socket.
CloseWorktree = Callable[[str, str, str], Awaitable[None]]


class CloseBlocked(Exception):
    """The worktree must not close now. The message says why, for the user."""


@dataclass(frozen=True)
class LaunchRequest:
    """A task moved to in-progress, and the ``start`` the agent host needs.

    Returned by :meth:`TaskSource.launch` once the worktree is open and the
    task is in-progress; :meth:`TaskSource.rollback` undoes the move when the
    host refuses the start.
    """

    project: str
    task_id: str
    #: The status the task had before the launch moved it, for the rollback.
    previous_status: str
    payload: dict[str, Any]


class TaskSource(Protocol):
    """The notebook, as tasks across every project."""

    def version(self) -> str | None:
        """A stamp that changes when any task changes. ``None`` when unknown."""
        ...

    def read(self) -> list[Task]:
        """Every task the server shows, with ``actionable`` decided by the notebook."""
        ...

    def launch(self, task_id: str, model_name: str | None) -> LaunchRequest:
        """Open the task's worktree, move it in-progress, and say what to start.

        Raises:
            KeyError: If no task has ``task_id``.
            LaunchBlocked: If a live session holds the task or its rebase failed.
        """
        ...

    def rollback(self, request: LaunchRequest) -> None:
        """Move a task the host refused to start back to where it was."""
        ...

    def set_status(self, task_id: str, status: str) -> None:
        """Move a task to ``status``, running its status actions.

        Raises:
            KeyError: If no task has ``task_id``.
        """
        ...

    def update(self, task_id: str, fields: dict[str, Any]) -> None:
        """Write the named fields of a task.

        Raises:
            KeyError: If no task has ``task_id``.
            ValueError: If a field holds a value the notebook refuses.
        """
        ...

    def infer(self, draft: str) -> TaskNames:
        """Read a title, a branch and a command off a draft's prose.

        Blocking: it shells out to ``claude -p``.
        """
        ...

    def create(
        self, project: str, fields: dict[str, Any], extra: dict[str, Any] | None = None
    ) -> str:
        """Write a new ``todo`` task and return its wire id.

        ``fields`` is filtered to what a client may edit; ``extra`` is the
        server's own and is written as given.

        Raises:
            ValueError: If a field holds a value the notebook refuses.
        """
        ...

    def worktree_for(self, project: str, branch: str) -> WorktreeSetup:
        """Open the worktree a branch runs in, provisioning one when it has none.

        The path a free agent starts in. A task's launch opens its own
        worktree on the way, so this is only reached for work with no task.

        Raises:
            LaunchBlocked: If this source cannot open worktrees.
        """
        ...

    def promote(self, project: str, paths: list[Path], parent: str) -> list[str]:
        """Create one task per draft file, chained in the order given.

        The whole set is one notebook write: a draft that will not parse
        leaves nothing created and no file consumed. Returns the wire ids, in
        the order the drafts were listed.

        Raises:
            ValueError: If a draft is missing or does not parse. The message
                names the file, so the user knows which one to fix.
        """
        ...


class WorktreeSource(Protocol):
    """``list-all``, as projects and their worktrees, and the close over them.

    ``read`` may answer directly or return an awaitable. The production source
    shells out to git and ``gh``, so it is a coroutine and never blocks the
    server's loop; a test double answers from a list it holds. The server takes
    either.

    ``active_branches`` names the branches worth a pull request lookup; see
    :func:`maelstrom.orchestrator.desk.active_branches`. ``None`` asks about
    all of them.
    """

    def read(
        self,
        active_branches: set[str] | None = None,
    ) -> (
        tuple[list[Project], list[Worktree]]
        | Awaitable[tuple[list[Project], list[Worktree]]]
    ): ...

    #: Closes a worktree, or ``None`` on a source that cannot.
    close: CloseWorktree | None

    #: Whether the last read had its pull request lookup refused for quota. The
    #: rows still stand — a refused lookup costs the pull request column, not
    #: the read — so the caller stands the next read off rather than dropping
    #: this one.
    rate_limited: bool


class NotebookTaskSource:
    """Tasks read from a :class:`~maelstrom.task_store.TaskStore` through the model.

    Works over an ``InMemoryStore`` in tests and a ``GitFileStore`` in
    production; only the injected collaborators differ. ``projects`` names the
    projects to read. ``version`` defaults to the store's head, which an
    in-memory store never moves, so a test supplies its own counter.
    """

    def __init__(
        self,
        store: TaskStore,
        projects: Callable[[], list[str]],
        *,
        index: TaskIndex | None = None,
        version: Callable[[], str | None] | None = None,
        open_worktree: OpenWorktree | None = None,
        live_sessions: Callable[[], LiveSessionSet] = LiveSessionSet,
        has_transcript: Callable[[Path, str], bool] = has_claude_transcript,
    ) -> None:
        self.store = store
        self.projects = projects
        self.index = index
        self._version = version
        self.open_worktree = open_worktree
        self.live_sessions = live_sessions
        self.has_transcript = has_transcript

    def version(self) -> str | None:
        return self._version() if self._version is not None else self.store.head()

    def read(self) -> list[Task]:
        head = self.store.head()
        entities: list[Task] = []
        for project in self.projects():
            # Full files, not index rows: the UI shows the task's content.
            for task in model.list_tasks(self.store, project=project, no_index=True):
                actionable = model.is_actionable(
                    task, self.store, index=self.index, head=head
                )
                entities.append(task_entity(task, actionable=actionable))
        return entities

    def launch(self, task_id: str, model_name: str | None) -> LaunchRequest:
        """``task_id`` is the wire id; the notebook is asked for the bare one."""
        if self.open_worktree is None:
            raise LaunchBlocked("This server cannot open worktrees")
        project, notebook_id = split_task_key(task_id)
        task = model.load(self.store, project, notebook_id)
        plan = plan_launch(task.project, task)
        check_not_live(task.id, plan.session_id, self.live_sessions())
        setup = self.open_worktree(task.project, plan.branch, task.base or "")
        check_synced(task.id, plan.branch, setup)
        self._move(task.project, task.id, model.STATUS_IN_PROGRESS)
        payload = build_start_payload(
            setup.path,
            prompt=plan.prompt,
            permission_mode=plan.permission_mode,
            model=model_name or plan.model,
            session_id=plan.session_id,
            env=plan.env,
            # A task that has run before already owns its session id.
            resume=self.has_transcript(setup.path, plan.session_id),
        )
        return LaunchRequest(task.project, task.id, task.status, payload)

    def rollback(self, request: LaunchRequest) -> None:
        self._move(request.project, request.task_id, request.previous_status)

    def set_status(self, task_id: str, status: str) -> None:
        project, notebook_id = split_task_key(task_id)
        self._move(project, notebook_id, status)

    def update(self, task_id: str, fields: dict[str, Any]) -> None:
        """Write a task's fields.

        Only the keys in :data:`~maelstrom.orchestrator.validate.EDITABLE` are
        written, so a client cannot reach a field the wire does not offer.
        """
        project, notebook_id = split_task_key(task_id)
        wanted = {k: v for k, v in fields.items() if k in EDITABLE}
        with self._stamped() as index:
            model.update(self.store, project, notebook_id, index=index, **wanted)

    def infer(self, draft: str) -> TaskNames:
        return infer_task_names(draft)

    def worktree_for(self, project: str, branch: str) -> WorktreeSetup:
        if self.open_worktree is None:
            raise LaunchBlocked("This server cannot open worktrees")
        # No base to seed: work with no task has no base to carry.
        return self.open_worktree(project, branch, "")

    def create(
        self, project: str, fields: dict[str, Any], extra: dict[str, Any] | None = None
    ) -> str:
        """Write a new task and return its wire id.

        Only the keys in :data:`~maelstrom.orchestrator.validate.EDITABLE` are
        taken from ``fields``, as ``update`` does. ``branch`` is one of them, so
        an explicit branch skips ``model.create``'s own generation.

        ``extra`` is written unfiltered, and is the server's own to set — a
        Linear plan's ``parent`` and ``post_action``, which no client may
        choose. It never carries request data, so the filter above stays the
        only door a client writes through.
        """
        wanted = {k: v for k, v in fields.items() if k in EDITABLE}
        wanted.update(extra or {})
        with self._stamped() as index:
            task = model.create(self.store, project=project, index=index, **wanted)
        return task_key(project, task.id)

    def promote(self, project: str, paths: list[Path], parent: str) -> list[str]:
        """Promote every draft in one transaction, wiring the chain as it goes.

        The first task follows the end of its parent's child-chain, exactly as
        ``mael task promote --follow-end '*'`` wires it; each later one follows
        the task before it. See ``docs/dev/orchestrator-server.md``,
        "Approving a task set".
        """
        created: list[str] = []
        with self._stamped():
            with self.store.transaction(message=f"task: promote {len(paths)} draft(s)"):
                for path in paths:
                    follows = (
                        [created[-1]]
                        if created
                        else model._resolve_follow_end(self.store, project, "*", parent)
                    )
                    try:
                        # The draft's own parent wins, as it does through the
                        # CLI: a planning session that named one meant it.
                        draft = model.read_draft(path)
                        # A cache outside the transaction: a row here would
                        # outlive a rollback.
                        task = model.promote_draft(
                            self.store,
                            project=project,
                            path=path,
                            overrides={"parent": draft.parent or parent},
                            draft=draft,
                            follows=follows,
                            index=None,
                            # Deferred until the transaction commits; see
                            # `consume_draft`.
                            consume=False,
                        )
                    except (OSError, ValueError) as exc:
                        raise ValueError(f"{path.name}: {exc}") from exc
                    created.append(task.id)
        # Committed: the drafts have moved into the notebook, so consume them.
        for path in paths:
            model.consume_draft(path)
        return [task_key(project, task_id) for task_id in created]

    def _move(self, project: str, task_id: str, status: str) -> None:
        with self._stamped() as index:
            task_actions.move_with_actions(
                self.store, project, task_id, status, index=index
            )

    @contextmanager
    def _stamped(self) -> Iterator[TaskIndex | None]:
        """Wrap a notebook write, keeping the index's head stamp honest.

        The store's HEAD moves under the write, so the freshness has to be read
        before it and the stamp written after. Every write here goes through
        this, as the CLI's own writes do.
        """
        index = self.index
        was_fresh = index is not None and task_actions.index_is_fresh(self.store, index)
        yield index
        if index is not None:
            task_actions.restamp(self.store, index, was_fresh=was_fresh)


class InMemoryWorktreeSource:
    """A fixed reading, editable by the test that owns it."""

    def __init__(
        self,
        projects: list[Project] | None = None,
        worktrees: list[Worktree] | None = None,
        close: CloseWorktree | None = None,
    ) -> None:
        self.projects = list(projects or [])
        self.worktrees = list(worktrees or [])
        self.close = close
        #: How many times the source has been read, so a test can check that a
        #: poll did *not* run. The real source's reads cost GitHub quota, and
        #: an unwanted one is invisible in the world it produces.
        self.reads = 0
        #: What the last read was asked about, so a test can check the poll
        #: narrowed its GitHub call rather than only what it returned.
        self.asked: set[str] | None = None
        #: Set by a test that wants the caller to see a refused read.
        self.rate_limited = False

    def read(
        self, active_branches: set[str] | None = None
    ) -> tuple[list[Project], list[Worktree]]:
        self.reads += 1
        self.asked = active_branches
        return list(self.projects), list(self.worktrees)


class ListAllWorktreeSource:
    """Projects and worktrees from :func:`maelstrom.list_all.build_list_all_data`.

    ``close`` is how the server closes one. A source built without it serves
    the world read-only, and a close is refused rather than half-done.
    """

    def __init__(self, projects_dir: Path, close: CloseWorktree | None = None) -> None:
        self.projects_dir = projects_dir
        #: The last pull request seen, by project and then by branch, answering
        #: the branches a read did not ask about; see
        #: :func:`maelstrom.list_all.resolve_pr`. Keyed by project because
        #: branch names are not unique across them.
        self._pr_cache: dict[str, dict[str, PrStatus]] = {}
        #: Whether the last read had its pull request lookup refused for quota.
        #: The rows still stand; the caller stands the next read off.
        self.rate_limited = False
        self.close = close

    async def read(
        self, active_branches: set[str] | None = None
    ) -> tuple[list[Project], list[Worktree]]:
        self.rate_limited = False
        try:
            data = await build_list_all_data(
                self.projects_dir,
                active_branches=active_branches,
                pr_cache=self._pr_cache,
            )
        except RateLimited as refused:
            # Only the pull request read was refused; the rows are built and
            # carry each branch's last known pull request. Keep them, and let
            # the caller read ``rate_limited`` to decide the next tick.
            self.rate_limited = True
            data = refused.args[1]
        self._remember_prs(data)
        projects = [project_entity(p) for p in data["projects"]]
        worktrees = [
            worktree_entity(p["name"], row)
            for p in data["projects"]
            for row in p["worktrees"]
        ]
        return projects, worktrees

    def _remember_prs(self, data: dict) -> None:
        """Keep what this read learned, to answer the branches the next one
        does not ask about.

        A row with no pull request is remembered as such: forgetting it instead
        would let a stale entry outlive a PR that really has gone.
        """
        for project in data["projects"]:
            by_branch = self._pr_cache.setdefault(project["name"], {})
            for row in project["worktrees"]:
                branch = row.get("branch")
                if not branch or row.get("is_closed"):
                    # A closed row carries no pull request whether or not the
                    # branch has one, so it says nothing worth remembering.
                    continue
                pr = pr_from_row(row)
                if pr is None:
                    by_branch.pop(branch, None)
                else:
                    by_branch[branch] = pr
