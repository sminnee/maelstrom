"""The wire types the orchestrator server serves the web UI, and their reducer.

The entity shapes are ``orchestrator-ui/src/protocol/`` — ``entities.ts``, ``transcript.ts``,
``attention.ts``, ``documents.ts`` — as ``TypedDict``s, in the wire's own
camelCase. Pure: no I/O, no clock. :func:`apply_event` is how the server's
world changes; the normaliser and ``agent_view`` reduce with it too.
``docs/dev/orchestrator-server.md`` documents what the routes serve.
"""

from collections.abc import Iterable
from typing import Any, Literal, TypedDict, cast

Phase = Literal["shaping", "planning", "executing", "finalising"]
TaskStatus = Literal["todo", "in-progress", "blocked", "done", "cancelled", "template"]
AgentStateName = Literal[
    "idle",
    "processing",
    "delegating",
    "background",
    "awaiting-permission",
    "awaiting-question",
    "awaiting-plan-review",
    "exited",
]


class Project(TypedDict):
    id: str
    name: str
    stackTip: str
    #: Whether ``linear.team_id`` is set in the project's ``.maelstrom.yaml``.
    hasLinear: bool


#: The **Env state** in ``CONTEXT.md``.
EnvStateName = Literal["running", "partial", "stopped"]


class WorktreeServiceRow(TypedDict):
    """One per-worktree service. ``url`` is ``""`` unless it is web-facing."""

    name: str
    optional: bool
    running: bool
    url: str


class WorktreeEnv(TypedDict):
    """The worktree's env state, and its per-worktree services.

    ``services`` is the declared ones in config order, or for a Procfile project
    its tracked services and a synthetic ``app``.
    """

    state: EnvStateName
    services: list[WorktreeServiceRow]


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
    #: Commits on the open pull request, or ``None`` with no PR.
    prCommits: int | None
    #: Commits pushed with no open pull request — what "remote branch commits"
    #: means for a branch waiting on a new one. ``cli.pr_display`` reads both,
    #: so the table and the chip agree on one reading.
    pushedCommits: int | None
    prUrl: str
    #: A :data:`mael_domain.github_model.PrState`, or ``""`` with no PR.
    prState: str
    prDraft: bool
    env: WorktreeEnv
    sessionCount: int
    #: The ``cmux://`` link to the pane of the worktree's first terminal, or ``""`` with none.
    shellUrl: str


class ChangedFile(TypedDict):
    """One **Dirty file**: its path and ``git status``'s letter, ``?`` when untracked."""

    path: str
    status: str


class BranchCommit(TypedDict):
    """One commit on a worktree's branch that its base does not have."""

    sha: str
    shortSha: str
    subject: str
    author: str
    #: ISO 8601, the author date.
    date: str
    filesChanged: int


class WorktreeChanges(TypedDict):
    """``GET /api/worktrees/{id}/changes``: what the Changes tab can show."""

    dirtyFiles: list[ChangedFile]
    #: The **Base** the commits are measured against: ``main`` when the base was pruned.
    base: str
    #: Newest first.
    commits: list[BranchCommit]


DiffLineKind = Literal["context", "add", "remove"]
FileDiffStatus = Literal["added", "modified", "deleted", "renamed"]


class DiffLine(TypedDict):
    kind: DiffLineKind
    text: str
    #: ``None`` on an added line.
    oldLine: int | None
    #: ``None`` on a removed line.
    newLine: int | None


class DiffHunk(TypedDict):
    #: The ``@@ -a,b +c,d @@`` line as git wrote it.
    header: str
    lines: list[DiffLine]


class FileDiff(TypedDict):
    """One file of ``GET /api/worktrees/{id}/diff``."""

    path: str
    #: The path before a rename, else ``None``.
    oldPath: str | None
    status: FileDiffStatus
    binary: bool
    additions: int
    deletions: int
    #: The file passed the line cap, so ``hunks`` holds its first lines only.
    truncated: bool
    hunks: list[DiffHunk]


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
    executeModel: str
    content: str
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
    executeModel: str
    created: str
    updated: str
    actionable: bool


#: The ``Task`` fields a ``TaskRow`` leaves out.
TASK_DETAIL_FIELDS = ("content", "log")


def task_row(task: Task) -> TaskRow:
    return cast(TaskRow, {k: v for k, v in task.items() if k not in TASK_DETAIL_FIELDS})


class BackgroundShell(TypedDict):
    """One background shell an agent runs: the task id and its ``Bash`` description."""

    id: str
    description: str


