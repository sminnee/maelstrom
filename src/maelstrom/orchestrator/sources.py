"""Where the orchestrator server reads tasks and worktrees from.

Storage layer. Each source is a Protocol with a real implementation over the
notebook or ``list-all`` and an in-memory one for tests. The methods block:
the server runs them in its executor, so a slow git read never stalls the
socket. Both return wire entities built by :mod:`.world_build`, so the server
holds one shape of the world and diffs readings of it.
"""

from collections.abc import Callable
from typing import Protocol

from .. import task as model
from ..task_index import TaskIndex
from ..task_store import TaskStore
from .protocol import Project, Task, Worktree
from .world_build import task_entity


class TaskSource(Protocol):
    """The notebook, as tasks across every project."""

    def version(self) -> str | None:
        """A stamp that changes when any task changes. ``None`` when unknown."""
        ...

    def read(self) -> list[Task]:
        """Every task the server shows, with ``actionable`` decided by the notebook."""
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
    ) -> None:
        self.store = store
        self.projects = projects
        self.index = index
        self._version = version

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
