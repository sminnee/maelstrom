"""Where the orchestrator server reads tasks and worktrees from.

Storage layer. Each source is a Protocol with a real implementation over the
notebook or ``list-all`` and an in-memory one for tests. Both return wire
entities built by :mod:`.world_build`, so the server holds one shape of the
world and diffs readings of it.

Both sources run on the loop. :class:`ListAllWorktreeSource` awaits its git
and ``gh`` calls, and :class:`NotebookTaskSource` awaits the task table. A
source may block or answer with an awaitable, and the server takes either; a
blocking one goes to the executor — see ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from mael_agent.agent_wire import build_start_payload
from mael_agent.harness_model import resolve_execute_model
from mael_common.claude_paths import has_claude_transcript
from mael_domain import task as model
from mael_domain import task_actions
from mael_domain.agent_store import AgentStore
from mael_domain.github import get_open_prs
from mael_domain.github_model import (
    AUTO_MERGE_WAITING_STATES,
    PrStatus,
    RateLimited,
)
from mael_domain.landing import Landings
from mael_domain.list_all import (
    ProjectPrs,
    PrSnapshot,
    build_list_all_data,
    project_branches,
)
from mael_domain.protocol import (
    Project,
    StatusMoved,
    Task,
    TaskFollower,
    Worktree,
)
from mael_domain.session_discovery import LiveSessionSet
from mael_domain.shared_dir import agent_prompt_file
from mael_domain.task_launch import (
    LaunchBlocked,
    LaunchPlan,
    check_not_live,
    check_synced,
    plan_launch,
)
from mael_domain.task_metadata_generator import TaskNames, infer_task_names
from mael_domain.task_table import TaskTable
from mael_domain.worktree import (
    WorktreeSetup,
    current_branch_or_none,
    get_head_sha_async,
)
from mael_domain.worktree_steps import StepHook

from .validate import CREATABLE, EDITABLE, WIRE_RENAMES
from .world_build import (
    project_entity,
    split_task_key,
    task_entity,
    task_key,
    worktree_entity,
)

log = logging.getLogger(__name__)

#: Opens a worktree on a branch: ``(project, branch, base) -> WorktreeSetup``.
#: ``base`` is what seeds the branch's stored base the first time; ``""`` leaves
#: it to the launcher. Named without a task, so work that has none — a free
#: agent — opens a worktree through the same injected collaborator.
OpenWorktree = Callable[[str, str, str], WorktreeSetup]

#: Rebuilds an opened worktree's ``.env``, and says what it did:
#: ``(project, setup) -> lines``. Blocking: it reads and writes files.
RefreshEnv = Callable[[str, WorktreeSetup], list[str]]


class CloseWorktree(Protocol):
    """Closes one worktree: ``(project, nato, path, *, hook) -> None``.

    The server passes what the world already holds, so the closer resolves
    nothing itself. ``hook`` hears each step, for the operation the close runs
    as. Raises :class:`CloseBlocked` with the reason when it will not close —
    the words the operation ends with. Awaited: the close stops the worktree's
    agents over the agent host's socket.
    """

    def __call__(
        self, project: str, nato: str, path: str, *, hook: StepHook
    ) -> Awaitable[None] | None: ...


class SyncWorktree(Protocol):
    """Rebases one worktree: ``(project, nato, path, mode, *, hook) -> None``.

    ``mode`` is ``plain``, ``autorepair`` or ``squash``, the three settings
    ``mael sync`` already has. One callable rather than three: it is one
    operation the user chooses a setting for, not three operations.
    """

    def __call__(
        self, project: str, nato: str, path: str, mode: str, *, hook: StepHook
    ) -> Awaitable[None] | None: ...


#: Merges one worktree's pull request: ``(project, branch, path) -> None``.
#: Raises :class:`CloseBlocked` with GitHub's refusal.
MergeWorktreePr = Callable[[str, str, str], Awaitable[None]]

#: Merges one pull request, if its head is still the commit named:
#: ``(path, number, head_oid) -> None``. Blocking: it waits on GitHub.
MergePr = Callable[[str, int, str], None]

#: Turns **Auto-merge** on or off for one worktree's pull request:
#: ``(project, branch, path, on) -> None``. Raises :class:`CloseBlocked` with
#: GitHub's refusal.
AutoMergeWorktreePr = Callable[[str, str, str, bool], Awaitable[None]]

#: Turns **Auto-merge** on or off for one pull request:
#: ``(path, number, head_oid, on) -> None``. On pins it to ``head_oid``.
#: Blocking: it waits on GitHub.
SetAutoMerge = Callable[[str, int, str, bool], None]

#: Removes one worktree outright: ``(project, nato, path, *, hook) -> None``.
#: The close parks a worktree for reuse; this deletes the checkout.
RemoveWorktree = CloseWorktree


class EnvWorktree(Protocol):
    """Starts or stops a worktree's environment.

    ``(project, nato, path, action, service, *, hook) -> None`` where
    ``action`` is ``start``, ``stop`` or ``restart``, and ``service`` names one
    optional service, or is ``None`` for the whole environment. The environment
    is keyed by project and worktree name, but a start reads the checkout's own
    ``.env`` and services, so the path comes too.
    """

    def __call__(
        self,
        project: str,
        nato: str,
        path: str,
        action: str,
        service: str | None,
        *,
        hook: StepHook,
    ) -> Awaitable[None] | None: ...


#: Makes sure a worktree has a terminal in cmux, and returns the ``cmux://``
#: link to its pane: ``(project, nato, path) -> url``. Raises
#: :class:`CloseBlocked` when cmux cannot. Blocking: it shells out to cmux.
EnsureTerminal = Callable[[str, str, str], str]

#: The terminal's ``cmux://`` link, by ``(project, nato)``, for the open
#: worktrees it is given: ``pairs -> {(project, nato): url}``. A worktree with
#: no terminal is absent. Blocking: cmux is asked over a subprocess.
TerminalUrls = Callable[[list[tuple[str, str]]], dict[tuple[str, str], str]]


def _no_terminal_urls(_pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    return {}


class CloseBlocked(Exception):
    """The operation must not run now. The message says why, for the user.

    Raised by every worktree operation, not only the close: what reaches the
    button is one message, and one refusal type is what keeps it that way.
    The name predates the others.
    """


def _rename_wire_fields(
    fields: dict[str, Any], allowed: tuple[str, ...]
) -> dict[str, Any]:
    """``fields`` filtered to ``allowed`` and renamed from wire to model keys.

    Membership is checked under the wire name a key would take were it
    renamed, since ``fields`` arrives wire-spelled and ``allowed`` does not.
    """
    wanted: dict[str, Any] = {}
    for key, value in fields.items():
        model_key = WIRE_RENAMES.get(key, key)
        if model_key in allowed:
            wanted[model_key] = value
    return wanted


@dataclass(frozen=True)
class PreparedLaunch:
    """A task moved to in-progress in its open worktree, before its agent starts.

    Returned by :meth:`TaskSource.prepare_launch`. :meth:`TaskSource.finish_launch`
    turns it into the ``start`` the agent host needs, and
    :meth:`TaskSource.rollback` undoes the move.
    """

    project: str
    task_id: str
    #: The status the task had before the launch moved it, for the rollback.
    previous_status: str
    setup: WorktreeSetup
    plan: LaunchPlan
    model_name: str | None


@dataclass(frozen=True)
class TaskReading:
    """What moved in the notebook since some revision, as the wire holds it.

    The wire-side counterpart of
    :class:`~mael_domain.task_table.TaskChanges`: entities rather than model
    tasks, and wire ids rather than row ids.

    ``removed`` is carried in its own right because absence cannot be read as
    deletion. A caller holding only the changed rows could not tell a deleted
    task from an untouched one, and diffing that against the whole world would
    drop every task the read left out.

    ``revision`` is what the caller stores and asks from next time.
    """

    tasks: list[Task]
    removed: list[str]
    revision: int


class TaskSource(Protocol):
    """The notebook, as tasks across every project."""

    async def version(self) -> str | None:
        """A stamp that changes when any task changes. ``None`` when unknown."""
        ...

    async def read(self) -> list[Task]:
        """Every task the server shows, with ``actionable`` decided by the notebook."""
        ...

    async def read_since(self, since: int) -> TaskReading:
        """What moved since ``since``: the tasks that changed, and the ids that went.

        The poll's read. :meth:`read` answers for the whole notebook and is
        what a forced refresh and a first start take; this answers for one
        tick's worth of change.
        """
        ...

    async def revision_now(self) -> int:
        """The revision a later :meth:`read_since` should be asked from."""
        ...

    async def markdown(self, task_id: str) -> str | None:
        """The task as the notebook renders it, by wire id; ``None`` when there is none.

        The wire task is a projection: it fills in a default branch and splits
        the log. ``mael task read`` shows the notebook's own text.
        """
        ...

    async def read_some(self, task_ids: list[str]) -> list[Task]:
        """The named tasks, by wire id. An id that names no task is left out.

        What the server re-reads when something outside the task table moved
        a task's entity, such as a landing step.
        """
        ...

    #: Whether :meth:`version` returns this source's own table revision. A
    #: source with an injected version answers ``False``, because its counter
    #: names no revision :meth:`read_since` could be asked from — so the server
    #: reads the whole notebook rather than a cursor the source cannot honour.
    version_is_revision: bool

    async def prepare_launch(
        self, task_id: str, model_name: str | None
    ) -> PreparedLaunch:
        """Open the task's worktree and move the task in-progress.

        Raises:
            KeyError: If no task has ``task_id``.
            LaunchBlocked: If a live session holds the task or its rebase failed.
        """
        ...

    async def write_env(self, project: str, setup: WorktreeSetup) -> list[str]:
        """Rebuild an opened worktree's ``.env`` when it needs it, and say what it did."""
        ...

    def finish_launch(self, prepared: PreparedLaunch) -> dict[str, Any]:
        """The ``start`` payload the agent host needs for a prepared launch."""
        ...

    async def rollback(self, prepared: PreparedLaunch) -> None:
        """Move a task whose agent did not start back to where it was."""
        ...

    async def set_status(self, task_id: str, status: str) -> StatusMoved:
        """Move a task to ``status``, run its status actions, and say what followed.

        Raises:
            KeyError: If no task has ``task_id``.
        """
        ...

    async def update(self, task_id: str, fields: dict[str, Any]) -> None:
        """Write the named fields of a task.

        Raises:
            KeyError: If no task has ``task_id``.
            ValueError: If a field holds a value the notebook refuses.
        """
        ...

    async def register_pr(self, task_id: str, number: int, url: str) -> None:
        """Record a task's Registered PR.

        Raises:
            KeyError: If no task has ``task_id``.
        """
        ...

    async def withdraw_pr(self, task_id: str) -> None:
        """Mark a task's Registered PR as withdrawn.

        Raises:
            KeyError: If no task has ``task_id``.
            ValueError: If the task has no Registered PR.
        """
        ...

    async def delete(self, task_id: str) -> None:
        """Remove a task, and strip it from every dependent's ``follows``.

        Raises:
            KeyError: If no task has ``task_id``.
        """
        ...

    def infer(self, draft: str) -> TaskNames:
        """Read a title, a branch and a command off a draft's prose.

        Blocking: it calls the task metadata generator, which makes HTTP calls.
        """
        ...

    async def create(
        self,
        project: str,
        fields: dict[str, Any],
        extra: dict[str, Any] | None = None,
        *,
        generate_branch: bool = True,
    ) -> str:
        """Write a new ``todo`` task and return its wire id.

        ``fields`` is filtered to what a client may edit; ``extra`` is the
        server's own and is written as given. ``generate_branch=False`` writes
        an empty branch as ``task/<id>``, for a caller that names it later.

        Raises:
            ValueError: If a field holds a value the notebook refuses.
        """
        ...

    async def worktree_for(self, project: str, branch: str) -> WorktreeSetup:
        """Open the worktree a branch runs in, provisioning one when it has none.

        The path a free agent starts in. A task's launch opens its own
        worktree on the way, so this is only reached for work with no task.

        Raises:
            LaunchBlocked: If this source cannot open worktrees.
        """
        ...

    async def promote(
        self, project: str, paths: list[Path], parent: str, after: str | None
    ) -> list[str]:
        """Create one task per draft file, chained in the order given.

        The set splices in after ``after``, the planning task's notebook id,
        or follows nothing when it is ``None``. The whole set is one notebook write: a draft that will not parse
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
    :func:`mael_orchestrator.desk.active_branches`. ``None`` asks about
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

    #: Closes a worktree past its refusals, or ``None`` on a source that
    #: cannot. Separate from ``close`` because forcing writes a wip commit: it
    #: is a different decision, taken behind a confirm. It writes the reopen
    #: task when the close went over unmerged work.
    force_close: CloseWorktree | None

    #: Trashes a worktree's branch and closes the worktree, or ``None`` on a
    #: source that cannot.
    trash: CloseWorktree | None

    #: Rebases a worktree, or ``None`` on a source that cannot.
    sync: SyncWorktree | None

    #: Merges a worktree's pull request, or ``None`` on a source that cannot.
    merge: MergeWorktreePr | None

    #: Turns **Auto-merge** on or off, or ``None`` on a source that cannot.
    auto_merge: AutoMergeWorktreePr | None

    #: Removes a worktree, or ``None`` on a source that cannot.
    remove: RemoveWorktree | None

    #: Starts or stops a worktree's environment, or ``None``.
    env: EnvWorktree | None

    #: Makes a worktree's terminal and returns its link, or ``None``.
    ensure_terminal: EnsureTerminal | None

    @property
    def rate_limited(self) -> bool:
        """Whether pull request reads stand off after GitHub refused one for
        quota. The rows still read; only the PR state goes stale."""
        ...

    def stand_off(self) -> None:
        """Stand pull request reads off, for a refusal another reader met."""
        ...

    @property
    def prs(self) -> "PrStateCache | None":
        """The PR cache the rows read from, or ``None`` on a source with none.
        The server listens on it for the PR watch."""
        ...

    def refresh_prs(self, project: str, branch: str) -> Awaitable[None] | None:
        """Read ``branch``'s pull request again, because a caller changed it."""
        ...


