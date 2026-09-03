"""What a task launch settles before anything runs.

The model half of launching a task, shared by ``mael task run`` (which then
places a session in cmux or the current shell) and the orchestrator server
(which then asks the agent host to start an agent). Both derive the same
session id, environment, permission mode, branch and prompt from the task,
and refuse for the same two reasons: a live session already holds the task,
or the worktree's rebase failed.

Pure apart from :func:`check_not_live`, which reads the given live-session
sweep. The status moves stay with the callers, at
:func:`maelstrom.task_actions.move_with_actions`.
"""

from dataclasses import dataclass

from . import task as model
from .session_discovery import LiveSessionSet
from .worktree import WorktreeSetup


class LaunchBlocked(Exception):
    """The task must not launch now. The message says why, for the user."""


@dataclass(frozen=True)
class LaunchPlan:
    """Everything a launch derives from the task before it starts anything."""

    project: str
    task_id: str
    #: The task session id: stable per task, what links a session back to it.
    session_id: str
    #: ``MAEL_TASK_ID`` and its siblings, for the skills inside the session.
    env: dict[str, str]
    #: Claude's ``--permission-mode`` value, or ``None`` for its default.
    permission_mode: str | None
    branch: str
    model: str | None
    prompt: str


def plan_launch(project: str, task: model.Task) -> LaunchPlan:
    """The launch plan for ``task``. Pure."""
    session_id = model.session_id_for(project, task.id)
    return LaunchPlan(
        project=project,
        task_id=task.id,
        session_id=session_id,
        env={
            "MAEL_TASK_ID": task.id,
            # A parentless task self-parents: children it emits nest under it
            # and share its branch (one PR per chain). See docs/dev/tasks.md.
            "MAEL_TASK_PARENT": task.parent or task.id,
            "MAEL_TASK_SESSION_ID": session_id,
        },
        permission_mode=model.permission_mode_for(task.mode),
        branch=task.branch or model.default_branch(task.id, task.parent),
        model=task.model or None,
        prompt=model.build_prompt(task),
    )


def check_not_live(task_id: str, session_id: str, live: LiveSessionSet) -> None:
    """Refuse a second parallel launch of the same task.

    Keyed on the task session id, not on worktree occupancy, so a sibling task
    sharing the worktree can run at the same time. A finished session leaves
    nothing running, so a finished task stays re-runnable.

    Raises:
        LaunchBlocked: When a live ``claude`` reports ``session_id``.
    """
    existing = live.for_session_id(session_id)
    if existing is not None:
        raise LaunchBlocked(
            f"Task {task_id} already has a live Claude session "
            f"(pid {existing.pid}). Close it before relaunching, or run "
            f"`mael task reconcile` to inspect."
        )


def check_synced(task_id: str, branch: str, setup: WorktreeSetup) -> None:
    """Refuse to run against code the open could not rebase.

    Raises:
        LaunchBlocked: When the open's sync ran and failed.
    """
    if setup.sync is not None and not setup.sync.success:
        raise LaunchBlocked(
            f"Sync of {branch} failed; {task_id} left TODO: {setup.sync.message}"
        )
