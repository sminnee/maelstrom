"""Wire entities from the notebook, ``list-all`` rows and agent-host rows.

Pure builders and one differ. Each ``*_entity`` function takes what the source
already produces — a :class:`~maelstrom.task.Task`, a ``list-all`` worktree
row, a ``build_agent_row`` dict — and returns the entity the wire carries.
:func:`diff_kind` turns two readings of one table into the upserts and removes
that take a client from the first to the second.
"""

import re
from dataclasses import dataclass
from typing import Any

from .. import task as model
from ..worktree_model import get_worktree_folder_name
from .protocol import (
    Agent,
    Project,
    ServerEvent,
    Task,
    TaskLogEntry,
    TaskStep,
    Worktree,
)

#: The phase each task ``command`` puts its work in. ``web/src/protocol/phase.ts``
#: holds the same table; a command missing from it is executing.
PHASE_FOR_COMMAND = {
    "shape": "shaping",
    "plan-task": "planning",
    "plan-next-step": "planning",
    "watch-pr": "finalising",
}


def phase_for_command(command: str) -> str:
    return PHASE_FOR_COMMAND.get(command, "executing")


_STEP_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s*(.*)$")
_LOG_RE = re.compile(r"^\s*[-*]\s+(\S+)\s+(.*)$")


def parse_steps(text: str) -> list[TaskStep]:
    """The ``## Steps`` checklist as items. A line without a checkbox is an open step."""
    steps: list[TaskStep] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _STEP_RE.match(line)
        if match:
            steps.append(
                {"text": match.group(2).strip(), "done": match.group(1) != " "}
            )
        else:
            steps.append({"text": line.strip().lstrip("-* ").strip(), "done": False})
    return steps