class NotebookTaskSource:
    """Tasks read from a :class:`~mael_domain.task_store.TaskStore` through the model.

    Works over an ``InMemoryStore`` in tests and a ``GitFileStore`` in
    production; only the injected collaborators differ. ``projects`` names the
    projects to read. ``version`` defaults to the store's head, which an
    in-memory store never moves, so a test supplies its own counter.
    """

    def __init__(
        self,
        table: TaskTable,
        projects: Callable[[], list[str]],
        *,
        version: Callable[[], str | None] | None = None,
        open_worktree: OpenWorktree | None = None,
        refresh_env: RefreshEnv | None = None,
        worktree_executor: Executor | None = None,
        live_sessions: Callable[[], LiveSessionSet] = LiveSessionSet,
        has_transcript: Callable[[Path, str], bool] = has_claude_transcript,
        agents: AgentStore | None = None,
        landings: Landings | None = None,
    ) -> None:
        self.table = table
        self.agents = agents
        #: Where each done task's landing is read. See CONTEXT.md, "Landing".
        self.landings = landings if landings is not None else Landings()
        #: Task session id -> when its first agent started, folded from the
        #: Agent records a revision at a time.
        self._first_starts: dict[str, str] = {}
        self._agents_revision = 0
        self.projects = projects
        self._version = version
        self.open_worktree = open_worktree
        self.refresh_env = refresh_env
        #: Where a worktree opens. ``None`` is the loop's default pool, which
        #: is still off the loop.
        self.worktree_executor = worktree_executor
        #: Per project, held while a worktree opens there. See :meth:`_open`.
        self._opening: dict[str, asyncio.Lock] = {}
        self.live_sessions = live_sessions
        self.has_transcript = has_transcript
        #: An injected version is some test's own counter, not the table's
        #: revision, so it names nothing :meth:`read_since` can be asked from.
        #: Such a source is read whole, which is what it was always doing.
        self.version_is_revision = version is None

    async def revision_now(self) -> int:
        """The table's revision, for a caller about to start reading from it."""
        return await self.table.revision()

    async def version(self) -> str | None:
        """The table's revision, as a stamp that moves when any task moves.

        One number for the whole database, so a task write and a desk write
        both advance it — which costs an occasional no-op re-read and saves
        keeping a second counter honest.
        """
        if self._version is not None:
            return self._version()
        return str(await self.table.revision())

    async def read(self) -> list[Task]:
        entities: list[Task] = []
        await self._fold_starts()
        for project in self.projects():
            # Whole rows: the row carries the prose, so this is one query per
            # project rather than a parse of every file.
            for task in await model.list_tasks(self.table, project=project):
                entities.append(await self._entity(task))
        return entities

    async def _fold_starts(self) -> set[str]:
        """Fold the Agent records written since the last read into the first
        starts, and return the task sessions whose first start moved.

        Ended records count: the start must outlive the agent, which leaves the
        world when it ends. A record with no session, or no readable aware
        start, says nothing.
        """
        if self.agents is None:
            return set()
        records, self._agents_revision = await self.agents.changed_since(
            self._agents_revision
        )
        moved: set[str] = set()
        for record in records:
            session = record.get("task_session_id") or ""
            stamp = record.get("started_at") or ""
            try:
                at = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            if not session or at.tzinfo is None:
                continue
            known = self._first_starts.get(session)
            if known is None or at < datetime.fromisoformat(known):
                self._first_starts[session] = stamp
                moved.add(session)
        return moved

    async def _entity(self, task: model.Task) -> Task:
        session = model.session_id_for(task.project, task.id)
        return task_entity(
            task,
            actionable=await model.is_actionable(task, self.table),
            started_at=self._first_starts.get(session, ""),
            landing=await self.landings.landing_of(task),
        )

    async def markdown(self, task_id: str) -> str | None:
        task = await self.table.load(*split_task_key(task_id))
        return task.to_markdown() if task is not None else None

    async def read_some(self, task_ids: list[str]) -> list[Task]:
        entities: list[Task] = []
        for task_id in task_ids:
            project, notebook_id = split_task_key(task_id)
            task = await self.table.load(project, notebook_id)
            if task is not None:
                entities.append(await self._entity(task))
        return entities

    async def read_since(self, since: int) -> TaskReading:
        """The tasks that moved after ``since``, and the ids that went.

        What the poll reads instead of :meth:`read`. One query answers for the
        whole notebook, so a tick costs the rows that changed rather than every
        task in every project.

        Removals are named, never inferred: a reading that held only the changed
        rows could not tell a deleted task from an untouched one, and a caller
        diffing it against the whole world would drop every task left out.
        """
        wanted = set(self.projects())
        changed = await self.table.changed_since(since)
        # An Agent record does not move its task's row, so a task whose first
        # start just appeared is read here by its session.
        moved = await self._fold_starts()
        tasks = list(changed.tasks)
        moved -= {model.session_id_for(t.project, t.id) for t in tasks}
        for session in sorted(moved):
            if (task := await self.table.find_by_session_id(session)) is not None:
                tasks.append(task)
        entities: list[Task] = []
        for task in tasks:
            if task.project not in wanted:
                continue
            entities.append(await self._entity(task))
        removed = [key for key in changed.removed if split_task_key(key)[0] in wanted]
        return TaskReading(tasks=entities, removed=removed, revision=changed.revision)

    async def prepare_launch(
        self, task_id: str, model_name: str | None
    ) -> PreparedLaunch:
        """``task_id`` is the wire id; the notebook is asked for the bare one."""
        if self.open_worktree is None:
            raise LaunchBlocked("This server cannot open worktrees")
        project, notebook_id = split_task_key(task_id)
        task = await model.load(self.table, project, notebook_id)
        plan = plan_launch(task.project, task)
        if plan.execute_model:
            # The board's launch reaches the daemon without passing `validate`,
            # which guards the free-agent path only. Refused here for the same
            # reason the CLI refuses it: after approval the context is gone.
            try:
                resolve_execute_model(plan.execute_model)
            except ValueError as exc:
                raise LaunchBlocked(str(exc)) from exc
        live = await self.live_sessions().sweep()
        check_not_live(task.id, plan.session_id, live)
        setup = await self._open(task.project, plan.branch, task.base or "")
        check_synced(task.id, plan.branch, setup)
        await self._move(task.project, task.id, model.STATUS_IN_PROGRESS)
        return PreparedLaunch(
            task.project, task.id, task.status, setup, plan, model_name
        )

    async def write_env(self, project: str, setup: WorktreeSetup) -> list[str]:
        """Rebuild the ``.env`` of a worktree that was reused or recycled.

        A created worktree's ``.env`` is fresh, and ``_main``'s is the user's own.
        """
        if self.refresh_env is None or not setup.rebuilds_env:
            return [".env unchanged."]
        return await asyncio.get_running_loop().run_in_executor(
            self.worktree_executor, self.refresh_env, project, setup
        )

    def finish_launch(self, prepared: PreparedLaunch) -> dict[str, Any]:
        plan, setup = prepared.plan, prepared.setup
        return build_start_payload(
            setup.path,
            prompt=plan.prompt,
            permission_mode=plan.permission_mode,
            model=prepared.model_name or plan.model,
            execute_model=plan.execute_model,
            session_id=plan.session_id,
            env=plan.env,
            # A task that has run before already owns its session id.
            resume=self.has_transcript(setup.path, plan.session_id),
            system_prompt_file=agent_prompt_file(),
        )

    async def rollback(self, prepared: PreparedLaunch) -> None:
        await self._move(prepared.project, prepared.task_id, prepared.previous_status)

    async def set_status(self, task_id: str, status: str) -> StatusMoved:
        project, notebook_id = split_task_key(task_id)
        lines: list[str] = []

        def report(line: str) -> None:
            log.warning("%s", line)
            lines.append(line)

        await self._move(project, notebook_id, status, warn=report)
        moved: StatusMoved = {}
        if lines:
            moved["actionLines"] = lines
        if status == model.STATUS_DONE:
            follower = await self._follower(project, notebook_id)
            if follower is not None:
                moved["follower"] = follower
        return moved

    async def _follower(self, project: str, done_id: str) -> TaskFollower | None:
        """The follower ``mael task next --run`` would start, unless one already runs."""
        running = await model.running_follower(self.table, project, done_id)
        follower = running or await model.next_follower(self.table, project, done_id)
        if follower is None:
            return None
        return {
            "id": task_key(project, follower.id),
            "title": follower.title,
            "running": running is not None,
        }

    async def update(self, task_id: str, fields: dict[str, Any]) -> None:
        """Write a task's fields.

        Only the keys in :data:`~mael_orchestrator.validate.EDITABLE` are
        written, so a client cannot reach a field the wire does not offer.
        """
        project, notebook_id = split_task_key(task_id)
        wanted = _rename_wire_fields(fields, EDITABLE)
        # The notebook stores a bare id; ``world_build`` qualifies it on the way
        # out, so a wire id has to lose its project again here.
        if "follows" in wanted:
            wanted["follows"] = [split_task_key(f)[1] for f in wanted["follows"]]
        if "comms" in wanted:
            wanted["comms"] = list(dict.fromkeys(wanted["comms"]))
        await model.update(self.table, project, notebook_id, **wanted)

    async def register_pr(self, task_id: str, number: int, url: str) -> None:
        project, notebook_id = split_task_key(task_id)
        await model.register_pr(self.table, project, notebook_id, number, url)

    async def withdraw_pr(self, task_id: str) -> None:
        project, notebook_id = split_task_key(task_id)
        await model.withdraw_pr(self.table, project, notebook_id)

    async def delete(self, task_id: str) -> None:
        """Remove a task from the notebook.

        The dependent rewrite is the model's, not this layer's: ``delete``
        covers the removal and every dependent in one transaction.
        """
        project, notebook_id = split_task_key(task_id)
        await model.delete(self.table, project, notebook_id)

    def infer(self, draft: str) -> TaskNames:
        return infer_task_names(draft)

    async def worktree_for(self, project: str, branch: str) -> WorktreeSetup:
        # No base to seed: work with no task has no base to carry.
        return await self._open(project, branch, "")

    async def _open(self, project: str, branch: str, base: str) -> WorktreeSetup:
        """Open a worktree on the worktree pool, one open per project at a time.

        Off the loop, because the fetch and rebase take seconds. One at a time,
        because ``create_worktree`` picks a NATO name and adds the worktree with
        no scope held: two opens in one project could pick the same name.
        """
        if self.open_worktree is None:
            raise LaunchBlocked("This server cannot open worktrees")
        async with self._opening.setdefault(project, asyncio.Lock()):
            return await asyncio.get_running_loop().run_in_executor(
                self.worktree_executor, self.open_worktree, project, branch, base
            )

    async def create(
        self,
        project: str,
        fields: dict[str, Any],
        extra: dict[str, Any] | None = None,
        *,
        generate_branch: bool = True,
    ) -> str:
        """Write a new task and return its wire id.

        Only the keys in :data:`~mael_orchestrator.validate.CREATABLE` are
        taken from ``fields``, which is ``EDITABLE`` without ``follows``: a new
        task is wired after it exists, never by the create body. ``branch`` is
        one of them, so an explicit branch skips ``model.create``'s own
        generation.

        ``extra`` is written unfiltered, and is the server's own to set — a
        Linear plan's ``parent`` and ``post_action``, which no client may
        choose. It never carries request data, so the filter above stays the
        only door a client writes through.
        """
        wanted = _rename_wire_fields(fields, CREATABLE)
        wanted.update(extra or {})
        task = await model.create(
            self.table, project=project, generate_branch=generate_branch, **wanted
        )
        return task_key(project, task.id)

    async def promote(
        self, project: str, paths: list[Path], parent: str, after: str | None
    ) -> list[str]:
        """Promote every draft as one chain, spliced in after ``after``.

        The same step as ``mael task promote`` with no follow flag. See
        ``docs/dev/orchestrator-server.md``, "Approving a task set".
        """
        created = await model.promote_chain(
            self.table, project=project, paths=paths, parent=parent, after=after
        )
        return [task_key(project, task.id) for task in created]

    async def _move(
        self,
        project: str,
        task_id: str,
        status: str,
        *,
        warn: Callable[[str], None] = log.warning,
    ) -> None:
        await task_actions.move_with_actions(
            self.table, project, task_id, status, warn=warn
        )


