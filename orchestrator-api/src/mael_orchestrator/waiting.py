"""What waits on the user, across every project: ``GET /api/waiting``.

A pure query over the world. It ports the UI's reading of a task node, so
``mael attention`` and the attention chip agree. The rules are in
``CONTEXT.md``: **Attention item**, **Unanswered agent**, and
``docs/specs/tasks/CONTEXT.md``: **Awaiting merge**. The UI's copy is
``nodeState`` in ``orchestrator-ui/src/protocol/progress.ts``, and the order is
``selectors/attention.ts``.
"""

from collections.abc import Iterable

from mael_domain.protocol import (
    Agent,
    Attention,
    AwaitingMergeTask,
    Task,
    UnansweredAgent,
    WaitingAttention,
    WaitingReport,
    Worktree,
    World,
)

#: The order of the attention kinds, as the attention chip cycles them.
_RANK = {"plan_review": 0, "document_review": 1, "question": 2, "permission": 3}


def waiting_on_user(world: World) -> WaitingReport:
    """The open attention items, the Unanswered agents, and the tasks Awaiting merge.

    Attention items come by kind, oldest first within a kind. Unanswered
    agents come oldest message first. Tasks Awaiting merge come by project,
    then id.

    An item on a task that is history is left out, as the UI leaves it out.
    An ``agent_exited`` item stays, though the node of a faulted agent draws
    ``exited``: the user can resume the agent.
    """
    agents = _agent_by_task(world["agents"].values())
    open_items = [
        item
        for item in world["attention"].values()
        if item["clearedAt"] is None and not _is_history_item(world, agents, item)
    ]
    unanswered: list[UnansweredAgent] = []
    awaiting: list[AwaitingMergeTask] = []
    for task in world["tasks"].values():
        agent = agents.get(task["id"])
        state = _task_wait(world, task, agent, open_items)
        if state == "unanswered" and agent is not None:
            unanswered.append(
                {
                    "agentId": agent["id"],
                    "taskId": task["id"],
                    "project": task["project"],
                    "taskTitle": task["title"],
                    "lastMessage": agent["lastMessage"],
                    "lastMessageAt": agent["lastMessageAt"],
                }
            )
        elif state == "awaiting-merge":
            awaiting.append(
                {
                    "taskId": task["id"],
                    "project": task["project"],
                    "title": task["title"],
                    "prs": task["prs"],
                }
            )
    unanswered.sort(key=lambda row: row["lastMessageAt"])
    awaiting.sort(key=lambda row: (row["project"], row["taskId"]))
    attention = sorted(
        open_items, key=lambda item: (_RANK.get(item["kind"], 4), item["raisedAt"])
    )
    return {
        "attention": [_named(world, item) for item in attention],
        "unanswered": unanswered,
        "awaitingMerge": awaiting,
    }


def _agent_by_task(agents: Iterable[Agent]) -> dict[str, Agent]:
    """Each task's agent: its first live top-level agent, else its last one.

    ``agentsByTask`` in ``orchestrator-ui/src/selectors/graph.ts``.
    """
    live: dict[str, Agent] = {}
    last: dict[str, Agent] = {}
    for agent in agents:
        if not agent["taskId"] or agent["parent"]:
            continue
        last[agent["taskId"]] = agent
        if agent["state"] != "exited":
            live.setdefault(agent["taskId"], agent)
    return {**last, **live}


def _task_wait(
    world: World, task: Task, agent: Agent | None, open_items: list[Attention]
) -> str | None:
    """``unanswered`` or ``awaiting-merge``, else ``None``.

    Follows the steps of ``nodeState`` in its order, up to each state. A task
    with an open item gives ``None``: the attention list holds that item.
    """
    if _is_history(task, agent):
        return None
    if agent and agent["state"] == "exited" and agent["exitCode"] not in (0, None):
        return None
    if any(
        item["taskId"] == task["id"] or (agent and item["agentId"] == agent["id"])
        for item in open_items
    ):
        return None
    past_build = task["status"] in ("built", "merged", "published")
    if past_build and agent is not None and _is_turning(agent):
        return None
    if task["status"] == "built":
        return "awaiting-merge"
    if agent is None or agent["state"] != "idle":
        return None
    worktree = _worktree(world, task, agent)
    if worktree is not None and worktree.get("gate"):
        # Waiting on its gate: see CONTEXT.md, Unanswered agent.
        return None
    return "unanswered" if agent["lastMessage"] else None


def _is_history(task: Task, agent: Agent | None) -> bool:
    """The task is Done or cancelled, and no agent works on it or waits."""
    terminal = task["done"] or task["status"] == "cancelled"
    return terminal and not (agent is not None and _is_turning(agent))


def _is_history_item(world: World, agents: dict[str, Agent], item: Attention) -> bool:
    task = world["tasks"].get(item["taskId"] or "")
    return task is not None and _is_history(task, agents.get(task["id"]))


def _worktree(world: World, task: Task, agent: Agent) -> Worktree | None:
    """The agent's worktree, else the open worktree of the task's branch.

    ``taskWorktree`` in ``orchestrator-ui/src/selectors/graph.ts``.
    """
    found = world["worktrees"].get(agent["worktreeId"])
    if found is not None or not task["branch"]:
        return found
    return next(
        (
            w
            for w in world["worktrees"].values()
            if not w["isClosed"]
            and (w["project"], w["branch"]) == (task["project"], task["branch"])
        ),
        None,
    )


def _is_turning(agent: Agent) -> bool:
    """The agent works, or waits on the user. ``isTurning`` in ``progress.ts``."""
    state = agent["state"]
    return state in ("processing", "delegating", "background") or state.startswith(
        "awaiting-"
    )


def _named(world: World, item: Attention) -> WaitingAttention:
    task = world["tasks"].get(item["taskId"] or "")
    agent = world["agents"].get(item["agentId"] or "")
    document = world["documents"].get(item["documentId"] or "")
    project = task["project"] if task else agent["project"] if agent else ""
    return {
        **item,
        "project": project,
        "taskTitle": task["title"] if task else "",
        "documentTitle": document["title"] if document else "",
    }
