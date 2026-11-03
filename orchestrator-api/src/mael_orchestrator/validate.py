"""Whether the world can take a command.

The codes mirror the agent host's own refusals, so a command is refused the
same way whether the server or the fake backend answers it, and before the
host is touched. ``orchestrator-ui/src/test/fakeServer.ts`` refuses the same commands for
the web tests, so a rule added here belongs there too.
"""

from typing import Any

from mael_agent.agent_wire import MODES as AGENT_MODES
from mael_agent.harness_model import resolve_execute_model
from mael_domain.attachments import is_bare_name
from mael_domain.build_runs import parse_trigger
from mael_domain.feedback import SCOPES
from mael_domain.github_model import AUTO_MERGE_WAITING_STATES
from mael_domain.protocol import Agent, Worktree, World
from mael_domain.task import PROMOTABLE_FIELDS
from mael_domain.worktree_model import is_worktree_closable

from .desk import split_desk_id
from .world_build import split_task_key

#: The six folders a task can sit in. A move names one of these.
TASK_STATUSES = ("todo", "in-progress", "blocked", "done", "cancelled", "template")

#: The keys ``task.update`` writes. Anything else in ``fields`` is not an edit.
EDITABLE = (
    "title",
    "content",
    "branch",
    "command",
    "mode",
    "priority",
    "model",
    "execute_model",
    "base",
    "follows",
    "comms",
    "references",
)

#: The keys ``task.create`` writes: :data:`EDITABLE`, and the fields only a
#: new task takes. ``comms`` is here so a task made from a comm is linked in
#: the same write; left out, a new task inherits its parent task's comms.
#: ``status`` is ``template`` or left out. Two more keys are not fields:
#: ``followEnds`` resolves into ``follows``, and ``from`` names a task to
#: duplicate.
CREATABLE = (
    *EDITABLE,
    "parent",
    "pre_action",
    "post_action",
    "status",
    "schedule",
    "trigger",
)

#: Wire keys that spell a model field differently, mapped wire -> model. Every
#: other ``EDITABLE``/``CREATABLE`` key is spelled identically in both, so a
#: membership check against either tuple must go by the wire name.
WIRE_RENAMES = {
    "executeModel": "execute_model",
    "preAction": "pre_action",
    "postAction": "post_action",
}

#: The three permission modes, shared with a live agent — see CONTEXT.md.
MODES = AGENT_MODES

#: The notebook's four priorities, from :data:`mael_domain.task.PRIORITIES`.
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

#: The commands that mutate one worktree. They share a validator because they
#: share their preconditions: the world must hold the worktree, ``_main`` may
#: not be torn down, and an operation on a branch needs a worktree still on one.
WORKTREE_COMMANDS = (
    "worktree.close",
    "worktree.forceClose",
    "worktree.trash",
    "worktree.remove",
    "worktree.sync",
    "worktree.mergePr",
    "worktree.autoMerge",
    "worktree.env",
    "worktree.createTerminal",
    "worktree.comment",
    "worktree.feedback",
    "worktree.setFeedbackParams",
)

#: The commands that take a worktree away, which ``_main`` refuses. A sync or
#: an environment on ``_main`` is ordinary work.
TEARDOWN_COMMANDS = (
    "worktree.close",
    "worktree.forceClose",
    "worktree.trash",
    "worktree.remove",
)

#: The commands needing a worktree that still holds a branch and a checkout. A
#: remove is the exception: deleting a parked worktree is the point of it.
NEEDS_OPEN_COMMANDS = (
    "worktree.close",
    "worktree.forceClose",
    "worktree.trash",
    "worktree.sync",
    "worktree.mergePr",
    "worktree.autoMerge",
    "worktree.env",
    "worktree.createTerminal",
    "worktree.comment",
    "worktree.feedback",
    "worktree.setFeedbackParams",
)

#: The commands that relay a message to the agents in a worktree. Each needs
#: an agent to tell, and ``agents_in_worktree`` names the same ones for all.
RELAY_COMMANDS = (
    "worktree.comment",
    "worktree.feedback",
    "worktree.setFeedbackParams",
)

