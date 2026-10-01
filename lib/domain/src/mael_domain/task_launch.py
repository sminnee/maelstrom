"""What a task launch settles before anything runs.

The model half of launching a task, shared by ``mael task run`` (which then
places a session in cmux or the current shell) and the orchestrator server
(which then asks the agent host to start an agent). Both derive the same
environment, permission mode, branch and prompt from the task, choose its
session the same way, and refuse for the same two reasons: a live session
already holds the task, or the worktree's rebase failed.

A task's sessions are its Agent records — see
:class:`mael_domain.agent_store.AgentStore`. The callers read them with
``for_task`` and hand them in, so this module stays pure apart from
:func:`check_not_live`, which reads the given live-session sweep. The status
moves stay with the callers, at
:func:`mael_domain.task_actions.move_with_actions`.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from . import task as model
from .session_discovery import LiveSession, LiveSessionSet
from .task_table import row_id
from .worktree import WorktreeSetup


class LaunchBlocked(Exception):
    """The task must not launch now. The message says why, for the user."""


@dataclass(frozen=True)
class LaunchPlan:
    """Everything a launch derives from the task before it starts anything."""

    project: str
    task_id: str
    #: The task row id, ``<project>/<task_id>``. The launch's Agent record
    #: names it, which is what links the session back to the task.
    task: str
    #: ``MAEL_TASK_ID`` and its siblings, for the skills inside the session.
    env: dict[str, str]
    #: Claude's ``--permission-mode`` value, or ``None`` for its default.
    permission_mode: str | None
    branch: str
    #: The task's model, or :data:`~mael_domain.task.DEFAULT_MODEL` when it names none.
    model: str
    #: The model the session switches to when its plan is approved, raw from the
    #: task. No default is substituted: empty means no switch, and a fallback to
    #: ``DEFAULT_MODEL`` here would switch every planning session.
    execute_model: str
    prompt: str


def plan_launch(project: str, task: model.Task) -> LaunchPlan:
    """The launch plan for ``task``. Pure.

    The session is not here. Whether a task resumes depends on a transcript in
    the worktree the launch opens, which does not exist until the plan's own
    branch has been opened — see :func:`choose_session`.
    """
    return LaunchPlan(
        project=project,
        task_id=task.id,
        task=row_id(project, task.id),
        env={
            "MAEL_TASK_ID": task.id,
            # A parentless task self-parents: children it emits nest under it
            # and share its branch (one PR per chain). See docs/dev/tasks.md.
            "MAEL_TASK_PARENT": task.parent or task.id,
        },
        permission_mode=model.permission_mode_for(task.mode),
        branch=task.branch or model.default_branch(task.id, task.parent),
        model=task.model or model.DEFAULT_MODEL,
        execute_model=task.execute_model,
        prompt=model.build_prompt(task),
    )


@dataclass(frozen=True)
class SessionChoice:
    """The session a launch runs on, and whether it is there already."""

    session_id: str
    #: ``True`` continues the session's transcript. ``False`` claims the id.
    resume: bool


def _mint() -> str:
    return str(uuid.uuid4())


def choose_session(
    records: Sequence[dict[str, Any]],
    has_transcript: Callable[[str], bool],
    *,
    fresh: bool = False,
    mint: Callable[[], str] = _mint,
) -> SessionChoice:
    """The session a task's next launch runs on.

    ``records`` are the task's Agent records, newest first. The launch resumes
    the newest session that left a transcript where this launch runs, which is
    what ``has_transcript`` answers for one session id. A record with no such
    transcript is passed over: its launch failed, or its session ran in a
    worktree this launch did not open, and ``claude --resume`` finds neither.

    With nothing to resume the launch takes a new id. An id is never reused, so
    a new session cannot collide with a transcript an old one left.

    ``fresh`` takes a new id whatever the records say.
    """
    if not fresh:
        for record in records:
            session_id = str(record.get("session_id") or "")
            if session_id and has_transcript(session_id):
                return SessionChoice(session_id, resume=True)
    return SessionChoice(mint(), resume=False)


def live_session_of(
    records: Sequence[dict[str, Any]], live: LiveSessionSet
) -> LiveSession | None:
    """The live session one of ``records`` names, or ``None``.

    ``records`` are one task's Agent records. A record with no session id
    names none: a bare ``claude`` reports no id either, and the two must not
    match.
    """
    for record in records:
        session_id = str(record.get("session_id") or "")
        existing = live.for_session_id(session_id) if session_id else None
        if existing is not None:
            return existing
    return None


def check_not_live(
    task_id: str, records: Sequence[dict[str, Any]], live: LiveSessionSet
) -> None:
    """Refuse a second parallel launch of the same task.

    ``records`` are the task's Agent records. Keyed on the task's own sessions,
    not on worktree occupancy, so a sibling task sharing the worktree can run
    at the same time. A finished session leaves nothing running, so a finished
    task stays re-runnable.

    Raises:
        LaunchBlocked: When a live ``claude`` reports one of the task's
            session ids.
    """
    existing = live_session_of(records, live)
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
