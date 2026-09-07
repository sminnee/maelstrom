"""Every worktree across every project, as data.

The model behind ``mael list-all`` and the orchestrator server's worktree
source. :func:`build_list_all_data` returns the same JSON shape
``mael --json list-all`` prints, so the table and the server read one set of
rows. The three helpers below are shared with ``mael list``.

Not pure: it reads git, ``gh`` and the process table through the worktree,
github and session-discovery modules. It is the one place that knows how the
rows are assembled, so those reads happen once per project rather than once
per caller.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import session_discovery
from . import task as task_model
from .base_store import GitConfigBaseStore
from .config import linear_team_id
from .github import get_open_prs, get_pr_for_branch
from .github_model import PrStatus, RateLimited, is_open_pr
from .ports import get_app_url
from .task_store import GitFileStore
from .worktree import (
    WorktreeInfo,
    closed_worktrees_async,
    find_all_projects,
    get_local_only_commits_async,
    get_pushed_commit_count_async,
    get_worktree_dirty_files_async,
    list_worktrees_async,
    run_git_async,
)
from .worktree_model import extract_worktree_name_from_folder, has_claude_transcript

log = logging.getLogger(__name__)


def branch_session_ids(project_name: str) -> dict[str, list[str]]:
    """Map ``branch -> [session_id, ...]`` for every task in ``project_name``.

    Several tasks can share a branch/worktree (one PR per parent), so each branch
    maps to the deterministic session ids of *all* its tasks. Used to detect a
    stopped-but-not-live session for a worktree: any of a branch's task sessions
    having an on-disk transcript means that worktree "ran before". Returns an empty
    map when the task notebook is absent or unreadable — the SESSION column then
    simply shows no stopped marker; this cosmetic feature must never break ``list``.
    """
    try:
        store = GitFileStore()
        result: dict[str, list[str]] = {}
        for t in task_model.list_tasks(store, project=project_name, no_index=True):
            branch = t.branch or task_model.default_branch(t.id, t.parent)
            result.setdefault(branch, []).append(
                task_model.session_id_for(project_name, t.id)
            )
        return result
    except (OSError, ValueError, KeyError):
        # An absent/unreadable notebook or a malformed task must degrade to "no
        # marker", not crash `list`. Kept narrow: a logic bug (AttributeError etc.)
        # still surfaces rather than being silently swallowed.
        return {}


async def resolve_pr(
    open_prs: dict[str, PrStatus] | None,
    project_path: Path,
    branch: str | None,
    *,
    asked: set[str] | None = None,
    pr_cache: dict[str, PrStatus] | None = None,
) -> PrStatus | None:
    """Resolve ``branch`` to its pull request, or ``None`` when it has none.

    ``open_prs`` is the batch from
    :func:`~maelstrom.github.get_open_prs`, or ``None`` when that call
    failed. A successful batch is authoritative **for the branches it asked
    about**: one of those missing from it has no PR, so we answer without a
    second network call. A failed batch falls back to the per-branch lookup,
    which keeps a broken ``gh`` no worse than it was before batching — one
    blank row rather than a blank column.

    ``asked`` names those branches. A branch outside it was never queried, so
    its absence from ``open_prs`` means *unknown*, not *no PR*: answer from
    ``pr_cache`` instead. Reading the two the same way would retire a live PR
    from the row the moment the poll stopped asking about its branch.
    """
    if not branch:
        return None
    if asked is not None and branch not in asked:
        return (pr_cache or {}).get(branch)
    if open_prs is not None:
        return open_prs.get(branch)
    return await get_pr_for_branch(project_path, branch)


def session_display(count: int, stopped: bool) -> str:
    """Render the SESSION cell: live count wins, else a stopped marker, else blank.

    ``stopped`` says a task on the row's branch left an on-disk transcript in
    the worktree (ran and stopped), which tells it apart from a never-run
    worktree, which stays blank.
    """
    if count:
        return str(count)
    return "— stopped" if stopped else ""


def session_stopped(worktree_path, branch, branch_sessions) -> bool:
    """Whether a task on ``branch`` ran in ``worktree_path`` and stopped."""
    if not branch:
        return False
    return any(
        has_claude_transcript(worktree_path, session_id)
        for session_id in branch_sessions.get(branch, [])
    )


def repo_url_from_remote(remote: str) -> str | None:
    """The browse URL for a git remote, or ``None`` when it names no web host.

    Handles the two shapes git writes: ``git@host:owner/repo.git`` and
    ``https://host/owner/repo.git``. The host is kept, so a GitHub Enterprise
    remote browses on its own domain. A path-only remote has no browse URL.
    """
    url = remote.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if match := re.fullmatch(r"(?:ssh://)?git@([^:/]+)[:/](.+)", url):
        host, path = match.groups()
    elif match := re.fullmatch(r"https?://(?:[^@/]+@)?([^/]+)/(.+)", url):
        host, path = match.groups()
    else:
        return None
    return f"https://{host}/{path}" if path else None


def pr_url(repo_url: str | None, pr_number: int | None) -> str | None:
    """The browse URL for a worktree's pull request, or ``None``.

    Built here rather than by the reader: the row carries a URL, not two halves
    to join.
    """
    if not repo_url or not pr_number:
        return None
    return f"{repo_url}/pull/{pr_number}"


async def project_repo_url(project_path: Path) -> str | None:
    """The project's browse URL, read from ``remote.origin.url``.

    Read from git config rather than ``gh``: ``build_list_all_data`` runs on the
    orchestrator's 15-second poll, and a subprocess round trip per project per
    poll would cost far more than a value that never changes is worth.

    Returns ``None`` for a project with no origin, and for a directory git
    cannot be run in at all — a path that has gone since the scan listed it.
    ``list-all`` visits every project, so one bad directory must cost that
    project its PR links, not the whole read.
    """
    try:
        result = await run_git_async(
            ["config", "--get", "remote.origin.url"],
            cwd=project_path,
            quiet=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return repo_url_from_remote(result.stdout)


DEFAULT_CONCURRENCY = 24
"""How many project and worktree reads may run at once.

