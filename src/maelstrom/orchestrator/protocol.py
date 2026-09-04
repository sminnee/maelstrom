"""The wire types the orchestrator server serves the web UI, and their reducer.

The entity shapes are ``web/src/protocol/`` — ``entities.ts``, ``transcript.ts``,
``attention.ts``, ``documents.ts`` — as ``TypedDict``s, in the wire's own
camelCase. Pure: no I/O, no clock. :func:`apply_event` is how the server's
world changes; the normaliser and ``agent_view`` reduce with it too.
``docs/dev/orchestrator-server.md`` documents what the routes serve.
"""

from typing import Any, Literal, TypedDict, cast

Phase = Literal["shaping", "planning", "executing", "finalising"]
TaskStatus = Literal["todo", "in-progress", "blocked", "done", "cancelled", "template"]
AgentStateName = Literal[
    "idle",
    "processing",
    "awaiting-permission",
    "awaiting-question",
    "awaiting-plan-review",
    "exited",
]


class Project(TypedDict):
    id: str
    name: str
    stackTip: str


class Worktree(TypedDict):
    """One row of ``mael --json list-all``."""

    id: str
    project: str
    nato: str
    path: str
    branch: str
    base: str
    isClosed: bool
    dirtyFiles: int
    localCommits: int
    prNumber: int | None
    appUrl: str
    appRunning: bool
    sessionCount: int


class TaskStep(TypedDict):
    text: str
    done: bool


class TaskLogEntry(TypedDict):
    ts: str
    text: str


class Task(TypedDict):
    """A task file's frontmatter plus the fields the server derives.

    ``id`` is the wire id, ``<project>/<notebook id>``; ``notebookId`` is the
    bare id the notebook itself uses.
    """

    id: str
    notebookId: str
    project: str
    title: str
    status: str
    command: str
    mode: str
    branch: str
    parent: str
    follows: list[str]
    priority: str
    model: str
    base: str
    content: str
    steps: list[TaskStep]
    log: list[TaskLogEntry]
    created: str
    updated: str
    actionable: bool


class TaskRow(TypedDict):
    """A task as the task list carries it: every field but ``content`` and ``log``.

    The list holds every task in every project, so the two fields that hold
    prose stay behind ``GET /api/tasks/{project}/{id}``.
    """

    id: str
    notebookId: str
    project: str
    title: str
    status: str
    command: str
    mode: str
    branch: str
    parent: str
    follows: list[str]
    priority: str
    model: str
    base: str
    steps: list[TaskStep]
    created: str
    updated: str
    actionable: bool


#: The ``Task`` fields a ``TaskRow`` leaves out.
TASK_DETAIL_FIELDS = ("content", "log")


def task_row(task: Task) -> TaskRow:
    return cast(TaskRow, {k: v for k, v in task.items() if k not in TASK_DETAIL_FIELDS})


class Agent(TypedDict):
    """``build_agent_row`` plus what links the agent to the rest of the world."""

    id: str
    state: str
    session: str
    cwd: str
    model: str
    waitingOn: str
    lastMessage: str
    costUsd: float
    taskId: str
    project: str
    worktreeId: str
    exitCode: int | None
    pendingRequestId: str | None


class Attention(TypedDict):
    id: str
    kind: str
    agentId: str | None
    taskId: str | None
    documentId: str | None
    requestId: str | None
    summary: str
    raisedAt: str
    clearedAt: str | None


class Document(TypedDict):
    id: str
    agentId: str
    taskId: str
    kind: str
    title: str
    markdown: str
    version: int
    status: str
    source: dict[str, Any]


class DocumentRow(TypedDict):
    """A document as the list carries it: everything but the ``markdown``."""

    id: str
    agentId: str
    taskId: str
    kind: str
    title: str
    version: int
    status: str
    source: dict[str, Any]


#: The ``Document`` fields a ``DocumentRow`` leaves out.
DOCUMENT_DETAIL_FIELDS = ("markdown",)


def document_row(doc: Document) -> DocumentRow:
    return cast(
        DocumentRow, {k: v for k, v in doc.items() if k not in DOCUMENT_DETAIL_FIELDS}
    )


#: A render-ready transcript item. The variants are in ``transcript.ts``; the
#: server treats them as dicts keyed by ``type``.
TranscriptItem = dict[str, Any]


class Anchor(TypedDict):
    """Where a comment sits: a W3C TextQuoteSelector plus cached offsets."""

    quote: str
    prefix: str
    suffix: str
    start: int
    end: int


class Comment(TypedDict):
    id: str
    documentId: str
    version: int
    #: ``"user"`` or an agent id.
    author: str
    anchor: Anchor
    body: str
    resolved: bool
    createdAt: str


class DeskEntry(TypedDict):
    """One task on the desk: its wire id, and when the user put it there."""

    id: str
    addedAt: str


class World(TypedDict):
    projects: dict[str, Project]
    worktrees: dict[str, Worktree]
    tasks: dict[str, Task]
    agents: dict[str, Agent]
    documents: dict[str, Document]
    comments: dict[str, Comment]
    attention: dict[str, Attention]
    desk: dict[str, DeskEntry]


class ClientState(TypedDict):
    """The world, and nothing per-agent: transcripts live in their own logs."""

    world: World


#: A server event: ``upsert``, ``remove``, ``transcript.append``,
#: ``transcript.update`` or ``transcript.truncated``, as a plain dict.
ServerEvent = dict[str, Any]


ENTITY_KINDS = (
    "project",
    "worktree",
    "task",
    "agent",
    "document",
    "comment",
    "attention",
    "desk",
)

#: Which ``World`` key each entity kind lives under.
WORLD_KEY = {
    "project": "projects",
    "worktree": "worktrees",
    "task": "tasks",
    "agent": "agents",
    "document": "documents",
    "comment": "comments",
    "attention": "attention",
    "desk": "desk",
}


def empty_world() -> World:
    return {
        "projects": {},
        "worktrees": {},
        "tasks": {},
        "agents": {},
        "documents": {},
        "comments": {},
        "attention": {},
        "desk": {},
    }


def initial_client_state() -> ClientState:
    return {"world": empty_world()}


def state_with(world: World) -> ClientState:
    return {"world": world}


def apply_event(state: ClientState, event: ServerEvent) -> ClientState:
    """The state after one event.

    Never mutates ``state``: every changed table is copied, so a caller may
    hold an earlier state by reference. A malformed event raises: it is a
    protocol bug, not a runtime condition.
    """
    kind = event.get("type")
    if kind == "upsert":
        key = _world_key(event["kind"])
        entity = event["entity"]
        table = {**state["world"][key], entity["id"]: entity}
        return _with_world(state, cast(World, {**state["world"], key: table}))
    if kind == "remove":
        key = _world_key(event["kind"])
        table = dict(state["world"][key])
        table.pop(event["id"], None)
        return _with_world(state, cast(World, {**state["world"], key: table}))
    if kind in ("transcript.append", "transcript.update", "transcript.truncated"):
        # Not the world's business: each agent's TranscriptLog keeps these.
        return state
    raise ValueError(f"Unknown server event: {event!r}")


def _with_world(state: ClientState, world: World) -> ClientState:
    return {**state, "world": world}


def _world_key(kind: str) -> str:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"Unknown entity kind: {kind}")
    return WORLD_KEY[kind]
