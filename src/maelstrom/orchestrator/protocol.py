"""The wire types the orchestrator server shares with the web UI, and their reducer.

A port of ``web/src/protocol/`` — ``entities.ts``, ``events.ts``,
``transcript.ts``, ``attention.ts``, ``documents.ts`` and ``reducer.ts``.

Pure: no I/O, no clock. :func:`apply_event` is the same reduction the browser
runs, so the server's snapshot and a client's replay of the same frames agree.
``docs/dev/orchestrator-server.md`` documents the protocol.
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
    """A task file's frontmatter plus two fields the server derives."""

    id: str
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
    phase: str
    actionable: bool


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
    phase: str
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


#: A render-ready transcript item. The variants are in ``transcript.ts``; the
#: server treats them as dicts keyed by ``type``.
TranscriptItem = dict[str, Any]


class Transcript(TypedDict):
    agentId: str
    items: list[TranscriptItem]
    #: True when the agent host's event window dropped older items.
    truncatedBefore: bool


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


class World(TypedDict):
    projects: dict[str, Project]
    worktrees: dict[str, Worktree]
    tasks: dict[str, Task]
    agents: dict[str, Agent]
    documents: dict[str, Document]
    comments: dict[str, Comment]
    attention: dict[str, Attention]


class ClientState(TypedDict):
    world: World
    transcripts: dict[str, Transcript]
    lastSeq: int
    errors: list[dict[str, Any]]


#: A server event: ``snapshot``, ``upsert``, ``remove``, ``transcript.append``,
#: ``transcript.update``, ``transcript.truncated`` or ``error``, as a plain dict.
ServerEvent = dict[str, Any]


class EventFrame(TypedDict):
    """One event as it travels: seq-stamped and replayable."""

    seq: int
    ts: str
    event: ServerEvent


ENTITY_KINDS = (
    "project",
    "worktree",
    "task",
    "agent",
    "document",
    "comment",
    "attention",
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
    }


def initial_client_state() -> ClientState:
    return {"world": empty_world(), "transcripts": {}, "lastSeq": 0, "errors": []}


def apply_server_event(state: ClientState, frame: EventFrame) -> ClientState:
    """The state after one frame, with the seq guard.

    A frame whose seq is not newer than the last one applied is dropped, which
    is what makes replay idempotent. A snapshot is the exception to the guard:
    see the snapshot epoch rule in ``docs/dev/orchestrator-server.md``.
    """
    seq = frame["seq"]
    if frame["event"].get("type") != "snapshot" and seq <= state["lastSeq"]:
        return state
    nxt = apply_event(state, frame["event"], seq)
    return {**nxt, "lastSeq": seq}


def apply_event(state: ClientState, event: ServerEvent, seq: int = 0) -> ClientState:
    """The same reduction without the seq guard, for a producer stamping its own.

    Never mutates ``state``: every changed table is copied, so the event log can
    hold earlier states by reference. A malformed event raises: it is a
    protocol bug, not a runtime condition.
    """
    kind = event.get("type")
    if kind == "snapshot":
        return {**state, "world": event["world"], "transcripts": event["transcripts"]}
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
    if kind == "transcript.append":
        agent_id = event["agentId"]
        current = state["transcripts"].get(agent_id) or {
            "agentId": agent_id,
            "items": [],
            "truncatedBefore": False,
        }
        transcript = cast(
            Transcript, {**current, "items": [*current["items"], event["item"]]}
        )
        return _with_transcript(state, agent_id, transcript)
    if kind == "transcript.update":
        agent_id = event["agentId"]
        current = state["transcripts"].get(agent_id)
        if current is None:
            return state
        items = [
            {**item, **event["patch"]} if item["id"] == event["itemId"] else item
            for item in current["items"]
        ]
        return _with_transcript(
            state, agent_id, cast(Transcript, {**current, "items": items})
        )
    if kind == "transcript.truncated":
        agent_id = event["agentId"]
        current = state["transcripts"].get(agent_id) or {
            "agentId": agent_id,
            "items": [],
            "truncatedBefore": False,
        }
        return _with_transcript(
            state, agent_id, cast(Transcript, {**current, "truncatedBefore": True})
        )
    if kind == "error":
        entry = {
            "seq": seq,
            "message": event["message"],
            "agentId": event.get("agentId"),
        }
        return {**state, "errors": [*state["errors"], entry]}
    raise ValueError(f"Unknown server event: {event!r}")


def _with_world(state: ClientState, world: World) -> ClientState:
    return {**state, "world": world}


def _with_transcript(
    state: ClientState, agent_id: str, transcript: Transcript
) -> ClientState:
    return {**state, "transcripts": {**state["transcripts"], agent_id: transcript}}


def _world_key(kind: str) -> str:
    if kind not in ENTITY_KINDS:
        raise ValueError(f"Unknown entity kind: {kind}")
    return WORLD_KEY[kind]
