"""The orchestrator server: the world, its sources, and the clients watching it.

The service layer, and the only asyncio orchestration in the package. It owns
the :class:`~mael_orchestrator.world.WorldState`, polls the task and
worktree sources, keeps one attach stream per agent against the agent host,
answers commands, and tells the notice and transcript hubs what changed.
:mod:`~mael_orchestrator.routes` puts it on the network; what the routes
serve is documented in ``docs/dev/orchestrator-server.md``.
"""

import asyncio
import functools
import inspect
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from concurrent.futures import Executor
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from mael_agent.agent_wire import (
    AGENT_DETAIL,
    AGENT_EXITED,
    AUTO,
    BACKLOG_END,
    SEQ_KEY,
    TRUNCATED,
    build_resume_payload,
    build_start_payload,
    is_compact_command,
)
from mael_agent.harness_model import HARNESS_CODEX, resolve_model_reference
from mael_agent.host_ids import PARENT_URL_ENV, IdSpace, owner_of
from mael_common.image import MAX_BYTES, is_image
from mael_common.shell import run_cmd_async
from mael_common.util import now_iso
from mael_domain import comms as comms_model
from mael_domain import sarif, task_documents
from mael_domain.agent_store import (
    AGENT_ENDED,
    AgentStore,
    InMemoryMilestoneStore,
    MilestoneStore,
)
from mael_domain.attachments import attachment_urls
from mael_domain.cmux.mael_layout import remote_workspace_name, workspace_name
from mael_domain.comm_store import Comm as DomainComm
from mael_domain.comm_store import (
    CommStore,
    CommUpdate,
    CommUpdateStore,
    InMemoryCommStore,
    InMemoryCommUpdateStore,
)
from mael_domain.desk_store import DeskStore, InMemoryDeskStore
from mael_domain.document_tags import (
    DRAFT_KIND,
    FINAL_STAGE,
    CompactRequest,
    ParamSet,
)
from mael_domain.external_event import (
    CHANGES_COMMENTED,
    DOCUMENT_APPROVED,
    DOCUMENT_CHANGES_REQUESTED,
    JIG,
    JIG_CONTROLS,
    JIG_MONKEYPATCH,
    JIG_PARAM_SET,
    JIG_PICK,
    ORCHESTRATOR,
    ORCHESTRATOR_UI,
    SESSION_COMPACTED,
    SESSION_NOT_COMPACTED,
    ExternalEvent,
    gate_event,
    ledger_without,
    pr_events,
)
from mael_domain.external_event import render as render_event
from mael_domain.feedback import (
    JIG_DRAFTS,
    format_controls,
    format_instruction,
    format_monkeypatch,
    format_param_set,
    format_pick,
)
from mael_domain.file_registry import FileRegistry
from mael_domain.github_model import PrStatus, RateLimited, parse_pr_ref
from mael_domain.integrations.errors import IntegrationError
from mael_domain.landing import Landings
from mael_domain.landing_store import StepEvent
from mael_domain.list_all import project_repo_url
from mael_domain.merge_request import (
    MergeRequest,
    MergeRequestError,
    merge_request_json,
)
from mael_domain.merge_request_store import (
    BuildStore,
    InMemoryBuildStore,
    InMemoryMergeRequestStore,
    MergeRequestStore,
)
from mael_domain.normalise import (
    Milestone,
    NormaliseContext,
    Normalised,
    apply_agent_detail,
    close_partial_message,
    context_for_agent,
    mark_exited,
    normalise_gap,
    normalise_milestone,
    normalise_stream_event,
    revive_agent,
)
from mael_domain.operation_store import (
    InMemoryOperationStore,
    OperationRecord,
    OperationStore,
)
from mael_domain.operations import DONE
from mael_domain.pr_ledger_store import InMemoryPrLedgerStore, PrLedgerStore
from mael_domain.protocol import (
    HOST_ID,
    Agent,
    Comm,
    Document,
    GateJob,
    GateMetrics,
    Host,
    HostUsage,
    MetricsSample,
    Operation,
    RemoteHostState,
    ServerEvent,
    Task,
    TranscriptItem,
    Worktree,
    WorktreeMergeRequest,
    World,
    group_members,
)
from mael_domain.shared_dir import (
    agent_prompt_file,
    core_plugin_dir,
    investigation_prompt_file,
)
from mael_domain.ssh_target import SshTarget
from mael_domain.system_info import SystemPaths, can_update
from mael_domain.task import (
    CODEX_PLAN_FILE,
    STATUS_DONE,
    default_branch,
    expand_task_dir,
    mode_for_command,
)
from mael_domain.task import permission_mode_for as model_permission_mode
from mael_domain.task_attachments import (
    InMemoryTaskAttachmentTable,
    TaskAttachment,
    TaskAttachmentTable,
    Version,
    task_key,
)
from mael_domain.task_documents import (
    PLAN_NAME,
    ReviewGroup,
    attach,
    set_status,
)
from mael_domain.task_launch import LaunchBlocked, check_not_live, check_synced
from mael_domain.task_metadata_generator import lead_with_number
from mael_domain.worktree import WorktreeSetup
from mael_domain.worktree_changes import format_change_comments
from mael_domain.worktree_model import extract_project_name, get_worktree_folder_name
from mael_domain.worktree_steps import Step, StepHook, StepOutcome, run_sequence

from . import desk as desk_model
from . import linear_source
from .build_queue import BuildHost, BuildQueue, Run, run_process
from .clients import normal_ip
from .daemon_bridge import (
    EARLIER_SESSIONS,
    PARENT_URL,
    TASK_ID,
    AsyncDaemonClient,
    ended_agent_row,
)
from .desk import DeskTable, desk_id_for_agent, desk_id_for_task, split_desk_id
from .gate_source import GateSourceError
from .gate_table import GateTable
from .hubs import (
    COALESCE_SECS,
    METRICS_QUEUE_LIMIT,
    WS_QUEUE_LIMIT,
    MetricsHub,
    NoticeHub,
    TranscriptHub,
)
from .language_servers import LanguageServers
from .live_feedback import LiveFeedbackHub
from .loop_watch import LoopWatch
from .machine_metrics import METRICS_PERIOD_SECS, MachineReading
from .notices import notices_for
from .operations import (
    Busy,
    ChainHook,
    OperationHook,
    OperationPlan,
    OperationRunner,
    worktree_key,
)
from .remote_hosts import MERGED_KINDS, HostUnreachable, RemoteHost, owns
from .sources import (
    CloseBlocked,
    LaunchClaim,
    TaskHome,
    TaskHomes,
    TaskSource,
    TaskSourceRemote,
    WorktreeOpener,
    WorktreeSource,
)
from .transcript_log import (
    TRANSCRIPT_RING,
    TranscriptFrame,
    TranscriptLog,
    TranscriptPartial,
    TranscriptSnapshot,
)
from .validate import (
    DISMISS_CHAINS,
    WAIT_ITEM_FOR_STATE,
    agents_in_worktree,
    check_linear_project,
    live_agent_of,
    validate_command,
)
from .world import WorldState
from .world_build import (
    AgentLink,
    agent_entity,
    comm_entity,
    diff_kind,
    document_entity,
    gate_job_entity,
    host_usage,
    link_agent,
    linked_tasks,
    merge_request_entity,
    parse_agent_state,
    row_totals,
    split_task_key,
    with_gates,
    with_merge_requests,
)
from .world_build import task_key as wire_task_key

log = logging.getLogger(__name__)

#: How often the notebook's version is checked.
TASK_POLL_SECS = 2.0
#: How often ``list-all`` is re-read. It shells out per worktree, so not often.
#:
#: The read also asks GitHub about the branches on the desk, and GraphQL is
#: charged by node count against a budget that refills hourly. A minute is slow
#: enough to leave that budget for the work, and fast enough that a PR chip and
#: a dev-env link still appear on their own.
WORKTREE_POLL_SECS = 60.0
#: How often the agent host's ``list`` is reconciled against the world.
AGENT_POLL_SECS = 2.0
#: The first wait before a failed gate watch starts again. It doubles on each
#: failure in a row, up to ``GATE_BACKOFF_MAX_SECS``.
GATE_BACKOFF_SECS = 5.0
GATE_BACKOFF_MAX_SECS = 60.0
#: How many consecutive failed agent polls make the host unreachable. Two, so
#: the one dropped connection a daemon restart costs does not raise the banner.
HOST_UNREACHABLE_AFTER = 2
#: How long adopting an agent waits for its replayed backlog to end.
BACKLOG_TIMEOUT_SECS = 5.0
#: How long a subagent's stream stays attached after its last transcript
#: socket closes. A tab that closes and reopens within it keeps the watch.
CHILD_DETACH_SECS = 5.0
#: How long a Compaction hold lasts when no outcome comes. Matches the UI's compact backstop.
COMPACT_BACKSTOP_SECS = 300.0


def _kilo(tokens: int) -> str:
    """A token count as the replies write it: ``123k``."""
    return f"{round(tokens / 1000)}k"


