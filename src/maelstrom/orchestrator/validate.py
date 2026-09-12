"""Whether the world can take a command.

The codes mirror the agent host's own refusals, so a command is refused the
same way whether the server or the fake backend answers it, and before the
host is touched. ``web/src/test/fakeServer.ts`` refuses the same commands for
the web tests, so a rule added here belongs there too.
"""

from typing import Any

from ..agent_model import MODES as AGENT_MODES
from ..worktree_model import is_worktree_closable
from .desk import split_desk_id
from .protocol import World

#: The six folders a task can sit in. A move names one of these.
TASK_STATUSES = ("todo", "in-progress", "blocked", "done", "cancelled", "template")

#: The keys ``task.update`` writes. Anything else in ``fields`` is not an edit.
EDITABLE = ("title", "content", "branch", "command", "mode", "priority", "model")

#: The three permission modes, shared with a live agent — see CONTEXT.md.
MODES = AGENT_MODES

#: The notebook's four priorities, from :data:`maelstrom.task.PRIORITIES`.
PRIORITIES = ("critical", "high", "medium", "low")

#: The commands that drive one agent: write to it, end it, or bring it back.
#: A subagent takes none; its parent does. Same wording as the host's refusal.
DRIVING_COMMANDS = (
    "agent.approve",
    "agent.deny",
    "agent.answer",
    "agent.say",
    "agent.run",
    "agent.stop",
    # No state check below: the world's state is a reconciled snapshot, the
    # daemon's is the truth, so a check here would race the host.
    "agent.interrupt",
    "agent.setMode",
    "agent.resume",
)

#: Which kinds of wait each reply answers, by the transcript item that carries
#: the request. Keyed on the item, not the agent's state: one state cannot
#: describe several waits at once — see CONTEXT.md, "Wait kind".
WAIT_FOR_COMMAND = {
    "agent.approve": ("permission_request", "plan_review"),
    "agent.deny": ("permission_request", "plan_review"),
    "agent.answer": ("question",),
}

#: The item a waiting state implies. Only safe when the agent holds one wait:
#: a state cannot name one of several. See :meth:`Orchestrator._wait_kind`.
WAIT_ITEM_FOR_STATE: dict[str, str] = {
    "awaiting-permission": "permission_request",
    "awaiting-plan-review": "plan_review",
    "awaiting-question": "question",
}

#: How to name each wait in a refusal, in the reader's words rather than the
#: transcript's field names.
WAIT_NAMES: dict[str, str] = {
    "permission_request": "a permission",
    "plan_review": "a plan review",
    "question": "a question",
}


def _wait_name(*kinds: str) -> str:
    """What to call ``kinds`` in a refusal a user reads."""
    return " or ".join(WAIT_NAMES.get(kind, kind) for kind in kinds)


