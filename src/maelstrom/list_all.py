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
from .base_store import GitConfigBaseStore
from .config import linear_team_id
from .github import get_open_prs, get_pr_for_branch
from .github_model import PrStatus, RateLimited, is_open_pr
from .ports import get_app_url
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
from .worktree_model import extract_worktree_name_from_folder

log = logging.getLogger(__name__)


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


def session_display(count: int) -> str:
    """Render the SESSION cell: the live session count, or blank at zero."""
    return str(count) if count else ""


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
    orchestrator's worktree poll, and a subprocess round trip per project per
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
    pr_cache: dict[str, dict[str, PrStatus]] | None = None,
) -> dict[str, Any]:
    """Every project under ``projects_dir`` with its worktrees, as ``list-all`` data.

    The shape is what ``mael --json list-all`` prints: ``{"projects": [...]}``,
    each project carrying ``name``, ``path``, ``stack_tip``, ``repo_url`` and
    ``worktrees``.
    A closed worktree is included with ``is_closed`` true and its counts
    zeroed.

    ``concurrency`` caps how many reads run at once across both fan-out
    levels — see :data:`DEFAULT_CONCURRENCY`.

    ``active_branches`` limits the pull request read to the branches being
    worked on. GitHub charges GraphQL by node count and the query asks for 20
    pull requests per branch, so asking about every branch in every project is
    what exhausts the hourly budget. ``None`` asks about all of them, which is
    what ``mael list-all`` wants: a one-shot read has no budget to protect.

    ``pr_cache`` answers the branches that were not asked about, keyed by
    project and then by branch; see :func:`resolve_pr` for why the two must
    read differently. Branch names are not unique across projects — every
    project's ``_main`` sits on ``main`` — so a flat key would answer one
    project's row with another's pull request.
    """
    projects = find_all_projects(projects_dir)
    # One live-session sweep shared across every project/worktree row, plus a
    # memo so the per-session worktree-list lookup runs once, not per row.
    live_sessions = await session_discovery.LiveSessionSet().sweep()

    # One budget for both levels. Every acquire wraps a leaf read, never a
    # coroutine that waits on another: a project holding a permit while its
    # worktrees queue for one would deadlock the whole read.
    limit = asyncio.Semaphore(concurrency)
    # Projects whose pull request read was refused for quota. Collected rather
    # than raised per project: every project fails the same way once the budget
    # is spent, and the caller needs to hear it once.
    spent: list[str] = []

    # Each project's reads are independent, so they run together. Read one
    # after another, 16 projects took 19s here, and every route waits on this.
    read = await asyncio.gather(
        *(
            _project_data(
                project_path,
                live_sessions,
                limit,
                active_branches=active_branches,
                pr_cache=(pr_cache or {}).get(project_path.name, {}),
                spent=spent,
            )
            for project_path in projects
        ),
        return_exceptions=True,
    )

    # A project that cannot be read costs its own row, not the whole read. A
    # worktree directory can go between the listing and the row read, and the
    # server polls this on its worktree poll: one raise would blank the UI.
    projects_data = []
    for project_path, outcome in zip(projects, read, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("could not read project %s: %s", project_path.name, outcome)
            continue
        projects_data.append(outcome)
    if spent:
        # Raised after the rows are built, so a caller that wants them can keep
        # them from the exception rather than losing the whole read. The rows
        # already carry each branch's last known pull request.
        raise RateLimited(
            f"GitHub GraphQL budget spent; {len(spent)} projects kept their "
            f"last known PR state",
            {"projects": projects_data},
        )
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
    live_sessions: session_discovery.LiveSessionSet
    limit: asyncio.Semaphore


async def _project_data(
    project_path: Path,
    live_sessions: session_discovery.LiveSessionSet,
    limit: asyncio.Semaphore,
    *,
    active_branches: set[str] | None = None,
    pr_cache: dict[str, PrStatus] | None = None,
    spent: list[str] | None = None,
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
    # One PR lookup per project, not per worktree. The batch is repo-scoped, so
    # it belongs here rather than in the worktree loop below. A project whose
    # worktrees are all detached has no branch to ask about, and `list-all`
    # visits every project — so skip the round trip rather than spend one.
    branches = {wt.branch for wt in worktrees if wt.branch}
    asked = branches if active_branches is None else branches & active_branches
    open_prs = {}
    if asked:
        # This is the `gh` call the cap exists for — see DEFAULT_CONCURRENCY.
        async with limit:
            try:
                open_prs = await get_open_prs(project_path, asked)
            except RateLimited:
                # Empty ``asked`` so the rows keep what the last poll learned
                # and nothing looks a branch up on its own. Every other project
                # is about to fail the same way, so the caller is told once
                # rather than each of them raising.
                if spent is not None:
                    spent.append(project_name)
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
    }