def _refused(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


async def _start_update_job() -> subprocess.CompletedProcess:
    """``mael self update --detach``, from the venv this server runs in."""
    mael = Path(sys.executable).parent / "mael"
    return await run_cmd_async(
        [str(mael), "self", "update", "--detach"], check=False, quiet=True, timeout=60
    )


def task_document_refusal(exc: Exception) -> dict[str, Any]:
    """A Task document rule's refusal: not found is ``unknown_id``, else ``invalid``."""
    code = (
        "unknown_id" if isinstance(exc, task_documents.DocumentNotFound) else "invalid"
    )
    return _refused(code, str(exc))


def _finding_json(finding: sarif.Finding) -> dict[str, Any]:
    """One finding of a SARIF document; ``key`` is the fragment that links it."""
    return {
        "key": finding.key,
        "label": finding.label,
        "level": finding.level,
        "rule": finding.rule,
        "cwe": finding.cwe,
        "title": finding.title,
        "file": finding.file,
        "line": finding.line,
        "symbol": finding.symbol,
    }


def _version_json(version: Version) -> dict[str, Any]:
    return {
        "version": version.version,
        "title": version.title,
        "createdAt": version.created_at,
        "reviewStatus": version.review_status,
    }


#: The host route that runs each worktree end of a **Dismiss** chain, as a
#: method and a path after ``/api/worktrees/{id}``.
_HOST_WORKTREE_ROUTES: dict[str, tuple[str, str]] = {
    "worktree.close": ("POST", "/close"),
    "worktree.forceClose": ("POST", "/force-close"),
    "worktree.trash": ("POST", "/trash"),
    "worktree.remove": ("DELETE", ""),
}


async def _nothing() -> None:
    """The refresh of an operation whose change another server makes."""


#: Each command that runs as an operation: its kind, the ``WorktreeSource``
#: callable, the refusal of a server without one, and its words — doing, done,
#: and the start of a fault. ``worktree.forceClose`` is the UI's **Shelve**.
_OPERATION_COMMANDS: dict[str, tuple[str, str, str, str, str, str]] = {
    "worktree.close": (
        "close",
        "close",
        "This server cannot close worktrees",
        "Closing",
        "Closed",
        "Could not close the worktree",
    ),
    "worktree.forceClose": (
        "shelve",
        "force_close",
        "This server cannot close worktrees",
        "Shelving",
        "Shelved",
        "Could not close the worktree",
    ),
    "worktree.trash": (
        "trash",
        "trash",
        "This server cannot trash worktrees",
        "Trashing",
        "Trashed",
        "Could not trash the worktree",
    ),
    "worktree.remove": (
        "delete",
        "remove",
        "This server cannot remove worktrees",
        "Deleting",
        "Deleted",
        "Could not remove the worktree",
    ),
    "worktree.sync": (
        "sync",
        "sync",
        "This server cannot sync worktrees",
        "Syncing",
        "Synced",
        "Could not sync the worktree",
    ),
    "worktree.mergePr": (
        "merge",
        "merge",
        "This server cannot merge pull requests",
        "Merging",
        "Merged",
        "Could not merge the pull request",
    ),
    # The words are the action's: see _ENV_WORDS.
    "worktree.env": (
        "env",
        "env",
        "This server cannot start or stop environments",
        "",
        "",
        "Could not change the environment",
    ),
}


def _screenshot(worktree_path: str, name: Any) -> str | None:
    """The path of the jig's screenshot ``name``, or ``None`` when it is no usable image.

    The daemon refuses a whole turn over one bad attachment, so a screenshot
    it would refuse is left off here, and the words go alone. Validation holds
    ``name`` to one path segment.
    """
    if name is None:
        return None
    path = Path(worktree_path) / JIG_DRAFTS / name
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return str(path) if len(data) <= MAX_BYTES and is_image(data) else None


#: What an environment operation reads, by action: while it runs, and once done.
_ENV_WORDS = {
    "start": ("Starting", "Started"),
    "stop": ("Stopping", "Stopped"),
    "restart": ("Restarting", "Restarted"),
}

#: What each kind of **Feedback** becomes, from the branch and the feedback.
#: Validation has checked the fields each one reads. An instruction stays a
#: plain message: the agent prompt keys on its "From the jig for" opening.
_FEEDBACK_FORMATTERS: dict[
    str, Callable[[str, dict[str, Any]], ExternalEvent | str]
] = {
    "monkeypatch": lambda branch, feedback: ExternalEvent(
        JIG,
        JIG_MONKEYPATCH,
        {"branch": branch},
        format_monkeypatch(
            feedback["css"],
            feedback.get("note"),
            screenshot="screenshot" in feedback,
        ),
    ),
    "instruction": lambda branch, feedback: format_instruction(
        branch,
        feedback["text"],
        feedback.get("element"),
        screenshot="screenshot" in feedback,
    ),
    "pick": lambda branch, feedback: ExternalEvent(
        JIG,
        JIG_PICK,
        {"branch": branch},
        format_pick(
            branch,
            feedback["scope"],
            feedback["element"],
            feedback.get("note"),
            screenshot="screenshot" in feedback,
        ),
    ),
    "controls": lambda branch, feedback: ExternalEvent(
        JIG,
        JIG_CONTROLS,
        {"branch": branch},
        format_controls(
            branch,
            feedback["title"],
            feedback["changes"],
            feedback.get("css"),
            feedback.get("note"),
            screenshot="screenshot" in feedback,
        ),
    ),
}


#: The steps that launch a task or start an agent, in order. They hold what
#: they learn in memory, so a retry runs them all again.
LAUNCH_STEPS = ("open_worktree", "write_env", "start_agent")
#: The step of a launch on a **Remote host**: the host runs the open, the
#: ``.env`` and the start as its own operation.
REMOTE_LAUNCH_STEPS = ("start_on_host",)

#: The keys of a start spec: what ``POST /api/agents`` takes, and what a
#: delegated launch sends. See "Delegation" in ``docs/dev/remote-hosts.md``.
START_SPEC_KEYS = (
    "project",
    "branch",
    "prompt",
    "mode",
    "model",
    "executeModel",
    "session",
    "env",
    "taskId",
    "parentUrl",
)


@dataclass
class _Launching:
    """What a launch kind learns as its steps run."""

    project: str
    task_id: str = ""
    branch: str = ""
    claim: LaunchClaim | None = None
    setup: WorktreeSetup | None = None
    agent_id: str = ""
    #: A remote host took the start, so the claim stays even if no agent id
    #: comes back: the host may still start the agent.
    handed_off: bool = False
    #: The launch starts a **New session**, not the task's current one.
    new_session: bool = False


def _opened(setup: WorktreeSetup) -> str:
    """The log line of an opened worktree: ``Reused alpha.``"""
    return f"{setup.action.capitalize()} {setup.name}."


def _branch_stand_in(task_id: str, parent: str) -> str:
    """The branch a create writes for an empty branch, until naming replaces it.

    The first task under a Linear parent takes ``feat/<n>``; any other task with
    no branch to inherit takes ``task/<id>``.
    """
    linear = parent if parent.startswith("linear.") else ""
    return default_branch(split_task_key(task_id)[1], linear)


def _first_line(content: str, limit: int = 60) -> str:
    """A task's stand-in title: its prose's first line, cut to ``limit``.

    Validation refuses a create with neither a title nor prose, so there is a line.
    """
    return content.strip().splitlines()[0][:limit].rstrip()


async def _run_own_steps(steps: list[Step], hook: StepHook) -> None:
    """Run the server's own steps of an operation. A refusal ends it refused."""
    ran = await run_sequence(steps, announce=lambda line: None, hook=hook)
    if not ran.ok:
        raise CloseBlocked(ran.blocked or "A step refused")


async def _github_call_as_a_step(
    name: str, call: Callable[[], Awaitable[None]], hook: StepHook
) -> None:
    """Run one GitHub call on a pull request as an operation of one step.

    A merge is one call, not a sequence. It is still a
    step, so the console reads it as it reads every other operation.
    """

    async def step() -> StepOutcome:
        try:
            await call()
        except CloseBlocked as exc:
            return StepOutcome(blocked=str(exc))
        return StepOutcome()

    ran = await run_sequence(
        [Step(name=name, run=step)], announce=lambda line: None, hook=hook
    )
    if not ran.ok:
        raise CloseBlocked(ran.blocked or "GitHub did not answer")


def _totals_of(agent: Agent) -> tuple[int, int, float]:
    """What an agent has spent: own tokens, subagent tokens, dollars."""
    return agent["totalTokens"], agent["subagentTokens"], agent["costUsd"]


def _stage_total(rows: list[dict[str, Any]]) -> int:
    """What the agent had spent by its last recorded stage, own and subagent.

    The last row's cumulative figure rather than a sum of the deltas: the
    snapshot is the reading the host gave, where a sum could drift.
    """
    last = rows[-1]
    return int(last["own_total"]) + int(last["sub_total"])


def _is_task_set(members: list[Document]) -> bool:
    """Whether approving this review group should promote draft files.

    A task set is a group of ``tasks`` documents, each minted from a draft file.
    """
    return all(
        d["kind"] == DRAFT_KIND and d["source"].get("type") == "draft_file"
        for d in members
    )


class AgentWatch:
    """One agent's attach stream and the normaliser state it feeds."""

    def __init__(self, agent_id: str, ctx: NormaliseContext) -> None:
        self.agent_id = agent_id
        self.ctx = ctx
        self.task: asyncio.Task[None] | None = None
        #: Set once the backlog marker has arrived, or the stream ended first.
        self.caught_up = asyncio.Event()
        #: A milestone waiting for its turn to end. The agent writes the marker
        #: on an ``assistant`` event, but the turn's tokens only reach the
        #: world when its ``result`` lands — so the snapshot waits for that.
        self.pending_milestone: Milestone | None = None
        #: A compaction request waiting for its turn to end, for the reason a
        #: milestone waits: the turn's last occupancy reading, and an idle
        #: agent to send ``/compact`` to.
        self.pending_compact: CompactRequest | None = None


class CompactHold:
    """One agent's **Compaction hold**: the turns told to it while it compacts.

    See ``docs/dev/orchestrator-server.md``, "The compaction hold".
    """

    def __init__(self, agent_id: str, *, reply: bool, sent: bool) -> None:
        self.agent_id = agent_id
        #: The agent asked, so it is told how the compact went.
        self.reply = reply
        #: ``/compact`` was sent and its outcome is not in yet. A boundary
        #: says it compacted; a refusal says it did not.
        self.sent = sent
        #: Each held turn and its attachments, in the order they were told.
        self.held: list[tuple[ExternalEvent | str, list[dict[str, str]] | None]] = []
        self.backstop: asyncio.TimerHandle | None = None
        #: Its drain has started. It still takes turns, which the drain
        #: delivers, but a new opener gets a hold of its own.
        self.releasing = False


class Cursor:
    """Where a watch got to on the host's stream: the seq, and the life it belongs to."""

    def __init__(self) -> None:
        self.seq = 0
        self.epoch = ""


class Orchestrator:
    """The world, its sources, and the clients watching it."""

    def __init__(
        self,
        tasks: TaskSource,
        worktrees: WorktreeSource,
        daemon: AsyncDaemonClient,
        *,
        desk: DeskStore | None = None,
        milestones: MilestoneStore | None = None,
        agent_records: AgentStore | None = None,
        document_store: TaskAttachmentTable | None = None,
        landings: Landings | None = None,
        comms: CommStore | None = None,
        comm_updates: CommUpdateStore | None = None,
        operations: OperationStore | None = None,
        pr_ledger: PrLedgerStore | None = None,
        merge_requests: MergeRequestStore | None = None,
        builds: BuildStore | None = None,
        build_host: BuildHost | None = None,
        build_run: Run | None = None,
        clock: Callable[[], str] = now_iso,
        executor: Executor | None = None,
        worktree_executor: Executor | None = None,
        task_poll: float = TASK_POLL_SECS,
        worktree_poll: float = WORKTREE_POLL_SECS,
        agent_poll: float = AGENT_POLL_SECS,
        gate_watch: Callable[[], AsyncGenerator[dict[str, Any]]] | None = None,
        gate_backoff: float = GATE_BACKOFF_SECS,
        read_machine: Callable[[], MachineReading] | None = None,
        metrics_period: float = METRICS_PERIOD_SECS,
        metrics_queue_limit: int = METRICS_QUEUE_LIMIT,
        notice_coalesce: float = COALESCE_SECS,
        transcript_ring: int = TRANSCRIPT_RING,
        ws_queue_limit: int = WS_QUEUE_LIMIT,
        child_detach: float = CHILD_DETACH_SECS,
        compact_backstop: float = COMPACT_BACKSTOP_SECS,
        language_servers: LanguageServers | None = None,
        system: SystemPaths | None = None,
        start_update: Callable[[], Awaitable[subprocess.CompletedProcess]]
        | None = None,
        remote_hosts: Mapping[str, RemoteHost] | None = None,
        opener: WorktreeOpener | None = None,
        ids: IdSpace | None = None,
        own_url: str = "",
        own_name: str = "",
    ) -> None:
        self.tasks = tasks
        #: Where each agent's task work goes: see "The task seam" in
        #: ``docs/dev/remote-hosts.md``.
        self.homes = TaskHomes(tasks)
        #: Opens the worktree an agent starts in, and writes its ``.env``.
        self.opener = opener if opener is not None else WorktreeOpener()
        #: This server's ids: each one it makes carries the prefix.
        self.ids = ids if ids is not None else IdSpace()
        #: This server's own URL: the ``parentUrl`` of a launch it delegates.
        self.own_url = own_url
        #: The name a user reads for this server, on the ``host`` entity.
        self.own_name = own_name
        #: Each **Remote host** in ``hosts:``, by name: the link, the merged
        #: slices, and the session a delegated launch and a forward use.
        self.remote_hosts: Mapping[str, RemoteHost] = remote_hosts or {}
        self.worktrees = worktrees
        self.daemon = daemon
        self.desk = desk if desk is not None else InMemoryDeskStore()
        #: The ledger of what each agent had spent at each stage of its work,
        #: written when an agent's message carries a ``<milestone>``. The
        #: normaliser reads the tag; the write is here, because the normaliser
        #: is a pure function and the database is the server's.
        self.milestones = (
            milestones if milestones is not None else InMemoryMilestoneStore()
        )
        #: Every Agent record. The world reads the ended ones once, at start,
        #: so a task keeps its earlier agents through a restart.
        self.agent_records = agent_records
        #: The Task documents — see ``CONTEXT.md``, "Task document". The world
        #: reads them once, at start.
        self.document_store = (
            document_store
            if document_store is not None
            else InMemoryTaskAttachmentTable()
        )
        #: Each task's landing — see ``CONTEXT.md``, "Landing". Synced in the
        #: worktree read.
        self.landings = landings if landings is not None else Landings()
        #: The comms — see ``CONTEXT.md``, "Comm". Polled on the task tick.
        self.comms = comms if comms is not None else InMemoryCommStore()
        #: Each comm's updates — see ``CONTEXT.md``, "Comm update". The server
        #: is their one writer.
        self.comm_updates = (
            comm_updates if comm_updates is not None else InMemoryCommUpdateStore()
        )
        #: The comm rows, every task step and every comm update, held so
        #: ``_apply`` can derive a comm without a read. See
        #: ``docs/dev/orchestrator-server.md``, "Comm updates".
        self._comm_rows: dict[str, DomainComm] = {}
        self._steps: list[StepEvent] = []
        self._comm_update_rows: dict[str, CommUpdate] = {}
        #: The comm store revision the last comm read reached, or ``None``
        #: before the first whole read.
        self._comm_cursor: int | None = None
        #: Bumped on every comm change published; the comm list's ETag.
        self.comm_revision = 0
        #: What each Registered PR's agents were last told. See
        #: orchestrator-server.md, "External events".
        self.pr_ledger = pr_ledger if pr_ledger is not None else InMemoryPrLedgerStore()
        #: The Registered PRs that had a recipient at the last world change, so
        #: a new one is checked against what the cache already holds.
        self._watched_prs: set[str] = set()
        #: One PR check at a time, so two reads cannot both tell the same news.
        self._pr_watch_lock = asyncio.Lock()
        #: Registered PRs a read found closed without a merge. GitHub stops
        #: answering for one, so only this keeps the poll from waiting on it.
        self._closed_prs: set[str] = set()
        #: The PR checks in flight, kept so a stop can cancel them.
        self._pr_watches: set[asyncio.Task[None]] = set()
        self.clock = clock
        self.executor = executor
        #: Kept for a worktree operation that is not a sequence. The five that
        #: are take the pool directly, because it is each blocking step that
        #: needs a thread — see ``cli.build_orchestrator``.
        self.worktree_executor = worktree_executor or executor
        self.state = WorldState()
        #: Each worktree's **Language servers**, for the definition route. The
        #: default knows none; ``cli.build_orchestrator`` passes the real list.
        self.language_servers = (
            language_servers if language_servers is not None else LanguageServers()
        )
        #: The install's paths, for ``GET /api/system``. ``None`` answers
        #: ``not_implemented``: a test that does not need the route skips it.
        self.system = system
        #: Starts an **Update job** and returns at once. The default runs
        #: ``mael self update --detach`` from this server's own venv: the
        #: import fence keeps the CLI out of the server.
        self.start_update = start_update or _start_update_job
        #: The stops of a closed worktree's servers, kept so a stop can await them.
        self._server_stops: set[asyncio.Task[None]] = set()
        #: The operations — see ``CONTEXT.md``, "Operation". Loaded at start;
        #: each one is a background task this runner owns.
        self.operations = OperationRunner(
            operations if operations is not None else InMemoryOperationStore(),
            self._apply,
            clock,
            self.ids,
        )
        #: The **Build queue**: the Merge requests and their Builds. Started
        #: after the agents are read, because a re-tell may resume one.
        self.build_queue = BuildQueue(
            merge_requests
            if merge_requests is not None
            else InMemoryMergeRequestStore(),
            builds if builds is not None else InMemoryBuildStore(),
            operations=self.operations,
            tell=self._tell_chaperone,
            host=build_host or BuildHost(Path.home() / "Projects"),
            clock=clock,
            on_change=self._publish_merge_requests,
            run=build_run or run_process,
        )
        #: Every file an agent named, by the id that stands for it.
        self.files = FileRegistry()
        #: Minted per server life, so a client can tell a restart from a reconnect.
        self.epoch = uuid.uuid4().hex[:8]
        self.notices = NoticeHub(notice_coalesce)
        self.notices.on_first_subscriber = self._watch_arrived
        #: The catch-up read an arriving client triggers, kept so it can be
        #: cancelled at stop and awaited by a test.
        self._catch_up: asyncio.Task[None] | None = None
        #: The read an event triggers, kept for the same two reasons. Separate
        #: from the catch-up: an arriving client and a new pull request can
        #: land together, and neither should drop the other's task.
        self._event_read: asyncio.Task[None] | None = None
        #: Bumped on every task change published; the task list's ETag.
        self.task_revision = 0
        #: One transcript per agent seen, fed by the normaliser and served to
        #: the transcript sockets. It outlives the agent's exit: a resume keeps
        #: the agent id and its history.
        self._transcripts: dict[str, TranscriptLog] = {}
        self._transcript_ring = transcript_ring
        self.transcripts = TranscriptHub(ws_queue_limit, on_idle=self._transcript_idle)
        #: Set by an agent's `<live-feedback>` marker, by the UI, and by the page.
        self.live_feedback = LiveFeedbackHub(on_change=self._live_feedback_changed)
        self._child_detach = child_detach
        self._compact_backstop = compact_backstop
        #: Per agent, the Compaction hold open on it; it outlives a resume's new watch.
        self._compact_holds: dict[str, CompactHold] = {}
        #: The drains of released holds, held so a stop can cancel them.
        self._hold_releases: set[asyncio.Task[None]] = set()
        #: Per subagent, the detach waiting for its grace to pass.
        self._detaches: dict[str, asyncio.Task[None]] = {}
        self._task_poll = task_poll
        self._worktree_poll = worktree_poll
        self._agent_poll = agent_poll
        #: Starts tangier's gate watch; ``None`` keeps the world's gate jobs empty.
        self._gate_watch = gate_watch
        self._gate_backoff = gate_backoff
        #: Reads the machine for the metrics sampler; ``None`` runs no sampler.
        self._read_machine = read_machine
        self._metrics_period = metrics_period
        #: Whether the last machine read failed, so a lasting failure logs once.
        self._metrics_failed = False
        #: The last samples, served to the metrics sockets. In memory only.
        self.metrics = MetricsHub(queue_limit=metrics_queue_limit)
        #: The unfinished jobs the watch holds. See ``CONTEXT.md``, "Gate watch".
        self._gate_table = GateTable()
        #: The jobs whose end was told, or is being told, so a repeated end
        #: tells once.
        self._gate_told: set[str] = set()
        #: The ended jobs whose event no agent took, by job id. The next
        #: agent poll that the host answers sends each again.
        self._gates_due: dict[str, dict[str, Any]] = {}
        #: The PRs whose events no agent took, by URL. Sent again likewise.
        self._prs_due: set[str] = set()
        #: The due PRs with a redelivery in flight.
        self._prs_again: set[str] = set()
        #: The agents that asked to end the session, until the turn ends. On
        #: the server, not on the watch: a lost stream must not drop it.
        self._pending_ends: set[str] = set()
        #: Whether the last watch failed, so a lasting failure logs once.
        self._gate_watch_failed = False
        #: The gate tells in flight, kept so a stop can cancel them.
        self._gate_tells: set[asyncio.Task[None]] = set()
        #: Loop time the last read that asked GitHub about every branch
        #: finished, or ``None`` before the first. ``GET /api/worktrees?max_age=``
        #: reads against it: a poll asks about the desk's branches only.
        self._every_branch_read_at: float | None = None
        #: Loop time the last arrival-triggered read finished, or ``None``
        #: while no client has been read for. Only arrivals are spaced by it:
        #: the poll's own tick is already spaced by its sleep. The poll
        #: interval is the arrival floor by design, so tuning one tunes both.
        self._served_at: float | None = None
        self._task_version: Any = _NEVER
        #: The table revision the task poll last read up to, or ``None`` while
        #: it has none. A source whose version is not a table revision never
        #: sets it, which is what keeps such a source on the whole-notebook
        #: read rather than on a cursor it cannot honour.
        self._task_cursor: int | None = None
        self._worktree_read = asyncio.Lock()
        self._pollers: list[asyncio.Task[None]] = []
        self._started = asyncio.Event()
        self._watches: dict[str, AgentWatch] = {}
        #: Per agent, the last seq and epoch a watch read. It outlives the
        #: watch, so a re-attach after a dropped stream asks for what it
        #: missed rather than the whole window again.
        self._cursors: dict[str, Cursor] = {}
        #: Per agent, the highest transcript-item number handed out so far.
        #: Outlives the agent's exit: a resume reuses the agent id, so the
        #: mark has to survive it or the revived agent re-mints old ids.
        self._item_seeds: dict[str, int] = {}
        #: Consecutive agent polls the host answered with an error. The host
        #: is reported unreachable on the second, so one dropped connection
        #: during a daemon restart does not raise the banner.
        self._host_failures = 0
        #: Per **Delegated agent**, the session last sent to its **Task home**.
        self._sessions_sent: dict[str, str] = {}
        #: Per agent, the session whose append failed last, so one outage is
        #: logged once.
        self._sessions_refused: dict[str, str] = {}
        #: The loop-stall watchdog, started with the pollers.
        self.loop_watch = LoopWatch(now=clock)

    # -- running --

    async def start(self) -> None:
        """Read every source once, then keep them fresh in the background."""
        await self.refresh_tasks()
        await self.refresh_comms()
        await self._load_documents()
        await self._load_desk()
        await self.operations.load()
        if self.worktrees.prs is not None:
            self.worktrees.prs.on_change = self._pr_changed
        await self.refresh_worktrees()
        await self._restore_ended_agents()
        await self.refresh_agents()
        await self._drop_dead_agent_entries()
        await self.build_queue.start()
        self._started.set()
        self._pollers = [
            asyncio.create_task(self.loop_watch.run()),
            asyncio.create_task(self._poll(self._task_poll, self.refresh_tasks)),
            asyncio.create_task(self._poll(self._task_poll, self.refresh_comms)),
            asyncio.create_task(
                self._poll(
                    self._worktree_poll,
                    self.refresh_worktrees,
                    when=self._worktree_read_wanted,
                )
            ),
            asyncio.create_task(self._poll(self._agent_poll, self.refresh_agents)),
        ]
        if self._gate_watch is not None:
            self._pollers.append(asyncio.create_task(self._watch_gates()))
        for host in self.remote_hosts.values():
            self._pollers.append(
                asyncio.create_task(
                    host.run(self._remote_flipped, self._remote_changed)
                )
            )
        if self.remote_hosts:
            self._remote_flipped()
        if self._read_machine is not None:
            self._pollers.append(asyncio.create_task(self._sample_metrics()))

    async def stop(self) -> None:
        # First: a shielded PR read outlives its cancelled caller, and must not
        # start a watch after the gather below.
        if self.worktrees.prs is not None:
            self.worktrees.prs.on_change = None
        watching = [w.task for w in self._watches.values() if w.task is not None]
        detaching = list(self._detaches.values())
        reading = [
            task for task in (self._catch_up, self._event_read) if task is not None
        ]
        for task in [
            *self._pollers,
            *watching,
            *detaching,
            *reading,
            *self._pr_watches,
            *self._gate_tells,
            *self._hold_releases,
        ]:
            task.cancel()
        for hold in self._compact_holds.values():
            if hold.backstop is not None:
                hold.backstop.cancel()
        await asyncio.gather(
            *self._pollers,
            *watching,
            *detaching,
            *reading,
            *self._pr_watches,
            *self._gate_tells,
            *self._hold_releases,
            return_exceptions=True,
        )
        self._pollers = []
        self._catch_up = None
        self._event_read = None
        await self.build_queue.stop()
        await self.operations.stop()
        await asyncio.gather(*self._server_stops, return_exceptions=True)
        await self.language_servers.stop_all()
        for host in self.remote_hosts.values():
            await host.close()
        await self.homes.close()
        # Cleared so a subscriber arriving after the stop schedules no read.
        self._started.clear()
        self._detaches.clear()

    def _kept_fresh(self) -> bool:
        """Whether an arriving client was already read for inside the interval.

        Only a read an arrival triggered counts: ``start`` and the poll both
        read for clients already watching, so neither says the next client to
        arrive has been served. See docs/dev/orchestrator-server.md.
        """
        if self._served_at is None:
            return False
        elapsed = asyncio.get_running_loop().time() - self._served_at
        return elapsed < self._worktree_poll

    def _watch_arrived(self) -> None:
        """Catch the world up for a client that just subscribed.

        Synchronous because it is called from ``subscribe``, so the read is
        scheduled rather than awaited. A read already in flight is left alone —
        :meth:`refresh_worktrees` takes one at a time anyway.
        """
        if not self._started.is_set():
            # ``start`` reads every source before it serves anyone, so a
            # subscriber arriving during start needs no catch-up.
            return
        if self._catch_up is not None and not self._catch_up.done():
            return
        if self._kept_fresh():
            return
        self._catch_up = asyncio.create_task(self._catch_up_read())

    async def _catch_up_read(self) -> None:
        """:meth:`refresh_worktrees`, with its failures logged rather than lost.

        Nothing awaits this task, so an exception raised here would surface as
        an unretrieved-exception warning at garbage-collection time instead of
        as a message about the read.
        """
        try:
            await self.refresh_worktrees()
        except Exception:  # noqa: BLE001 — mirrors the poller's own guard
            log.exception("catch-up read failed")
        finally:
            # Stamped from the end, and stamped even on failure: a source that
            # refuses every read must not be asked once per reconnect.
            self._served_at = asyncio.get_running_loop().time()

    async def refresh_now(self, *, every_branch_within: float | None = None) -> None:
        """Re-read the worktrees because something changed the world.

        For an event rather than a tick: a pull request that was just raised is
        news, and the row carrying it is what the user is watching for. So this
        skips :meth:`_kept_fresh`, which exists to stop a flapping notice stream
        reading once per reconnect — a cost guard, not a correctness one.

        ``every_branch_within`` is for a one-shot list: the read asks GitHub
        about every branch, not only the desk's, unless such a read finished
        within that many seconds. That is checked again after the wait below,
        so two lists that arrive together cost one read. The PR cache honours
        its rate-limit stand-off whoever asks.

        A read already in flight is waited out rather than skipped.
        :meth:`refresh_worktrees` returns at once when its lock is held, and
        that read chose its branches — and may have asked GitHub — before this
        event happened. Returning there would drop the news the event carries,
        in exactly the case the event exists for: a poll tick landing while
        ``create-pr`` finishes.

        Failures are logged, never raised: the caller is an action that has
        already succeeded, and a stale chip must not fail it.
        """
        try:
            # Wait out a read that started before this event, then read for it.
            async with self._worktree_read:
                pass
            if every_branch_within is None:
                await self.refresh_worktrees()
            elif not self.every_branch_read_within(every_branch_within):
                await self.refresh_worktrees(every_branch=True)
        except Exception:  # noqa: BLE001 — mirrors the poller's own guard
            log.exception("event-triggered read failed")

    async def _poll(
        self,
        interval: float,
        refresh: Callable[[], Any],
        *,
        when: Callable[[], bool] | None = None,
    ) -> None:
        """Run ``refresh`` every ``interval`` seconds, forever.

        ``when`` holds a tick back while it answers false. See
        :meth:`_worktree_read_wanted`.
        """
        while True:
            await asyncio.sleep(interval)
            if when is not None and not when():
                continue
            try:
                await refresh()
            except Exception:  # noqa: BLE001 — a poller must outlive one bad read
                log.exception("refresh failed")

    def _worktree_read_wanted(self) -> bool:
        """Whether the worktree tick should read.

        While a client watches, or while an agent waits on a PR that has not
        merged. The UI never polls on its own, so with neither a read buys
        nothing. :meth:`_watch_arrived` reads on arrival, so a client that
        comes mid-interval does not wait for the next tick.
        """
        if self.notices.watching:
            return True
        recipients = self._pr_recipients()
        if not recipients:
            return False
        done = self._closed_prs | {
            pr.url for pr in self._cached_prs() if pr.state == "merged"
        }
        return any(url not in done for url in recipients)

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run source work off the loop, when it blocks and an executor was given.

        A source that is already async needs neither: it hands back an
        awaitable, which is awaited here rather than offloaded. So a source
        converted to ``async`` keeps working through this one call site, and a
        source still blocking keeps its thread.
        """
        return await self._run_on(self.executor, fn, *args)

    async def _run_worktree(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run one worktree operation, which is a sequence of steps.

        Deliberately not ``_run``: worktree work touches git, ports and the
        process table, so it must not queue behind a task read on the index's
        one thread — a fetch takes seconds. Its one notebook write, the reopen
        task of a forced close, is awaited on the loop and takes no thread.

        The pool it runs on is handed to the sequence when the operation is
        built, not applied here, because it is each blocking *step* that needs
        a thread. The operation itself is a coroutine that awaits them. See
        :mod:`mael_domain.worktree_steps` and ``mael_orchestrator.cli``.
        """
        return await self._run_on(self.worktree_executor, fn, *args)

    async def _run_on(
        self, executor: Executor | None, fn: Callable[..., Any], *args: Any
    ) -> Any:
        """Run ``fn`` on ``executor``, or inline when it is async or there is none."""
        if executor is None or inspect.iscoroutinefunction(fn):
            result = fn(*args)
        else:
            result = await asyncio.get_running_loop().run_in_executor(
                executor, fn, *args
            )
        return await result if inspect.isawaitable(result) else result

    # -- keeping the world fresh --

    def _apply(self, events: list[ServerEvent]) -> None:
        """Apply ``events`` to the world, then tell the hubs what changed.

        Synchronous on the loop, and so is a transcript socket's subscribe
        plus snapshot, so no client can see a world that is half-applied or
        miss a frame between its snapshot and its first live one.
        """
        if not events:
            return
        touched = self._comms_touched(events)
        self.state.apply(events)
        relinked = self._rederived_comms(touched)
        if relinked:
            self.state.apply(relinked)
            events = [*events, *relinked]
        self._record_transcripts(events)
        self._stop_language_servers(events)
        notices = notices_for(events)
        if "task" in notices or "agent" in notices:
            self._watch_new_prs()
        if "task" in notices:
            self.task_revision += 1
        if "comm" in notices:
            self.comm_revision += 1
        self.notices.notify(notices)

    def _stop_language_servers(self, events: list[ServerEvent]) -> None:
        """Stop the servers of each worktree ``events`` close or remove."""
        for event in events:
            if event.get("kind") != "worktree":
                continue
            if event["type"] == "remove":
                worktree_id = event["id"]
            elif event["type"] == "upsert" and event["entity"].get("isClosed"):
                worktree_id = event["entity"]["id"]
            else:
                continue
            task = asyncio.create_task(
                self.language_servers.close_worktree(worktree_id)
            )
            self._server_stops.add(task)
            task.add_done_callback(self._server_stops.discard)

    def _comms_touched(self, events: list[ServerEvent]) -> set[str]:
        """The comms whose task links ``events`` change: old and new values both.

        Read before the events apply, because a removed task's links are only
        in the world as it was.
        """
        tasks = self.world["tasks"]
        touched: set[str] = set()
        for event in events:
            if event.get("kind") != "task":
                continue
            if event["type"] == "upsert":
                entity = event["entity"]
                before = tasks.get(entity["id"])
                old = before["comms"] if before is not None else []
                if old != entity["comms"]:
                    touched.update(old, entity["comms"])
            elif event["type"] == "remove" and event["id"] in tasks:
                touched.update(tasks[event["id"]]["comms"])
        return touched

    def _comm_entity(self, comm: DomainComm) -> Comm:
        return comm_entity(
            comm,
            self.world["tasks"],
            self._steps,
            list(self._comm_update_rows.values()),
        )

    def _rederived_comms(self, comm_ids: Iterable[str]) -> list[ServerEvent]:
        """An upsert for each named comm whose derived fields moved.

        A link is a task field, a step is a landing row, and an update is its
        own row, so the comm's own row did not change: its ``taskIds``,
        ``queued`` and ``updates`` are derived again from what the server holds.
        """
        events: list[ServerEvent] = []
        for comm_id in sorted(set(comm_ids)):
            comm = self._comm_rows.get(comm_id)
            if comm is None or comm_id not in self.world["comms"]:
                continue
            entity = self._comm_entity(comm)
            if entity != self.world["comms"][comm_id]:
                events.append({"type": "upsert", "kind": "comm", "entity": entity})
        return events

    def _record_transcripts(self, events: list[ServerEvent]) -> None:
        """Append each transcript event to its agent's log, and push the frame out.

        Synchronous on the loop, like ``subscribe_transcript``: no frame can
        land between a socket's subscribe and its snapshot.
        """
        recorded: dict[str, list[TranscriptFrame | TranscriptPartial]] = {}
        for event in events:
            if not str(event.get("type", "")).startswith("transcript."):
                continue
            agent_id = event["agentId"]
            frame = self.transcript_log(agent_id).record(event)
            recorded.setdefault(agent_id, []).append(frame)
        for agent_id, frames in recorded.items():
            self.transcripts.push(agent_id, frames)

    def transcript_log(self, agent_id: str) -> TranscriptLog:
        """The agent's transcript, made on first use."""
        log = self._transcripts.get(agent_id)
        if log is None:
            log = self._transcripts[agent_id] = TranscriptLog(self._transcript_ring)
        return log

    def transcript_snapshot(self, agent_id: str) -> TranscriptSnapshot:
        return self.transcript_log(agent_id).snapshot()

    @property
    def world(self) -> World:
        return self.state.world

    @property
    def task_version(self) -> str | None:
        """The notebook version the task table was last read at."""
        return None if self._task_version is _NEVER else self._task_version

    @property
    def ready(self) -> asyncio.Event:
        """Set once the first source reads have finished."""
        return self._started

    def pending_requests(self, agent_id: str) -> list[TranscriptItem]:
        """Every item an agent waits on: its questions, permissions and plan reviews.

        Empty when the agent waits on nothing. An agent can be blocked on
        several at once, and each request id names one item.
        """
        agent = self.world["agents"].get(agent_id)
        if agent is None:
            return []
        log = self.transcript_log(agent_id)
        found = [
            log.find(lambda item, rid=rid: item.get("requestId") == rid)
            for rid in agent["pendingRequestIds"]
        ]
        return [item for item in found if item is not None]

    async def task_markdown(self, task_id: str) -> str | None:
        """The task as the notebook renders it, read from the notebook itself."""
        return await self._run(self.tasks.markdown, task_id)

    async def refresh_tasks(self, *, force: bool = False) -> None:
        """Re-read the notebook when its version moved, and publish the difference.

        A forced refresh reads the whole notebook: every command path forces,
        and a command has just written the row the client is waiting to see.
        The poll reads only what moved — see :meth:`_refresh_changed_tasks`.
        """
        version = await self._run(self.tasks.version)
        # An unknown version (a notebook with no commits yet) never matches,
        # so the poll degrades to a re-read rather than a permanent stale table.
        if not force and version is not None and version == self._task_version:
            return
        self._task_version = version
        if not force and await self._refresh_changed_tasks():
            return
        entities = await self._run(self.tasks.read)
        new = {task["id"]: task for task in entities}
        self._apply(diff_kind("task", self.world["tasks"], new))
        self._task_cursor = await self._run(self.tasks.revision_now)
        await self._prune_desk()

    async def _refresh_changed_tasks(self) -> bool:
        """Publish just the tasks that moved. ``False`` when that cannot be done.

        The poll's path. ``diff_kind`` is deliberately not used: it removes any
        id absent from the reading it is given, so handing it one tick's worth
        of rows would drop every task the tick left alone. The upserts come
        from the changed rows and the removes from the source, which is the
        only authority on what actually went.

        Falls back by returning ``False`` when there is no cursor to read from
        — a first read, or a source whose version is not a table revision.
        """
        read_since = getattr(self.tasks, "read_since", None)
        if read_since is None or self._task_cursor is None:
            return False
        if not getattr(self.tasks, "version_is_revision", False):
            # The version is some other counter, so it names no revision this
            # source could be read from. Such a source only ever gets the
            # whole-notebook read.
            return False
        changed = await self._run(read_since, self._task_cursor)
        known = self.world["tasks"]
        events: list[ServerEvent] = [
            {"type": "upsert", "kind": "task", "entity": entity}
            for entity in changed.tasks
            if known.get(entity["id"]) != entity
        ]
        events += [
            {"type": "remove", "kind": "task", "id": task_id}
            for task_id in changed.removed
            if task_id in known
        ]
        self._task_cursor = changed.revision
        self._apply(events)
        if events:
            await self._prune_desk()
        return True

    async def refresh_comms(self, *, force: bool = False) -> None:
        """Re-read the comms that moved, and publish them.

        The first read, and a forced one, read every comm, step and update.
        After that the poll reads only the comm rows written since the cursor,
        so a comm written by ``mael comms`` reaches the tab on the next tick.
        """
        if force or self._comm_cursor is None:
            comms = await self.comms.list()
            revision = await self.comms.revision()
            steps = await self.landings.steps.list()
            updates = await self.comm_updates.list()
            # Assigned together after the last await, so a write held while
            # the reads ran is not thrown away with the old containers.
            self._steps = steps
            self._comm_update_rows = {u.id: u for u in updates}
            self._comm_rows = {c.id: c for c in comms}
            new = {c.id: self._comm_entity(c) for c in comms}
            self._comm_cursor = revision
            self._apply(diff_kind("comm", self.world["comms"], new))
            return
        changes = await self.comms.changed_since(self._comm_cursor)
        self._comm_cursor = changes.revision
        known = self.world["comms"]
        events: list[ServerEvent] = []
        for comm in changes.comms:
            self._comm_rows[comm.id] = comm
            entity = self._comm_entity(comm)
            if known.get(comm.id) != entity:
                events.append({"type": "upsert", "kind": "comm", "entity": entity})
        self._apply(events)

    def _hold_steps(self, steps: list[StepEvent]) -> None:
        """Hold the steps a landing sync wrote, and publish the comms they queue in."""
        if not steps:
            return
        self._steps.extend(steps)
        tasks = self.world["tasks"]
        comm_ids = {
            comm_id
            for e in steps
            for comm_id in tasks.get(wire_task_key(e.project, e.task_id), {}).get(
                "comms", []
            )
        }
        self._apply(self._rederived_comms(comm_ids))

    async def _reload_steps(self) -> None:
        """Read every step again, and publish each comm whose queue moved."""
        self._steps = await self.landings.steps.list()
        self._apply(self._rederived_comms(self._comm_rows))

    async def _refresh_landings(self) -> None:
        """Push each done tracked task whose landing moved in the sync.

        A landing is no task write; see orchestrator-server.md, "The landing on
        a task".
        """
        tracked = await self.landings.tracked()
        await self._refresh_some_tasks(
            {
                wire_task_key(t.project, t.task_id)
                for t in tracked
                if t.status == STATUS_DONE
            }
        )

    async def _refresh_some_tasks(self, task_ids: set[str]) -> None:
        """Re-read the named tasks and publish the ones whose entity moved."""
        entities = await self._run(self.tasks.read_some, sorted(task_ids))
        known = self.world["tasks"]
        self._apply(
            [
                {"type": "upsert", "kind": "task", "entity": entity}
                for entity in entities
                if known.get(entity["id"]) != entity
            ]
        )

    async def refresh_worktrees(
        self,
        extra_branches: set[str] | None = None,
        *,
        every_branch: bool = False,
        force: bool = False,
    ) -> None:
        """Re-read ``list-all``, one read in flight at a time.

        The read asks GitHub only about the branches on the desk. GraphQL is
        charged by node count and the budget refills hourly, so asking about
        every branch in every project is what spends it. A branch left out
        keeps the pull request the last read saw; see
        :class:`mael_orchestrator.sources.PrStateCache`. ``extra_branches`` are
        asked about beyond the desk. ``every_branch`` asks about every branch
        on disk. A read that names some, or all, waits out one in flight, which
        chose its branches without them. ``force`` waits one out too, for a
        change on disk that the read in flight may have missed.

        The landing sync reads GitHub too, so it is skipped while the PR cache
        stands off, and a refusal it meets stands the cache off.
        """
        if (
            self._worktree_read.locked()
            and not extra_branches
            and not every_branch
            and not force
        ):
            return
        async with self._worktree_read:
            # Read before the await; the world below is re-read after it,
            # because the read yields and the world can move while it runs.
            before = self.world
            active = desk_model.active_branches(
                before["desk"],
                agents=before["agents"],
                worktrees=before["worktrees"],
                tasks=before["tasks"],
            ) | (extra_branches or set())
            projects, worktrees = await self._run(
                self.worktrees.read, None if every_branch else active
            )
            world = self.world
            events = diff_kind(
                "project", world["projects"], {p["id"]: p for p in projects}
            )
            # ``list-all`` knows nothing of live feedback or gates: the hub's
            # state and the gate jobs go on top. The jobs re-link to the new
            # rows, so a job whose worktree is new gets it at once. Each id
            # takes this server's prefix, and the diff touches only those: a
            # merged row is its remote host's to remove.
            rows = {
                self.ids.own(w["id"]): {
                    **w,
                    "id": self.ids.own(w["id"]),
                    "liveFeedback": self.live_feedback.state(self.ids.own(w["id"])),
                }
                for w in worktrees
            }
            jobs = self._gate_jobs(rows)
            own = self.ids.prefix
            events += diff_kind("gateJob", world["gateJobs"], jobs, owner=own)
            events += diff_kind(
                "worktree",
                world["worktrees"],
                self._with_merge_requests(with_gates(rows, jobs), jobs),
                owner=own,
            )
            # Applied before the landing sync, so its reads do not delay the rows.
            self._apply(events)
            if every_branch:
                self._every_branch_read_at = asyncio.get_running_loop().time()
            if not self.worktrees.rate_limited:
                try:
                    written = await self.landings.sync(self.clock())
                except RateLimited:
                    self.worktrees.stand_off()
                except Exception:
                    # A landing is not worth failing the worktree read over.
                    log.exception("the landing sync failed")
                    # It may have written steps before it failed, and the next
                    # sync does not return them again.
                    await self._reload_steps()
                else:
                    self._hold_steps(written)
                    await self._refresh_landings()

    def every_branch_read_within(self, max_age: float) -> bool:
        """Whether a read of every branch finished less than ``max_age`` ago."""
        if self._every_branch_read_at is None:
            return False
        age = asyncio.get_running_loop().time() - self._every_branch_read_at
        return age < max_age

    # -- the desk --

    async def _load_desk(self) -> None:
        """Put the stored desk in the world, less any task the notebook lost.

        This runs before the first agent read, because the read saves the
        desk as it joins agents to it and would overwrite the stored file.
        :meth:`_drop_dead_agent_entries` finishes the job once the agents are
        known.
        """
        stored = await self._run(self.desk.load)
        await self._set_desk(self._pruned(stored))

    async def _restore_ended_agents(self) -> None:
        """Put each ended agent of a task back in the world, as ``exited``.

        A record has no subagents, so none come back. See
        ``docs/dev/orchestrator-server.md``, "Agents".
        """
        if self.agent_records is None:
            return
        sessions = {
            entry["id"]
            for task in self.world["tasks"].values()
            for entry in task["sessions"]
        }
        entities: list[Agent] = []
        for record in await self.agent_records.list():
            if record.get("status") != AGENT_ENDED or not record.get("id"):
                continue
            if record["id"] in self.world["agents"]:
                continue
            row = ended_agent_row(record)
            # Most records have no task. The set skips them before the scan.
            if row["session"] not in sessions and not row.get(PARENT_URL):
                continue
            link = self._link(row)
            if not link.task_id:
                continue
            entities.append(
                agent_entity(
                    row,
                    task_id=link.task_id,
                    project=link.project,
                    worktree_id=link.worktree_id,
                )
            )
        self._apply(
            [{"type": "upsert", "kind": "agent", "entity": e} for e in entities]
        )

    async def _drop_dead_agent_entries(self) -> None:
        """Drop a stored ``agent:`` entry for an agent the host no longer has.

        The world's agents are rebuilt from the host on every start, so such
        an entry would draw nothing and could never be dismissed. This runs
        once, after the first agent read. During a run the opposite rule
        applies: an agent stays in the world once seen, which is what keeps a
        stopped agent on the canvas.
        """
        world = self.world
        kept = desk_model.drop_unknown_agents(world["desk"], world["agents"])
        await self._set_desk(kept)

    async def _prune_desk(self) -> None:
        """Drop desk entries for tasks that are no longer in the notebook."""
        pruned = self._pruned(self.world["desk"])
        await self._set_desk(pruned)

    def _pruned(self, table: DeskTable) -> DeskTable:
        """``table`` pruned against the projects the last task read covered."""
        tasks = self.world["tasks"]
        return desk_model.prune(table, tasks, {t["project"] for t in tasks.values()})

    async def _set_desk(self, table: DeskTable) -> None:
        """Save the desk, then publish what changed about it.

        Saving first is what makes a restart show the desk the last client
        saw, rather than one change behind it.

        The diff answers "what moved in the world", which is not what the
        write's own rows answer: at a restart the rows are already stored, so
        the write moves nothing while every one of them is new to the world.
        """
        old = self.world["desk"]
        if table == old:
            return
        await self._run(self.desk.save, table)
        self._apply(diff_kind("desk", old, table))

    async def _desk_add(self, command: dict[str, Any]) -> dict[str, Any]:
        await self._add_to_desk(command["id"])
        return {"ok": True, "result": {}}

    async def _add_to_desk(self, desk_id: str) -> None:
        table = self.world["desk"]
        await self._set_desk(desk_model.add(table, desk_id, self.clock()))

    async def _join_desk(self, agent_id: str) -> None:
        """Put a newly adopted live agent on the desk, under its task or itself.

        An agent already exited when the server first sees it does not join:
        only running work puts itself on the canvas.
        """
        agent = self.world["agents"].get(agent_id)
        if agent is None or agent["state"] == "exited":
            return
        if self._delegated(agent):
            # Its task is on its parent, whose desk shows it.
            return
        task_id = agent["taskId"]
        desk_id = desk_id_for_task(task_id) if task_id else desk_id_for_agent(agent_id)
        await self._add_to_desk(desk_id)

    def _delegated(self, agent: Agent | None) -> bool:
        """Whether this server runs ``agent`` for a **Parent server** that holds its task."""
        return bool(agent and agent["parentUrl"] and self.ids.is_own(agent["id"]))

    def _home(self, agent: Agent | None) -> TaskHome:
        """Where ``agent``'s task work goes: its parent's tasks, or this server's."""
        delegated = agent if self._delegated(agent) else None
        return self.homes.for_parent(delegated["parentUrl"] if delegated else "")

    def _remote_owner(self, entity_id: str) -> RemoteHost | None:
        """The **Remote host** that owns ``entity_id``, or ``None`` for this server's own."""
        if self.ids.is_own(entity_id):
            return None
        return self.remote_hosts.get(owner_of(entity_id))

    def _worktree_home(self, worktree_id: str) -> TaskHome:
        """The **Task home** of a worktree: its newest delegated agent's, or this server's."""
        delegated = [
            a
            for a in self.world["agents"].values()
            if a["worktreeId"] == worktree_id and self._delegated(a)
        ]
        newest = max(delegated, key=lambda a: a["startedAt"], default=None)
        return self._home(newest)

    async def _task_of(self, agent: Agent) -> Task | None:
        """``agent``'s task, from its home. ``None`` for a free agent or a lost one."""
        task_id = agent["taskId"]
        if not task_id:
            return None
        if not self._delegated(agent):
            return self.world["tasks"].get(task_id)
        try:
            found = await self._home(agent).read_some([task_id])
        except (TaskSourceRemote, ValueError) as exc:
            log.warning("agent %s: its task %s: %s", agent["id"], task_id, exc)
            return None
        return found[0] if found else None

    async def _desk_remove(self, command: dict[str, Any]) -> dict[str, Any]:
        table = self.world["desk"]
        await self._set_desk(desk_model.remove(table, command["id"]))
        return {"ok": True, "result": {}}

    # -- agents --

    async def refresh_agents(self) -> None:
        """Reconcile the agent host's ``list`` against the world.

        A new id is adopted and attached. An id that is gone has exited: the
        host drops a stopped agent, so ``exited(0)`` is the state it left in.
        A row reporting an exit the stream never showed is applied as-is. A
        live agent whose stream ended without an exit is attached again. And a
        live agent's links are re-resolved, so a task or worktree that arrived
        after the agent still finds it.

        A newly adopted live agent joins the desk, under its task when it has
        one and under itself when it does not. The canvas draws running work
        either way; the entry is what keeps it drawn once the agent stops. The
        join happens once, at adoption: a later poll must not re-add an entry
        the user has dismissed.

        A row with a ``parent`` is a subagent. It is adopted as an entity, so
        the parent's session tab can list it, but it is not attached and does
        not join the desk: its stream is followed only while a transcript
        socket is open on it (:meth:`ensure_attached`), so a subagent's events
        never cross to this server until someone asks for them.
        """
        # Read before ``list``: a wait raised while the reply is in transit is
        # newer than the row.
        held_before = {
            agent_id: set(agent["pendingRequestIds"])
            for agent_id, agent in self.world["agents"].items()
        }
        reply = await self.daemon.request({"cmd": "list"})
        if "error" in reply:
            if self._host_failures == 0:
                log.warning("agent host: %s", reply["error"])
            self._host_failures += 1
            if self._host_failures >= HOST_UNREACHABLE_AFTER:
                self._set_host_reachable(False)
            if "agents" not in reply:
                return
            # The router answers with its stored rows beside the error, each
            # in its last seen state. They are read as any rows are, so the
            # other harness's agents stay current through the outage.
        else:
            self._host_failures = 0
            self._set_host_reachable(True)
            self._redeliver()
        self._set_host_usage(
            host_usage(reply.get("usage")), host_usage(reply.get("codex_usage"))
        )
        rows = {row["id"]: row for row in reply.get("agents", [])}
        agents = self.world["agents"]
        for agent_id, row in rows.items():
            is_child = bool(row.get("parent"))
            if agent_id not in agents:
                await self._adopt(row)
                if not is_child:
                    await self._join_desk(agent_id)
                continue
            state, exit_code = parse_agent_state(row.get("state", ""))
            if agents[agent_id]["state"] == "exited":
                if state != "exited":
                    # A resume keeps the agent id, so the row that came back is
                    # this agent alive again, not a new one.
                    await self._revive(row, state)
                continue
            if state == "exited":
                await self._exit(agent_id, exit_code)
                continue
            if agent_id not in self._watches and not is_child:
                await self._attach(agent_id)
            if not is_child:
                await self._follow_session(row)
            await self._relink(row)
            await self._close_wait_the_host_no_longer_holds(
                agent_id, state, held_before.get(agent_id, set())
            )
        for agent_id, agent in list(agents.items()):
            # A merged agent is its remote host's to exit.
            if not self.ids.is_own(agent_id):
                continue
            if agent_id not in rows and agent["state"] != "exited":
                await self._exit(agent_id, 0)

    async def _follow_session(self, row: dict[str, Any]) -> None:
        """Append the session ``row`` reports to its task, when the task lacks it.

        A **Delegated agent**'s task is on its **Parent server**, so the append
        goes to its **Task home**, once for each session. See "A session that
        moves" in ``docs/dev/orchestrator-server.md``.
        """
        agent_id, session = row["id"], str(row.get("session") or "")
        if not session or not row.get(EARLIER_SESSIONS):
            return
        link = self._link(row)
        if not link.task_id:
            return
        if row.get("parentUrl"):
            if self._sessions_sent.get(agent_id) == session:
                return
            home = self.homes.for_parent(str(row["parentUrl"]))
        elif link.session_due:
            home = self.homes.for_parent("")
        else:
            return
        try:
            await self._run(home.add_session, link.task_id, session)
        except (KeyError, ValueError, TaskSourceRemote) as exc:
            # Tried again at each poll, and logged at the first failure.
            if self._sessions_refused.get(agent_id) != session:
                self._sessions_refused[agent_id] = session
                log.warning(
                    "agent %s: its session %s is not on task %s: %s",
                    agent_id,
                    session,
                    link.task_id,
                    exc,
                )
            return
        self._sessions_refused.pop(agent_id, None)
        if row.get("parentUrl"):
            self._sessions_sent[agent_id] = session
        else:
            await self.refresh_tasks(force=True)

    def _set_host_reachable(self, reachable: bool) -> None:
        """Record whether the host answers, publishing only a change.

        The agents are never exited for the host being away: a daemon restart
        is a few seconds of silence and then the same ids, revived, and the
        clients keep their cursors through it. The host entity is what tells a
        client the agents it shows are the last known ones.
        """
        self._set_host(reachable=reachable)

    def _set_host_usage(
        self, usage: HostUsage | None, codex_usage: HostUsage | None
    ) -> None:
        """Record the account's budget, publishing only a change.

        Separate from :meth:`_set_host_reachable` because the two change for
        unrelated reasons: the budget moves as agents work, reachability only
        when the daemon goes. A poll runs every couple of seconds, so a reading
        that has not moved must publish nothing.
        """
        # A poll that carried no reading is not news that the budget is gone —
        # only that this listing had nothing to add.
        if usage is not None or codex_usage is not None:
            self._set_host(usage=usage, codex_usage=codex_usage)

    def _remote_flipped(self) -> None:
        """Publish the remote hosts' online state after a link went up or down.

        A remote host's agents are never exited for it: see "Offline is not
        dead" in ``docs/dev/remote-hosts.md``.
        """
        self._set_host(
            remote_hosts=[host.state() for host in self.remote_hosts.values()]
        )

    async def _remote_changed(self, host: RemoteHost, kinds: set[str]) -> None:
        """Read ``kinds`` on ``host`` again, and merge each into the world.

        Each reading is diffed against ``host``'s slice, what it gave last
        time, so a row this server owns, or another host does, is never
        removed by it. A host that does not answer keeps its slices. A
        worktree merges only when this server has its project.
        """
        for kind in sorted(kinds):
            path, key = MERGED_KINDS[kind]
            try:
                reply = await host.get_json(path)
            except HostUnreachable as exc:
                log.warning("remote host %s: %s", host.name, exc)
                return
            new: dict[str, Any] = {}
            for entity in reply.get(key) or []:
                if not isinstance(entity, dict) or not owns(host.name, kind, entity):
                    continue
                if not isinstance(entity.get("id"), str) or not entity["id"]:
                    continue
                if (
                    kind == "worktree"
                    and entity.get("project") not in self.world["projects"]
                ):
                    continue
                if kind == "agent":
                    entity = self._merged_agent(cast(Agent, entity))
                elif kind == "operation":
                    entity = self._merged_operation(cast(Operation, entity))
                new[entity["id"]] = entity
            old = host.slices.get(kind, {})
            # A merged kind's list key is its world table's name.
            table = self.world[key]
            shown = {i: table[i] for i in old if i in table}
            host.slices[kind] = new
            self._apply(diff_kind(kind, shown, new))
            if kind == "agent":
                for agent_id, agent in new.items():
                    if agent_id not in old and not agent["parent"]:
                        await self._join_desk(agent_id)

    def _merged_agent(self, agent: Agent) -> Agent:
        """A remote host's agent as this server shows it.

        Its task is one of this server's only when this server delegated it.
        Any other task id names a task in another notebook, which may share
        the id of one here.
        """
        if self._delegated_here(agent):
            return agent
        return {**agent, "taskId": ""}

    def _delegated_here(self, agent: Agent | None) -> bool:
        """Whether a remote host's ``agent`` runs for this server as its **Parent server**."""
        return bool(
            agent and agent.get("parentUrl") and agent.get("parentUrl") == self.own_url
        )

    def _merged_operation(self, op: Operation) -> Operation:
        """A remote host's operation as this server shows it.

        Its task id is kept by the rule of :meth:`_merged_agent`: only when its
        agent is one this server delegated. The agents merge first, because the
        kinds merge in name order.
        """
        if self._delegated_here(self.world["agents"].get(op.get("agentId") or "")):
            return op
        return {**op, "taskId": None}

    def _set_host(
        self,
        *,
        reachable: bool | None = None,
        usage: HostUsage | None = None,
        codex_usage: HostUsage | None = None,
        remote_hosts: list[RemoteHostState] | None = None,
    ) -> None:
        """Publish the host entity when a fact on it has changed.

        One writer for every fact, so none overwrites another's field. A
        ``None`` reading keeps the last one.
        ``since`` tracks ``reachable`` alone: it is how long the host has been
        down, and a budget reading arriving must not reset that clock.
        """
        old = self.world["host"]
        current = old.get(HOST_ID)
        was_reachable = current["reachable"] if current else None
        next_reachable = was_reachable if reachable is None else reachable
        next_usage = (
            (current.get("usage") if current else None) if usage is None else usage
        )
        next_codex_usage = (
            (current.get("codexUsage") if current else None)
            if codex_usage is None
            else codex_usage
        )
        next_remote_hosts = (
            (current.get("remoteHosts", []) if current else [])
            if remote_hosts is None
            else remote_hosts
        )
        if (
            current is not None
            and current["reachable"] == next_reachable
            and current.get("usage") == next_usage
            and current.get("codexUsage") == next_codex_usage
            and current.get("remoteHosts", []) == next_remote_hosts
        ):
            return
        entity: Host = {
            "id": HOST_ID,
            "name": self.own_name,
            "idPrefix": self.ids.prefix,
            "reachable": bool(next_reachable),
            "since": current["since"]
            if current is not None and was_reachable == next_reachable
            else self.clock(),
            "socket": str(getattr(self.daemon, "socket_path", "")),
            "usage": next_usage,
            "codexUsage": next_codex_usage,
            "remoteHosts": next_remote_hosts,
        }
        self._apply(diff_kind("host", old, {HOST_ID: entity}))

    async def _close_wait_the_host_no_longer_holds(
        self, agent_id: str, state: str, held_before: set[str]
    ) -> None:
        """End a wait the world holds that the host's row does not report.

        The row wins when the two disagree — see ``docs/dev/orchestrator-server.md``,
        "Agents". The wait ends the way the child ends one it withdraws, so the
        transcript item goes stale with it.

        The row judges only ``held_before``, the waits the world held when
        ``list`` went out. A later wait is newer than the row.

        Runs after ``_attach``. A watch whose backlog has not landed holds no
        wait of its own to end, because the frame that raises one is applied
        at ``BACKLOG_END``, so a slow host costs nothing here.
        """
        watch = self._watches.get(agent_id)
        if watch is None:
            return
        agent = self.world["agents"].get(agent_id)
        held = [
            request_id
            for request_id in (agent["pendingRequestIds"] if agent else [])
            if request_id in held_before
        ]
        if not held or state.startswith("awaiting-"):
            return
        for request_id in held:
            await self._normalise(
                watch, {"type": "control_cancel_request", "request_id": request_id}
            )

    async def _revive(self, row: dict[str, Any], state: str) -> None:
        """Bring an exited agent back: clear its exit, and follow it again.

        The re-attached backlog is relayed with the ids the items already had,
        so a client holding them applies nothing new. The links are resolved
        again in the same pass: a
        task or worktree that arrived while the agent was gone would otherwise
        be missing from it until some later poll took the live-agent branch.
        """
        agent_id = row["id"]
        watch = self._watches.pop(agent_id, None)
        if watch is not None and watch.task is not None:
            watch.task.cancel()
        # A new life: the host will not honour the old cursor, and nor should we.
        self._cursors.pop(agent_id, None)
        ctx = context_for_agent(agent_id, self._next_item_seed(agent_id))
        link = self._link(row)
        out = revive_agent(
            self.state.state,
            ctx,
            state,
            self.clock(),
            task_id=link.task_id,
            project=link.project,
            worktree_id=link.worktree_id,
        )
        self._apply(out.events)
        if not row.get("parent"):
            await self._attach(agent_id)

    def _link(self, row: dict[str, Any]) -> AgentLink:
        world = self.world
        return link_agent(row, worktrees=world["worktrees"], tasks=world["tasks"])

    async def _adopt(self, row: dict[str, Any]) -> None:
        """Put a new agent in the world and start following its stream.

        An agent the world already holds is left as it stands. A launch adopts
        the synthetic row it built from its own payload, which names no mode
        and may name no model or session; the poll adopts the host's real row.
        When the poll gets there first, overwriting it would blank those
        fields until the next list, and ``_relink`` repairs only the links and
        the three numbers the row keeps fresh.
        """
        known = self.world["agents"].get(row["id"])
        if known is not None:
            if not known["parent"]:
                await self._attach(row["id"])
            return
        link = self._link(row)
        entity = agent_entity(
            row,
            task_id=link.task_id,
            project=link.project,
            worktree_id=link.worktree_id,
        )
        self._apply([{"type": "upsert", "kind": "agent", "entity": entity}])
        if not entity["parent"]:
            await self._attach(entity["id"])

    async def ensure_attached(self, agent_id: str) -> None:
        """Follow ``agent_id``'s stream if it is a subagent nobody follows yet.

        The transcript routes call this before they read: a top-level agent
        is always attached, so it is a no-op there. For a subagent it attaches
        and waits for the backlog, so the snapshot the route serves holds it.
        A detach still waiting on its grace is cancelled: the reader came back.
        """
        agent = self.world["agents"].get(agent_id)
        if agent is None or not agent["parent"]:
            return
        pending = self._detaches.pop(agent_id, None)
        if pending is not None:
            pending.cancel()
        if agent_id not in self._watches:
            await self._attach(agent_id)

    def _transcript_idle(self, agent_id: str) -> None:
        """The last transcript socket on ``agent_id`` closed.

        A subagent's watch is dropped after :data:`CHILD_DETACH_SECS`, unless a
        socket comes back first. The cursor outlives the watch, so the next
        attach asks the host for what was missed and not the whole window.
        """
        agent = self.world["agents"].get(agent_id)
        if agent is None or not agent["parent"] or agent_id not in self._watches:
            return
        if agent_id in self._detaches:
            return
        self._detaches[agent_id] = asyncio.create_task(self._detach_later(agent_id))

    def release_unwatched(self, agent_id: str) -> None:
        """Start the detach grace for a watch no socket ever subscribed to.

        ``ensure_attached`` runs before a socket subscribes, so a client that
        goes away inside it leaves a watch that :meth:`_transcript_idle` never
        hears about. A watch some other socket is reading is left alone.
        """
        if self.transcripts.count(agent_id):
            return
        self._transcript_idle(agent_id)

    async def _detach_later(self, agent_id: str) -> None:
        try:
            await asyncio.sleep(self._child_detach)
            if self.transcripts.count(agent_id):
                return
            watch = self._watches.pop(agent_id, None)
            if watch is not None and watch.task is not None:
                watch.task.cancel()
        finally:
            self._detaches.pop(agent_id, None)

    async def _attach(self, agent_id: str) -> None:
        """Follow an agent's stream, and wait for its replayed backlog to end.

        One watch per agent. See ``docs/dev/orchestrator-server.md``, "Agents".
        A caller that finds a watch already there waits for that watch's
        backlog, so every caller returns on the same promise.
        """
        held = self._watches.get(agent_id)
        if held is not None:
            await self._wait_for_backlog(held)
            return
        watch = AgentWatch(
            agent_id, context_for_agent(agent_id, self._next_item_seed(agent_id))
        )
        self._watches[agent_id] = watch
        cursor = self._cursors.setdefault(agent_id, Cursor())
        watch.task = asyncio.create_task(self._follow(watch, cursor))
        await self._wait_for_backlog(watch)

    async def _wait_for_backlog(self, watch: AgentWatch) -> None:
        """Wait for a watch to replay its backlog.

        A host that never sends the marker only delays the caller; it does not
        block the server.
        """
        try:
            await asyncio.wait_for(watch.caught_up.wait(), BACKLOG_TIMEOUT_SECS)
        except asyncio.TimeoutError:
            log.warning(
                "agent %s: no backlog marker within %ss",
                watch.agent_id,
                BACKLOG_TIMEOUT_SECS,
            )

    def _next_item_seed(self, agent_id: str) -> int:
        """Where a new context's id counter starts, so it mints no id twice.

        A re-attach gets a fresh context, and the ids it hands out must not
        collide with the ones the previous context already sent to clients.
        """
        return self._item_seeds.get(agent_id, 0)

    async def _relink(self, row: dict[str, Any]) -> None:
        agent = self.world["agents"][row["id"]]
        link = self._link(row)
        # The host's row carries what the session has spent and how large it has
        # grown, re-read here on every poll rather than only at adoption. That
        # is what keeps an agent adopted before its first turn from reading 0
        # for ever: the live stream moves these numbers only on an event this
        # server saw, and a synthesised launch row carries none of them.
        totals = row_totals(row)
        linked: Agent = {
            **agent,
            "taskId": link.task_id,
            "project": link.project,
            "worktreeId": link.worktree_id,
            # The launch's own row names no parent: the router's record does.
            "parentUrl": row.get("parentUrl") or "",
            # A `/clear` moves it. A launch's own row may name none.
            "session": row.get("session") or agent["session"],
            # Spend and size only ever climb within one agent, so the larger
            # reading wins. A poll can carry a row the host stamped before a
            # turn this server has already seen on the stream, and taking it
            # flat would walk the numbers backwards between polls.
            "costUsd": max(agent["costUsd"], totals["costUsd"]),
            "totalTokens": max(agent["totalTokens"], totals["totalTokens"]),
            "subagentTokens": max(agent["subagentTokens"], totals["subagentTokens"]),
        }
        # Occupancy is not monotonic — a compact is meant to drop it — so it
        # takes the row as it stands. Only when the row is entitled to speak:
        # a subagent's row reports 0 by construction, and an older host reports
        # nothing at all, and neither means "the context emptied". The stream's
        # own reading stands in both cases.
        if "context_tokens" in row and not row.get("parent"):
            linked = {**linked, "contextTokens": totals["contextTokens"]}
        if row.get("parent"):
            # A subagent has no stream of its own to learn this from: its ask
            # arrives on the parent's, because a ``control_request`` carries no
            # ``parent_tool_use_id``. The row is the only route, and the
            # session tab's strip reads it to draw the wait beside the subagent
            # that raised it.
            state, _ = parse_agent_state(row.get("state", ""))
            linked = {
                **linked,
                "state": state,
                "waitingOn": row.get("waiting_on") or "",
            }
        if linked != agent:
            self._apply([{"type": "upsert", "kind": "agent", "entity": linked}])

    async def _follow(self, watch: AgentWatch, cursor: Cursor) -> None:
        """Normalise one agent's attach stream into its transcript until it ends.

        The attach carries the cursor the last watch left, so the host replays
        only what this server missed. The watch is dropped when the stream
        ends, whatever ended it. An agent still listed by the host is attached
        again on the next reconciliation.
        """
        agent_id = watch.agent_id
        detail: dict[str, Any] = {}
        try:
            async for event in self.daemon.attach(agent_id, cursor.seq, cursor.epoch):
                if "error" in event and "type" not in event:
                    # The host would not give us the stream. The next
                    # reconciliation tries again.
                    log.warning("agent %s: %s", agent_id, event["error"])
                    return
                kind = event.get("type")
                if kind == AGENT_DETAIL:
                    # The host's opening frame: what the agent waits on. Held,
                    # not applied yet — the backlog that follows usually
                    # replays the ``control_request`` that opened the wait, and
                    # raising it from both would duplicate the item and its
                    # attention. It is applied at BACKLOG_END, and only for a
                    # wait the backlog did not carry.
                    detail = event.get("agent") or {}
                    continue
                if kind == BACKLOG_END:
                    cursor.epoch = str(event.get("epoch") or "")
                    seq = event.get("seq")
                    if isinstance(seq, int):
                        cursor.seq = seq
                    # A wait the host named that the backlog did not replay:
                    # the request went out before the host's window, so only
                    # the detail frame knows about it.
                    out = apply_agent_detail(
                        self.state.state, watch.ctx, detail, self.clock()
                    )
                    await self._emit(watch, out)
                    watch.caught_up.set()
                    continue
                if kind == TRUNCATED:
                    await self._gap(watch, event)
                    continue
                if kind == AGENT_EXITED:
                    await self._exit(agent_id, event.get("exit_code"), from_stream=True)
                    return
                seq = event.get(SEQ_KEY)
                if isinstance(seq, int):
                    cursor.seq = seq
                await self._normalise(watch, event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — one bad stream must not take the server down
            log.exception("attach stream for %s failed", agent_id)
        finally:
            # The context goes with the watch, and with it the id of a partial
            # message. An exit closed it already, so this is the lost stream.
            if watch.ctx.partial is not None:
                await self._emit(
                    watch,
                    close_partial_message(self.state.state, watch.ctx, self.clock()),
                )
            watch.caught_up.set()
            if self._watches.get(agent_id) is watch:
                del self._watches[agent_id]

    async def _gap(self, watch: AgentWatch, event: dict[str, Any]) -> None:
        """Mark what the host dropped.

        Before any item, the transcript is truncated: its start is not the
        agent's start. Mid-stream, a ``gap`` item stands where the dropped
        events were. A wait whose answer was among them is closed by
        :meth:`_close_wait_the_host_no_longer_holds`.
        """
        dropped = event.get("dropped")
        dropped = dropped if isinstance(dropped, int) else 0
        agent_id = watch.agent_id
        if not self.transcript_log(agent_id).items:
            self._apply([{"type": "transcript.truncated", "agentId": agent_id}])
            return
        out = normalise_gap(self.state.state, watch.ctx, dropped, self.clock())
        await self._emit(watch, out)

    async def _normalise(self, watch: AgentWatch, raw: dict[str, Any]) -> None:
        replay = not watch.caught_up.is_set()
        out = normalise_stream_event(
            self.state.state,
            watch.ctx,
            raw,
            self.clock(),
            files=self.files,
            show_refs=attachment_urls,
            # A replayed turn is already in the row's own totals: the host
            # summed it before it handed the row over. Only a live turn adds.
            replay=replay,
        )
        await self._emit(watch, out)
        await self._store_documents(out.events, replay=replay)
        if out.milestone is not None:
            if raw.get("type") == "control_response":
                # Recorded at once, not parked: no `result` for the planning
                # turn is coming. See `docs/dev/orchestrator-server.md`, "One
                # milestone does not wait".
                if watch.caught_up.is_set():
                    await self._record_milestone(watch, out.milestone)
            else:
                # Held, not recorded: see `AgentWatch.pending_milestone`. A
                # second marker in one turn replaces the first, as last-wins
                # says.
                watch.pending_milestone = out.milestone
        if out.pr_link and out.pr_withdrawn:
            # A replayed withdrawal is history: a later push may have made the
            # PR ready again since.
            if watch.caught_up.is_set():
                await self._withdraw_pr(watch.agent_id, out.pr_link)
        elif out.pr_link:
            await self._register_pr(
                watch.agent_id, out.pr_link, replay=not watch.caught_up.is_set()
            )
        # A replayed marker is history: a restart turns every param off, and
        # the backlog must not turn one on again.
        if out.feedback_params and watch.caught_up.is_set():
            agent = self.world["agents"].get(watch.agent_id)
            if agent and agent["worktreeId"]:
                self._apply_feedback_params(agent["worktreeId"], out.feedback_params)
        if out.end_session:
            # Parked even in replay, as a compaction request is.
            self._pending_ends.add(watch.agent_id)
        await self._follow_compact(watch, out)
        if out.turn_end and watch.pending_milestone is not None:
            milestone, watch.pending_milestone = watch.pending_milestone, None
            # A replayed marker was recorded by the run that first read it. The
            # ledger keys on an ordinal, so recording it again would append a
            # second row rather than replace the first — and its delta would be
            # 0, because no tokens were spent between the two writes.
            if watch.caught_up.is_set():
                await self._record_milestone(watch, milestone)
        # Last: the stop cancels this watch, so the turn's milestone goes first.
        await self._follow_end(watch, out)

    async def _follow_end(self, watch: AgentWatch, out: Normalised) -> None:
        """Terminate the agent when the turn that asked to end does.

        See ``docs/dev/orchestrator-server.md``, "An end-session request".
        """
        if not out.turn_end or watch.agent_id not in self._pending_ends:
            return
        self._pending_ends.discard(watch.agent_id)
        if not watch.caught_up.is_set():
            # A replayed turn end was acted on by the run that read it live.
            return
        reply = await self.handle_command(
            {"type": "agent.stop", "agentId": watch.agent_id}
        )
        if not reply.get("ok"):
            log.warning(
                "agent %s: end-session not acted on: %s",
                watch.agent_id,
                reply["error"]["message"],
            )

    async def _follow_compact(self, watch: AgentWatch, out: Normalised) -> None:
        """Act on a compaction request, and tell the agent how it went.

        See ``docs/dev/orchestrator-server.md``, "A compaction request".
        """
        agent_id = watch.agent_id
        if out.compact_request is not None:
            # Parked even in replay, as a milestone is: the turn may end live.
            # Last wins: a second marker in one turn replaces the first.
            watch.pending_compact = out.compact_request
            if watch.caught_up.is_set():
                # From the marker, not the `/compact`: an event told before
                # the turn ends would be queued ahead of the `/compact`.
                self._open_hold(agent_id, reply=True, sent=False)
        if not watch.caught_up.is_set():
            # A replayed turn end was answered by the run that read it live.
            if out.turn_end:
                watch.pending_compact = None
            return
        hold = self._compact_holds.get(agent_id)
        compacting = hold is not None and hold.sent
        if compacting and out.compacted is not None:
            pre, post = out.compacted
            if pre is None or post is None:
                event = ExternalEvent(
                    ORCHESTRATOR, SESSION_COMPACTED, {}, "Session compacted. Continue."
                )
            else:
                event = ExternalEvent(
                    ORCHESTRATOR,
                    SESSION_COMPACTED,
                    {"pre": _kilo(pre), "post": _kilo(post)},
                    f"Session compacted from {_kilo(pre)} to {_kilo(post)} tokens. "
                    "Continue.",
                )
            self._settle_hold(agent_id, event)
        elif compacting and out.compact_refused is not None:
            self._settle_hold(
                agent_id,
                ExternalEvent(
                    ORCHESTRATOR,
                    SESSION_NOT_COMPACTED,
                    {"reason": "refused"},
                    f"Compaction did not occur: {out.compact_refused} Continue.",
                ),
            )
        elif out.turn_end and watch.pending_compact is not None:
            request, watch.pending_compact = watch.pending_compact, None
            if agent_id in self._pending_ends:
                # The end wins: no `/compact` goes to an agent that is stopping.
                # Released, not dropped: the stop can fail. A stop drops it.
                self._release_hold(agent_id)
            else:
                await self._request_compact(watch, request)

    async def _request_compact(
        self, watch: AgentWatch, request: CompactRequest
    ) -> None:
        agent_id = watch.agent_id
        # A request parked in replay whose turn ends live has no hold yet.
        hold = self._open_hold(agent_id, reply=True, sent=False)
        if request.invalid is not None:
            self._settle_hold(
                agent_id,
                ExternalEvent(
                    ORCHESTRATOR,
                    SESSION_NOT_COMPACTED,
                    {"reason": "invalid-threshold"},
                    f'Could not read min-tokens="{request.invalid}", '
                    "no compaction occurred. Continue.",
                ),
            )
            return
        agent = self.world["agents"].get(agent_id)
        if agent is None:
            # No occupancy to read, and no agent to compact.
            self._release_hold(agent_id)
            return
        tokens = agent["contextTokens"]
        if request.min_tokens is not None and tokens < request.min_tokens:
            self._settle_hold(
                agent_id,
                ExternalEvent(
                    ORCHESTRATOR,
                    SESSION_NOT_COMPACTED,
                    {"reason": "below-threshold"},
                    f"Session only {_kilo(tokens)} tokens "
                    f"(minimum {_kilo(request.min_tokens)}), "
                    "no compaction occurred. Continue.",
                ),
            )
            return
        # A slash command, not an External event: the harness runs it only
        # when the turn is the command and nothing else.
        if await self._tell_of_compact(
            agent_id, f"/compact {request.instructions}".rstrip()
        ):
            hold.sent = True
        else:
            self._release_hold(agent_id)

    async def _tell_of_compact(self, agent_id: str, event: ExternalEvent | str) -> bool:
        """Say one compaction turn to the agent; whether the host took it.

        Past the hold: the agent hears its compaction's own turns first. A
        refusal is logged, not raised: only the agent waits on it.
        """
        _, refusals = await self._tell_now([agent_id], event)
        for _, refused in refusals:
            log.warning(
                "agent %s: compaction turn not delivered: %s",
                agent_id,
                refused["error"]["message"],
            )
        return not refusals

    def _open_hold(self, agent_id: str, *, reply: bool, sent: bool) -> CompactHold:
        """Hold the agent's External events until its compaction settles.

        An open hold is kept, and takes the stronger of each flag. A hold
        already releasing is not: its outcome is in, so the new compaction
        gets a hold of its own.
        """
        hold = self._compact_holds.get(agent_id)
        if hold is None or hold.releasing:
            hold = CompactHold(agent_id, reply=reply, sent=sent)
            self._compact_holds[agent_id] = hold
            hold.backstop = asyncio.get_running_loop().call_later(
                self._compact_backstop, self._backstop_hold, hold
            )
        else:
            hold.reply = hold.reply or reply
            hold.sent = hold.sent or sent
        return hold

    def _backstop_hold(self, hold: CompactHold) -> None:
        """No outcome came in time: deliver what the hold kept."""
        if self._compact_holds.get(hold.agent_id) is not hold or hold.releasing:
            return
        log.warning("agent %s: compaction hold timed out", hold.agent_id)
        self._release_hold(hold.agent_id)

    def _settle_hold(self, agent_id: str, reply: ExternalEvent) -> None:
        """Release the hold, with the compaction's outcome first when the agent asked."""
        hold = self._compact_holds.get(agent_id)
        self._release_hold(agent_id, reply if hold is not None and hold.reply else None)

    def _release_hold(self, agent_id: str, reply: ExternalEvent | None = None) -> None:
        """Start the hold's one drain: ``reply``, then the held turns, in order.

        A task, not inline: the outcome is read on the agent's stream, and a
        long hold must not stop that stream while each turn goes to the host.
        """
        hold = self._compact_holds.get(agent_id)
        if hold is None or hold.releasing:
            return
        hold.releasing = True
        if hold.backstop is not None:
            hold.backstop.cancel()
        task = asyncio.create_task(self._drain_hold(hold, reply))
        self._hold_releases.add(task)
        task.add_done_callback(self._hold_releases.discard)

    async def _drain_hold(self, hold: CompactHold, reply: ExternalEvent | None) -> None:
        if reply is not None:
            await self._tell_of_compact(hold.agent_id, reply)
        # Drained while still in place, so a turn told during the drain
        # queues behind the held ones rather than passing them.
        while hold.held:
            event, attachments = hold.held.pop(0)
            _, refusals = await self._tell_now([hold.agent_id], event, attachments)
            for _, refused in refusals:
                log.warning(
                    "agent %s: held event not delivered: %s",
                    hold.agent_id,
                    refused["error"]["message"],
                )
        if self._compact_holds.get(hold.agent_id) is hold:
            del self._compact_holds[hold.agent_id]

    def _drop_hold(self, agent_id: str) -> None:
        """Close the agent's hold and discard what it kept: no agent will hear it."""
        hold = self._compact_holds.pop(agent_id, None)
        if hold is None:
            return
        if hold.backstop is not None:
            hold.backstop.cancel()
        if hold.held:
            log.info(
                "agent %s: %d held event(s) dropped: the agent stopped",
                agent_id,
                len(hold.held),
            )
        hold.held.clear()

    async def _load_documents(self) -> None:
        """Put every markdown Task document in the world, before any agent is read.

        Before, so an agent whose backlog presents a stored document finds it
        under its store id and makes its next version, not a second entry.
        """
        events: list[ServerEvent] = []
        for row in await self.document_store.all_documents():
            entity = await self._stored_entity(row)
            if entity is not None:
                events.append({"type": "upsert", "kind": "document", "entity": entity})
        self._apply(events)

    async def _stored_entity(
        self, row: TaskAttachment, version: Version | None = None
    ) -> Document | None:
        """``row``'s world document at ``version``, else at its latest version.

        The world id is the store id with this server's prefix, as the
        normaliser makes it. The live source of the world's entry stays: a task
        set's promote reads the file id it carries.
        """
        if version is None:
            versions = await self.document_store.versions(row.id)
            if not versions:
                return None
            version = versions[-1]
        entity = document_entity(row, version, await self.document_store.links(row.id))
        if entity is None:
            return None
        entity["id"] = self.ids.own(row.id)
        current = self.world["documents"].get(entity["id"])
        if current is not None:
            entity["source"] = current["source"]
        return entity

    async def _show_stored(
        self, row: TaskAttachment, version: Version | None = None
    ) -> None:
        """Make the world's document agree with the store."""
        entity = await self._stored_entity(row, version)
        if entity is not None and entity != self.world["documents"].get(entity["id"]):
            self._apply([{"type": "upsert", "kind": "document", "entity": entity}])

    async def _store_documents(
        self, events: list[ServerEvent], *, replay: bool, verdict: bool = False
    ) -> None:
        """Store each task document that ``events`` upserts.

        ``verdict``: the upserts only settle a review, so they write a status
        and never add a version.

        A failure is logged and goes no further: the document is still in the
        world, and a stream must not stop because the store refused a file.
        """
        for event in events:
            if event.get("type") != "upsert" or event.get("kind") != "document":
                continue
            document: Document = event["entity"]  # type: ignore[typeddict-item]
            try:
                await self._store_document(document, replay=replay, verdict=verdict)
            except Exception:  # noqa: BLE001 — see the docstring
                log.exception("storing document %s failed", document["id"])

    async def _store_document(
        self, document: Document, *, replay: bool, verdict: bool = False
    ) -> None:
        """Store ``document`` as its Task document, then show what the store holds.

        A free agent's document is not stored. Whether an upsert adds a version
        or writes a status follows the table in ``docs/dev/orchestrator-server.md``,
        "Storing a Task document". A settled stored status retires the review
        item a replayed tag raised again.
        """
        task = self.world["tasks"].get(document["taskId"])
        if task is None or self._delegated(
            self.world["agents"].get(document["agentId"])
        ):
            # A delegated agent's task is on its parent, which keeps no
            # attachment from another host yet.
            return
        source = document["source"]
        body: str | None = None
        if source.get("type") == "draft_file":
            path = name = str(source["filename"])
        elif document["kind"] == "plan" and source.get("type") == "plan_review":
            path, name = str(source.get("planFilePath") or PLAN_NAME), PLAN_NAME
            body = document["markdown"]
        elif source.get("type") == "stored":
            path, name = "", str(source["name"])
        else:
            return
        project, notebook_id = task["project"], task["notebookId"]
        row = await self.document_store.read(task_key(project, notebook_id), name)
        versions = await self.document_store.versions(row.id) if row else []
        latest = versions[-1] if versions else None
        if verdict and (latest is None or document["version"] > latest.version):
            # The user settled text the store does not hold, such as the
            # placeholder of a file that could not be read.
            return
        # A plan's text travels in its event, so a replay can tell a plan the
        # server missed from one it stored.
        missed = (
            replay
            and row is not None
            and body is not None
            and not await task_documents.holds(
                self.document_store, row, body, self._agent_cwd(document)
            )
        )
        stored: tuple[TaskAttachment, Version] | None
        if (
            row is not None
            and latest is not None
            and not missed
            and (replay or document["version"] <= latest.version or not path)
        ):
            stored = row, latest
        else:
            if not path:
                return
            stored = await attach(
                self.document_store,
                project=project,
                task_id=notebook_id,
                cwd=self._agent_cwd(document),
                path=path,
                name=name,
                title=document["title"],
                now=self.clock(),
                body=body,
                review_status=document["status"],
                role=document["kind"],
                agent_id=document["agentId"],
                group=ReviewGroup(**document["group"]),
            )
        # None: the file could not be read. The document says so, and it stays.
        if stored is None:
            return
        row, version = stored
        if not replay and version.review_status != document["status"]:
            version = await set_status(self.document_store, row, document["status"])
        await self._show_stored(row, version)
        if version is not None and version.review_status != "awaiting-review":
            self._retire_review(self.ids.own(row.id))

    def _agent_cwd(self, document: Document) -> str:
        """The working directory of the agent that presented ``document``."""
        agent = self.world["agents"].get(document["agentId"])
        return agent["cwd"] if agent else ""

    def _retire_review(self, document_id: str) -> None:
        """Clear the open review items of a document the store holds as settled."""
        events: list[ServerEvent] = [
            {
                "type": "upsert",
                "kind": "attention",
                "entity": {**item, "clearedAt": self.clock()},
            }
            for item in self.world["attention"].values()
            if item["kind"] == "document_review"
            and item["documentId"] == document_id
            and item["clearedAt"] is None
        ]
        if events:
            self._apply(events)

    async def _register_pr(self, agent_id: str, ref: str, *, replay: bool) -> None:
        """Register the PR an agent's ``<link rel="gh-pr">`` named on its task.

        A free agent registers nothing. A bare number takes its URL from the
        repo of the agent's worktree. A replayed tag registers only on a task
        with no Registered PR; see docs/dev/orchestrator-server.md.
        """
        agent = self.world["agents"].get(agent_id)
        task_id = agent.get("taskId") if agent else ""
        if not agent or not task_id:
            return
        task = await self._task_of(agent) if replay else None
        if replay and (task is None or task["prNumber"]):
            return
        worktree = self.world["worktrees"].get(agent.get("worktreeId", ""))
        repo_url = await project_repo_url(Path(worktree["path"])) if worktree else None
        try:
            number, url = parse_pr_ref(ref, repo_url)
        except ValueError:
            log.warning("agent %s named no PR in its gh-pr link: %r", agent_id, ref)
            return
        reply = await self._write_task(
            self._home(agent).register_pr, task_id, number, url
        )
        if not reply["ok"]:
            log.warning(
                "agent %s could not register PR #%s on %s: %s",
                agent_id,
                number,
                task_id,
                reply.get("error"),
            )
        elif worktree is not None and not replay:
            # Read the PR now, so the watch's first event lands within a read.
            await self._refresh_worktrees_now(
                {"project": worktree["project"], "branch": worktree["branch"]}
            )

    async def _withdraw_pr(self, agent_id: str, ref: str) -> None:
        """Withdraw the PR an agent's ``<link rel="gh-pr" withdrawn>`` named.

        A free agent withdraws nothing, and nor does a tag that names a PR other
        than the task's Registered PR. See CONTEXT.md, "Withdrawn PR".
        """
        agent = self.world["agents"].get(agent_id)
        task_id = agent.get("taskId") if agent else ""
        if not agent or not task_id:
            return
        task = await self._task_of(agent)
        if task is None:
            log.warning(
                "agent %s withdrew a PR on %s, which is unknown", agent_id, task_id
            )
            return
        try:
            number, _url = parse_pr_ref(ref, None)
        except ValueError:
            number = 0
        if not number or number != task["prNumber"]:
            log.warning(
                "agent %s withdrew %r, which is not the PR registered on %s",
                agent_id,
                ref,
                task_id,
            )
            return
        reply = await self._write_task(self._home(agent).withdraw_pr, task_id)
        if not reply["ok"]:
            log.warning(
                "agent %s could not withdraw PR #%s on %s: %s",
                agent_id,
                number,
                task_id,
                reply.get("error"),
            )

    async def _record_milestone(self, watch: AgentWatch, milestone: Milestone) -> None:
        """Snapshot what the agent had spent when it marked a stage reached.

        Read off the world rather than the raw event: the world is where the
        poll and the stream have both put the agent's totals, so it is the one
        place that holds the figure as it stands now. An agent the world does
        not know writes nothing — there would be no totals to record.

        Called once the declaring turn's ``result`` has been normalised, so the
        figures include that turn. It is usually the stage's most expensive one.

        The write also appends the transcript's bar, from the row it returns:
        the delta the bar reports is the one the ledger just computed, so the
        CLI's report and the UI's bar cannot drift.
        """
        agent = self.world["agents"].get(milestone.agent_id)
        if agent is None:
            return
        row = await self.milestones.record(
            {
                "agent_id": milestone.agent_id,
                "name": milestone.name,
                "at": milestone.at,
                "recognised": milestone.recognised,
                "own_tokens": agent["totalTokens"],
                "subagent_tokens": agent["subagentTokens"],
                "cost_usd": agent["costUsd"],
            }
        )
        await self._emit(
            watch, normalise_milestone(self.state.state, watch.ctx, row, self.clock())
        )

    async def _close_ledger(
        self, agent_id: str, totals: tuple[int, int, float]
    ) -> None:
        """Record what the agent spent after its last stage, as it exits.

        See ``docs/dev/orchestrator-server.md``, "One row comes from no marker
        at all", for why the row is written rather than synthesised on read.

        ``totals`` are the agent's own, subagent and dollar figures as its
        caller read them off the world. They are passed rather than read here
        because this runs after the exit is applied, by which time the world
        reports the agent as gone.

        Nothing is written when the agent spent nothing since its last stage:
        an empty row would report a stage that cost nothing.
        """
        own, sub, cost = totals
        previous = await self.milestones.list(agent_id)
        if own + sub - (_stage_total(previous) if previous else 0) <= 0:
            return
        row = await self.milestones.record(
            {
                "agent_id": agent_id,
                "name": FINAL_STAGE,
                "at": self.clock(),
                "recognised": True,
                "own_tokens": own,
                "subagent_tokens": sub,
                "cost_usd": cost,
            }
        )
        # The bar needs a watch to append to. An agent whose watch has already
        # gone still gets the row: the ledger outlives the transcript, and
        # `mael agent cost` reads the ledger.
        if (watch := self._watches.get(agent_id)) is not None:
            await self._emit(
                watch,
                normalise_milestone(self.state.state, watch.ctx, row, self.clock()),
            )

    async def _emit(self, watch: AgentWatch, out: Normalised) -> None:
        """Take a normaliser's output: keep its context, and publish its events."""
        watch.ctx = out.ctx
        self._item_seeds[watch.agent_id] = out.ctx.next_id - 1
        self._apply(out.events)

    async def _exit(
        self, agent_id: str, exit_code: int | None, *, from_stream: bool = False
    ) -> None:
        """Mark an agent exited, once.

        An exit learned from anywhere but the agent's own stream also ends
        that stream: a host that dropped the connection would otherwise leave
        the watch waiting forever.
        """
        agent = self.world["agents"].get(agent_id)
        if agent is None or agent["state"] == "exited":
            return
        # The totals as the agent finished, read before the exit is applied.
        # Held rather than read again below, because `_close_ledger` runs after
        # the state flips and the world no longer carries them.
        totals = _totals_of(agent)
        watch = self._watches.get(agent_id)
        ctx = watch.ctx if watch else context_for_agent(agent_id)
        out = mark_exited(self.state.state, ctx, exit_code, self.clock())
        if watch:
            watch.ctx = out.ctx
        self._apply(out.events)
        self._drop_hold(agent_id)
        self._pending_ends.discard(agent_id)
        self._sessions_sent.pop(agent_id, None)
        self._sessions_refused.pop(agent_id, None)
        # A param left on with no agent to hear it could not be turned off:
        # every change needs an agent.
        worktree_id = agent["worktreeId"]
        if worktree_id and not agents_in_worktree(self.world, worktree_id):
            self.live_feedback.clear(worktree_id)
        # After the state says exited, so the guard above closes the door on a
        # second `_exit`. `_exit` has four callers and the stream task and the
        # poll loop run concurrently: closing the ledger before the flip would
        # let two of them through and write the closing row twice.
        await self._close_ledger(agent_id, totals)
        if watch and watch.task and not from_stream:
            watch.task.cancel()

    # -- commands --

    async def handle_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run one command and return its reply: ``ok`` with a result, or an error.

        Every command is validated against the world first, so the host is
        only asked things it can do. The agent commands then become one host
        request each, and nothing more: the world change comes back on the
        host's own stream, because the host records what it writes to the
        child. The desk, task and document commands reach the host not at all,
        beyond the message a request for changes relays: the desk and the
        documents are the server's own tables, and a task write goes to the
        notebook. Everything else answers ``invalid`` — comments and shaping
        are out of scope for this server.
        """
        kind = str(command.get("type"))
        handlers = {
            "agent.approve": self._approve,
            "agent.deny": self._deny,
            "agent.answer": self._answer,
            "agent.say": self._say,
            "agent.run": self._run_shell,
            "agent.stop": self._operate,
            "agent.interrupt": self._interrupt,
            "agent.setMode": self._set_mode,
            "agent.resume": self._operate,
            "agent.launch": self._operate,
            "desk.add": self._desk_add,
            "desk.remove": self._desk_remove,
            "desk.dismiss": self._operate,
            "task.setStatus": self._set_status,
            "task.update": self._update_task,
            "task.delete": self._delete_task,
            "task.registerPr": self._register_task_pr,
            "task.withdrawPr": self._withdraw_task_pr,
            "task.addSession": self._add_task_session,
            "mergeRequest.submit": self._submit_merge_request,
            "mergeRequest.withdraw": self._withdraw_merge_request,
            "mergeRequest.merge": self._merge_merge_request,
            "mergeRequest.rebase": self._rebase_merge_request,
            "mergeRequest.retry": self._retry_merge_request,
            "task.infer": self._infer_task,
            "task.create": self._create_task,
            "task.promote": self._promote,
            "task.loadMany": self._load_many,
            "taskDocument.add": self._add_task_document,
            "taskDocument.link": self._link_task_document,
            "taskDocument.unlink": self._link_task_document,
            "taskDocument.archive": self._archive_task_document,
            "comm.create": self._create_comm,
            "comm.update": self._update_comm,
            "comm.draftUpdates": self._draft_comm_updates,
            "comm.updateUpdate": self._update_comm_update,
            "linear.plan": self._operate,
            "agent.start": self._operate,
            "project.add": self._operate,
            "document.approve": self._approve_document,
            "document.requestChanges": self._request_changes,
            "worktree.close": self._operate,
            "worktree.forceClose": self._operate,
            "worktree.trash": self._operate,
            "worktree.remove": self._operate,
            "worktree.sync": self._operate,
            "worktree.mergePr": self._operate,
            "worktree.autoMerge": self._set_auto_merge,
            "worktree.env": self._operate,
            "worktree.createTerminal": self._create_worktree_terminal,
            "worktree.comment": self._comment_on_changes,
            "worktree.feedback": self._send_feedback,
            "worktree.setFeedbackParams": self._set_feedback_params,
            "worktree.refresh": self._refresh_worktrees_now,
            "operation.seen": self._mark_seen,
            "operation.retry": self._retry,
            "self.update": self._self_update,
        }
        handler = handlers.get(kind)
        if handler is None:
            return _refused("invalid", f"Unsupported command: {kind}")
        error = validate_command(
            self.world, command, wait_kind=self._wait_kind(command)
        )
        if error:
            return {"ok": False, "error": error}
        return await handler(command)

    def _wait_kind(self, command: dict[str, Any]) -> str | None:
        """The item type of the request a reply names, or ``None`` if unknown.

        The world holds request ids without their kinds, so the kind is read off
        the transcript item the request opened. A reply is judged by that item,
        never by the agent's state — see ``CONTEXT.md``, "Wait kind".

        A trimmed transcript falls back to the kind the agent's state names, and
        only when the agent holds one wait. The state is the oldest wait on the
        daemon and the newest in the world, so it says nothing reliable about a
        particular request while several are open. One wait makes it exact, and
        it is the case worth covering: approving a question sends an allow with
        no answers, which the agent reads as no answer at all, and no other
        layer refuses that.
        """
        if command.get("type") not in ("agent.approve", "agent.deny", "agent.answer"):
            return None
        agent_id = str(command.get("agentId", ""))
        request_id = command.get("requestId")
        for item in self.pending_requests(agent_id):
            if item.get("requestId") == request_id:
                return str(item.get("type"))
        agent = self.world["agents"].get(agent_id)
        if agent is None or len(agent["pendingRequestIds"]) != 1:
            return None
        return WAIT_ITEM_FOR_STATE.get(agent["state"])

    async def _ask_host(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """One host request; the mapped refusal, or ``None`` on success.

        An agent a remote host owns is asked through that host's own agent
        route, as a browser's request for it would be.
        """
        remote = self._remote_owner(str(payload.get("id") or ""))
        if remote is not None:
            return await self._ask_remote(remote, payload)
        reply = await self.daemon.request(payload)
        if "error" in reply:
            return _refused(_code_for(reply["error"]), reply["error"])
        return None

    async def _ask_remote(
        self, remote: RemoteHost, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """``payload`` as a remote host's agent route takes it: ``say`` only.

        A stop is :meth:`_stop_remote`'s, because it waits for the host's
        operation. Other commands reach a remote agent only through its
        forwarded route. A ``say`` sends its text alone: an attachment's path
        is this server's.
        """
        command = str(payload["cmd"])
        if command != "say":
            return _refused("invalid", f"{command} is not sent to a remote host")
        try:
            await remote.post_json(
                f"/api/agents/{payload['id']}/say", {"text": payload.get("text", "")}
            )
        except HostUnreachable as exc:
            return _refused("unreachable", str(exc))
        return None

    async def _approve(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {
                "cmd": "approve",
                "id": command["agentId"],
                "request": command.get("requestId", ""),
                **({"remember": True} if command.get("remember") is True else {}),
            }
        )

    async def _deny(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {
                "cmd": "deny",
                "id": command["agentId"],
                "request": command.get("requestId", ""),
                "reason": command["reason"],
            }
        )

    async def _answer(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._relay(
            {
                "cmd": "answer",
                "id": command["agentId"],
                "request": command.get("requestId", ""),
                "answers": dict(command["answers"]),
            }
        )

    async def _say(self, command: dict[str, Any]) -> dict[str, Any]:
        """Relay one message, and the paths of any images on it.

        Paths, not bytes: the host is local to the files, so sending base64
        here would carry each image twice over.
        """
        # Defaulted, not indexed: an image-only turn is valid, and the
        # route omits `text` when the body carries none.
        text = command.get("text", "")
        agent = self.world["agents"].get(command["agentId"])
        if agent and agent.get("project"):
            # An attachment ref holds the portable token. The agent needs a
            # path it can read, as a task's content gets at launch.
            text = expand_task_dir(text, agent["project"])
        payload: dict[str, Any] = {"cmd": "say", "id": command["agentId"], "text": text}
        attachments = _attachment_paths(command.get("attachments"))
        if attachments:
            payload["attachments"] = attachments
        if not is_compact_command(payload["text"]):
            return await self._relay(payload)
        # The user asked, not the agent: held, but no reply.
        agent_id = command["agentId"]
        self._open_hold(agent_id, reply=False, sent=True)
        reply = await self._relay(payload)
        if not reply.get("ok"):
            self._release_hold(agent_id)
        return reply

    async def _run_shell(self, command: dict[str, Any]) -> dict[str, Any]:
        """Ask the host to run a shell command in the agent's directory.

        A pure relay, like ``_say``. The host runs it, because the host is
        where the agent's working directory is.
        """
        return await self._relay(
            {"cmd": "run", "id": command["agentId"], "command": command["command"]}
        )

    async def _set_mode(self, command: dict[str, Any]) -> dict[str, Any]:
        """Ask the host to change a running agent's permission mode.

        A pure relay — see docs/dev/orchestrator-server.md.
        """
        return await self._relay(
            {
                "cmd": "set-mode",
                "id": command["agentId"],
                "mode": command["mode"],
            }
        )

    async def _relay(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the host, and let its stream say what happened.

        The four commands that write to the child are pure relays: the wait
        resolves when the host's echoed reply arrives on the attach stream.
        See ``docs/dev/orchestrator-server.md``, "The host owns the control
        plane".
        """
        refused = await self._ask_host(payload)
        if refused:
            return refused
        return {"ok": True, "result": {}}

    async def _interrupt(self, command: dict[str, Any]) -> dict[str, Any]:
        """Abandon the turn the agent is running, and leave the agent alive.

        No exit is synthesised, unlike :meth:`_stop`: the host keeps an
        interrupted agent in its ``list``, and the agent's own stream carries
        the turn ending.
        """
        return await self._relay({"cmd": "interrupt", "id": command["agentId"]})

    async def _infer_task(self, command: dict[str, Any]) -> dict[str, Any]:
        """Name a task from its prose. Reads only — nothing is written.

        The inference calls the task metadata generator, so it runs on the executor
        like every other blocking source call. It is slow enough that the UI
        shows a wait for it.
        """
        names = await self._run(self.tasks.infer, command["draft"])
        return {
            "ok": True,
            "result": {
                "title": names.title,
                "branch": names.branch,
                "command": names.command,
                "mode": mode_for_command(names.command),
            },
        }

    async def _create_task(self, command: dict[str, Any]) -> dict[str, Any]:
        """Write a new task and put it on the desk, then name and launch it.

        The write is done before the reply, so the reply carries the task id
        and the desk entry is there at once. A title or branch left empty is
        named, and a launch runs, as one operation after the reply. A task
        with every field typed and no launch needs no operation. See
        ``docs/dev/orchestrator-server.md``, "Creating work".
        """
        content = str(command.get("content", "")).strip()
        # A duplicate takes its source's title.
        untitled = "from" not in command and not str(command.get("title", "")).strip()
        fields = dict(command)
        if untitled:
            # A stand-in until the operation names it.
            fields["title"] = _first_line(content)
        try:
            task_id = await self._run(
                functools.partial(
                    self.tasks.create,
                    command["project"],
                    fields,
                    None,
                    generate_branch=False,
                )
            )
        except ValueError as exc:
            return _refused("invalid", str(exc))
        await self.refresh_tasks(force=True)
        await self._add_to_desk(desk_id_for_task(task_id))
        # A branch left empty is named only when it took the stand-in: a child
        # takes its sibling's or its parent's branch, and a duplicate may too.
        row = self.world["tasks"][task_id]
        unnamed = ["title"] if untitled else []
        if row["branch"] == _branch_stand_in(task_id, row["parent"]):
            unnamed.append("branch")
        if not unnamed and not command.get("launch"):
            return {"ok": True, "result": {"taskId": task_id}}
        # The operation's key is the task's own, minted a moment ago, so
        # nothing can hold it and the start cannot refuse.
        started = await self._operate(
            {**command, "taskId": task_id, "unnamed": unnamed}
        )
        return {"ok": True, "result": {"taskId": task_id, **started["result"]}}

    async def _promote(self, command: dict[str, Any]) -> dict[str, Any]:
        """Promote a client's drafts as one chain, and put each task on the desk."""
        project = command["project"]
        follows = command.get("follows")
        ends = command.get("followEnds")
        after = command.get("after")
        try:
            created = await self._run(
                functools.partial(
                    self.tasks.promote,
                    project,
                    [(d["name"], d["text"]) for d in command["drafts"]],
                    str(command.get("parent", "")),
                    split_task_key(after)[1] if after else None,
                    overrides=command.get("overrides") or {},
                    # Either flag wires the first task, and moves no follower.
                    follows=None if follows is None and not ends else follows or [],
                    follow_ends=ends,
                )
            )
        except ValueError as exc:
            return _refused("invalid", str(exc))
        await self._desk_new_tasks(created)
        return {"ok": True, "result": {"taskIds": created}}

    async def _load_many(self, command: dict[str, Any]) -> dict[str, Any]:
        """Create every block of a plan file, put each on the desk, and launch.

        With ``launch``, each created task that waits on nothing starts as its
        own launch operation, which its row names. A launch the validator
        refuses is a warning: the tasks exist either way.
        """
        try:
            created, warnings = await self._run(
                self.tasks.load_many,
                command["project"],
                command["text"],
                str(command.get("defaultParent", "")),
            )
        except ValueError as exc:
            return _refused("invalid", str(exc))
        await self._desk_new_tasks([task_id for task_id, _ in created])
        tasks: list[dict[str, Any]] = []
        for task_id, title in created:
            row: dict[str, Any] = {"taskId": task_id, "title": title}
            tasks.append(row)
            if not command.get("launch"):
                continue
            if not self.world["tasks"][task_id]["actionable"]:
                continue
            started = await self.handle_command(
                {"type": "agent.launch", "taskId": task_id}
            )
            if started["ok"]:
                row["operationId"] = started["result"]["operationId"]
            else:
                warnings.append(f"{task_id}: {started['error']['message']}")
        return {"ok": True, "result": {"tasks": tasks, "warnings": warnings}}

    async def _desk_new_tasks(self, task_ids: list[str]) -> None:
        """Read new tasks into the world, and put each on the desk."""
        await self.refresh_tasks(force=True)
        for task_id in task_ids:
            await self._add_to_desk(desk_id_for_task(task_id))

    async def linear_issues(self, project: str) -> dict[str, Any]:
        """A project's Linear issues for the current cycle.

        A read, so it answers directly rather than going through
        ``handle_command``: nothing in the world changes. The Linear call
        blocks, so it runs on the executor like every other source read.
        """
        error = check_linear_project(self.world, project)
        if error:
            return {"ok": False, "error": error}
        try:
            issues = await self._run(linear_source.cycle_issues, project)
        except IntegrationError as exc:
            # A missing key or an unreachable API: say what Linear said.
            return _refused("invalid", str(exc))
        except Exception as exc:  # noqa: BLE001 — the client hears why
            log.exception("could not read %s's Linear issues", project)
            return _refused("invalid", f"Could not reach Linear: {exc}")
        return {
            "ok": True,
            "result": {
                "issues": [
                    {
                        "id": issue["identifier"],
                        "title": issue.get("title") or "",
                        "status": (issue.get("state") or {}).get("name") or "",
                    }
                    for issue in issues
                ]
            },
        }

    async def _approve_document(self, command: dict[str, Any]) -> dict[str, Any]:
        """The user's verdict on a document, and — for a task set — the tasks.

        Approving a task set also promotes its drafts. Either way the agent
        hears the verdict — see ``_tell_agent_of_approval``.
        """
        document = self.world["documents"][command["documentId"]]
        members = self._group_members(document)
        created: list[str] = []
        if _is_task_set(members):
            created, refused = await self._promote_drafts(document, members)
            if refused:
                return refused
        await self._settle_group(members, "approved")
        await self._tell_agent_of_approval(document, created)
        return {"ok": True, "result": {"taskIds": created}}

    async def _tell_agent_of_approval(
        self, document: Document, created: list[str]
    ) -> None:
        """Tell the agent its document is approved, and which tasks it became.

        Best-effort: the document is settled and any tasks exist, so a host
        that refuses the message must not turn the approve into a refusal. The
        agent is left waiting, so the refusal is logged. A promote names the
        tasks because it consumed the agent's drafts.
        """
        title = document["group"]["title"]
        if created:
            body = (
                f"The user approved {title}, which promoted the drafts. The "
                f"tasks now exist: {', '.join(created)}. Do not promote them again."
            )
        elif await self._end_codex_planning(document):
            body = f"The user approved {title}. Plan mode is off. Build the plan."
        else:
            body = f"The user approved {title}. Continue with the work."
        event = ExternalEvent(
            ORCHESTRATOR_UI, DOCUMENT_APPROVED, {"title": title}, body
        )
        _, refusals = await self._tell([document["agentId"]], event)
        for agent_id, refused in refusals:
            log.warning(
                "agent %s: approval not delivered: %s",
                agent_id,
                refused["error"]["message"],
            )

    async def _end_codex_planning(self, document: Document) -> bool:
        """Put a Codex agent in ``auto`` if ``document`` is its plan; return whether it did.

        See ``docs/dev/codex-app-server-events.md``, "Plan mode".
        """
        agent_id = document["agentId"]
        agent = self.world["agents"].get(agent_id)
        if not agent or agent["harness"] != HARNESS_CODEX:
            return False
        if agent["permissionMode"] != "plan":
            return False
        if document["source"].get("filename") != CODEX_PLAN_FILE:
            return False
        refused = await self._ask_host(
            {"cmd": "set-mode", "id": agent_id, "mode": AUTO}
        )
        if refused:
            log.warning(
                "agent %s: plan mode not ended: %s",
                agent_id,
                refused["error"]["message"],
            )
        return not refused

    def _group_members(self, document: Document) -> list[Document]:
        """The current members of ``document``'s review group, in tag order."""
        return group_members(self.world["documents"].values(), document["group"]["id"])

    async def _promote_drafts(
        self, document: Document, members: list[Document]
    ) -> tuple[list[str], dict[str, Any] | None]:
        """Promote a task set's drafts, or say which one stopped it.

        One call for the whole group, in tag order, so the chain runs in the
        order the agent listed the drafts. The paths resolve against the
        agent's worktree and nothing outside it, as reading them did.
        """
        agent = self.world["agents"].get(document["agentId"])
        # The agent's project, not the task's: a free agent may plan a chain
        # too, and its worktree is what says where the tasks belong.
        project = agent["project"] if agent else ""
        if not project:
            return [], _refused("invalid", "The agent is in no project's worktree")
        paths: list[Path] = []
        drafts: list[tuple[str, str]] = []
        for member in members:
            # A `<doc-file>` registers every file it names, so the id resolves
            # to the path the registry already validated. A member with no id
            # was minted when its file was not readable, and there is nothing
            # to promote.
            source = member["source"]
            file_id = source.get("fileId")
            found = self.files.resolve(file_id) if file_id else None
            if found is None:
                name = source.get("filename") or source.get("name")
                return [], _refused("invalid", f"{name} is not a file in the worktree")
            try:
                drafts.append((found.name, found.read_text()))
            except OSError as exc:
                return [], _refused("invalid", f"{found.name}: {exc}")
            paths.append(found)
        try:
            created = await self._run(
                self._home(agent).promote,
                project,
                drafts,
                *await self._planning_task(document),
            )
        except ValueError as exc:
            return [], _refused("invalid", str(exc))
        except TaskSourceRemote as exc:
            return [], _refused("unreachable", str(exc))
        # After the commit: a rollback cannot restore a deleted draft.
        # `missing_ok`: a file already gone must not undo the promote.
        for path in paths:
            path.unlink(missing_ok=True)
        # A delegated agent's tasks are its parent's, and go on its desk.
        if not self._delegated(agent):
            await self._desk_new_tasks(created)
        return created, None

    async def _planning_task(self, document: Document) -> tuple[str, str | None]:
        """The parent the set joins and the task it splices after.

        The server's reading of ``$MAEL_TASK_PARENT`` and ``$MAEL_TASK_ID``. A
        planning session chains what it creates under its own parent, or under
        itself when it has none, so the whole chain shares one branch and one
        PR. The set splices in after the planning task. A free agent has no
        task, so its set joins no parent and follows nothing. A delegated
        agent's task is read from its parent.
        """
        agent = self.world["agents"].get(document["agentId"])
        task = (
            await self._task_of(agent)
            if agent is not None
            else self.world["tasks"].get(document["taskId"])
        )
        if task is None:
            return "", None
        return task["parent"] or task["notebookId"], task["notebookId"]

    async def _request_changes(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send the review group back, and relay the summary to the agent once.

        The relay comes first: a summary the host refuses never reached the
        agent, so the group has to stay where the user left it, awaiting a
        review nobody has answered.
        """
        document = self.world["documents"][command["documentId"]]
        title = document["group"]["title"]
        summary = str(command["summary"]).strip()
        event = ExternalEvent(
            ORCHESTRATOR_UI,
            DOCUMENT_CHANGES_REQUESTED,
            {"title": title},
            f"The user requested changes to {title}:\n\n{summary}",
        )
        _, refusals = await self._tell([document["agentId"]], event)
        if refusals:
            return refusals[0][1]
        await self._settle_group(self._group_members(document), "changes-requested")
        return {"ok": True, "result": {}}

    async def _comment_on_changes(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send the change comments to each agent in the worktree, as one message.

        See orchestrator-server.md, "Commands".
        """
        worktree_id = command["worktreeId"]
        event = ExternalEvent(
            ORCHESTRATOR_UI,
            CHANGES_COMMENTED,
            {"branch": self.world["worktrees"][worktree_id]["branch"]},
            format_change_comments(command["comments"]),
        )
        return await self._tell_worktree(worktree_id, event)

    async def _send_feedback(self, command: dict[str, Any]) -> dict[str, Any]:
        """Send the feedback to each agent in the worktree, as one turn.

        Validation refuses a type with no validator, and each validator has a
        formatter in ``_FEEDBACK_FORMATTERS``.
        """
        worktree_id = command["worktreeId"]
        feedback = dict(command["feedback"])
        worktree = self.world["worktrees"][worktree_id]
        shot = _screenshot(worktree["path"], feedback.pop("screenshot", None))
        if shot:
            feedback["screenshot"] = shot
        event = _FEEDBACK_FORMATTERS[feedback["type"]](worktree["branch"], feedback)
        attachments = [{"path": shot}] if shot else []
        return await self._tell_worktree(worktree_id, event, attachments)

    def live_feedback_worktree(self, path: str) -> str | None:
        """The id of the worktree a live feedback client at ``path`` serves, by real path."""
        real = os.path.realpath(path)
        for worktree in self.world["worktrees"].values():
            if os.path.realpath(worktree["path"]) == real:
                return worktree["id"]
        return None

    def _apply_feedback_params(
        self, worktree_id: str, sets: tuple[ParamSet, ...]
    ) -> None:
        """Apply the params an agent's markers set. The agent set them, so nobody is told."""
        for param in sets:
            if param.service is None:
                self.live_feedback.set_everywhere(worktree_id, param.name, param.value)
            else:
                self.live_feedback.set(
                    worktree_id, param.service, {param.name: param.value}
                )

    async def _set_feedback_params(self, command: dict[str, Any]) -> dict[str, Any]:
        """The user set live feedback params, from the UI or the page. The agents hear of it.

        A set to the current value changes nothing and tells nobody. A change
        no agent took is undone, so the reply and the params agree.
        """
        worktree_id, service = command["worktreeId"], command["service"]
        current = self.live_feedback.values(worktree_id, service)
        changed = {
            name: value
            for name, value in command["params"].items()
            if current[name] != value
        }
        if not changed:
            return {"ok": True, "result": {}}
        self.live_feedback.set(worktree_id, service, changed)
        branch = self.world["worktrees"][worktree_id]["branch"]
        declared = self.live_feedback.declared(worktree_id, service)
        event = ExternalEvent(
            JIG,
            JIG_PARAM_SET,
            {"branch": branch, "service": service},
            "\n".join(
                format_param_set(branch, service, declared[name]["label"], value)
                for name, value in changed.items()
            ),
        )
        reply = await self._tell_worktree(worktree_id, event)
        if not reply["ok"]:
            # Through `revert`, not `set`: the service can close during the
            # say, and a change made meanwhile must stand.
            self.live_feedback.revert(
                worktree_id, service, {name: current[name] for name in changed}, changed
            )
        return reply

    def _live_feedback_changed(self, worktree_id: str) -> None:
        """Write the hub's state for one worktree into its row."""
        worktrees = self.world["worktrees"]
        if worktree_id not in worktrees:
            return
        updated = {
            **worktrees,
            worktree_id: {
                **worktrees[worktree_id],
                "liveFeedback": self.live_feedback.state(worktree_id),
            },
        }
        self._apply(diff_kind("worktree", worktrees, updated))

    async def _tell(
        self,
        agent_ids: list[str],
        event: ExternalEvent | str,
        attachments: list[dict[str, str]] | None = None,
    ) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
        """Say one turn to each agent, one at a time: who took it, and who refused.

        The one way a server-originated turn reaches an agent. Every turn but
        a jig instruction is an External event; see orchestrator-server.md.
        An agent with a Compaction hold open takes the turn into the hold: it
        counts as delivered, because the release delivers it.
        """
        now: list[str] = []
        held: list[str] = []
        for agent_id in agent_ids:
            hold = self._compact_holds.get(agent_id)
            if hold is None:
                now.append(agent_id)
            else:
                hold.held.append((event, attachments))
                held.append(agent_id)
        delivered, refusals = await self._tell_now(now, event, attachments)
        return held + delivered, refusals

    async def _tell_now(
        self,
        agent_ids: list[str],
        event: ExternalEvent | str,
        attachments: list[dict[str, str]] | None = None,
    ) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
        """Say one turn to each agent now, past any Compaction hold."""
        say: dict[str, Any] = {
            "cmd": "say",
            "text": event if isinstance(event, str) else render_event(event),
        }
        if attachments:
            say["attachments"] = attachments
        delivered: list[str] = []
        refusals: list[tuple[str, dict[str, Any]]] = []
        for agent_id in agent_ids:
            refused = await self._ask_host({**say, "id": agent_id})
            if refused:
                refusals.append((agent_id, refused))
            else:
                delivered.append(agent_id)
        return delivered, refusals

    async def _tell_worktree(
        self,
        worktree_id: str,
        event: ExternalEvent | str,
        attachments: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Tell each agent in the worktree. Ok when one agent or more took it."""
        delivered, refusals = await self._tell(
            [agent["id"] for agent in agents_in_worktree(self.world, worktree_id)],
            event,
            attachments,
        )
        if not delivered:
            return refusals[0][1]
        return {
            "ok": True,
            "result": {
                "agentIds": delivered,
                "refused": [
                    {"agentId": agent_id, "message": refused["error"]["message"]}
                    for agent_id, refused in refusals
                ],
            },
        }

    def _task_agents(self, task_key: str) -> list[Agent]:
        """The task's live top-level agents that this server tells.

        A delegated agent is left out: its parent tells it.
        """
        return [
            agent
            for agent in self.world["agents"].values()
            if not agent["parent"]
            and agent["state"] != "exited"
            and agent["taskId"] == task_key
            and not self._delegated(agent)
        ]

    async def _tell_chaperone(self, mr: MergeRequest, event: ExternalEvent) -> bool:
        """Tell the **Chaperone** of ``mr``'s task; true when an agent took it.

        With no live agent, the task's latest exited top-level agent is resumed
        with the event as its first turn. With no agent at all, the event is
        dropped: the status still shows on the node.
        """
        live = [agent["id"] for agent in self._task_agents(mr.task)]
        if live:
            delivered, refusals = await self._tell(live, event)
            for agent_id, refused in refusals:
                log.warning(
                    "agent %s: %s not delivered: %s",
                    agent_id,
                    event.type,
                    refused["error"]["message"],
                )
            return bool(delivered)
        exited = sorted(
            (
                agent
                for agent in self.world["agents"].values()
                if not agent["parent"]
                and agent["state"] == "exited"
                and agent["taskId"] == mr.task
            ),
            key=lambda agent: agent["startedAt"],
        )
        if not exited:
            log.info("task %s has no agent: %s dropped", mr.task, event.type)
            return False
        outcome = await self._resume(exited[-1]["id"], text=render_event(event))
        if outcome.blocked:
            log.warning("could not resume %s: %s", exited[-1]["id"], outcome.blocked)
            return False
        return True

    # -- the PR watch --

    def _pr_recipients(self) -> dict[str, tuple[list[str], bool]]:
        """Each Registered PR whose task has a live top-level agent.

        By PR URL: the agents, and whether the PR is withdrawn. A free agent
        has no task, so it hears nothing.
        """
        world = self.world
        found: dict[str, tuple[list[str], bool]] = {}
        for agent in world["agents"].values():
            if agent["parent"] or agent["state"] == "exited" or not agent["taskId"]:
                continue
            if self._delegated(agent):
                # Its parent watches its task's PR.
                continue
            task = world["tasks"].get(agent["taskId"])
            if task is None or not task["prUrl"]:
                continue
            agent_ids, _ = found.setdefault(task["prUrl"], ([], task["prWithdrawn"]))
            agent_ids.append(agent["id"])
        return found

    def _cached_prs(self) -> list[PrStatus]:
        """Every PR the cache holds, whatever its project or branch."""
        prs = self.worktrees.prs
        if prs is None:
            return []
        return [pr for p in prs.snapshot().values() for pr in p.by_branch.values()]

    def _watch_new_prs(self) -> None:
        """Check a PR that just gained a recipient against what the cache holds.

        The cache tells only of a change, so a PR registered after its last
        read, or one whose agents were read after it at start, is checked here.
        """
        urls = set(self._pr_recipients())
        new, self._watched_prs = urls - self._watched_prs, urls
        if not new:
            return
        for pr in self._cached_prs():
            if pr.url in new:
                self._schedule_pr_watch(pr)

    def _pr_changed(
        self, project: str, branch: str, before: PrStatus | None, after: PrStatus | None
    ) -> None:
        """The cache's listener. The check runs off the read, so a slow ``say``
        never holds a read in flight.

        The read asks for open and merged PRs only, so a PR that leaves the
        answer was closed without a merge.
        """
        if after is None:
            if before is not None:
                self._closed_prs.add(before.url)
            return
        self._closed_prs.discard(after.url)
        self._schedule_pr_watch(after)

    def _schedule_pr_watch(self, pr: PrStatus) -> None:
        task = asyncio.create_task(self._pr_watch(pr))
        self._pr_watches.add(task)
        task.add_done_callback(self._pr_watches.discard)

    async def _pr_watch(self, pr: PrStatus) -> None:
        """Tell ``pr``'s agents what is news in it, and write the ledger.

        The ledger moves past each event an agent took. An event that no
        agent took stays due, with each event after it: the host was down,
        and :meth:`_redeliver` sends that news again. A PR with no recipient
        moves nothing: :meth:`_watch_new_prs` checks it when one arrives.
        """
        try:
            async with self._pr_watch_lock:
                found = self._pr_recipients().get(pr.url)
                if found is None:
                    self._prs_due.discard(pr.url)
                    return
                agent_ids, withdrawn = found
                told = await self.pr_ledger.read(pr.url)
                events, ledger = pr_events(pr, told, withdrawn)
                again = pr.url in self._prs_due
                for sent, event in enumerate(events):
                    if not await self._tell_logged(agent_ids, event, quiet=again):
                        self._prs_due.add(pr.url)
                        if sent:
                            ledger = ledger_without(told, ledger, events[sent:])
                            await self.pr_ledger.save(pr.url, ledger)
                        return
                self._prs_due.discard(pr.url)
                if ledger != told:
                    await self.pr_ledger.save(pr.url, ledger)
        except Exception:  # noqa: BLE001 — nothing awaits this task
            log.exception("the PR watch failed for %s", pr.url)

    async def _pr_watch_again(self, url: str) -> None:
        """Run the watch for a due PR on what the cache holds now."""
        try:
            pr = next((pr for pr in self._cached_prs() if pr.url == url), None)
            if pr is None:
                self._prs_due.discard(url)
            else:
                await self._pr_watch(pr)
        finally:
            self._prs_again.discard(url)

    # -- machine metrics --

    async def _sample_metrics(self) -> None:
        """Read the machine every period, with the running gate jobs' usage.

        See orchestrator-server.md, "Machine metrics".
        """
        assert self._read_machine is not None
        while True:
            try:
                reading = await asyncio.to_thread(self._read_machine)
                self.metrics.add(self._metrics_sample(reading))
                self._metrics_failed = False
            except Exception:  # noqa: BLE001 — one bad read must not stop the sampler
                if not self._metrics_failed:
                    log.exception("machine metrics read failed")
                self._metrics_failed = True
            await asyncio.sleep(self._metrics_period)

    def _metrics_sample(self, reading: MachineReading) -> MetricsSample:
        gates: list[GateMetrics] = [
            {
                "jobId": job["id"],
                "worktree": job["worktree"],
                "path": job["path"],
                "usage": job["usage"],
            }
            for job in self.world["gateJobs"].values()
            if job["state"] == "running" and job["usage"] is not None
        ]
        return {
            "at": self.clock(),
            "cpuPercent": reading.cpu_percent,
            "cpuCount": reading.cpu_count,
            "memoryUsedBytes": reading.memory_used_bytes,
            "memoryTotalBytes": reading.memory_total_bytes,
            "cpuTemperatureC": reading.cpu_temperature_c,
            "gates": gates,
        }

    # -- the gate watch --

    async def _watch_gates(self) -> None:
        """Keep the gate jobs from tangier's watch, and tell each job's end.

        See orchestrator-server.md, "The gate watch".
        """
        assert self._gate_watch is not None
        delay = self._gate_backoff
        while True:
            try:
                async with aclosing(self._gate_watch()) as events:
                    async for event in events:
                        ended = self._gate_table.apply(event)
                        self._publish_gates()
                        for job in ended:
                            self._schedule_gate_tell(job.job)
                            self.build_queue.job_ended(job.job)
                        if event.get("type") == "status":
                            # Only a recent job can end again: forget the rest.
                            self._gate_told &= {
                                str(job.get("id")) for job in event.get("recent") or []
                            }
                        else:
                            # A change, not the snapshot: a tangier that prints
                            # its snapshot and exits stays one outage.
                            self._gate_watch_failed = False
                            delay = self._gate_backoff
                raise GateSourceError("the watch ended")
            except GateSourceError as e:
                if not self._gate_watch_failed:
                    log.warning("gate watch: %s", e)
                self._gate_watch_failed = True
            except Exception:  # noqa: BLE001 — the watch must outlive one bad line
                if not self._gate_watch_failed:
                    log.exception("gate watch failed")
                self._gate_watch_failed = True
            await asyncio.sleep(delay)
            delay = min(delay * 2, GATE_BACKOFF_MAX_SECS)

    def _gate_worktree_ids(
        self, worktrees: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Each worktree's id, by its real path. ``worktrees`` defaults to the world's.

        Tangier records each job's real path; a projects directory under a
        symlink would otherwise match no worktree.
        """
        rows = self.world["worktrees"] if worktrees is None else worktrees
        # Only this server's own: a merged worktree's path is on its host.
        return {
            os.path.realpath(wt["path"]): wt["id"]
            for wt in rows.values()
            if self.ids.is_own(wt["id"])
        }

    def _gate_jobs(self, worktrees: dict[str, Any]) -> dict[str, GateJob]:
        """The gate table's jobs, each linked to its worktree in ``worktrees``."""
        worktree_ids = self._gate_worktree_ids(worktrees)
        return {
            entity["id"]: entity
            for entity in (
                gate_job_entity(job, worktree_ids, self.ids)
                for job in self._gate_table.jobs.values()
            )
        }

    def _publish_gates(self) -> None:
        worktrees = {
            worktree_id: row
            for worktree_id, row in self.world["worktrees"].items()
            if self.ids.is_own(worktree_id)
        }
        jobs = self._gate_jobs(worktrees)
        own = self.ids.prefix
        self._apply(
            diff_kind("gateJob", self.world["gateJobs"], jobs, owner=own)
            + diff_kind(
                "worktree",
                worktrees,
                self._with_merge_requests(with_gates(worktrees, jobs), jobs),
            )
        )

    def _publish_merge_requests(self) -> None:
        """Put each branch's current Merge request on its worktree node."""
        worktrees = {
            worktree_id: row
            for worktree_id, row in self.world["worktrees"].items()
            if self.ids.is_own(worktree_id)
        }
        jobs = self._gate_jobs(worktrees)
        self._apply(
            diff_kind("worktree", worktrees, self._with_merge_requests(worktrees, jobs))
        )

    def _with_merge_requests(
        self, worktrees: dict[str, Any], jobs: dict[str, GateJob]
    ) -> dict[str, Any]:
        """``worktrees``, each with its branch's Merge request from the Build queue.

        A running Build's gate job is in its Build worktree, which is not in
        the world, so its lozenge is found by the Build worktree's path.
        """
        host = self.build_queue.host
        overlay: dict[tuple[str, str], WorktreeMergeRequest] = {}
        for (project, branch), current in self.build_queue.current.items():
            build = current.build
            path = (
                os.path.realpath(host.slot_path(project, build.slot))
                if build is not None and build.state == "running"
                else None
            )
            overlay[(project, branch)] = merge_request_entity(current, jobs, path)
        return with_merge_requests(worktrees, overlay)

    def _schedule_gate_tell(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        if job_id in self._gate_told:
            return
        self._gate_told.add(job_id)
        worktree_id = self._gate_worktree_ids().get(str(job.get("worktree") or ""))
        agent_ids = (
            [a["id"] for a in agents_in_worktree(self.world, worktree_id)]
            if worktree_id is not None
            else []
        )
        if not agent_ids:
            # Nobody to tell, now or again.
            self._gates_due.pop(job_id, None)
            return
        task = asyncio.create_task(self._gate_tell(agent_ids, job))
        self._gate_tells.add(task)
        task.add_done_callback(self._gate_tells.discard)

    async def _gate_tell(self, agent_ids: list[str], job: dict[str, Any]) -> None:
        """Tell the agents of a job's worktree that it ended. Off the watch, so
        a slow ``say`` never holds the next line.

        An event that no agent took leaves the job due, not told.
        """
        job_id = str(job["id"])
        try:
            again = job_id in self._gates_due
            if await self._tell_logged(agent_ids, gate_event(job), quiet=again):
                self._gates_due.pop(job_id, None)
            else:
                self._gate_told.discard(job_id)
                self._gates_due[job_id] = job
        except Exception:  # noqa: BLE001 — nothing awaits this task
            log.exception("the gate tell failed for job %s", job_id)

    async def _tell_logged(
        self, agent_ids: list[str], event: ExternalEvent, *, quiet: bool = False
    ) -> bool:
        """Tell ``event`` and log each refusal. Whether one agent or more took it.

        ``quiet`` is for a tell sent again: its first refusal is in the log.
        """
        delivered, refusals = await self._tell(agent_ids, event)
        for agent_id, refused in () if quiet else refusals:
            log.warning(
                "agent %s: %s not delivered: %s",
                agent_id,
                event.type,
                refused["error"]["message"],
            )
        return bool(delivered)

    def _redeliver(self) -> None:
        """Send again each External event that no agent took.

        Called at each poll the host answers. See "Redelivery" in
        ``docs/dev/orchestrator-server.md``.
        """
        for job in list(self._gates_due.values()):
            self._schedule_gate_tell(job)
        # One watch at a time for each PR: a `say` can outlast a poll.
        for url in self._prs_due - self._prs_again:
            self._prs_again.add(url)
            task = asyncio.create_task(self._pr_watch_again(url))
            self._pr_watches.add(task)
            task.add_done_callback(self._pr_watches.discard)

    async def _settle_group(self, members: list[Document], status: str) -> None:
        """Move every member, and retire the items that asked for the review.

        The document is the server's own table, not the host's, so the change
        is applied here rather than awaited on a stream. A task's member writes
        the status of its stored version too.
        """
        ids = {d["id"] for d in members}
        events: list[ServerEvent] = [
            {"type": "upsert", "kind": "document", "entity": {**d, "status": status}}
            for d in members
        ]
        for item in self.world["attention"].values():
            if item["documentId"] in ids and item["clearedAt"] is None:
                events.append(
                    {
                        "type": "upsert",
                        "kind": "attention",
                        "entity": {**item, "clearedAt": self.clock()},
                    }
                )
        self._apply(events)
        await self._store_documents(events, replay=False, verdict=True)

    # -- operations --

    async def _operate(self, command: dict[str, Any]) -> dict[str, Any]:
        """Start the operation ``command`` names, and answer with its id at once.

        The work runs in the background; its progress is the operation entity,
        on the change stream. See ``docs/dev/orchestrator-server.md``,
        "Operations".
        """
        plan = self._plan(command)
        if isinstance(plan, dict):
            return plan
        return await self._begin(command, plan)

    async def _begin(
        self,
        command: dict[str, Any],
        plan: OperationPlan,
        again: OperationRecord | None = None,
    ) -> dict[str, Any]:
        try:
            operation_id = await self.operations.begin(command, plan, again=again)
        except Busy as exc:
            refused = _refused("invalid", str(exc))
            refused["error"]["operationId"] = exc.operation_id
            return refused
        return {"ok": True, "result": {"operationId": operation_id}}

    def _plan(self, command: dict[str, Any]) -> OperationPlan | dict[str, Any]:
        """What ``command`` runs as an operation, or the refusal when it cannot."""
        kind = command["type"]
        if kind == "desk.dismiss":
            return self._dismiss_plan(command)
        if kind in ("agent.stop", "agent.resume"):
            return self._agent_plan(command)
        if kind == "agent.launch":
            return self._launch_plan(command)
        if kind == "task.create":
            return self._create_plan(command)
        if kind == "linear.plan":
            return self._linear_plan(command)
        if kind == "agent.start":
            return self._start_plan(command)
        if kind == "project.add":
            return self._add_project_plan(command)
        return self._worktree_plan(command)

    # -- launch kinds --

    def _task_launch_steps(
        self,
        launching: "_Launching",
        model_name: str | None,
        hook: OperationHook,
        host: str = "",
    ) -> list[Step]:
        """Claim a task, then start its agent here or on a **Remote host**.

        The steps of a launch, and the tail of a create or a plan that
        launches. The claim moves the task in-progress before anything opens;
        :meth:`_run_launch` rolls it back when no agent starts.

        Here, the steps are the agent start's: open the worktree, write its
        ``.env``, start. On ``host``, one step posts the start spec to the
        host, which runs those steps as its own operation.
        """
        if host:
            return [
                Step(
                    name="start_on_host",
                    run=functools.partial(
                        self._start_on_host, launching, model_name, hook, host
                    ),
                )
            ]
        spec: dict[str, Any] = {}

        async def claim_and_open() -> StepOutcome:
            blocked = await self._claim(launching, model_name)
            if blocked is not None:
                return blocked
            assert launching.claim is not None
            spec.update(self._spec_for(launching.claim))
            return await open_worktree()

        open_worktree, write_env, start_agent = self._agent_start_steps(
            spec, launching, hook
        )
        return [
            Step(name="open_worktree", run=claim_and_open),
            Step(name="write_env", run=write_env),
            Step(name="start_agent", run=start_agent),
        ]

    async def _claim(
        self, launching: "_Launching", model_name: str | None
    ) -> StepOutcome | None:
        """Claim ``launching``'s task for a launch; the refusal, or ``None``."""
        if holder := self._session_holder(launching.task_id):
            return StepOutcome(
                blocked=f"Task {launching.task_id} already has a live agent, "
                f"{holder}. Stop it before relaunching."
            )
        try:
            launching.claim = await self._run(
                functools.partial(
                    self.tasks.claim_launch, new_session=launching.new_session
                ),
                launching.task_id,
                model_name,
            )
        except KeyError:
            return StepOutcome(blocked=f"No task {launching.task_id}")
        except LaunchBlocked as exc:
            return StepOutcome(blocked=str(exc))
        await self.refresh_tasks(force=True)
        return None

    def _spec_for(self, claim: LaunchClaim) -> dict[str, Any]:
        """The start spec of a claimed task's agent."""
        plan = claim.plan
        return {
            "project": claim.project,
            "branch": plan.branch,
            "prompt": plan.prompt,
            "mode": plan.permission_mode or "",
            "model": plan.model,
            "executeModel": plan.execute_model,
            "session": plan.session_id,
            "env": plan.env,
            "taskId": wire_task_key(claim.project, claim.task_id),
        }

    async def _start_on_host(
        self,
        launching: "_Launching",
        model_name: str | None,
        hook: OperationHook,
        host: str,
    ) -> StepOutcome:
        """Claim the task here, and start its agent on ``host`` as a **Delegated agent**."""
        remote = self.remote_hosts.get(host)
        if remote is None:
            return StepOutcome(blocked=f"No remote host {host}")
        if not self.own_url:
            return StepOutcome(
                blocked="This server has no orchestrator_url to give a remote host"
            )
        blocked = await self._claim(launching, model_name)
        if blocked is not None:
            return blocked
        assert launching.claim is not None
        spec = {**self._spec_for(launching.claim), "parentUrl": self.own_url}
        try:
            reply = await remote.post_json("/api/agents", spec)
            operation_id = str(reply["operationId"])
        except (HostUnreachable, KeyError) as exc:
            return StepOutcome(blocked=f"{host} did not start the agent: {exc}")
        launching.handed_off = True
        try:
            operation = await remote.await_operation(operation_id)
        except HostUnreachable as exc:
            return StepOutcome(
                blocked=f"{host} took the start, but its result is unknown: {exc}. "
                "The task stays in progress."
            )
        agent_id = str(operation.get("agentId") or "")
        if operation["state"] != DONE or not agent_id:
            launching.handed_off = False
            return StepOutcome(blocked=f"{host}: {operation.get('words') or 'failed'}")
        launching.agent_id = agent_id
        hook.target(agent_id=agent_id, worktree_id=operation.get("worktreeId"))
        # The remote host's router resumes a Codex thread by its bare id.
        await self._run(
            self.tasks.started, launching.claim, IdSpace(host).strip(agent_id)
        )
        await self._add_to_desk(desk_id_for_task(launching.task_id))
        return StepOutcome(messages=[f"Started {agent_id} on {host}."])

    def _agent_start_steps(
        self,
        spec: dict[str, Any],
        launching: "_Launching",
        hook: OperationHook,
        prompt_file: Path | None = None,
    ) -> tuple[
        Callable[[], Awaitable[StepOutcome]],
        Callable[[], Awaitable[StepOutcome]],
        Callable[[], Awaitable[StepOutcome]],
    ]:
        """Open ``spec``'s worktree, write its ``.env``, and start its agent.

        The one agent start, whatever asked for it: a free agent, a task
        launch, or a parent's delegated launch. ``spec`` holds the start spec;
        a task launch fills it in once its claim has run. ``prompt_file``
        stands in for the agent prompt, for an investigation.
        """

        async def open_worktree() -> StepOutcome:
            session = str(spec.get("session") or "")
            task_id = str(spec.get("taskId") or "")
            try:
                if session:
                    live = await self.opener.live_sessions().sweep()
                    check_not_live(task_id, session, live)
                setup = await self.opener.open(
                    spec["project"],
                    launching.branch or str(spec.get("branch") or ""),
                )
                # A never-run Codex task has no session yet, so the task id,
                # not the session, marks a task launch.
                if task_id:
                    check_synced(task_id, str(spec.get("branch") or ""), setup)
            except LaunchBlocked as exc:
                return StepOutcome(blocked=str(exc))
            launching.setup = setup
            hook.target(
                worktree_id=self.ids.own(
                    get_worktree_folder_name(spec["project"], setup.name)
                )
            )
            return StepOutcome(messages=[_opened(setup)])

        async def write_env() -> StepOutcome:
            assert launching.setup is not None  # open_worktree ran first
            return StepOutcome(
                messages=await self.opener.write_env(spec["project"], launching.setup)
            )

        async def start_agent() -> StepOutcome:
            assert launching.setup is not None  # open_worktree ran first
            payload = self._start_payload(spec, launching.setup, prompt_file)
            reply = await self.daemon.request(payload)
            agent_id = reply.get("id") if "error" not in reply else None
            if not agent_id:
                return StepOutcome(
                    blocked=reply.get("error") or "agent host sent no agent id"
                )
            launching.agent_id = agent_id
            hook.target(agent_id=agent_id)
            session = ""
            if launching.claim is not None:
                session = await self._run(self.tasks.started, launching.claim, agent_id)
                await self.refresh_tasks(force=True)
                await self._add_to_desk(desk_id_for_task(launching.task_id))
            else:
                # A worktree provisioned a moment ago is not in the world yet,
                # and the adoption links the agent to it by cwd.
                await self.refresh_worktrees()
            await self._adopt(_started_row(agent_id, payload, session))
            if launching.claim is None:
                # The desk join is explicit: ``_adopt`` alone does not file an
                # agent, only the reconcile that follows one does.
                await self._join_desk(agent_id)
            return StepOutcome(messages=[f"Started {agent_id}."])

        return open_worktree, write_env, start_agent

    def _start_payload(
        self, spec: dict[str, Any], setup: WorktreeSetup, prompt_file: Path | None
    ) -> dict[str, Any]:
        """The daemon's ``start`` for ``spec`` in its opened worktree.

        A task that has run before already owns its session id, so it resumes.
        For Codex, the session is the thread id the task stores.
        A **Delegated agent** gets ``MAEL_PARENT_URL``, and the router keeps
        its parent and task on its record.
        """
        session = str(spec.get("session") or "")
        model = str(spec.get("model") or "") or None
        parent_url = str(spec.get("parentUrl") or "")
        env = dict(spec.get("env") or {})
        if parent_url:
            env[PARENT_URL_ENV] = parent_url
        codex = bool(model) and resolve_model_reference(model).harness == HARNESS_CODEX
        payload = build_start_payload(
            setup.path,
            prompt=str(spec.get("prompt") or ""),
            permission_mode=model_permission_mode(str(spec.get("mode") or "")),
            model=model,
            execute_model=str(spec.get("executeModel") or "") or None,
            session_id=session or None,
            env=env or None,
            resume=bool(session)
            and (codex or self.opener.has_transcript(setup.path, session)),
            system_prompt_file=prompt_file or agent_prompt_file(),
            plugin_dir=core_plugin_dir(),
        )
        if parent_url:
            payload[PARENT_URL] = parent_url
            payload[TASK_ID] = str(spec.get("taskId") or "")
        return payload

    def _session_holder(self, task_id: str) -> str:
        """The live agent that holds ``task_id``'s session, else ``""``.

        The live-session sweep reads the process table, which holds no Codex
        thread. The host's own rows hold both harnesses.
        """
        task = self.world["tasks"].get(task_id)
        sessions = {entry["id"] for entry in task["sessions"]} if task else set()
        return next(
            (
                agent["id"]
                for agent in self.world["agents"].values()
                if agent["session"] in sessions
                and not agent["parent"]
                and agent["state"] != "exited"
            ),
            "",
        )

    async def _run_launch(
        self, steps: list[Step], hook: StepHook, launching: "_Launching"
    ) -> None:
        """Run a launch kind's steps; put the task back if no agent started."""
        try:
            await _run_own_steps(steps, hook)
        except BaseException:
            if (
                launching.claim is not None
                and not launching.agent_id
                and not launching.handed_off
            ):
                await self._run(self.tasks.rollback, launching.claim)
            raise

    async def _refresh_launched(self) -> None:
        # A launch moves the task, and may have provisioned the worktree.
        await self.refresh_tasks(force=True)
        await self.refresh_worktrees()

    def _task_title(self, task_id: str) -> str:
        task = self.world["tasks"].get(task_id)
        return task["title"] if task else task_id

    def _launch_plan(self, command: dict[str, Any]) -> OperationPlan:
        """Launch a task: its worktree opens, its ``.env`` is written, its agent starts."""
        task_id = command["taskId"]
        launching = _Launching(
            project=split_task_key(task_id)[0],
            task_id=task_id,
            new_session=bool(command.get("newSession")),
        )
        host = str(command.get("host") or "")
        steps = REMOTE_LAUNCH_STEPS if host else LAUNCH_STEPS

        async def work(hook: OperationHook) -> None:
            launch_steps = self._task_launch_steps(
                launching, command.get("model"), hook, host
            )
            await self._run_launch(launch_steps, hook, launching)

        title = self._task_title(task_id)
        return OperationPlan(
            "launch",
            frozenset({desk_id_for_task(task_id)}),
            f"Launching {title}",
            f"Launched {title}",
            "Could not launch",
            work,
            self._refresh_launched,
            project=launching.project,
            steps=steps,
            replay=frozenset(steps),
            task_id=task_id,
        )

    def _create_plan(self, command: dict[str, Any]) -> OperationPlan:
        """Name a task just written, then launch it when asked.

        ``unnamed`` lists the fields the user left empty. Each is written only
        while it still holds its stand-in, so a field edited while the
        inference ran is kept. A task with no prose is named from its title.
        """
        task_id = command["taskId"]
        launching = _Launching(project=command["project"], task_id=task_id)
        unnamed = list(command.get("unnamed") or [])
        launch = bool(command.get("launch"))

        parent = self.world["tasks"][task_id]["parent"]
        # What `_create_task` wrote in place of each empty field.
        stand_ins = {
            "title": _first_line(command["content"]) if "title" in unnamed else "",
            "branch": _branch_stand_in(task_id, parent),
        }

        async def name_task() -> StepOutcome:
            task = self.world["tasks"].get(task_id)
            draft = (task["content"] or task["title"]) if task else ""
            names = await self._run(self.tasks.infer, draft)
            # Read again after the inference: the user may have edited meanwhile.
            task = self.world["tasks"].get(task_id)
            named = {
                f: getattr(names, f)
                for f in unnamed
                if task is not None and task[f] == stand_ins[f]
            }
            if "branch" in named and parent.startswith("linear."):
                # The bare issue number leads the branch, as `linear plan` writes it.
                named["branch"] = lead_with_number(
                    named["branch"], parent.rsplit("-", 1)[-1]
                )
            if named:
                await self._run(self.tasks.update, task_id, named)
            await self.refresh_tasks(force=True)
            return StepOutcome(messages=[f"{f.title()}: {v}" for f, v in named.items()])

        async def work(hook: OperationHook) -> None:
            steps = [Step(name="name_task", run=name_task)] if unnamed else []
            if launch:
                steps += self._task_launch_steps(launching, command.get("model"), hook)
            await self._run_launch(steps, hook, launching)

        title = self._task_title(task_id)
        steps = (("name_task",) if unnamed else ()) + (LAUNCH_STEPS if launch else ())
        return OperationPlan(
            "create",
            frozenset({desk_id_for_task(task_id)}),
            f"Creating {title}",
            f"Created {title}",
            "Could not create the task",
            work,
            self._refresh_launched,
            project=launching.project,
            steps=steps,
            replay=frozenset(LAUNCH_STEPS),
            task_id=task_id,
        )

    def _linear_plan(self, command: dict[str, Any]) -> OperationPlan:
        """Plan a Linear issue: the equivalent of ``mael linear plan``.

        Reads the issue's brief, names its branch, and writes the planning task
        ``linear plan`` would write, then launches it when asked. The task id
        is known only once it is written, so it is kept on the stored command:
        a retry does not write the task twice.
        """
        project = command["project"]
        issue_id = command["issueId"]
        launching = _Launching(project=project, task_id=command.get("taskId", ""))
        launch = bool(command.get("launch"))
        level = {k: command[k] for k in ("command", "mode") if k in command}
        read: dict[str, Any] = {}

        async def read_issue() -> StepOutcome:
            if launching.task_id:
                return StepOutcome(messages=["Written already."])
            try:
                read.update(
                    await self._run(linear_source.plan_fields, project, issue_id)
                )
            except IntegrationError as exc:
                # A missing key or an unreachable API is the user's to fix.
                return StepOutcome(blocked=str(exc))
            return StepOutcome(messages=[f"Read {issue_id}."])

        async def name_task() -> StepOutcome:
            if launching.task_id:
                return StepOutcome(messages=["Written already."])
            names = await self._run(self.tasks.infer, read["content"])
            # The bare issue number leads the branch, as `linear plan` writes it.
            read["branch"] = lead_with_number(names.branch, issue_id.rsplit("-", 1)[-1])
            return StepOutcome(messages=[f"Branch: {read['branch']}"])

        def write_task(hook: OperationHook) -> Step:
            async def run() -> StepOutcome:
                # A retry after a fault past the write has the id already.
                if launching.task_id:
                    return StepOutcome(messages=["Written already."])
                task_id = await self._run(
                    self.tasks.create,
                    project,
                    {**read, **level, "project": project},
                    # Not a client's to choose: the parent binds the task to
                    # its issue, and the action moves that issue when the
                    # planning session ends.
                    {"parent": read["parent"], "post_action": read["post_action"]},
                )
                launching.task_id = task_id
                hook.target(task_id=task_id)
                hook.remember(taskId=task_id)
                await self.refresh_tasks(force=True)
                await self._add_to_desk(desk_id_for_task(task_id))
                return StepOutcome(messages=[f"Wrote {task_id}."])

            return Step(name="write_task", run=run)

        async def work(hook: OperationHook) -> None:
            steps = [
                Step(name="read_issue", run=read_issue),
                Step(name="name_task", run=name_task),
                write_task(hook),
            ]
            if launch:
                steps += self._task_launch_steps(launching, None, hook)
            await self._run_launch(steps, hook, launching)

        steps = ("read_issue", "name_task", "write_task")
        return OperationPlan(
            "plan",
            frozenset({f"linear:{project}/{issue_id}"}),
            f"Planning {issue_id}",
            f"Planned {issue_id}",
            f"Could not plan {issue_id}",
            work,
            self._refresh_launched,
            project=project,
            steps=steps + (LAUNCH_STEPS if launch else ()),
            replay=frozenset({"read_issue", "name_task", *LAUNCH_STEPS}),
            task_id=launching.task_id or None,
        )

    def _add_project_plan(
        self, command: dict[str, Any]
    ) -> OperationPlan | dict[str, Any]:
        """Clone a new project: the equivalent of ``mael project add``. Runs on the worktree pool."""
        add = self.worktrees.add_project
        if add is None:
            return _refused("invalid", "This server cannot add projects")
        git_url = command["gitUrl"].strip()
        name = extract_project_name(git_url)

        async def clone() -> StepOutcome:
            try:
                await self._run_worktree(add, git_url)
            except CloseBlocked as exc:
                return StepOutcome(blocked=str(exc))
            return StepOutcome(messages=[f"Cloned {git_url}."])

        async def work(hook: OperationHook) -> None:
            await _run_own_steps([Step(name="clone", run=clone)], hook)

        return OperationPlan(
            "addProject",
            frozenset({f"project:{name}"}),
            f"Adding {name}",
            f"Added {name}",
            f"Could not add {name}",
            work,
            lambda: self.refresh_worktrees(force=True),
            project=name,
            steps=("clone",),
        )

    def _start_plan(self, command: dict[str, Any]) -> OperationPlan | dict[str, Any]:
        """Start an agent from a start spec: a free agent, or a parent's delegated one.

        A free agent's spec names no ``session``, ``env`` or ``taskId``. A
        **Parent server**'s delegated launch names them, and its ``parentUrl``; see
        ``docs/dev/remote-hosts.md``. The worktree is opened first, so a branch
        with none gets one provisioned. An empty branch is named from the
        prompt first, and kept on the stored command for a retry.

        An investigation whose rules file is gone is refused before the
        operation starts: without the rules it is an ordinary free agent.
        """
        prompt_file: Path | None = None
        if command.get("investigate"):
            prompt_file = investigation_prompt_file()
            if prompt_file is None:
                return _refused("invalid", "The investigation rules are missing")
        project = command["project"]
        typed = str(command.get("branch", "")).strip()
        launching = _Launching(
            project=project,
            branch=typed or str((command.get("named") or {}).get("branch", "")),
        )
        spec = {k: command[k] for k in START_SPEC_KEYS if k in command}

        def name_branch(hook: OperationHook) -> Step:
            async def run() -> StepOutcome:
                names = await self._run(self.tasks.infer, command["prompt"])
                launching.branch = names.branch
                hook.remember(named={"branch": names.branch})
                return StepOutcome(messages=[f"Branch: {names.branch}"])

            return Step(name="name_branch", run=run)

        async def work(hook: OperationHook) -> None:
            open_worktree, write_env, start_agent = self._agent_start_steps(
                spec, launching, hook, prompt_file
            )
            steps = [] if typed else [name_branch(hook)]
            steps += [
                Step(name="open_worktree", run=open_worktree),
                Step(name="write_env", run=write_env),
                Step(name="start_agent", run=start_agent),
            ]
            await _run_own_steps(steps, hook)

        subject = f"an agent on {launching.branch}" if launching.branch else "an agent"
        return OperationPlan(
            "start",
            frozenset(
                {f"branch:{project}/{launching.branch}"} if launching.branch else ()
            ),
            f"Starting {subject}",
            f"Started {subject}",
            "Could not start the agent",
            work,
            # Again, now the branch is on the desk: the node draws with its
            # pull request at once rather than at the next poll.
            self.refresh_worktrees,
            project=project,
            steps=(() if typed else ("name_branch",)) + LAUNCH_STEPS,
            replay=frozenset(LAUNCH_STEPS),
        )

    def _agent_plan(self, command: dict[str, Any]) -> OperationPlan:
        """Terminate or resume one agent, as an operation of one step."""
        agent_id = command["agentId"]
        agent = self.world["agents"][agent_id]
        if command["type"] == "agent.stop":
            kind, doing, done, failing = (
                "terminate",
                "Terminating",
                "Terminated",
                "Could not terminate",
            )
            step = Step(
                name="stop_agent", run=functools.partial(self._stop_agent, agent_id)
            )
        else:
            kind, doing, done, failing = (
                "resume",
                "Resuming",
                "Resumed",
                "Could not resume",
            )
            text = str(command.get("text", "")).strip()
            step = Step(
                name="resume_agent",
                run=functools.partial(
                    self._resume, agent_id, text, compact=command.get("compact") is True
                ),
            )

        async def work(hook: OperationHook) -> None:
            await _run_own_steps([step], hook)

        return OperationPlan(
            kind,
            frozenset({desk_id_for_agent(agent_id)}),
            f"{doing} {agent_id}",
            f"{done} {agent_id}",
            failing,
            work,
            self.refresh_agents,
            project=agent["project"],
            steps=(step.name,),
            # The agent's wire fields are "" when absent; the operation's are None.
            worktree_id=agent["worktreeId"] or None,
            task_id=agent["taskId"] or None,
            agent_id=agent_id,
        )

    async def _resume(
        self, agent_id: str, text: str, *, compact: bool = False
    ) -> StepOutcome:
        """Ask the host to start an exited agent again, under its own id.

        Nothing is synthesised, unlike a stop: the host's own ``list`` is the
        evidence the agent is back, and the operation's refresh reads it.

        With ``compact``, the first turn is ``/compact``, under a Compaction
        hold opened before the child starts: no event can take the turn first.
        """
        if compact:
            text = "/compact"
            self._open_hold(agent_id, reply=True, sent=True)
        payload = build_resume_payload(
            agent_id,
            text=text,
            system_prompt_file=agent_prompt_file(),
            plugin_dir=core_plugin_dir(),
        )
        refused = await self._ask_host(payload)
        if refused:
            if compact:
                self._release_hold(agent_id)
            return StepOutcome(blocked=refused["error"]["message"])
        return StepOutcome(messages=[f"Resumed {agent_id}."])

    def _unit_name(self, desk_id: str) -> str:
        """What a person calls a desk unit: its task's title, or its agent's id."""
        entity_kind, entity_id = split_desk_id(desk_id)
        task = self.world["tasks"].get(entity_id) if entity_kind == "task" else None
        return task["title"] if task else entity_id

    def _dismiss_plan(self, command: dict[str, Any]) -> OperationPlan | dict[str, Any]:
        """A **Dismiss**: the agent's stop or the worktree's end, then off the desk.

        A worktree chain sends no stop of its own: the teardown stops the
        agents. A refusal of the end leaves the node on the desk. Validation
        proved the unit is in the world, and a terminate's agent is running.
        """
        desk_id = command["id"]
        entity_kind, entity_id = split_desk_id(desk_id)
        off_desk = Step(name="off_desk", run=functools.partial(self._off_desk, desk_id))
        ending = DISMISS_CHAINS[command["chain"]]
        hold = {desk_id}
        agent_id = entity_id if entity_kind == "agent" else None
        worktree_id: str | None = None
        refresh: Callable[[], Awaitable[None]] = self.refresh_agents
        if ending is None:
            agent = live_agent_of(self.world, desk_id)
            assert agent is not None  # validated
            agent_id = agent["id"]
            hold.add(desk_id_for_agent(agent_id))
            steps = ["stop_agent", "off_desk"]
            stop = Step(
                name="stop_agent", run=functools.partial(self._stop_agent, agent_id)
            )

            async def work(hook: OperationHook) -> None:
                await _run_own_steps([stop, off_desk], hook)

        else:
            ending_id: str = command["worktreeId"]
            worktree_id = ending_id
            remote = self._remote_owner(ending_id)
            if remote is not None:
                # The host ends its own worktree. The merge brings the change,
                # so nothing here is read again.
                end = Step(
                    name="end_on_host",
                    run=functools.partial(self._end_on_host, remote, ending_id, ending),
                )
                hold.add(worktree_key(ending_id))
                refresh = _nothing
                steps = ["end_on_host", "off_desk"]

                async def work(hook: OperationHook) -> None:
                    await _run_own_steps([end, off_desk], hook)

            else:
                inner = self._worktree_plan({"type": ending, "worktreeId": worktree_id})
                if isinstance(inner, dict):
                    return inner
                hold |= inner.keys
                refresh = inner.refresh
                steps = ["off_desk"]

                async def work(hook: OperationHook) -> None:
                    chain = ChainHook(hook, ["off_desk"])
                    await inner.work(chain)
                    await _run_own_steps([off_desk], chain)

        name = self._unit_name(desk_id)
        return OperationPlan(
            "dismiss",
            frozenset(hold),
            f"Dismissing {name}",
            f"Dismissed {name}",
            "Could not dismiss",
            work,
            refresh,
            project=self._unit_project(desk_id),
            steps=tuple(steps),
            worktree_id=worktree_id,
            task_id=entity_id if entity_kind == "task" else None,
            agent_id=agent_id,
        )

    def _unit_project(self, desk_id: str) -> str:
        """The project a desk unit belongs to."""
        entity_kind, entity_id = split_desk_id(desk_id)
        table = self.world["tasks"] if entity_kind == "task" else self.world["agents"]
        entity = table.get(entity_id)
        return str(entity.get("project") or "") if entity else ""

    async def _stop_agent(self, agent_id: str) -> StepOutcome:
        """End an agent's process. The step of a Terminate, and of its Dismiss."""
        remote = self._remote_owner(agent_id)
        if remote is not None:
            return await self._stop_remote(remote, agent_id)
        refused = await self._ask_host({"cmd": "stop", "id": agent_id})
        if refused:
            return StepOutcome(blocked=refused["error"]["message"])
        # The host drops a stopped agent, so its stream ends without a marker
        # and the next list no longer names it: this is its clean exit.
        await self._exit(agent_id, 0)
        return StepOutcome(messages=[f"Stopped {agent_id}."])

    async def _stop_remote(self, remote: RemoteHost, agent_id: str) -> StepOutcome:
        """Stop a remote host's agent, and end when the host's stop has ended.

        The host runs the stop as its own operation. A refusal or a fault of
        it is this step's refusal, in the host's words. The host exits the
        agent, and the merge brings the exit here.
        """
        try:
            reply = await remote.post_json(f"/api/agents/{agent_id}/stop", {})
        except HostUnreachable as exc:
            return StepOutcome(blocked=str(exc))
        return await self._await_remote(remote, reply, f"Stopped {agent_id}.")

    async def _end_on_host(
        self, remote: RemoteHost, worktree_id: str, ending: str
    ) -> StepOutcome:
        """End a remote host's worktree through the host's own worktree route.

        The step of a **Dismiss** whose worktree the host owns. It ends when
        the host's operation ends.
        """
        method, suffix = _HOST_WORKTREE_ROUTES[ending]
        path = f"/api/worktrees/{worktree_id}{suffix}"
        try:
            if method == "DELETE":
                reply = await remote.delete_json(path)
            else:
                reply = await remote.post_json(path, {})
        except HostUnreachable as exc:
            return StepOutcome(blocked=str(exc))
        _op_kind, _attr, _cannot, _doing, done, _failing = _OPERATION_COMMANDS[ending]
        return await self._await_remote(
            remote, reply, f"{done} {worktree_id} on {remote.name}."
        )

    async def _await_remote(
        self, remote: RemoteHost, reply: dict[str, Any], done: str
    ) -> StepOutcome:
        """The step outcome of the host's operation that ``reply`` names, once it has ended."""
        operation_id = reply.get("operationId")
        if not operation_id:
            return StepOutcome(blocked=f"{remote.name} started no operation")
        try:
            operation = await remote.await_operation(str(operation_id))
        except HostUnreachable as exc:
            return StepOutcome(
                blocked=f"The request reached {remote.name}, but its result is "
                f"unknown: {exc}"
            )
        if operation["state"] != DONE:
            return StepOutcome(
                blocked=f"{remote.name}: {operation.get('words') or 'failed'}"
            )
        return StepOutcome(messages=[done])

    async def _off_desk(self, desk_id: str) -> StepOutcome:
        """Take a unit off the desk. A live node draws with no entry, so there may be none."""
        table = self.world["desk"]
        if desk_id not in table:
            return StepOutcome(messages=["Not on the desk."])
        await self._set_desk(desk_model.remove(table, desk_id))
        return StepOutcome(messages=["Taken off the desk."])

    def _worktree_plan(self, command: dict[str, Any]) -> OperationPlan | dict[str, Any]:
        """What a worktree command runs as an operation, or the refusal when it cannot.

        Validation proved the worktree is in the world, so its row is here.
        """
        kind = command["type"]
        if kind not in _OPERATION_COMMANDS:
            # A retry of a stored operation whose command is now a plain one.
            return _refused(
                "invalid", "This no longer runs as an operation. Use its control again."
            )
        worktree_id = command["worktreeId"]
        if not self.ids.is_own(worktree_id):
            # A merged row names the host's paths: git must not run on them here.
            return _refused(
                "unknown_id",
                f"{worktree_id} is a worktree of {owner_of(worktree_id)}, not of "
                "this server",
            )
        op_kind, attr, cannot, doing, done, failing = _OPERATION_COMMANDS[kind]
        fn = getattr(self.worktrees, attr)
        if fn is None:
            return _refused("invalid", cannot)
        row = self.world["worktrees"][worktree_id]
        project, nato, path = row["project"], row["nato"], row["path"]
        subject = nato

        async def teardown_refresh() -> None:
            # A teardown that fails partway has still stopped agents and freed
            # ports, so the world is stale whichever way it ended.
            await self.refresh_worktrees()
            await self.refresh_agents()

        async def sync_refresh() -> None:
            # A sync moves the branch, and starts and stops no agent. Every
            # mode pushes, so the PR is read after the write.
            await self._run(self.worktrees.refresh_prs, project, row["branch"])
            await self.refresh_worktrees(extra_branches={row["branch"]})

        async def merge_refresh() -> None:
            # A read in flight may hold the PR from before the merge, so this
            # one waits it out. A refusal means the reading was stale.
            await self.refresh_worktrees(extra_branches={row["branch"]})

        def work(*args: Any) -> Callable[[StepHook], Awaitable[None]]:
            return lambda hook: self._run_worktree_op(
                fn, project, nato, path, *args, hook=hook
            )

        run = work()
        # An environment change moves no agent, so it re-reads the worktrees alone.
        refresh: Callable[[], Awaitable[None]] = self.refresh_worktrees
        if kind in ("worktree.close", "worktree.trash", "worktree.remove"):
            refresh = teardown_refresh
        elif kind == "worktree.forceClose":
            home = self._worktree_home(worktree_id)
            run = lambda hook: self._run_worktree_op(  # noqa: E731
                fn, project, nato, path, hook=hook, home=home
            )

            async def shelve_refresh() -> None:
                # A forced close can write a reopen task, so the tasks are read
                # too. A parent's notebook is the parent's to read.
                await teardown_refresh()
                if home is self.homes.local:
                    await self.refresh_tasks(force=True)

            refresh = shelve_refresh
        elif kind == "worktree.sync":
            run, refresh = work(command["mode"]), sync_refresh
        elif kind == "worktree.mergePr":
            subject = f"the pull request of {nato}"
            refresh = merge_refresh
            run = lambda hook: _github_call_as_a_step(  # noqa: E731
                "merge_pr", lambda: fn(project, row["branch"], path), hook
            )
        elif kind == "worktree.env":
            service = command.get("service")
            subject = (
                f"{service} in {nato}" if service else f"the environment of {nato}"
            )
            doing, done = _ENV_WORDS[command["action"]]
            run = work(command["action"], service)
        return OperationPlan(
            op_kind,
            frozenset({worktree_key(worktree_id)}),
            f"{doing} {subject}",
            f"{done} {subject}",
            failing,
            run,
            refresh,
            project=project,
            worktree_id=worktree_id,
        )

    async def _run_worktree_op(
        self, fn: Callable[..., Any], *args: Any, hook: StepHook, **kwargs: Any
    ) -> Any:
        """Run one worktree operation's source call, telling ``hook`` its steps."""
        return await self._run_worktree(
            functools.partial(fn, hook=hook, **kwargs), *args
        )

    async def operation_log(self, operation_id: str) -> dict[str, Any] | None:
        """One operation's log lines, grouped by step in the order they ran.

        ``None`` when the operation is not kept.
        """
        record = await self.operations.read(operation_id)
        if record is None:
            return None
        order = [s["name"] for s in record.operation["steps"]]
        order += [name for name in record.log if name not in order]
        return {
            "operationId": operation_id,
            "steps": [
                {"name": name, "lines": record.log.get(name, [])} for name in order
            ],
        }

    async def _mark_seen(self, command: dict[str, Any]) -> dict[str, Any]:
        """Mark an operation seen. See ``CONTEXT.md``, "Unseen"."""
        await self.operations.mark_seen(
            self.world["operations"][command["operationId"]]
        )
        return {"ok": True, "result": {}}

    async def _retry(self, command: dict[str, Any]) -> dict[str, Any]:
        """Run a refused or failed operation again, from its first unfinished step.

        See ``docs/dev/orchestrator-server.md``, "Operations".
        """
        operation_id = command["operationId"]
        record = await self.operations.read(operation_id)
        if record is None:
            # The world held it at validation; the store dropped it past the cap.
            return _refused("unknown_id", f"No operation {operation_id}")
        again = record.command
        if again.get("type") == "build":
            # A Build's command cannot run again: a Rebuild queues a new Build.
            try:
                await self.build_queue.rebuild_build(again["buildId"])
            except MergeRequestError as exc:
                return _refused("invalid", str(exc))
            return {"ok": True, "result": {"operationId": operation_id}}
        if again.get("type") == "mergeRequest.merge":
            return await self._merge_merge_request(again)
        error = validate_command(self.world, again)
        if error:
            return {"ok": False, "error": error}
        plan = self._plan(again)
        if isinstance(plan, dict):
            return plan
        return await self._begin(again, plan, record)

    async def _self_update(self, command: dict[str, Any]) -> dict[str, Any]:
        """Start an **Update job**, which outlives this server's restart.

        Refused unless :func:`can_update`.
        """
        if self.system is None:
            return _refused("not_implemented", "This server has no install")
        allowed = await asyncio.get_running_loop().run_in_executor(
            self.executor, can_update, self.system
        )
        if not allowed:
            return _refused(
                "cannot_update",
                "This is a development build, not the host service: mael self "
                "update would update the production install, not this server.",
            )
        try:
            result = await self.start_update()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _refused("cannot_update", f"mael self update --detach: {exc}")
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            return _refused("cannot_update", message or f"exit {result.returncode}")
        return {"ok": True, "result": {}}

    async def _set_auto_merge(self, command: dict[str, Any]) -> dict[str, Any]:
        """Turn **Auto-merge** on or off, and answer once the world holds the result.

        A plain command, not an operation: see "Operations" in
        docs/dev/orchestrator-server.md. The re-read names the branch, so it
        waits out a read in flight. A refusal re-reads too: the reading was stale.
        """
        fn = self.worktrees.auto_merge
        if fn is None:
            return _refused("invalid", "This server cannot auto-merge pull requests")
        worktree_id = command["worktreeId"]
        # Validation proved the worktree is in the world, so the row is here.
        row = self.world["worktrees"][worktree_id]
        branch = row["branch"]
        refused: dict[str, Any] | None = None
        try:
            await self._run_worktree(
                fn, row["project"], branch, row["path"], command["on"]
            )
        except CloseBlocked as exc:
            refused = _refused("invalid", str(exc))
        except Exception as exc:  # noqa: BLE001 — the client hears why
            log.exception("could not change auto-merge for %s", worktree_id)
            refused = _refused(
                "invalid", f"Could not change auto-merge on the pull request: {exc}"
            )
        try:
            await self.refresh_worktrees(extra_branches={branch})
        except Exception:
            # The write's outcome stands; the reply reads what the world holds.
            log.exception("the refresh after auto-merge for %s failed", worktree_id)
        if refused is not None:
            return refused
        on = self.world["worktrees"].get(worktree_id, row).get("prAutoMerge")
        return {"ok": True, "result": {"on": on}}

    async def client_host(self, peer: str | None) -> RemoteHost | None:
        """The online **Remote host** whose addresses hold the IP ``peer``.

        A browser there is a **Remote client**.
        """
        address = normal_ip(peer)
        if address is None:
            return None
        for host in self.remote_hosts.values():
            if host.online and address in await host.addresses():
                return host
        return None

    async def _create_worktree_terminal(
        self, command: dict[str, Any]
    ) -> dict[str, Any]:
        """Make a worktree's terminal in the cmux the browser can see.

        ``clientHost`` is the host the browser is on, ``None`` for neither
        this server's machine nor a **Remote host**. ``worktreeHost`` owns the
        worktree. Each is a host name, this server's own being its id prefix.
        When the two are one host, that host opens a local terminal. When
        they differ, the client's host opens an SSH shell on the row's
        **SSH target**, and the link goes only into the reply. See "Remote
        clients" in ``docs/dev/cmux.md``.
        """
        worktree_id = command["worktreeId"]
        # Validation proved the worktree is in the world, so the row is here.
        row = self.world["worktrees"][worktree_id]
        own = self.ids.prefix
        client_host = command.get("clientHost")
        worktree_host = str(command.get("worktreeHost", own))
        if client_host is None or (
            client_host != own and client_host not in self.remote_hosts
        ):
            return _refused(
                "invalid",
                "Open cmux from this machine, or from a remote host of this server.",
            )
        if client_host == worktree_host:
            if worktree_host == own:
                return await self._create_local_terminal(worktree_id, row)
            return await self._ask_for_terminal(
                worktree_host, {"worktreeId": worktree_id}
            )
        text = row.get("sshTarget", "")
        if not text:
            return _refused(
                "invalid",
                f"Set dev_host on {worktree_host or 'this server'}, "
                "so another machine can reach it over SSH.",
            )
        if SshTarget.parse(text) is None:
            return _refused(
                "invalid", f"The SSH target {text!r} is not user@host:/path"
            )
        name = workspace_name(row["project"], row["nato"])
        if client_host == own:
            return await self.create_ssh_terminal(name, text)
        return await self._ask_for_terminal(
            client_host, {"name": name, "sshTarget": text}
        )

    async def _create_local_terminal(
        self, worktree_id: str, row: Worktree
    ) -> dict[str, Any]:
        """Make this server's own worktree's terminal here, and write its link into the row."""
        ensure_terminal = self.worktrees.ensure_terminal
        if ensure_terminal is None:
            return _refused("invalid", "This server cannot create terminals")
        try:
            url = await self._run_worktree(
                ensure_terminal, row["project"], row["nato"], row["path"]
            )
        except CloseBlocked as exc:
            return _refused("invalid", str(exc))
        except Exception as exc:  # noqa: BLE001 — the client hears why
            log.exception("could not create a terminal for %s", worktree_id)
            return _refused("invalid", f"Could not create the terminal: {exc}")
        worktrees = self.world["worktrees"]
        if worktree_id in worktrees:
            updated = {
                **worktrees,
                worktree_id: {**worktrees[worktree_id], "shellUrl": url},
            }
            self._apply(diff_kind("worktree", worktrees, updated))
        return {"ok": True, "result": {"shellUrl": url}}

    async def _ask_for_terminal(
        self, host: str, body: dict[str, str]
    ) -> dict[str, Any]:
        """``POST /api/terminals`` on remote host ``host``: its own reply's link."""
        try:
            reply = await self.remote_hosts[host].post_json("/api/terminals", body)
        except HostUnreachable as exc:
            return _refused("unreachable", str(exc))
        url = str(reply.get("shellUrl") or "")
        if not url:
            return _refused("unreachable", f"{host} gave no terminal link")
        return {"ok": True, "result": {"shellUrl": url}}

    async def create_ssh_terminal(self, name: str, ssh_target: str) -> dict[str, Any]:
        """Make a workspace in this machine's cmux, with an SSH shell on ``ssh_target``.

        For a browser here and a worktree on another host: a remote host's
        worktree, or a worktree of a server that names this host in
        ``hosts:``. See "Remote clients" in ``docs/dev/cmux.md``.
        """
        make = self.worktrees.ssh_terminal
        if make is None:
            return _refused("invalid", "This server cannot create terminals")
        target = SshTarget.parse(ssh_target)
        if target is None:
            return _refused("invalid", f"{ssh_target!r} is not user@host:/path")
        workspace = remote_workspace_name(name, target)
        if workspace is None:
            return _refused("invalid", f"{name!r} is not a workspace name")
        try:
            url = await self._run_worktree(make, workspace, target)
        except CloseBlocked as exc:
            return _refused("invalid", str(exc))
        return {"ok": True, "result": {"shellUrl": url}}

    async def _refresh_worktrees_now(self, command: dict[str, Any]) -> dict[str, Any]:
        """Re-read the worktrees because a caller changed something on GitHub.

        ``mael gh create-pr``, ``mael git merge`` and ``mael close --trash`` are
        the callers: the pull request they changed is not in any world until
        something looks it up, and the next poll is up to
        ``WORKTREE_POLL_SECS`` away.

        A command that names a ``project`` and ``branch`` reads that branch's
        pull request again first, after any read already in flight: one that
        began before the write cannot hold its result. That read is never
        absorbed by one already scheduled.

        The read is scheduled, not awaited. It shells out per worktree across
        every project and takes seconds, and the caller is a command holding the
        user's terminal open — it wants to know the server heard, not to wait
        for the answer. The UI hears the result on its own notice stream.

        A read already scheduled absorbs this one, as an arriving client's does:
        replacing the task would drop the reference to the first, leaving a
        coroutine nothing can cancel at stop.

        Always ``ok``. The caller's own work has already succeeded, and a read
        that could not run is the server's business rather than its failure.
        """
        branch = command.get("branch")
        in_flight = self._event_read is not None and not self._event_read.done()
        if branch:
            previous = self._event_read if in_flight else None
            self._event_read = asyncio.create_task(
                self._read_after_write(command["project"], branch, previous)
            )
        elif not in_flight:
            self._event_read = asyncio.create_task(self.refresh_now())
        return {"ok": True, "result": {}}

    async def _read_after_write(
        self, project: str, branch: str, previous: asyncio.Task[None] | None
    ) -> None:
        """Read ``branch``'s pull request again, then the worktrees.

        ``previous`` is the event read this one replaced in ``_event_read``, so
        nothing else holds it. A stop cancels this task, and this task cancels
        ``previous``.
        """
        try:
            try:
                await self._run(self.worktrees.refresh_prs, project, branch)
            except Exception:  # noqa: BLE001 — the caller's write already succeeded
                log.exception("the PR read for %s %s failed", project, branch)
            if previous is not None:
                await previous
        except asyncio.CancelledError:
            if previous is not None:
                previous.cancel()
            raise
        await self.refresh_now()

    async def _set_status(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._write_task(
            self.tasks.set_status, command["taskId"], command["status"]
        )

    async def _update_task(self, command: dict[str, Any]) -> dict[str, Any]:
        return await self._write_task(
            self.tasks.update, command["taskId"], dict(command["fields"])
        )

    async def _register_task_pr(self, command: dict[str, Any]) -> dict[str, Any]:
        """Register a PR on a task: what a delegated agent's ``gh-pr`` link sends."""
        task_id = command["taskId"]
        reply = await self._write_task(
            self.tasks.register_pr, task_id, command["number"], command["url"]
        )
        return self._with_task(reply, task_id)

    async def _add_task_session(self, command: dict[str, Any]) -> dict[str, Any]:
        """Append a session to a task: what a delegated agent's ``/clear`` sends."""
        return await self._write_task(
            self.tasks.add_session, command["taskId"], command["sessionId"]
        )

    async def _withdraw_task_pr(self, command: dict[str, Any]) -> dict[str, Any]:
        task_id = command["taskId"]
        reply = await self._write_task(self.tasks.withdraw_pr, task_id)
        return self._with_task(reply, task_id)

    async def _not_local(self, task: str) -> dict[str, Any] | None:
        """The refusal for a task whose project is not ``merge: local``, or ``None``."""
        project = task.partition("/")[0]
        config = await asyncio.to_thread(self.build_queue.host.config, project)
        if config.merge != "local":
            return _refused(
                "invalid", f"Project {project} merges through GitHub, not merge: local"
            )
        return None

    async def _submit_merge_request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Submit the task branch's head to its Merge request: a Build is queued."""
        if refused := await self._not_local(command["taskId"]):
            return refused
        project, _, task_id = command["taskId"].partition("/")
        mr, _ = await self.build_queue.submit(
            project,
            task_id,
            command["branch"],
            command["sha"],
            command["base"],
            command.get("description"),
        )
        return {"ok": True, "result": await self.merge_request_of(mr.task)}

    async def _merge_merge_request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Start the Merge of the task's ``passed`` request, pinned to ``sha``."""
        if refused := await self._not_local(command["taskId"]):
            return refused
        try:
            operation_id = await self.build_queue.merge(
                command["taskId"], command["sha"]
            )
        except MergeRequestError as exc:
            return _refused("invalid", str(exc))
        except Busy as exc:
            refused = _refused("invalid", str(exc))
            refused["error"]["operationId"] = exc.operation_id
            return refused
        return {"ok": True, "result": {"operationId": operation_id}}

    async def _rebase_merge_request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Tell the chaperone to rebase the task's ``stale`` request and submit again."""
        if refused := await self._not_local(command["taskId"]):
            return refused
        try:
            mr = await self.build_queue.rebase(command["taskId"])
        except MergeRequestError as exc:
            return _refused("invalid", str(exc))
        return {"ok": True, "result": await self.merge_request_of(mr.task)}

    async def _retry_merge_request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Queue a new Build of the task's ``failed`` request, at the same SHA."""
        if refused := await self._not_local(command["taskId"]):
            return refused
        try:
            mr, _ = await self.build_queue.rebuild(command["taskId"])
        except MergeRequestError as exc:
            return _refused("invalid", str(exc))
        return {"ok": True, "result": await self.merge_request_of(mr.task)}

    async def _withdraw_merge_request(self, command: dict[str, Any]) -> dict[str, Any]:
        try:
            mr = await self.build_queue.withdraw(command["taskId"])
        except MergeRequestError as exc:
            return _refused("invalid", str(exc))
        return {"ok": True, "result": await self.merge_request_of(mr.task)}

    async def merge_request_of(
        self, task_key: str, *, every: bool = False
    ) -> dict[str, Any] | None:
        """The task's current Merge request, its status and its Builds, newest first.

        ``every`` adds ``earlier``: the task's earlier requests, each the same
        shape. ``None`` when the task has never submitted.
        """
        found = await self.build_queue.read(task_key, every=every)
        if not found:
            return None
        shaped = [
            merge_request_json(mr, builds, self.build_queue.tips.get(mr.project, ""))
            for mr, builds in found
        ]
        return {**shaped[0], **({"earlier": shaped[1:]} if every else {})}

    def _with_task(self, reply: dict[str, Any], task_id: str) -> dict[str, Any]:
        """``reply``, answered with the task as it now stands when it succeeded."""
        if not reply["ok"]:
            return reply
        return {"ok": True, "result": self.world["tasks"].get(task_id, {})}

    async def _delete_task(self, command: dict[str, Any]) -> dict[str, Any]:
        """Remove a task. The forced refresh is also what prunes its desk entry."""
        return await self._write_task(self.tasks.delete, command["taskId"])

    async def task_documents_of(self, key: str) -> dict[str, Any]:
        """The Task documents task ``key`` owns, then those it links."""
        project, _, task_id = key.partition("/")
        table = self.document_store
        return {
            "owned": [
                await self._task_document_json(row) for row in await table.owned(key)
            ],
            "linked": [
                await self._task_document_json(row)
                for row in await table.linked(project, task_id)
            ],
        }

    async def task_document(self, ref: str) -> dict[str, Any]:
        """One Task document: its versions, the body ``ref`` selects, its links.

        Raises:
            TaskDocumentError: ``ref`` does not parse or names nothing.
        """
        parsed = task_documents.parse_ref(ref, project=None, task_id=None)
        row, version = await task_documents.resolve(self.document_store, parsed)
        links: dict[str, list[str]] = {}
        for found in await self.document_store.links(row.id):
            links.setdefault(found.fragment, []).append(
                task_key(found.project, found.task_id)
            )
        return {
            "document": await self._task_document_json(row),
            "versions": [
                _version_json(v) for v in await self.document_store.versions(row.id)
            ],
            "version": {**_version_json(version), "body": version.body},
            "links": {
                fragment: sorted(keys) for fragment, keys in sorted(links.items())
            },
            "findings": (
                [_finding_json(f) for f in sarif.parse(version.body).findings]
                if row.kind == "sarif"
                else []
            ),
        }

    async def _task_document_json(self, row: TaskAttachment) -> dict[str, Any]:
        versions = await self.document_store.versions(row.id)
        latest = versions[-1] if versions else None
        return {
            "id": row.id,
            "uri": str(task_documents.ref_of(row)),
            "taskId": row.task_key,
            "name": row.name,
            "kind": row.kind,
            "archived": row.archived,
            "title": latest.title if latest else "",
            "version": latest.version if latest else 0,
            "updatedAt": latest.created_at if latest else row.attached_at,
        }

    async def _add_task_document(self, command: dict[str, Any]) -> dict[str, Any]:
        """Add a version by hand, with no review. ``plan`` is the agent's own."""
        project, _, task_id = str(command["taskId"]).partition("/")
        role = command.get("role") or None
        if command["name"] == PLAN_NAME or role == "plan":
            return _refused(
                "invalid", f"{PLAN_NAME} is stored only when an agent submits a plan"
            )
        try:
            row, version = await task_documents.add(
                self.document_store,
                project=project,
                task_id=task_id,
                name=str(command["name"]),
                kind=str(command.get("kind") or "markdown"),
                title=str(command.get("title") or ""),
                body=str(command["body"]),
                now=self.clock(),
                role=role,
            )
        except task_documents.TaskDocumentError as exc:
            return task_document_refusal(exc)
        await self._show_stored(row, version)
        uri = str(task_documents.ref_of(row, version.version))
        return {"ok": True, "result": {"uri": uri, "version": version.version}}

    async def _link_task_document(self, command: dict[str, Any]) -> dict[str, Any]:
        """``taskDocument.link`` or ``.unlink``: the task in ``taskId``, to ``ref``."""
        project, _, task_id = str(command["taskId"]).partition("/")
        change = (
            task_documents.link
            if command["type"] == "taskDocument.link"
            else task_documents.unlink
        )
        try:
            ref = task_documents.parse_ref(
                str(command["ref"]), project=None, task_id=None
            )
            await change(self.document_store, ref, project=project, task_id=task_id)
        except task_documents.TaskDocumentError as exc:
            return task_document_refusal(exc)
        row = await self.document_store.read(ref.task_key, ref.name)
        if row is not None:
            await self._show_stored(row)
        return {"ok": True, "result": {}}

    async def _archive_task_document(self, command: dict[str, Any]) -> dict[str, Any]:
        """Archive or unarchive ``ref``. ``by``, when given, must be its owner."""
        try:
            ref = task_documents.parse_ref(
                str(command["ref"]), project=None, task_id=None
            )
            row = await task_documents.archive(
                self.document_store,
                ref,
                bool(command["archived"]),
                by=command.get("by") or None,
            )
        except task_documents.TaskDocumentError as exc:
            return task_document_refusal(exc)
        await self._show_stored(row)
        return {"ok": True, "result": {"archived": row.archived}}

    async def _create_comm(self, command: dict[str, Any]) -> dict[str, Any]:
        comm = await comms_model.new(
            self.comms,
            str(command["title"]).strip(),
            command.get("content") or "",
            list(command.get("recipients") or []),
            now=self.clock(),
            category=command.get("category") or "",
            project=command.get("project") or "",
        )
        await self.refresh_comms()
        return {"ok": True, "result": {"id": comm.id}}

    async def _update_comm(self, command: dict[str, Any]) -> dict[str, Any]:
        """Edit a comm's fields; ``closed`` closes or reopens it."""
        comm_id = command["commId"]
        fields = command["fields"]
        title = fields.get("title")
        await comms_model.edit(
            self.comms,
            comm_id,
            title=str(title).strip() if title is not None else None,
            content=fields.get("content"),
            recipients=fields.get("recipients"),
            category=fields.get("category"),
            project=fields.get("project"),
        )
        closed = fields.get("closed")
        if closed is True:
            await comms_model.close(self.comms, comm_id, now=self.clock())
        elif closed is False:
            await comms_model.reopen(self.comms, comm_id)
        await self.refresh_comms()
        return {"ok": True, "result": {}}

    async def _draft_comm_updates(self, command: dict[str, Any]) -> dict[str, Any]:
        """**Draft updates**: a new update for each open comm with a queue."""
        made = await comms_model.draft_updates(
            self.comms,
            self.comm_updates,
            self.landings.steps,
            linked_tasks(self.world["tasks"]),
            self.clock(),
        )
        self._kept_updates(made)
        return {"ok": True, "result": {"ids": [u.id for u in made]}}

    async def _update_comm_update(self, command: dict[str, Any]) -> dict[str, Any]:
        """Edit a draft's content; ``sent`` marks it sent."""
        update_id = command["updateId"]
        fields = command["fields"]
        if fields.get("content") is not None:
            update = await comms_model.edit_update(
                self.comm_updates, update_id, fields["content"]
            )
            self._kept_updates([update])
        if fields.get("sent") is True:
            update = await comms_model.send_update(
                self.comm_updates, update_id, self.clock()
            )
            self._kept_updates([update])
        return {"ok": True, "result": {}}

    def _kept_updates(self, updates: list[CommUpdate]) -> None:
        """Hold the updates just written, and publish their comms."""
        for update in updates:
            self._comm_update_rows[update.id] = update
        self._apply(self._rederived_comms(u.comm_id for u in updates))

    async def _write_task(
        self, write: Callable[..., Any], task_id: str, *args: Any
    ) -> dict[str, Any]:
        """One notebook write, then the upsert it caused, then the reply.

        A write that returns a result, as a status move does, sends it back.

        The refresh is forced, as the launch path forces it: a version-checked
        refresh races the poll, so the client could get its reply first.
        """
        try:
            result = await self._run(write, task_id, *args)
        except KeyError:
            return _refused("unknown_id", f"No task {task_id}")
        except ValueError as exc:
            return _refused("invalid", str(exc))
        except TaskSourceRemote as exc:
            return _refused("unreachable", str(exc))
        await self.refresh_tasks(force=True)
        return {"ok": True, "result": result or {}}


def _attachment_paths(attachments: Any) -> list[dict[str, str]]:
    """Turn the URLs a client sent into the paths the host reads.

    A client that could name a filesystem path would be naming any file the
    host can read, so anything that is not an attachment URL this server
    serves is dropped rather than forwarded.
    """
    from mael_domain.attachments import resolve_attachment

    resolved: list[dict[str, str]] = []
    for item in attachments or ():
        if not isinstance(item, dict):
            continue
        parts = str(item.get("url", "")).strip("/").split("/")
        # api/attachments/<project>/<bucket>/<name>
        if len(parts) != 5 or parts[0] != "api" or parts[1] != "attachments":
            continue
        try:
            found = resolve_attachment(parts[2], parts[3], parts[4])
        except (ValueError, KeyError):
            continue
        # The host sends each path as an image block and refuses a turn with
        # any other file in it. A file belongs in the text as a link.
        try:
            with found.open("rb") as handle:
                head = handle.read(16)
        except OSError:
            continue
        if not is_image(head):
            continue
        resolved.append({"path": str(found)})
    return resolved


_NEVER = object()


def _started_row(
    agent_id: str, payload: dict[str, Any], session: str = ""
) -> dict[str, Any]:
    """The list row a just-started agent would have, before the host lists it.

    ``session`` is the session the task stores for the agent: for Codex, the
    thread id the start replied with. Empty takes the payload's.
    """
    return {
        "id": agent_id,
        "parent": "",
        "description": "",
        "state": "idle",
        # ``start`` drops the fields the caller did not name, so a free agent
        # pins no session and a launch that named no model carries none. The
        # host fills both in, and the next ``list`` carries them.
        "session": session or payload.get("session", ""),
        "cwd": payload["cwd"],
        "model": payload.get("model", ""),
        # The child announces its mode in `system`/`init`, so a launched agent
        # has none until then. Naming the requested one here would show a mode
        # before anything confirmed it.
        "mode": "",
        "waiting_on": "",
        "last_message": "",
        "last_note": "",
        "last_note_at": "",
        "cost": "",
    }


def _code_for(error: str) -> str:
    """The wire code for one of the host's error strings."""
    if "no such agent" in error:
        return "unknown_id"
    if "has exited" in error:
        return "agent_exited"
    if "not waiting on a question" in error:
        return "wrong_wait_kind"
    if "not waiting" in error:
        return "not_waiting"
    return "invalid"