def parse_log(text: str) -> list[TaskLogEntry]:
    """The ``## Log`` lines ``append_log`` writes: ``- <timestamp> <text>``.

    A line without a timestamp continues the entry before it, so a hand-wrapped
    entry stays one entry. Before any entry, it is an entry with no timestamp.
    """
    entries: list[TaskLogEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LOG_RE.match(line)
        if match:
            entries.append({"ts": match.group(1), "text": match.group(2).strip()})
        elif entries:
            entries[-1]["text"] += " " + line.strip()
        else:
            entries.append({"ts": "", "text": line.strip()})
    return entries


def task_key(project: str, notebook_id: str) -> str:
    """The wire id for a task: its project, then its notebook id.

    A notebook id is unique inside its project but not across projects, so the
    wire qualifies it. ``is_safe_id`` forbids ``/`` in either half, which makes
    the split exact.
    """
    return f"{project}/{notebook_id}"


def split_task_key(key: str) -> tuple[str, str]:
    """The project and notebook id a wire task id holds.

    Raises:
        ValueError: If ``key`` carries no project.
    """
    project, sep, notebook_id = key.partition("/")
    if not sep:
        raise ValueError(f"Not a qualified task id: {key!r}")
    return project, notebook_id


def task_entity(task: model.Task, *, actionable: bool) -> Task:
    """The wire task for a notebook task. ``actionable`` comes from the notebook's own rule.

    ``follows`` is qualified with the task's own project: a task only ever
    follows a task beside it in the notebook. ``parent`` is left bare, because
    it is often virtual and names no real task.
    """
    return {
        "id": task_key(task.project, task.id),
        "notebookId": task.id,
        "project": task.project,
        "title": task.title,
        "status": task.status,
        "command": task.command,
        "mode": task.mode,
        "branch": task.branch or model.default_branch(task.id, task.parent),
        "parent": task.parent,
        "follows": [task_key(task.project, f) for f in task.follows],
        "priority": task.priority,
        "model": task.model,
        "base": task.base,
        "content": task.content.strip(),
        "steps": parse_steps(task.steps),
        "log": parse_log(task.log),
        "created": task.created,
        "updated": task.updated,
        "phase": phase_for_command(task.command),
        "actionable": actionable,
    }


def worktree_entity(project: str, row: dict[str, Any]) -> Worktree:
    """The wire worktree for one ``list-all`` row. Nulls become empty strings."""
    nato = row["name"]
    return {
        "id": get_worktree_folder_name(project, nato),
        "project": project,
        "nato": nato,
        "path": row["path"],
        "branch": row.get("branch") or "",
        "base": row.get("base") or "",
        "isClosed": bool(row.get("is_closed")),
        "dirtyFiles": int(row.get("dirty_files") or 0),
        "localCommits": int(row.get("local_commits") or 0),
        "prNumber": row.get("pr_number"),
        "appUrl": row.get("app_url") or "",
        "appRunning": bool(row.get("app_running")),
        "sessionCount": int(row.get("session_count") or 0),
    }


def project_entity(data: dict[str, Any]) -> Project:
    """The wire project for one ``list-all`` project, which carries its stack tip."""
    return {
        "id": data["name"],
        "name": data["name"],
        "stackTip": data.get("stack_tip") or "main",
    }


_EXITED_RE = re.compile(r"^exited\((-?\d+)\)$")


def parse_agent_state(raw: str) -> tuple[str, int | None]:
    """``build_agent_row`` renders an exit as ``exited(N)``; split it back out."""
    match = _EXITED_RE.match(raw)
    if match:
        return "exited", int(match.group(1))
    return raw, None


def agent_entity(
    row: dict[str, Any],
    *,
    task_id: str,
    project: str,
    worktree_id: str,
    phase: str,
    pending_request_id: str | None = None,
) -> Agent:
    """The wire agent for one agent-host row plus its links into the world.

    The row is what ``mael agent list --json`` prints. ``pendingRequestId`` is
    not in it — the request id comes from the event stream — so a caller that
    knows it passes it in, and a fresh row starts with none.
    """
    state, exit_code = parse_agent_state(row.get("state", ""))
    cost = row.get("cost") or 0
    return {
        "id": row["id"],
        "state": state,
        "session": row.get("session") or "",
        "cwd": row.get("cwd") or "",
        "model": row.get("model") or "",
        "waitingOn": row.get("waiting_on") or "",
        "lastMessage": row.get("last_message") or "",
        "costUsd": float(cost),
        "taskId": task_id,
        "project": project,
        "worktreeId": worktree_id,
        "phase": phase,
        "exitCode": exit_code,
        "pendingRequestId": pending_request_id,
    }


@dataclass(frozen=True)
class AgentLink:
    """What ties an agent to the rest of the world."""

    task_id: str
    project: str
    worktree_id: str
    phase: str


def link_agent(
    row: dict[str, Any], *, worktrees: dict[str, Worktree], tasks: dict[str, Task]
) -> AgentLink:
    """Link an agent row to its worktree, project and task.

    The worktree is the one whose path is the agent's ``cwd``, and the project
    follows from it. The task is the one whose task session id the agent
    reports as its session: a launch pins ``session_id_for(project, task.id)``
    on the agent, so the reverse lookup is exact. No match leaves the id empty
    and the phase ``executing``.
    """
    cwd = row.get("cwd") or ""
    session = row.get("session") or ""
    worktree = next((w for w in worktrees.values() if w["path"] == cwd), None)
    project = worktree["project"] if worktree else ""
    task = next(
        (
            t
            for t in tasks.values()
            if session
            and model.session_id_for(t["project"], t["notebookId"]) == session
        ),
        None,
    )
    if task is not None:
        project = project or task["project"]
    return AgentLink(
        task_id=task["id"] if task else "",
        project=project,
        worktree_id=worktree["id"] if worktree else "",
        phase=task["phase"] if task else "executing",
    )


def diff_kind(kind: str, old: dict[str, Any], new: dict[str, Any]) -> list[ServerEvent]:
    """The upserts and removes that take a table from ``old`` to ``new``.

    Upserts come first, in ``new``'s order, then removes in ``old``'s order.
    An unchanged entity yields nothing, so a poll that finds no change is silent.
    """
    events: list[ServerEvent] = []
    for entity_id, entity in new.items():
        if old.get(entity_id) != entity:
            events.append({"type": "upsert", "kind": kind, "entity": entity})
    for entity_id in old:
        if entity_id not in new:
            events.append({"type": "remove", "kind": kind, "id": entity_id})
    return events