Both fan-out levels share one budget, so the two do not multiply. A machine
with 16 projects and 90 worktrees would otherwise run several hundred
subprocesses at once, and ``gh`` secondary rate limits are the first thing
that breaks: :func:`~maelstrom.github.get_open_prs` then returns
``None``, every row falls back to the slower per-branch lookup, and the read
gets *slower* with no error to show for it.

24 is high enough that the read stays bounded by process start-up rather than
by the cap, and low enough to stay well inside those limits.
"""


async def build_list_all_data(
    projects_dir: Path,
    concurrency: int = DEFAULT_CONCURRENCY,
    *,
    active_branches: set[str] | None = None,
    pr_cache: dict[str, PrStatus] | None = None,
) -> dict[str, Any]:
    """Every project under ``projects_dir`` with its worktrees, as ``list-all`` data.

    The shape is what ``mael --json list-all`` prints: ``{"projects": [...]}``,
    each project carrying ``name``, ``path``, ``stack_tip``, ``repo_url`` and
    ``worktrees``.
    A closed worktree is included with ``is_closed`` true and its counts
    zeroed. ``session_stopped`` says a task on the row's branch ran here and
    stopped; the table renders it as the stopped marker.

    ``concurrency`` caps how many reads run at once across both fan-out
    levels — see :data:`DEFAULT_CONCURRENCY`.

    ``active_branches`` limits the pull request read to the branches being
    worked on. GitHub charges GraphQL by node count and the query asks for 20
    pull requests per branch, so asking about every branch in every project is
    what exhausts the hourly budget. ``None`` asks about all of them, which is
    what ``mael list-all`` wants: a one-shot read has no budget to protect.

    ``pr_cache`` answers the branches that were not asked about. Without it a
    skipped branch would read as *no pull request* rather than *not asked*,
    which tells the reader the PR went away — worse than showing it stale.
    """
    projects = find_all_projects(projects_dir)
    # One live-session sweep shared across every project/worktree row, plus a
    # memo so the per-session worktree-list lookup runs once, not per row.
    live_sessions = await session_discovery.LiveSessionSet().sweep()

    # One budget for both levels. Every acquire wraps a leaf read, never a
    # coroutine that waits on another: a project holding a permit while its
    # worktrees queue for one would deadlock the whole read.
    limit = asyncio.Semaphore(concurrency)

    # Each project's reads are independent, so they run together. Read one
    # after another, 16 projects took 19s here, and every route waits on this.
    read = await asyncio.gather(
        *(
            _project_data(
                project_path,
                live_sessions,
                limit,
                active_branches=active_branches,
                pr_cache=pr_cache or {},
            )
            for project_path in projects
        ),
        return_exceptions=True,
    )

    # A project that cannot be read costs its own row, not the whole read. A
    # worktree directory can go between the listing and the row read, and the
    # server polls this every 15s: one raise would blank the UI on every tick.
    projects_data = []
    for project_path, outcome in zip(projects, read, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("could not read project %s: %s", project_path.name, outcome)
            continue
        projects_data.append(outcome)
    return {"projects": projects_data}


@dataclass(frozen=True)
class _ProjectContext:
    """What every worktree row in one project needs, read once for all of them.

    Each field is a per-project batch — the PR lookup, the closed set, the
    bases, the repo URL — so a row reads only what is specific to itself.
    ``limit`` is shared with every other project too, so the two fan-out
    levels draw on one budget.
    """

    path: Path
    name: str
    closed_paths: set[Path]
    bases: dict[str, str]
    open_prs: dict[str, PrStatus] | None
    #: The branches the batch above actually asked about. A branch outside it
    #: was not asked, so its absence from ``open_prs`` says nothing.
    asked: set[str]
    #: What earlier polls learned, answering the branches that were not asked.
    pr_cache: dict[str, PrStatus]
    repo_url: str | None
    branch_sessions: dict[str, list[str]]
    live_sessions: session_discovery.LiveSessionSet
    limit: asyncio.Semaphore


async def _project_data(
    project_path: Path,
    live_sessions: session_discovery.LiveSessionSet,
    limit: asyncio.Semaphore,
    *,
    active_branches: set[str] | None = None,
    pr_cache: dict[str, PrStatus] | None = None,
) -> dict[str, Any]:
    """One project's row, with a row per worktree under it.

    ``limit`` caps this project's own reads and its worktrees' alike, and is
    shared with every other project so the two fan-out levels do not multiply.

    ``active_branches`` and ``pr_cache`` are as
    :func:`build_list_all_data` documents them.
    """
    project_name = project_path.name
    # Each leaf read takes a permit on its own, never the whole prologue: a
    # project holding one while its worktrees queue for the same budget would
    # deadlock the read.
    async with limit:
        worktrees = await list_worktrees_async(project_path)
    # Branch → task session ids for this project (stopped-marker detection).
    # Off the loop: it parses every task file for the project, measured at 2.4s
    # across 16 projects, and holding the loop for that gives back the
    # concurrency the gather buys. A thread is safe because the scan passes
    # ``no_index=True`` and so never touches the SQLite index, which is bound to
    # the thread that opened it.
    branch_sessions = await asyncio.to_thread(branch_session_ids, project_name)
    # One PR lookup per project, not per worktree. The batch is repo-scoped, so
    # it belongs here rather than in the worktree loop below. A project whose
    # worktrees are all detached has no branch to ask about, and `list-all`
    # visits every project — so skip the round trip rather than spend one.
    branches = {wt.branch for wt in worktrees if wt.branch}
    # Only the branches being worked on are worth a network read. The query
    # costs 20 pull-request nodes per branch, so this is the difference between
    # a handful of nodes and every branch in every project.
    asked = branches if active_branches is None else branches & active_branches
    open_prs = {}
    if asked:
        # This is the `gh` call the cap exists for — see DEFAULT_CONCURRENCY.
        async with limit:
            try:
                open_prs = await get_open_prs(project_path, asked)
            except RateLimited:
                # The budget is spent, so every other project is about to fail
                # the same way. Treat the branches as not asked: the rows keep
                # what the last poll learned, and nothing looks them up one by
                # one, which is what would keep the budget spent.
                log.warning(
                    "GitHub rate limit reached; %s keeps its last known PR state",
                    project_path.name,
                )
                asked = set()
    async with limit:
        # Likewise the closed check: one batch per project, not two
        # subprocesses per worktree.
        closed_paths = await closed_worktrees_async(project_path, worktrees)
        # One repo lookup per project answers the PR URL for every row.
        repo_url = await project_repo_url(project_path)
    # One store read per project answers the base for every row. Both store
    # calls shell out to `git config`, so they go to a thread: run inline they
    # would block every other project's reads behind this one.
    base_store = GitConfigBaseStore(project_path)
    bases, stack_tip, has_linear = await asyncio.gather(
        asyncio.to_thread(base_store.all),
        asyncio.to_thread(base_store.read_stack_tip),
        # Reads `.maelstrom.yaml` from disk, so it joins the two `git config`
        # calls off the loop rather than blocking every other project's reads.
        # Only the flag crosses to the UI: the orchestrator offers the Linear
        # kind on it, and the team id itself is of no use to a browser.
        asyncio.to_thread(
            lambda: bool(linear_team_id(project_path, [wt.path for wt in worktrees]))
        ),
    )

    # Skip the project root (bare repo). Resolved, because git reports the
    # real path and a symlinked projects dir would never match.
    rows = [wt for wt in worktrees if wt.path.resolve() != project_path.resolve()]

    # Each worktree's reads are independent too, so they run together. The
    # projects already read at the same time, which left the whole read
    # bounded by the slowest single project — a user who adds worktrees to one
    # project would otherwise slow down the poll for all of them.
    context = _ProjectContext(
        path=project_path,
        name=project_name,
        closed_paths=closed_paths,
        bases=bases,
        open_prs=open_prs,
        asked=asked,
        pr_cache=pr_cache or {},
        repo_url=repo_url,
        branch_sessions=branch_sessions,
        live_sessions=live_sessions,
        limit=limit,
    )
    read = await asyncio.gather(
        *(_worktree_row(wt, context) for wt in rows),
        return_exceptions=True,
    )

    # One unreadable worktree costs its own row, not the project's — the same
    # rule `build_list_all_data` applies to a project it cannot read.
    worktree_data = []
    for wt, outcome in zip(rows, read, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("could not read worktree %s: %s", wt.path, outcome)
            continue
        worktree_data.append(outcome)

    return {
        "name": project_name,
        "path": str(project_path),
        "stack_tip": stack_tip,
        "repo_url": repo_url,
        "has_linear": has_linear,
        "worktrees": worktree_data,
    }


async def _worktree_row(wt: WorktreeInfo, ctx: _ProjectContext) -> dict[str, Any]:
    """One worktree's row.

    ``wt`` is what varies per row; ``ctx`` is everything the project already
    read for all of them.
    """
    display_name = (
        extract_worktree_name_from_folder(ctx.name, wt.path.name) or wt.path.name
    )

    if wt.path in ctx.closed_paths:
        return {
            "name": display_name,
            "folder": wt.path.name,
            "path": str(wt.path),
            "branch": wt.branch or None,
            "base": None,
            "is_closed": True,
            "dirty_files": 0,
            "local_commits": 0,
            "pr_number": None,
            "pr_url": None,
            "pr_commits": None,
            "pr_state": None,
            "pr_draft": None,
            "pushed_commits": None,
            "app_url": None,
            "app_running": False,
            "session_count": 0,
            "session_stopped": False,
        }

    base = ctx.bases.get(wt.branch or "")
    # The three subprocess reads below are what the cap is for. Held together
    # rather than one permit each, so a row that takes a permit finishes and
    # gives it back, instead of queueing again between its own reads.
    async with ctx.limit:
        dirty_count = len(await get_worktree_dirty_files_async(wt.path))
        local_commits = await get_local_only_commits_async(wt.path, wt.branch)

        pr = await resolve_pr(
            ctx.open_prs,
            ctx.path,
            wt.branch,
            asked=ctx.asked,
            pr_cache=ctx.pr_cache,
        )
        # The per-branch fallback answers a number but no URL, so join one.
        row_pr_url = (pr.url or pr_url(ctx.repo_url, pr.number)) if pr else None
        pushed_commits = None
        if not is_open_pr(pr) and wt.branch:
            pushed_commits = await get_pushed_commit_count_async(wt.path, wt.branch)

    session_count = ctx.live_sessions.count_for(wt.path)
    stopped = not session_count and session_stopped(
        wt.path, wt.branch, ctx.branch_sessions
    )

    app_url = None
    app_running = False
    app_info = get_app_url(ctx.path, display_name)
    if app_info:
        app_url, app_running = app_info

    return {
        "name": display_name,
        "folder": wt.path.name,
        "path": str(wt.path),
        "branch": wt.branch or None,
        "base": base,
        "is_closed": False,
        "dirty_files": dirty_count,
        "local_commits": local_commits,
        "pr_number": pr.number if pr else None,
        "pr_url": row_pr_url,
        "pr_commits": pr.commits if pr else None,
        "pr_state": pr.state if pr else None,
        "pr_draft": pr.is_draft if pr else None,
        "pushed_commits": pushed_commits,
        "app_url": app_url,
        "app_running": app_running,
        "session_count": session_count,
        "session_stopped": stopped,
    }
