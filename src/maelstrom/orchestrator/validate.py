"""Whether the world can take a command. A port of ``web/src/protocol/validate.ts``.

The codes mirror the agent host's own refusals, so a command is refused the
same way whether the server or the fake backend answers it, and before the
host is touched.
"""

from typing import Any

from .desk import split_desk_id
from .protocol import World

WAIT_FOR_COMMAND = {
    "agent.approve": ("awaiting-permission", "awaiting-plan-review"),
    "agent.deny": ("awaiting-permission", "awaiting-plan-review"),
    "agent.answer": ("awaiting-question",),
}


def _err(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def validate_command(world: World, cmd: dict[str, Any]) -> dict[str, str] | None:
    """The refusal for ``cmd`` against ``world``, or ``None`` when it may run."""
    kind = cmd.get("type")

    if kind in ("agent.approve", "agent.deny", "agent.answer"):
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is None:
            return _err("unknown_id", f"No agent {agent_id}")
        if agent["state"] == "exited":
            return _err("agent_exited", f"Agent {agent_id} has exited")
        if not agent["pendingRequestId"]:
            return _err("not_waiting", f"Agent {agent_id} is not waiting")
        if agent["pendingRequestId"] != cmd.get("requestId"):
            return _err(
                "stale_request", f"Request {cmd.get('requestId')} is no longer pending"
            )
        allowed = WAIT_FOR_COMMAND[kind]
        if agent["state"] not in allowed:
            return _err(
                "wrong_wait_kind",
                f"Agent {agent_id} is {agent['state']}, not {'/'.join(allowed)}",
            )
        if kind == "agent.deny" and not str(cmd.get("reason", "")).strip():
            return _err("invalid", "A reason is required")
        if kind == "agent.answer" and not cmd.get("answers"):
            return _err("invalid", "No answers given")
        return None

    if kind in ("agent.say", "agent.stop"):
        agent_id = cmd.get("agentId", "")
        agent = world["agents"].get(agent_id)
        if agent is None:
            return _err("unknown_id", f"No agent {agent_id}")
        if agent["state"] == "exited":
            return _err("agent_exited", f"Agent {agent_id} has exited")
        if kind == "agent.say" and not str(cmd.get("text", "")).strip():
            return _err("invalid", "Message is empty")
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

    if kind in ("task.create", "shaping.start"):
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        text = cmd.get("draft" if kind == "task.create" else "brief", "")
        if not str(text).strip():
            return _err("invalid", "Nothing to create")
        return None

    return _err("invalid", f"Unknown command: {kind}")
