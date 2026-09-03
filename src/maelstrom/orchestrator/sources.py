"""Where the orchestrator server reads tasks and worktrees from.

Storage layer. Each source is a Protocol with a real implementation over the
notebook or ``list-all`` and an in-memory one for tests. The methods block:
the server runs them in its executor, so a slow git read never stalls the
socket. Both return wire entities built by :mod:`.world_build`, so the server
holds one shape of the world and diffs readings of it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .. import task as model
from .. import task_actions
from ..list_all import build_list_all_data
from ..session_discovery import LiveSessionSet
from ..task_index import TaskIndex
from ..task_launch import LaunchBlocked, check_not_live, check_synced, plan_launch
from ..task_store import TaskStore
from ..worktree import WorktreeSetup
from ..worktree_model import has_claude_transcript
from .protocol import Project, Task, Worktree
from .world_build import (
    project_entity,
    split_task_key,
    task_entity,
    worktree_entity,
)

#: Opens the worktree a task runs in: ``(project, task, branch) -> WorktreeSetup``.
OpenWorktree = Callable[[str, model.Task, str], WorktreeSetup]


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


class WorktreeSource(Protocol):
    """``list-all``, as projects and their worktrees."""

    def read(self) -> tuple[list[Project], list[Worktree]]: ...


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
        setup = self.open_worktree(task.project, task, plan.branch)
        check_synced(task.id, plan.branch, setup)
        self._move(task.project, task.id, model.STATUS_IN_PROGRESS)
        payload = {
            "cmd": "start",
            "cwd": str(setup.path),
            "prompt": plan.prompt,
            "mode": plan.permission_mode,
            "model": model_name or plan.model,
            "session": plan.session_id,
            "env": plan.env,
            # A task that has run before already owns its session id.
            "resume": self.has_transcript(setup.path, plan.session_id),
        }
        return LaunchRequest(task.project, task.id, task.status, payload)

    def rollback(self, request: LaunchRequest) -> None:
        self._move(request.project, request.task_id, request.previous_status)

    def _move(self, project: str, task_id: str, status: str) -> None:
        """Move a task, keeping the index's head stamp honest, as the CLI does."""
        index = self.index
        was_fresh = index is not None and task_actions.index_is_fresh(self.store, index)
        task_actions.move_with_actions(
            self.store, project, task_id, status, index=index
        )
        if index is not None:
            task_actions.restamp(self.store, index, was_fresh=was_fresh)


class InMemoryWorktreeSource:
    """A fixed reading, editable by the test that owns it."""

    def __init__(
        self,
        projects: list[Project] | None = None,
        worktrees: list[Worktree] | None = None,
    ) -> None:
        self.projects = list(projects or [])
        self.worktrees = list(worktrees or [])

    def read(self) -> tuple[list[Project], list[Worktree]]:
        return list(self.projects), list(self.worktrees)


class ListAllWorktreeSource:
    """Projects and worktrees from :func:`maelstrom.list_all.build_list_all_data`."""

    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = projects_dir

    def read(self) -> tuple[list[Project], list[Worktree]]:
        data = build_list_all_data(self.projects_dir)
        projects = [project_entity(p) for p in data["projects"]]
        worktrees = [
            worktree_entity(p["name"], row)
            for p in data["projects"]
            for row in p["worktrees"]
        ]
        return projects, worktrees