#: How long the PR read stands off after GitHub refuses it for quota.
#:
#: Fixed rather than read from the reset time: ``gh`` does not hand that header
#: back. Ten minutes picks up a budget freed early without the retries
#: themselves keeping it spent.
RATE_LIMIT_COOLDOWN_SECS = 600.0

#: How long one project's PR read may take before its rows go out stale.
PR_READ_TIMEOUT_SECS = 30.0

#: Reads the pull requests of some branches: ``(project_path, branches) ->
#: {branch: PrStatus}``, or ``None`` when the read failed. Raises
#: :class:`~mael_domain.github_model.RateLimited` when the budget is spent.
ReadPrs = Callable[[Path, set[str]], Awaitable[dict[str, PrStatus] | None]]
#: A PR change: project, branch, the entry before the read, the entry after.
PrChange = Callable[[str, str, PrStatus | None, PrStatus | None], None]


@dataclass
class _ProjectPrState:
    by_branch: dict[str, PrStatus]
    stale_branches: set[str] = field(default_factory=set)


@dataclass
class _Flight:
    """One project's PR read in progress, and what it asked about."""

    task: asyncio.Task[None]
    branches: set[str]
    #: The value of :attr:`PrStateCache._started` when this read began.
    started: int


class PrStateCache:
    """The one reader of pull request state, and what it last learned.

    It owns the ``gh`` call, so the worktree rows never wait on it for longer
    than ``timeout``. It also owns the rate-limit stand-off: during one, no
    caller reaches GitHub, whichever path it came by.

    A failed, timed-out or skipped read keeps the last known state and marks
    the branches it asked about stale. See ``docs/dev/orchestrator-server.md``.
    """

    def __init__(
        self,
        read: ReadPrs = get_open_prs,
        *,
        timeout: float = PR_READ_TIMEOUT_SECS,
        cooldown: float = RATE_LIMIT_COOLDOWN_SECS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._read = read
        self._timeout = timeout
        self._cooldown = cooldown
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self._projects: dict[str, _ProjectPrState] = {}
        self._in_flight: dict[str, _Flight] = {}
        #: How many reads have begun, so an after-write caller can tell a read
        #: that began before it from one that began after.
        self._started = 0
        self._stand_off_until: float | None = None
        #: Told of each branch whose PR a read moved: ``(project, branch,
        #: before, after)``. One listener, set by the server once it exists.
        self.on_change: PrChange | None = None

    def standing_off(self) -> bool:
        """Whether GitHub refused a read for quota and the cooldown still holds."""
        if self._stand_off_until is None:
            return False
        if self._clock() >= self._stand_off_until:
            self._stand_off_until = None
            return False
        return True

    def stand_off(self) -> None:
        """Stop reading for the cooldown. For a refusal another reader met."""
        self._stand_off_until = self._clock() + self._cooldown
        log.warning(
            "GitHub rate limit reached; PR reads stand off for %ss", self._cooldown
        )

    def snapshot(self) -> PrSnapshot:
        return {
            name: ProjectPrs(
                by_branch=dict(state.by_branch),
                stale_branches=frozenset(state.stale_branches),
            )
            for name, state in self._projects.items()
        }

    def get(self, project: str, branch: str) -> PrStatus | None:
        state = self._projects.get(project)
        return state.by_branch.get(branch) if state else None

    async def refresh(
        self, project_path: Path, branches: set[str], *, after_write: bool = False
    ) -> None:
        """Read ``branches``' pull requests, unless a read in flight covers them.

        ``after_write`` is for a caller that just changed a PR on GitHub. A
        read that began before the change cannot hold its result, so the
        caller waits that read out and reads again.

        Never raises for a failed read: the branches go stale instead.
        """
        name = project_path.name
        state = self._projects.setdefault(name, _ProjectPrState({}))
        if not branches:
            return
        if self.standing_off():
            state.stale_branches |= branches
            return
        entered = self._started
        while (flight := self._in_flight.get(name)) is not None:
            if flight.task.done():
                # Cancelled before it ran, so its own cleanup never did.
                del self._in_flight[name]
                continue
            covered = branches <= flight.branches and (
                not after_write or flight.started > entered
            )
            # Shielded: a cancelled caller must not cancel a read others share.
            await asyncio.shield(flight.task)
            if covered:
                return
        self._started += 1
        task = asyncio.create_task(self._read_into(state, project_path, branches))
        self._in_flight[name] = _Flight(task, set(branches), self._started)
        await asyncio.shield(task)

    async def _read_into(
        self, state: _ProjectPrState, project_path: Path, branches: set[str]
    ) -> None:
        name = project_path.name
        try:
            answer = await self._read_or_none(project_path, branches)
        finally:
            # Removed before the task completes, not in a done callback. A
            # callback runs a loop turn later, and a waiter that finds the
            # finished flight in between would await it without yielding,
            # forever.
            flight = self._in_flight.get(name)
            if flight is not None and flight.task is asyncio.current_task():
                del self._in_flight[name]
        if answer is None:
            state.stale_branches |= branches
            return
        # Only the branches asked about are answered. One not asked keeps its
        # entry: its absence from ``answer`` says nothing about it.
        moved: list[tuple[str, PrStatus | None, PrStatus | None]] = []
        for branch in branches:
            before = state.by_branch.get(branch)
            after = answer.get(branch)
            if after is not None:
                state.by_branch[branch] = after
            else:
                state.by_branch.pop(branch, None)
            if before != after:
                moved.append((branch, before, after))
        state.stale_branches -= branches
        if self.on_change is not None:
            for branch, before, after in moved:
                try:
                    self.on_change(name, branch, before, after)
                except Exception:  # noqa: BLE001 — a listener must not fail the read
                    log.exception("PR change listener failed for %s", branch)

    async def _read_or_none(
        self, project_path: Path, branches: set[str]
    ) -> dict[str, PrStatus] | None:
        """The read's answer, or ``None`` when it failed, timed out or was refused."""
        try:
            answer = await asyncio.wait_for(
                self._read(project_path, branches), self._timeout
            )
        except RateLimited:
            self.stand_off()
            return None
        except Exception as exc:  # noqa: BLE001 — a failed read costs freshness only
            log.warning(
                "could not read the pull requests of %s: %r", project_path.name, exc
            )
            return None
        if answer is None:
            log.warning("could not read the pull requests of %s", project_path.name)
        return answer


class InMemoryWorktreeSource:
    """A fixed reading, editable by the test that owns it."""

    def __init__(
        self,
        projects: list[Project] | None = None,
        worktrees: list[Worktree] | None = None,
        close: CloseWorktree | None = None,
        force_close: CloseWorktree | None = None,
        trash: CloseWorktree | None = None,
        sync: SyncWorktree | None = None,
        merge: MergeWorktreePr | None = None,
        remove: RemoveWorktree | None = None,
        env: EnvWorktree | None = None,
        ensure_terminal: EnsureTerminal | None = None,
        prs: PrStateCache | None = None,
        auto_merge: AutoMergeWorktreePr | None = None,
    ) -> None:
        self.projects = list(projects or [])
        #: A real cache over a fake read, for a test of the PR watch. A read
        #: refreshes it for each project's open worktree branches.
        self.prs = prs
        self.worktrees = list(worktrees or [])
        self.close = close
        self.force_close = force_close
        self.trash = trash
        self.sync = sync
        self.merge = merge
        self.auto_merge = auto_merge
        self.remove = remove
        self.env = env
        self.ensure_terminal = ensure_terminal
        #: How many times the source has been read, so a test can check that a
        #: poll did *not* run. The real source's reads cost GitHub quota, and
        #: an unwanted one is invisible in the world it produces.
        self.reads = 0
        #: What the last read was asked about, so a test can check the poll
        #: narrowed its GitHub call rather than only what it returned.
        self.asked: set[str] | None = None
        #: Set by a test that wants the caller to see a refused read, and by
        #: :meth:`stand_off`.
        self.rate_limited = False
        #: Each ``(project, branch)`` a caller asked to read again after a write.
        self.prs_refreshed: list[tuple[str, str]] = []
        #: Set by a test that needs a read to still be in flight while it does
        #: something else. The real read shells out per worktree and takes
        #: seconds; this one returns at once, so a test that cares about two
        #: reads overlapping has no window without it. The test supplies the
        #: read that waits on it — see ``_use_slow_read``.
        self.blocked_on: asyncio.Event | None = None

    def read(
        self, active_branches: set[str] | None = None
    ) -> (
        tuple[list[Project], list[Worktree]]
        | Awaitable[tuple[list[Project], list[Worktree]]]
    ):
        self.reads += 1
        self.asked = active_branches
        rows = list(self.projects), list(self.worktrees)
        if self.prs is None:
            return rows
        return self._read_prs(self.prs, active_branches, rows)

    async def _read_prs(
        self,
        prs: PrStateCache,
        active_branches: set[str] | None,
        rows: tuple[list[Project], list[Worktree]],
    ) -> tuple[list[Project], list[Worktree]]:
        held: dict[str, set[str]] = {}
        for worktree in self.worktrees:
            if not worktree["isClosed"]:
                held.setdefault(worktree["project"], set()).add(worktree["branch"])
        for project, branches in held.items():
            if active_branches is not None:
                branches &= active_branches
            await prs.refresh(Path(project), branches)
        return rows

    def stand_off(self) -> None:
        self.rate_limited = True

    def refresh_prs(self, project: str, branch: str) -> Awaitable[None] | None:
        self.prs_refreshed.append((project, branch))
        if self.prs is None:
            return None
        return self.prs.refresh(Path(project), {branch}, after_write=True)


class ListAllWorktreeSource:
    """Projects and worktrees from :func:`mael_domain.list_all.build_list_all_data`.

    The operations are how the server mutates one. A source built without one
    serves the world read-only for it, and that operation is refused rather
    than half-done.
    """

    def __init__(
        self,
        projects_dir: Path,
        close: CloseWorktree | None = None,
        force_close: CloseWorktree | None = None,
        trash: CloseWorktree | None = None,
        sync: SyncWorktree | None = None,
        merge_pr: MergePr | None = None,
        auto_merge: SetAutoMerge | None = None,
        remove: RemoveWorktree | None = None,
        env: EnvWorktree | None = None,
        ensure_terminal: EnsureTerminal | None = None,
        terminal_urls: TerminalUrls = _no_terminal_urls,
        prs: PrStateCache | None = None,
    ) -> None:
        self.projects_dir = projects_dir
        self.terminal_urls = terminal_urls
        self.prs = prs or PrStateCache()
        self.close = close
        self.force_close = force_close
        self._trash = trash
        self.trash: CloseWorktree | None = (
            self._trash_and_refresh if trash is not None else None
        )
        self.sync = sync
        self._merge_pr = merge_pr
        self.merge: MergeWorktreePr | None = (
            self._merge if merge_pr is not None else None
        )
        self._set_auto_merge = auto_merge
        self.auto_merge: AutoMergeWorktreePr | None = (
            self._auto_merge if auto_merge is not None else None
        )
        self.remove = remove
        self.env = env
        self.ensure_terminal = ensure_terminal

    @property
    def rate_limited(self) -> bool:
        return self.prs.standing_off()

    def stand_off(self) -> None:
        self.prs.stand_off()

    async def refresh_prs(self, project: str, branch: str) -> None:
        await self.prs.refresh(self.projects_dir / project, {branch}, after_write=True)

    async def _trash_and_refresh(
        self, project: str, nato: str, path: str, *, hook: StepHook
    ) -> None:
        """Trash the worktree, then read its branch's pull request again.

        The trash closes the PR. The branch is read first, because the trash
        removes it.
        """
        assert self._trash is not None
        branch = await asyncio.to_thread(current_branch_or_none, Path(path))
        result = self._trash(project, nato, path, hook=hook)
        if result is not None:
            await result
        if branch is not None:
            await self.refresh_prs(project, branch)

    async def _merge(self, project: str, branch: str, path: str) -> None:
        """Merge ``branch``'s pull request at the head commit the last read saw.

        The number and the commit come from this source's own reading, not from
        the caller: the head commit is not on the wire, and the merge must
        name the commit whose state the user was shown.
        """
        assert self._merge_pr is not None
        pr = self.prs.get(project, branch)
        if pr is None:
            raise CloseBlocked(f"No pull request is known for {branch}")
        # A read can land between the server's validation and here. The merge
        # names what this source holds, so this source checks it.
        state = "draft" if pr.is_draft else pr.state
        if state != "ready":
            raise CloseBlocked(f"PR #{pr.number} is not ready to merge ({state})")
        if not pr.head_oid:
            raise CloseBlocked(f"The head commit of PR #{pr.number} is not known yet")
        # A local commit can land after the last read.
        head = await get_head_sha_async(Path(path))
        if head is None:
            raise CloseBlocked(
                f"The local head of PR #{pr.number}'s branch cannot be read"
            )
        if head != pr.head_oid:
            raise CloseBlocked(
                f"The local branch differs from PR #{pr.number}. Sync it first."
            )
        await asyncio.to_thread(self._merge_pr, path, pr.number, pr.head_oid)
        await self.refresh_prs(project, branch)

    async def _auto_merge(self, project: str, branch: str, path: str, on: bool) -> None:
        """Turn **Auto-merge** on or off at the head commit the last read saw.

        As :meth:`_merge`: the number and the commit come from this source's
        own reading, and on checks them again, because a read can land after
        validation. GitHub then merges that commit only, not a later push. Off
        checks nothing: it is the way out of any state.
        """
        assert self._set_auto_merge is not None
        pr = self.prs.get(project, branch)
        if pr is None:
            raise CloseBlocked(f"No pull request is known for {branch}")
        if on:
            await self._check_auto_merge_on(pr, path)
        await asyncio.to_thread(self._set_auto_merge, path, pr.number, pr.head_oid, on)
        await self.refresh_prs(project, branch)

    @staticmethod
    async def _check_auto_merge_on(pr: PrStatus, path: str) -> None:
        """Refuse to turn **Auto-merge** on where the held reading does not allow it."""
        state = "draft" if pr.is_draft else pr.state
        if state not in AUTO_MERGE_WAITING_STATES:
            raise CloseBlocked(f"PR #{pr.number} is not waiting ({state})")
        if not pr.head_oid:
            raise CloseBlocked(f"The head commit of PR #{pr.number} is not known yet")
        if await get_head_sha_async(Path(path)) != pr.head_oid:
            raise CloseBlocked(
                f"The local branch differs from PR #{pr.number}. Sync it first."
            )

    async def read(
        self, active_branches: set[str] | None = None
    ) -> tuple[list[Project], list[Worktree]]:
        """The rows, with the PR state read first so ``pushed_commits`` sees it.

        ``active_branches`` limits the PR read; ``None`` asks about every
        branch. See :meth:`Orchestrator.refresh_worktrees`. A hung ``gh``
        delays the rows by one or two of the cache's timeouts.
        """
        branches = await project_branches(self.projects_dir)
        await asyncio.gather(
            *(
                self.prs.refresh(
                    path, held if active_branches is None else held & active_branches
                )
                for path, held in branches.items()
            )
        )
        data = await build_list_all_data(self.projects_dir, prs=self.prs.snapshot())
        projects = [project_entity(p) for p in data["projects"]]
        open_rows = [
            (p["name"], row["name"])
            for p in data["projects"]
            for row in p["worktrees"]
            if not row.get("is_closed")
        ]
        terminal_urls = await asyncio.to_thread(self.terminal_urls, open_rows)
        worktrees = [
            worktree_entity(
                p["name"],
                row,
                shell_url=terminal_urls.get((p["name"], row["name"]), ""),
            )
            for p in data["projects"]
            for row in p["worktrees"]
        ]
        return projects, worktrees