def _err(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def check_linear_project(world: World, project: str) -> dict[str, str] | None:
    """Refuse a project the Linear routes cannot serve, or ``None`` if it can.

    Both Linear routes ask this: the picker read and the plan command. The panel
    offers the kind on ``hasLinear`` alone, so the server checks the same flag
    rather than trusting the client to have looked.
    """
    if project not in world["projects"]:
        return _err("unknown_id", f"No project {project}")
    if not world["projects"][project].get("hasLinear"):
        return _err("invalid", f"{project} names no Linear team")
    return None


def validate_command(
    world: World, cmd: dict[str, Any], *, wait_kind: str | None = None
) -> dict[str, str] | None:
    """The refusal for ``cmd`` against ``world``, or ``None`` when it may run.

    ``wait_kind`` is the transcript item type of the request a reply names —
    the caller resolves it, because the world holds request ids without their
    kinds. ``None`` means the item could not be found, and the reply is let
    through: the transcript keeps ``TRANSCRIPT_ITEMS`` items, so a pending item
    can be trimmed while its id is still pending, and refusing then would strand
    a wait nobody can answer.
    """
    kind = cmd.get("type")

    if kind in DRIVING_COMMANDS:
        # Checked before anything else, so the refusal a user sees is the one
        # that names what to drive.
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is not None and agent.get("parent"):
            parent = agent["parent"]
            return _err(
                "invalid", f"{agent_id} is a subagent of {parent}; drive {parent}"
            )

    if kind in ("agent.approve", "agent.deny", "agent.answer"):
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is None:
            return _err("unknown_id", f"No agent {agent_id}")
        if agent["state"] == "exited":
            return _err("agent_exited", f"Agent {agent_id} has exited")
        if not agent["pendingRequestIds"]:
            return _err("not_waiting", f"Agent {agent_id} is not waiting")
        if cmd.get("requestId") not in agent["pendingRequestIds"]:
            return _err(
                "stale_request", f"Request {cmd.get('requestId')} is no longer pending"
            )
        allowed = WAIT_FOR_COMMAND[kind]
        if wait_kind is not None and wait_kind not in allowed:
            return _err(
                "wrong_wait_kind",
                f"Request {cmd.get('requestId')} is "
                f"{_wait_name(wait_kind)}, not {_wait_name(*allowed)}",
            )
        if kind == "agent.deny" and not str(cmd.get("reason", "")).strip():
            return _err("invalid", "A reason is required")
        if kind == "agent.answer" and not cmd.get("answers"):
            return _err("invalid", "No answers given")
        return None

    if kind in (
        "agent.say",
        "agent.run",
        "agent.stop",
        "agent.interrupt",
        "agent.setMode",
    ):
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is None:
            return _err("unknown_id", f"No agent {agent_id}")
        if agent["state"] == "exited":
            return _err("agent_exited", f"Agent {agent_id} has exited")
        if (
            kind == "agent.say"
            and not str(cmd.get("text", "")).strip()
            and not cmd.get("attachments")
        ):
            # An image with no words is a message in its own right, so only a
            # turn carrying neither is empty.
            return _err("invalid", "Message is empty")
        if kind == "agent.run" and not str(cmd.get("command", "")).strip():
            return _err("invalid", "Command is empty")
        if kind == "agent.setMode" and cmd.get("mode", "") not in MODES:
            return _err("invalid", f"Unknown mode: {cmd.get('mode', '')}")
        return None

    if kind == "agent.resume":
        # The one agent command that wants an exited agent: it starts the
        # process again under the same id. A running one would give the
        # session two children fighting over one transcript.
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is None:
            return _err("unknown_id", f"No agent {agent_id}")
        if agent["state"] != "exited":
            return _err("invalid", f"Agent {agent_id} is running")
        return None

    if kind == "agent.launch":
        task_id = cmd.get("taskId", "")
        task = world["tasks"].get(task_id)
        if task is None:
            return _err("unknown_id", f"No task {task_id}")
        if not task["actionable"]:
            return _err("invalid", f"Task {task_id} is not actionable")
        return None

    if kind == "desk.add":
        desk_id = cmd.get("id", "")
        try:
            entity_kind, entity_id = split_desk_id(desk_id)
        except ValueError:
            return _err("unknown_id", f"Not a desk id: {desk_id}")
        table = "tasks" if entity_kind == "task" else "agents"
        if entity_id not in world[table]:
            return _err("unknown_id", f"No {entity_kind} {entity_id}")
        return None

    if kind == "desk.remove":
        desk_id = cmd.get("id", "")
        if desk_id not in world["desk"]:
            return _err("unknown_id", f"{desk_id} is not on the desk")
        return None

    if kind in ("document.approve", "document.requestChanges", "comment.add"):
        document_id = cmd.get("documentId", "")
        doc = world["documents"].get(document_id)
        if doc is None:
            return _err("unknown_id", f"No document {document_id}")
        if doc["version"] != cmd.get("version"):
            return _err(
                "stale_version",
                f"Document is at v{doc['version']}, not v{cmd.get('version')}",
            )
        if kind == "comment.add" and not str(cmd.get("body", "")).strip():
            return _err("invalid", "Comment is empty")
        if kind != "comment.add" and doc["status"] != "awaiting-review":
            return _err("invalid", f"Document is {doc['status']}, not awaiting review")
        if kind != "comment.add" and doc["source"].get("type") == "plan_review":
            # A plan review is the agent's own wait. Settling the document
            # would retire the item pointing the user at it and leave the
            # child blocked on a control request nobody can now answer.
            return _err(
                "invalid",
                "A plan review is answered on the agent, not the document",
            )
        if (
            kind == "document.requestChanges"
            and not str(cmd.get("summary", "")).strip()
        ):
            unresolved = any(
                c["documentId"] == doc["id"]
                and c["version"] == doc["version"]
                and not c["resolved"]
                for c in world["comments"].values()
            )
            if not unresolved:
                return _err("invalid", "Say what should change, or leave a comment")
        return None

    if kind == "comment.resolve":
        comment_id = cmd.get("commentId", "")
        comment = world["comments"].get(comment_id)
        if comment is None:
            return _err("unknown_id", f"No comment {comment_id}")
        if comment["resolved"]:
            return _err("invalid", f"Comment {comment_id} is resolved already")
        return None

    if kind == "worktree.close":
        worktree_id = cmd.get("worktreeId", "")
        worktree = world["worktrees"].get(worktree_id)
        if worktree is None:
            return _err("unknown_id", f"No worktree {worktree_id}")
        # Refused here, so the button is told before any sync or teardown runs.
        if not is_worktree_closable(worktree["nato"]):
            return _err(
                "invalid",
                f"{worktree['nato']} holds the main checkout and cannot be closed",
            )
        if worktree["isClosed"]:
            return _err("invalid", f"Worktree {worktree_id} is closed already")
        return None

    if kind == "task.setStatus":
        task_id = cmd.get("taskId", "")
        if task_id not in world["tasks"]:
            return _err("unknown_id", f"No task {task_id}")
        status = cmd.get("status")
        if status not in TASK_STATUSES:
            return _err("invalid", f"No status {status}")
        return None

    if kind == "task.update":
        task_id = cmd.get("taskId", "")
        if task_id not in world["tasks"]:
            return _err("unknown_id", f"No task {task_id}")
        fields = cmd.get("fields") or {}
        edited = [key for key in EDITABLE if fields.get(key) is not None]
        if not edited:
            return _err("invalid", "Nothing to change")
        title = fields.get("title")
        if title is not None and not str(title).strip():
            return _err("invalid", "A title is required")
        # Neither reaches the notebook's own check: ``model.update`` validates
        # priority and nothing validates mode, so a bad mode would only fail
        # later, at launch.
        mode = fields.get("mode")
        if mode is not None and mode not in MODES:
            return _err("invalid", f"No mode {mode}")
        priority = fields.get("priority")
        if priority is not None and priority not in PRIORITIES:
            return _err("invalid", f"No priority {priority}")
        return None

    if kind in ("task.infer", "shaping.start"):
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        text = cmd.get("draft" if kind == "task.infer" else "brief", "")
        if not str(text).strip():
            return _err("invalid", "Nothing to create")
        return None

    if kind == "task.create":
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        # The same field discipline ``task.update`` applies, over the fields a
        # new task carries. A field left out takes the notebook's own default,
        # so only what was sent is checked. A null is not "left out": it
        # reaches the notebook, which writes strings, and breaks the write.
        if any(cmd.get(key, "") is None for key in EDITABLE):
            return _err("invalid", "A field is null")
        if not str(cmd.get("title", "")).strip():
            return _err("invalid", "A title is required")
        mode = cmd.get("mode")
        if mode is not None and mode not in MODES:
            return _err("invalid", f"No mode {mode}")
        priority = cmd.get("priority")
        if priority is not None and priority not in PRIORITIES:
            return _err("invalid", f"No priority {priority}")
        return None

    if kind == "linear.plan":
        error = check_linear_project(world, cmd.get("project", ""))
        if error:
            return error
        if not str(cmd.get("issueId", "")).strip():
            return _err("invalid", "An issue is required")
        return None

    if kind == "agent.start":
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        if not str(cmd.get("branch", "")).strip():
            return _err("invalid", "A branch is required")
        if not str(cmd.get("prompt", "")).strip():
            return _err("invalid", "A prompt is required")
        mode = cmd.get("mode")
        if mode is not None and mode not in MODES:
            return _err("invalid", f"No mode {mode}")
        return None

    if kind == "worktree.refresh":
        # Nothing to check: it names no entity and carries no body. The read it
        # asks for is the whole world's, and the server decides whether it can
        # afford one right now.
        return None

    return _err("invalid", f"Unknown command: {kind}")
