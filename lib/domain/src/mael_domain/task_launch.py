"""What a task launch settles before anything runs.

The model half of launching a task, shared by ``mael task run`` (which then
starts a daemon agent or runs the CLI in the current shell) and the orchestrator server
(which then asks the agent host to start an agent). Both read the same
session id from the task. Both derive the same environment, permission mode,
branch and prompt. Both refuse for the same two reasons: a live session
already holds the task, or the worktree's rebase failed.

Pure apart from :func:`prepare_launch`, which stores a Claude task's session id
before it plans, and :func:`check_not_live`, which reads the given live-session
sweep. The status moves stay with the callers, at
:func:`mael_domain.task_actions.move_with_actions`.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mael_agent.harness_model import resolve_model_reference

from . import task as model

if TYPE_CHECKING:
    from .task_table import TaskTable
from .session_discovery import LiveSessionSet
from .worktree import WorktreeSetup


class LaunchBlocked(Exception):
    """The task must not launch now. The message says why, for the user."""


@dataclass(frozen=True)
class LaunchPlan:
    """Everything a launch derives from the task before it starts anything."""

    project: str
    task_id: str
    #: The task's current session for the harness. ``""`` for a never-run
    #: Codex task: Codex makes its own thread id.
    session_id: str
    #: ``MAEL_TASK_ID`` and its siblings, for the skills inside the session.
    env: dict[str, str]
    #: Claude's ``--permission-mode`` value, or ``None`` for its default.
    permission_mode: str | None
    branch: str
    #: The launch's model, else the task's, else :data:`~mael_domain.task.DEFAULT_MODEL`.
    model: str
    #: The harness that model runs on.
    harness: str
    #: The model the session switches to when its plan is approved, raw from the
    #: task. No default is substituted: empty means no switch, and a fallback to
    #: ``DEFAULT_MODEL`` here would switch every planning session.
    execute_model: str
    prompt: str


def _launch_model(task: model.Task, model_name: str | None = None) -> str:
    """The model a launch runs: ``model_name``, else the task's, else the default."""
    return model_name or task.model or model.DEFAULT_MODEL


def _launch_harness(task: model.Task, model_name: str | None = None) -> str:
    """The harness a launch of ``task`` runs on."""
    return resolve_model_reference(_launch_model(task, model_name)).harness


async def prepare_launch(
    table: "TaskTable", project: str, task: model.Task, model_name: str | None = None
) -> LaunchPlan:
    """Store the session id a launch of ``task`` needs, then plan the launch.

    The one entry for a launch: :func:`plan_launch` alone gives a never-run
    Claude task no session id, so its agent would not link to the task.
    """
    await model.ensure_session(table, task, _launch_harness(task, model_name))
    return plan_launch(project, task, model_name)


def plan_launch(
    project: str, task: model.Task, model_name: str | None = None
) -> LaunchPlan:
    """The launch plan for ``task``. Pure.

    ``model_name`` overrides the task's model, and its harness shapes the prompt.
    """
    harness = _launch_harness(task, model_name)
    session_id = model.current_session(task, harness)
    return LaunchPlan(
        project=project,
        task_id=task.id,
        session_id=session_id,
        env={
            "MAEL_TASK_ID": task.id,
            # A parentless task self-parents: children it emits nest under it
            # and share its branch (one PR per chain). See docs/dev/tasks.md.
            "MAEL_TASK_PARENT": task.parent or task.id,
            # The two ids above are bare. This names their project, so a
            # command aimed at another project does not inherit them.
            "MAEL_TASK_PROJECT": project,
        },
        permission_mode=model.permission_mode_for(task.mode),
        branch=task.branch or model.default_branch(task.id, task.parent),
        model=_launch_model(task, model_name),
        harness=harness,
        execute_model=task.execute_model,
        prompt=model.build_prompt(task, harness),
    )


def check_not_live(task_id: str, session_id: str, live: LiveSessionSet) -> None:
    """Refuse a second parallel launch of the same task.

    Keyed on the task session id, not on worktree occupancy, so a sibling task
    sharing the worktree can run at the same time. A finished session leaves
    nothing running, so a finished task stays re-runnable.

    Raises:
        LaunchBlocked: When a live ``claude`` reports ``session_id``.
    """
    if not session_id:
        return
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
