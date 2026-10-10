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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from mael_agent.agent_wire import build_start_payload
from mael_agent.harness_model import resolve_execute_model
from mael_common.claude_paths import has_claude_transcript
from mael_domain import task as model
from mael_domain import task_actions
from mael_domain.agent_store import AgentStore
from mael_domain.github_model import PrStatus, RateLimited, pr_from_row
from mael_domain.landing import Landings
from mael_domain.list_all import build_list_all_data
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
    check_not_live,
    check_synced,
    plan_launch,
)
from mael_domain.task_metadata_generator import TaskNames, infer_task_names
from mael_domain.task_table import TaskTable
from mael_domain.worktree import WorktreeSetup, get_head_sha_async
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

    async def launch(self, task_id: str, model_name: str | None) -> LaunchRequest:
        """Open the task's worktree, move it in-progress, and say what to start.

        Raises:
            KeyError: If no task has ``task_id``.
            LaunchBlocked: If a live session holds the task or its rebase failed.
        """
        ...

    async def rollback(self, request: LaunchRequest) -> None:
        """Move a task the host refused to start back to where it was."""
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

    async def promote(self, project: str, paths: list[Path], parent: str) -> list[str]:
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

    #: Removes a worktree, or ``None`` on a source that cannot.
    remove: RemoveWorktree | None

    #: Starts or stops a worktree's environment, or ``None``.
    env: EnvWorktree | None

    #: Makes a worktree's terminal and returns its link, or ``None``.
    ensure_terminal: EnsureTerminal | None

    #: Whether the last read had its pull request lookup refused for quota. The
    #: rows still stand — a refused lookup costs the pull request column, not
    #: the read — so the caller stands the next read off rather than dropping
    #: this one.
    rate_limited: bool


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

    async def launch(self, task_id: str, model_name: str | None) -> LaunchRequest:
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
        check_not_live(task.id, plan.session_id, self.live_sessions())
        setup = self.open_worktree(task.project, plan.branch, task.base or "")
        check_synced(task.id, plan.branch, setup)
        await self._move(task.project, task.id, model.STATUS_IN_PROGRESS)
        payload = build_start_payload(
            setup.path,
            prompt=plan.prompt,
            permission_mode=plan.permission_mode,
            model=model_name or plan.model,
            execute_model=plan.execute_model,
            session_id=plan.session_id,
            env=plan.env,
            # A task that has run before already owns its session id.
            resume=self.has_transcript(setup.path, plan.session_id),
            system_prompt_file=agent_prompt_file(),
        )
        return LaunchRequest(task.project, task.id, task.status, payload)

    async def rollback(self, request: LaunchRequest) -> None:
        await self._move(request.project, request.task_id, request.previous_status)

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

    async def delete(self, task_id: str) -> None:
        """Remove a task from the notebook.

        The dependent rewrite is the model's, not this layer's: ``delete``
        covers the removal and every dependent in one transaction.
        """
        project, notebook_id = split_task_key(task_id)
        await model.delete(self.table, project, notebook_id)

    def infer(self, draft: str) -> TaskNames:
        return infer_task_names(draft)

    def worktree_for(self, project: str, branch: str) -> WorktreeSetup:
        if self.open_worktree is None:
            raise LaunchBlocked("This server cannot open worktrees")
        # No base to seed: work with no task has no base to carry.
        return self.open_worktree(project, branch, "")

    async def create(
        self, project: str, fields: dict[str, Any], extra: dict[str, Any] | None = None
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
        task = await model.create(self.table, project=project, **wanted)
        return task_key(project, task.id)

    async def promote(self, project: str, paths: list[Path], parent: str) -> list[str]:
        """Promote every draft in one transaction, wiring the chain as it goes.

        The first task follows the end of its parent's child-chain, exactly as
        ``mael task promote --follow-end '*'`` wires it; each later one follows
        the task before it. See ``docs/dev/orchestrator-server.md``,
        "Approving a task set".
        """
        created: list[str] = []
        async with self.table.transact():
            for path in paths:
                follows = (
                    [created[-1]]
                    if created
                    else await model._resolve_follow_end(
                        self.table, project, "*", parent
                    )
                )
                try:
                    # The draft's own parent wins, as it does through the
                    # CLI: a planning session that named one meant it.
                    draft = model.read_draft(path)
                    # The files are consumed after the block, not here: a
                    # rollback puts the rows back but cannot put a deleted
                    # draft back, and a half-deleted set leaves the user with
                    # no plan to fix.
                    task = await model.promote_draft(
                        self.table,
                        project=project,
                        path=path,
                        overrides={"parent": draft.parent or parent},
                        draft=draft,
                        follows=follows,
                        consume=False,
                    )
                except (OSError, ValueError) as exc:
                    raise ValueError(f"{path.name}: {exc}") from exc
                created.append(task.id)
        # Committed: every draft is now a row, so the files can go.
        for path in paths:
            model.consume_draft(path)
        return [task_key(project, task_id) for task_id in created]

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
    ) -> None:
        self.projects = list(projects or [])
        self.worktrees = list(worktrees or [])
        self.close = close
        self.force_close = force_close
        self.trash = trash
        self.sync = sync
        self.merge = merge
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
        #: Set by a test that wants the caller to see a refused read.
        self.rate_limited = False
        #: Set by a test that needs a read to still be in flight while it does
        #: something else. The real read shells out per worktree and takes
        #: seconds; this one returns at once, so a test that cares about two
        #: reads overlapping has no window without it. The test supplies the
        #: read that waits on it — see ``_use_slow_read``.
        self.blocked_on: asyncio.Event | None = None

    def read(
        self, active_branches: set[str] | None = None
    ) -> tuple[list[Project], list[Worktree]]:
        self.reads += 1
        self.asked = active_branches
        return list(self.projects), list(self.worktrees)


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
        remove: RemoveWorktree | None = None,
        env: EnvWorktree | None = None,
        ensure_terminal: EnsureTerminal | None = None,
        terminal_urls: TerminalUrls = _no_terminal_urls,
    ) -> None:
        self.projects_dir = projects_dir
        self.terminal_urls = terminal_urls
        #: The last pull request seen, by project and then by branch, answering
        #: the branches a read did not ask about; see
        #: :func:`mael_domain.list_all.resolve_pr`. Keyed by project because
        #: branch names are not unique across them.
        self._pr_cache: dict[str, dict[str, PrStatus]] = {}
        #: Whether the last read had its pull request lookup refused for quota.
        #: The rows still stand; the caller stands the next read off.
        self.rate_limited = False
        self.close = close
        self.force_close = force_close
        self.trash = trash
        self.sync = sync
        self._merge_pr = merge_pr
        self.merge: MergeWorktreePr | None = (
            self._merge if merge_pr is not None else None
        )
        self.remove = remove
        self.env = env
        self.ensure_terminal = ensure_terminal

    async def _merge(self, project: str, branch: str, path: str) -> None:
        """Merge ``branch``'s pull request at the head commit the last read saw.

        The number and the commit come from this source's own reading, not from
        the caller: the head commit is not on the wire, and the merge must
        name the commit whose state the user was shown.
        """
        assert self._merge_pr is not None
        pr = self._pr_cache.get(project, {}).get(branch)
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