class Agent(TypedDict):
    """``build_agent_row`` plus what links the agent to the rest of the world.

    ``parent`` is ``""`` for a top-level agent and the parent's id for a
    subagent, whose ``id`` is dotted (``X.1``). ``description`` is what the
    parent asked a subagent to do; a top-level agent has none.
    """

    id: str
    parent: str
    description: str
    state: str
    session: str
    cwd: str
    model: str
    permissionMode: str
    waitingOn: str
    lastMessage: str
    lastMessageAt: str
    #: What the agent said it is doing, from the ``<note>`` it wrote. Empty for
    #: a subagent, which writes none, and for an agent that has written none.
    lastNote: str
    #: When the agent wrote that note, ISO 8601; ``""`` until it writes one.
    lastNoteAt: str
    costUsd: float
    #: Tokens the session has consumed, summed over its turns: how much work it
    #: has done. Not how full its context is — a turn re-reads its prompt from
    #: cache each request, so this counts the same context again and again and
    #: runs past any window. :attr:`contextTokens` is the figure for that.
    totalTokens: int
    #: What this agent's subagents have consumed, summed. Disjoint from
    #: :attr:`totalTokens`, which is the agent's own; their sum is the tree's.
    #: Carried here rather than summed from the subagent entities, because an
    #: evicted subagent leaves the host's ``list`` and its tokens were spent.
    subagentTokens: int
    #: What the agent's prompt last held, off the newest ``assistant`` event: a
    #: level, not a total, so it falls when the agent compacts. This is what a
    #: reader deciding whether to compact wants.
    contextTokens: int
    taskId: str
    project: str
    worktreeId: str
    exitCode: int | None
    pendingRequestIds: list[str]
    #: The child's pid while it is alive, so the UI can name the process.
    pid: int | None
    #: The background shells running now, oldest first. Empty for a subagent.
    backgroundShells: list[BackgroundShell]


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


class DocumentGroup(TypedDict):
    """The review group a document is a member of — see ``CONTEXT.md``, "Review group".

    ``position`` is the member's place in the tag: a task set's chain order.
    """

    id: str
    title: str
    position: int


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
    group: DocumentGroup


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
    group: DocumentGroup


#: The ``Document`` fields a ``DocumentRow`` leaves out.
DOCUMENT_DETAIL_FIELDS = ("markdown",)


def group_members(documents: Iterable[Document], group_id: str) -> list[Document]:
    """The current members of review group ``group_id``, in tag order.

    A superseded member was dropped when the agent presented the tag again, so
    no verdict reaches it.
    """
    return sorted(
        (
            d
            for d in documents
            if d["group"]["id"] == group_id and d["status"] != "superseded"
        ),
        key=lambda d: d["group"]["position"],
    )


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


#: The one host entity's id. There is one agent host per server.
HOST_ID = "agent-host"


class UsageWindow(TypedDict):
    """One rolling budget the account spends against.

    ``utilization`` is quantised to whole percent by the source, so a reader
    renders what it was given and never a finer figure.
    """

    utilization: float
    #: When the window rolls over, unix seconds.
    resetsAt: int


class HostUsage(TypedDict):
    """The account's budget, as the host last heard it.

    A window nobody has reported is ``None``: nothing to say is said as
    nothing, never as zero. ``at`` is when the reading was taken — a reading
    only arrives while an agent takes a turn, so one taken long ago is stale
    and a reader needs the time to know whether to trust it.
    """

    fiveHour: UsageWindow | None
    sevenDay: UsageWindow | None
    at: str


class Host(TypedDict):
    """Whether the agent host answers, and since when it has not.

    The server never exits an agent because the host stopped answering — a
    daemon restart would otherwise flash every agent exited — so this is how a
    client learns that the agents it shows are the last known ones.

    It also carries the account's budget, which is one figure for the machine
    rather than one per agent: every agent spends the same account.
    """

    id: str
    reachable: bool
    #: When ``reachable`` last changed.
    since: str
    #: The socket the server reaches the host on.
    socket: str
    #: The account's budget, or ``None`` until a reading arrives.
    usage: HostUsage | None


class World(TypedDict):
    projects: dict[str, Project]
    worktrees: dict[str, Worktree]
    tasks: dict[str, Task]
    agents: dict[str, Agent]
    documents: dict[str, Document]
    comments: dict[str, Comment]
    attention: dict[str, Attention]
    desk: dict[str, DeskEntry]
    host: dict[str, Host]


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
    "host",
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
    "host": "host",
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
        "host": {},
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