#: What a **Dismiss** runs before the node leaves the desk: the agent's stop,
#: or the worktree command that ends its worktree.
DISMISS_CHAINS: dict[str, str | None] = {
    "terminate": None,
    "close": "worktree.close",
    "shelve": "worktree.forceClose",
    "trash": "worktree.trash",
    "delete": "worktree.remove",
}

#: The three settings ``mael sync`` has, which the one sync command chooses
#: between. ``--abort`` is implied on ``plain`` and ``squash``.
SYNC_MODES = ("plain", "autorepair", "squash")

#: What an environment can be asked to do. ``restart`` is the other two in order.
ENV_ACTIONS = ("start", "stop", "restart")

#: Which kinds of wait each reply answers, by the transcript item that carries
#: the request. Keyed on the item, not the agent's state: one state cannot
#: describe several waits at once — see CONTEXT.md, "Wait kind".
WAIT_FOR_COMMAND = {
    "agent.approve": ("permission_request", "plan_review"),
    "agent.deny": ("permission_request", "plan_review", "question"),
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


def _check_follows(world: World, task_id: str, follows: Any) -> dict[str, str] | None:
    """The refusal for rewiring ``task_id`` to follow ``follows``, or ``None``.

    The write replaces the whole list, so every id is checked. A task only ever
    follows a task beside it in its own notebook, and a cycle would strand both
    ends: neither could ever become actionable.

    The id's own shape is checked before the world is consulted, so one bad id
    reports the same refusal whatever else the world happens to hold.
    """
    if not isinstance(follows, list):
        return _err("invalid", "follows must be a list")
    project, _ = split_task_key(task_id)
    for followed in follows:
        if followed == task_id:
            return _err("invalid", "A task cannot follow itself")
        try:
            followed_project, _ = split_task_key(str(followed))
        except ValueError:
            return _err("invalid", f"Not a qualified task id: {followed}")
        if followed_project != project:
            return _err("invalid", f"{followed} is in another project")
        if followed not in world["tasks"]:
            return _err("unknown_id", f"No task {followed}")
    if _reaches(world, follows, task_id):
        return _err("invalid", "That would make a cycle")
    return None


def _check_task_refs(
    world: World, project: str, cmd: dict[str, Any]
) -> dict[str, str] | None:
    """The refusal for the task ids a create or a promote names, or ``None``.

    ``follows`` and ``from`` must name tasks the world holds. A ``followEnds``
    entry is ``*`` or an id, which may name no task: the chain of an id that
    nothing follows ends at the id. ``after`` may be stale, and the notebook
    ignores a stale one. Every id is qualified and in ``project``, since the
    notebook stores a bare one.
    """
    follows = cmd.get("follows")
    ends = cmd.get("followEnds")
    for key, value in (("follows", follows), ("followEnds", ends)):
        if value is not None and not isinstance(value, list):
            return _err("invalid", f"{key} must be a list")
    after = cmd.get("after")
    named = [
        *(follows or []),
        *[e for e in ends or [] if e != "*"],
        *([cmd["from"]] if "from" in cmd else []),
        *([after] if after is not None else []),
    ]
    for task_id in named:
        try:
            task_project, _ = split_task_key(str(task_id))
        except ValueError:
            return _err("invalid", f"Not a qualified task id: {task_id}")
        if task_project != project:
            return _err("invalid", f"{task_id} is in another project")
    for task_id in [*(follows or []), *([cmd["from"]] if "from" in cmd else [])]:
        if task_id not in world["tasks"]:
            return _err("unknown_id", f"No task {task_id}")
    return None


def _check_recipe(fields: dict[str, Any]) -> dict[str, str] | None:
    """The refusal for a mode or a priority the notebook cannot take, or ``None``.

    Nothing in the notebook checks a mode, so a bad one would fail only at
    launch. The priority check refuses before any write.
    """
    mode = fields.get("mode")
    if mode is not None and mode not in MODES:
        return _err("invalid", f"No mode {mode}")
    priority = fields.get("priority")
    if priority is not None and priority not in PRIORITIES:
        return _err("invalid", f"No priority {priority}")
    return None


def _wire_edited(fields: dict[str, Any], allowed: tuple[str, ...]) -> list[str]:
    """The wire-spelled keys of ``fields`` that name a field in ``allowed``.

    ``allowed`` (``EDITABLE``/``CREATABLE``) lists model-spelled keys, but
    ``fields``/``cmd`` arrive wire-spelled, so membership is checked under the
    wire name a key would take were it renamed.
    """
    return [key for key in fields if WIRE_RENAMES.get(key, key) in allowed]


#: The comm fields ``comm.update`` takes. ``closed`` closes or reopens it.
COMM_EDITABLE = ("title", "content", "recipients", "category", "project", "closed")


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _check_comm_fields(world: World, fields: dict[str, Any]) -> dict[str, str] | None:
    """A comm's text fields, ``recipients`` and ``project``, when sent.

    ``project`` is ``""`` or a project the world holds.
    """
    for key in ("content", "category", "project"):
        value = fields.get(key)
        if value is not None and not isinstance(value, str):
            return _err("invalid", f"{key} is text")
    project = fields.get("project")
    if project and project not in world["projects"]:
        return _err("unknown_id", f"No project {project}")
    recipients = fields.get("recipients")
    if recipients is not None and not _is_str_list(recipients):
        return _err("invalid", "recipients is a list of text")
    return None


def _check_comm_update(world: World, cmd: dict[str, Any]) -> dict[str, str] | None:
    """An edit of a draft's ``content``, or ``sent: true``. A sent update is fixed."""
    comm_id = cmd.get("commId", "")
    comm = world["comms"].get(comm_id)
    if comm is None:
        return _err("unknown_id", f"No comm {comm_id}")
    update_id = cmd.get("updateId", "")
    update = next((u for u in comm["updates"] if u["id"] == update_id), None)
    if update is None:
        return _err("unknown_id", f"No update {update_id}")
    fields = cmd.get("fields") or {}
    content, sent = fields.get("content"), fields.get("sent")
    if content is None and sent is None:
        return _err("invalid", "Nothing to change")
    if content is not None and not isinstance(content, str):
        return _err("invalid", "content is text")
    if sent is not None and sent is not True:
        return _err("invalid", "sent is true")
    if update["state"] == "sent":
        return _err("invalid", f"Update {update_id} is already sent")
    return None


def _check_comm_ids(world: World, held: list[str], comms: Any) -> dict[str, str] | None:
    """A task's new ``comms``: a list of ids, each naming a comm the world holds.

    An id in ``held``, the task's current comms, passes, so one unknown id
    written by a load-many block does not lock the task's other links.
    """
    if not _is_str_list(comms):
        return _err("invalid", "comms is a list of comm ids")
    for comm_id in comms:
        if comm_id not in world["comms"] and comm_id not in held:
            return _err("unknown_id", f"No comm {comm_id}")
    return None


def _reaches(world: World, starts: list[str], goal: str) -> bool:
    """Whether ``goal`` is reachable by walking ``follows`` from ``starts``.

    Walks the world's existing wires; ``seen`` ends a cycle in the data itself.
    """
    seen: set[str] = set()
    queue = list(starts)
    while queue:
        current = queue.pop()
        if current == goal:
            return True
        if current in seen:
            continue
        seen.add(current)
        task = world["tasks"].get(current)
        if task is not None:
            queue.extend(task["follows"])
    return False


def agents_in_worktree(world: World, worktree_id: str) -> list[Agent]:
    """The agents a post of change comments or feedback reaches, in the world's order.

    A subagent is driven through its parent, and an exited agent hears
    nothing. ``trackedAgents`` in the UI applies the same rule, so the dock
    names the agents the server writes to.
    """
    return [
        agent
        for agent in world["agents"].values()
        if agent["worktreeId"] == worktree_id
        and not agent["parent"]
        and agent["state"] != "exited"
    ]


def live_agent_of(world: World, desk_id: str) -> Agent | None:
    """The running top-level agent a desk unit stands for, if it has one."""
    entity_kind, entity_id = split_desk_id(desk_id)
    for agent in world["agents"].values():
        if agent["state"] == "exited" or agent.get("parent"):
            continue
        if (entity_kind == "agent" and agent["id"] == entity_id) or (
            entity_kind == "task" and agent["taskId"] == entity_id
        ):
            return agent
    return None


def _dismiss_error(world: World, cmd: dict[str, Any]) -> dict[str, str] | None:
    """Whether the world can take a **Dismiss** of one desk unit."""
    desk_id = cmd.get("id", "")
    try:
        entity_kind, entity_id = split_desk_id(desk_id)
    except ValueError:
        return _err("unknown_id", f"Not a desk id: {desk_id}")
    table = "tasks" if entity_kind == "task" else "agents"
    if entity_id not in world[table]:
        return _err("unknown_id", f"No {entity_kind} {entity_id}")
    chain = cmd.get("chain", "")
    if chain not in DISMISS_CHAINS:
        return _err("invalid", f"Unknown dismiss chain: {chain}")
    ending = DISMISS_CHAINS[chain]
    if ending is None:
        if live_agent_of(world, desk_id) is None:
            return _err("agent_exited", f"No agent is running for {desk_id}")
        return None
    return _worktree_error(world, ending, cmd)


def _is_change_comment(comment: Any) -> bool:
    """Whether ``comment`` has every field the message is built from."""
    return (
        isinstance(comment, dict)
        and all(isinstance(comment.get(key), str) for key in ("rev", "path", "body"))
        and comment.get("side") in ("new", "old")
        and all(type(comment.get(key)) is int for key in ("startLine", "endLine"))
        and isinstance(comment.get("lines"), list)
        and all(isinstance(line, str) for line in comment["lines"])
    )


def _monkeypatch_error(feedback: dict[str, Any]) -> dict[str, str] | None:
    css = feedback.get("css")
    if not isinstance(css, str) or not css.strip():
        return _err("invalid", "No CSS to send")
    if not isinstance(feedback.get("note"), str | None):
        return _err("invalid", "A note is text")
    return None


def _instruction_error(feedback: dict[str, Any]) -> dict[str, str] | None:
    text = feedback.get("text")
    if not isinstance(text, str) or not text.strip():
        return _err("invalid", "No text to send")
    element = feedback.get("element")
    return None if element is None else _element_error(element)


def _element_error(element: Any) -> dict[str, str] | None:
    """Whether a picked element has the selector the message names it by."""
    if not isinstance(element, dict):
        return _err("invalid", "A picked element is an object")
    selector = element.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        return _err("invalid", "A picked element has a selector")
    return None


def _pick_error(feedback: dict[str, Any]) -> dict[str, str] | None:
    if feedback.get("scope") not in SCOPES:
        return _err("invalid", "A pick has a scope: element, similar or area")
    if error := _element_error(feedback.get("element")):
        return error
    if not isinstance(feedback.get("note"), str | None):
        return _err("invalid", "A note is text")
    return None


def _controls_error(feedback: dict[str, Any]) -> dict[str, str] | None:
    if not isinstance(feedback.get("title"), str):
        return _err("invalid", "A control set has a title")
    changes = feedback.get("changes")
    if not isinstance(changes, list) or not changes:
        return _err("invalid", "No changes to send")
    for change in changes:
        if not isinstance(change, dict) or not all(
            isinstance(change.get(key), str) for key in ("var", "to")
        ):
            return _err("invalid", "A change has a var and a value")
        if not all(
            isinstance(change[k], str) for k in ("label", "from") if k in change
        ):
            return _err("invalid", "A change's label and from are text")
    if not isinstance(feedback.get("css"), str | None):
        return _err("invalid", "The CSS is text")
    if not isinstance(feedback.get("note"), str | None):
        return _err("invalid", "A note is text")
    return None


#: One validator per kind of **Feedback**, by its ``type``: the types the
#: server can build a message from.
_FEEDBACK_VALIDATORS = {
    "monkeypatch": _monkeypatch_error,
    "instruction": _instruction_error,
    "pick": _pick_error,
    "controls": _controls_error,
}


def _feedback_params_error(
    worktree: Worktree, service: Any, params: Any
) -> dict[str, str] | None:
    """Whether the service is connected, and exposes each param, set to a bool."""
    if not isinstance(params, dict) or not params:
        return _err("invalid", "No params to set")
    connected = {s["service"]: s["params"] for s in worktree.get("liveFeedback", [])}
    if service not in connected:
        return _err("invalid", f"{service} has no live feedback client")
    for name, value in params.items():
        if name not in connected[service]:
            return _err("invalid", f"{service} exposes no param {name}")
        if not isinstance(value, bool):
            return _err("invalid", "A param value is true or false")
    return None


def _auto_merge_error(
    worktree_id: str, worktree: Worktree, on: Any
) -> dict[str, str] | None:
    """Why **Auto-merge** cannot turn ``on`` or off here, or ``None`` when it can.

    Off has fewer rules than on: it is the way out of a PR whose build went red
    or which conflicts after the user turned auto-merge on.
    """
    if not worktree.get("prNumber"):
        return _err("invalid", f"Worktree {worktree_id} has no pull request")
    if worktree.get("prDraft"):
        return _err("invalid", "The pull request is a draft")
    if not worktree.get("prAutoMergeAllowed"):
        return _err("invalid", "The repository does not allow auto-merge")
    if not isinstance(on, bool):
        return _err("invalid", "on must be true or false")
    if not on:
        return None
    state = worktree.get("prState")
    if state == "ready":
        return _err("invalid", "The pull request is ready. Merge it instead.")
    if state not in AUTO_MERGE_WAITING_STATES:
        return _err("invalid", f"The pull request is not waiting ({state})")
    if worktree.get("prMatch") == "differ":
        return _err(
            "invalid",
            f"The local branch differs from PR #{worktree['prNumber']}. Sync it first.",
        )
    return None


def _feedback_error(feedback: Any) -> dict[str, str] | None:
    """Whether ``feedback`` holds a type the server knows, with its fields.

    A ``screenshot`` is optional for every type. It names a file the jig wrote
    under ``.drafts/jig/``, and the server joins it to that directory, so a
    separator or a ``..`` would reach outside it.
    """
    if not isinstance(feedback, dict):
        return _err("invalid", "Feedback is an object")
    kind = feedback.get("type")
    validator = _FEEDBACK_VALIDATORS.get(kind) if isinstance(kind, str) else None
    if validator is None:
        return _err("invalid", f"Unknown feedback type: {kind!r}")
    if "screenshot" in feedback and not is_bare_name(feedback["screenshot"]):
        return _err("invalid", "A screenshot is a file name")
    return validator(feedback)


def _worktree_error(
    world: World, kind: str, cmd: dict[str, Any]
) -> dict[str, str] | None:
    """Whether the world can take a worktree mutation.

    Every refusal is made here rather than in the model, so the button is told
    before any git, teardown or environment work runs.
    """
    worktree_id = cmd.get("worktreeId", "")
    worktree = world["worktrees"].get(worktree_id)
    if worktree is None:
        return _err("unknown_id", f"No worktree {worktree_id}")

    if kind in TEARDOWN_COMMANDS and not is_worktree_closable(worktree["nato"]):
        return _err(
            "invalid",
            f"{worktree['nato']} holds the main checkout and cannot be closed",
        )

    if kind in NEEDS_OPEN_COMMANDS and worktree["isClosed"]:
        # "closed already" for a close, because that is what the user asked
        # for; the others simply have no checkout to work on.
        if kind in ("worktree.close", "worktree.forceClose"):
            return _err("invalid", f"Worktree {worktree_id} is closed already")
        return _err("invalid", f"Worktree {worktree_id} is closed")

    if kind == "worktree.sync":
        mode = cmd.get("mode", "")
        if mode not in SYNC_MODES:
            return _err("invalid", f"Unknown sync mode: {mode}")

    if kind == "worktree.mergePr":
        if not worktree.get("prNumber"):
            return _err("invalid", f"Worktree {worktree_id} has no pull request")
        # The same test the Merge button draws on. A draft reads as its own
        # state because its checks can be green.
        state = "draft" if worktree.get("prDraft") else worktree.get("prState")
        if state != "ready":
            return _err("invalid", f"The pull request is not ready to merge ({state})")
        if worktree.get("prMatch") == "differ":
            return _err(
                "invalid",
                f"The local branch differs from PR #{worktree['prNumber']}. "
                "Sync it first.",
            )

    if kind == "worktree.autoMerge":
        error = _auto_merge_error(worktree_id, worktree, cmd.get("on"))
        if error:
            return error

    if kind == "worktree.comment":
        comments = cmd.get("comments")
        if not isinstance(comments, list) or not comments:
            return _err("invalid", "No comments to post")
        for comment in comments:
            if not _is_change_comment(comment):
                return _err("invalid", "A comment is malformed")
            if not comment["body"].strip():
                return _err("invalid", "A comment is empty")

    if kind == "worktree.feedback":
        error = _feedback_error(cmd.get("feedback"))
        if error:
            return error

    if kind == "worktree.setFeedbackParams":
        error = _feedback_params_error(worktree, cmd.get("service"), cmd.get("params"))
        if error:
            return error

    # A param change needs an agent, off as well as on: see docs/dev/live-feedback.md.
    if kind in RELAY_COMMANDS and not agents_in_worktree(world, worktree_id):
        return _err("invalid", f"No agent is running in {worktree_id}")

    if kind == "worktree.env":
        action = cmd.get("action", "")
        if action not in ENV_ACTIONS:
            return _err("invalid", f"Unknown environment action: {action}")
        service = cmd.get("service")
        if service is not None:
            if not isinstance(service, str):
                return _err("invalid", "A service is named by a string")
            optional = {s["name"] for s in worktree["env"]["services"] if s["optional"]}
            if service not in optional:
                return _err("invalid", f"{service} is not an optional service")
            if action == "restart":
                return _err("invalid", "A single service cannot restart")

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

    if kind == "desk.dismiss":
        return _dismiss_error(world, cmd)

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

    if kind in WORKTREE_COMMANDS:
        return _worktree_error(world, kind, cmd)

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
        edited = [
            key for key in _wire_edited(fields, EDITABLE) if fields.get(key) is not None
        ]
        if not edited:
            return _err("invalid", "Nothing to change")
        title = fields.get("title")
        if title is not None and not str(title).strip():
            return _err("invalid", "A title is required")
        error = _check_recipe(fields)
        if error:
            return error
        comms = fields.get("comms")
        if comms is not None:
            error = _check_comm_ids(world, world["tasks"][task_id]["comms"], comms)
            if error:
                return error
        if not _is_str_list(fields.get("references", [])):
            return _err("invalid", "references is a list of strings")
        follows = fields.get("follows")
        if follows is not None:
            return _check_follows(world, task_id, follows)
        return None

    if kind == "comm.create":
        if not str(cmd.get("title", "")).strip():
            return _err("invalid", "A title is required")
        return _check_comm_fields(world, cmd)

    if kind == "comm.update":
        comm_id = cmd.get("commId", "")
        if comm_id not in world["comms"]:
            return _err("unknown_id", f"No comm {comm_id}")
        fields = cmd.get("fields") or {}
        if not any(fields.get(key) is not None for key in COMM_EDITABLE):
            return _err("invalid", "Nothing to change")
        title = fields.get("title")
        if title is not None and not str(title).strip():
            return _err("invalid", "A title is required")
        closed = fields.get("closed")
        if closed is not None and not isinstance(closed, bool):
            return _err("invalid", "closed is true or false")
        return _check_comm_fields(world, fields)

    if kind == "comm.draftUpdates":
        return None

    if kind == "comm.updateUpdate":
        return _check_comm_update(world, cmd)

    if kind == "task.delete":
        task_id = cmd.get("taskId", "")
        if task_id not in world["tasks"]:
            return _err("unknown_id", f"No task {task_id}")
        # A live agent names its task, and the world keys agents by that id, so
        # deleting the task would leave the agent reachable from no row.
        running = [
            agent["id"]
            for agent in world["agents"].values()
            if agent["taskId"] == task_id and agent["state"] != "exited"
        ]
        if running:
            return _err(
                "invalid", f"{running[0]} is running on {task_id}; stop it first"
            )
        # Deleting a task that gates others is allowed: the model rewrites
        # the dependents. See ``NotebookTaskSource.delete``.
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
        if any(cmd.get(key) is None for key in _wire_edited(cmd, CREATABLE)):
            return _err("invalid", "A field is null")
        if (
            "from" not in cmd
            and not str(cmd.get("title", "")).strip()
            and not str(cmd.get("content", "")).strip()
        ):
            # An empty title is named from the prose; with no prose there is
            # nothing to name it from. A duplicate takes its source's title.
            return _err("invalid", "A title or prose is required")
        error = _check_recipe(cmd) or _check_task_refs(world, project, cmd)
        if error:
            return error
        status = cmd.get("status")
        if status is not None and status != "template":
            return _err("invalid", f"A new task is todo or template, not {status}")
        trigger = cmd.get("trigger")
        if trigger and parse_trigger(str(trigger)) is None:
            return _err(
                "invalid",
                f"{trigger!r} is not 'gh-action/<workflow>[@<branch>] "
                "[<conclusion>,...]'",
            )
        if not _is_str_list(cmd.get("references", [])):
            return _err("invalid", "references is a list of strings")
        comms = cmd.get("comms")
        if comms is not None:
            return _check_comm_ids(world, [], comms)
        return None

    if kind == "task.promote":
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        drafts = cmd.get("drafts")
        if not isinstance(drafts, list) or not drafts:
            return _err("invalid", "Nothing to promote")
        for draft in drafts:
            if not isinstance(draft, dict) or not all(
                isinstance(draft.get(key), str) for key in ("name", "text")
            ):
                return _err("invalid", "A draft is a name and a text")
        overrides = cmd.get("overrides") or {}
        if not isinstance(overrides, dict):
            return _err("invalid", "overrides must be an object")
        unknown = [
            k for k in overrides if WIRE_RENAMES.get(k, k) not in PROMOTABLE_FIELDS
        ]
        if unknown:
            return _err("invalid", f"Not a recipe field: {unknown[0]}")
        if not isinstance(cmd.get("parent", ""), str):
            return _err("invalid", "parent must be a string")
        return _check_recipe(overrides) or _check_task_refs(world, project, cmd)

    if kind == "task.loadMany":
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        if not str(cmd.get("text", "")).strip():
            return _err("invalid", "Nothing to create")
        if not isinstance(cmd.get("defaultParent", ""), str):
            return _err("invalid", "defaultParent must be a string")
        return None

    if kind == "linear.plan":
        error = check_linear_project(world, cmd.get("project", ""))
        if error:
            return error
        if not str(cmd.get("issueId", "")).strip():
            return _err("invalid", "An issue is required")
        # The planning level, as `task.create` checks it: a null breaks the
        # notebook's write, and nothing past here checks the mode.
        if any(key in cmd and cmd[key] is None for key in ("command", "mode")):
            return _err("invalid", "A field is null")
        mode = cmd.get("mode")
        if mode is not None and mode not in MODES:
            return _err("invalid", f"No mode {mode}")
        return None

    if kind == "agent.start":
        project = cmd.get("project", "")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        if not str(cmd.get("prompt", "")).strip():
            return _err("invalid", "A prompt is required")
        mode = cmd.get("mode")
        if mode is not None and mode not in MODES:
            return _err("invalid", f"No mode {mode}")
        if cmd.get("investigate") and mode == "plan":
            # A plan hands over to an execute session, which changes code.
            return _err("invalid", "An investigation cannot plan")
        execute_model = cmd.get("executeModel")
        if execute_model:
            # Unlike `model`, which the daemon refuses if it cannot use it, a
            # non-Claude execute model is *inert*: the switch would silently
            # never happen.
            try:
                resolve_execute_model(str(execute_model))
            except ValueError as exc:
                return _err("invalid", str(exc))
        return None

    if kind == "worktree.refresh":
        # The read it asks for is the whole world's. A caller that changed one
        # pull request names its project and branch, and needs both.
        project, branch = cmd.get("project"), cmd.get("branch")
        if project is None and branch is None:
            return None
        if not (isinstance(project, str) and project) or not (
            isinstance(branch, str) and branch
        ):
            return _err("invalid", "A refresh names both a project and a branch")
        if project not in world["projects"]:
            return _err("unknown_id", f"No project {project}")
        return None

    if kind in ("operation.seen", "operation.retry"):
        operation_id = cmd.get("operationId", "")
        operation = world["operations"].get(operation_id)
        if operation is None:
            return _err("unknown_id", f"No operation {operation_id}")
        # A running operation is still moving, so neither applies to it; and
        # only one that ended badly has a step left to run.
        if operation["state"] == "running" or (
            kind == "operation.retry" and operation["state"] == "done"
        ):
            return _err("invalid", f"Operation {operation_id} is {operation['state']}")
        return None

    return _err("invalid", f"Unknown command: {kind}")
