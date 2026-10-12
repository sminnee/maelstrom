"""Every worktree across every project, as data.

The model behind the orchestrator server's worktree source.
:func:`session_display` and :func:`project_repo_url` are shared with the CLI.

Not pure: it reads git and the process table through the worktree and
session-discovery modules. It is the one place that knows how the rows are
assembled, so those reads happen once per project rather than once per caller.

It does not read ``gh``. Pull request state is a :data:`PrSnapshot` the caller
hands in: the orchestrator's ``PrStateCache`` owns that read, its timeout and
its rate-limit stand-off, so a hung ``gh`` cannot stall the local rows.
"""

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import session_discovery
from .base_store import GitConfigBaseStore
from .config import (
    ServiceDef,
    linear_team_id,
    load_config_or_default,
    service_port_names,
)
from .env import (
    EnvSummary,
    ServiceStatus,
    get_env_status,
    get_shared_status,
    summarise_env,
)
from .env_store import JsonEnvStore
from .github_model import PrStatus, is_open_pr, pr_url
from .ports import (
    get_app_url,
    get_dev_origin,
    get_port_allocation,
    is_web_port_name,
    service_url,
)
from .worktree import (
    WorktreeInfo,
    closed_worktrees_async,
    find_all_projects,
    get_head_sha_async,
    get_local_only_commits_async,
    get_pushed_commit_count_async,
    get_worktree_dirty_files_async,
    list_worktrees_async,
    run_git_async,
)
from .worktree_model import extract_worktree_name_from_folder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectPrs:
    """One project's pull requests, by branch, as the last read left them.

    ``stale_branches`` have **Stale PR state**: their ``by_branch`` entry is
    the last known state, not the current one.
    """

    by_branch: Mapping[str, PrStatus] = field(default_factory=dict)
    stale_branches: frozenset[str] = frozenset()


#: Pull request state by project name. Keyed by project because branch names
#: are not unique across them: every project's ``_main`` sits on ``main``.
PrSnapshot = Mapping[str, ProjectPrs]


async def project_branches(projects_dir: Path) -> dict[Path, set[str]]:
    """The branches checked out in each project's worktrees.

    One ``git worktree list`` per project, run together. The orchestrator
    names these branches to ``gh`` before it builds the rows. A project git
    cannot read is left out, as :func:`build_list_all_data` leaves out its row.
    """
    projects = find_all_projects(projects_dir)
    read = await asyncio.gather(
        *(list_worktrees_async(path) for path in projects), return_exceptions=True
    )
    branches: dict[Path, set[str]] = {}
    for path, outcome in zip(projects, read, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("could not list the worktrees of %s: %s", path.name, outcome)
            continue
        branches[path] = {wt.branch for wt in outcome if wt.branch}
    return branches


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
with 16 projects and 90 worktrees would otherwise run several hundred ``git``
subprocesses at once. The ``gh`` call is not under this cap: the caller reads
pull request state before the build, and hands it in.

24 is high enough that the read stays bounded by process start-up rather than
by the cap, and low enough not to swamp the machine.
"""


async def build_list_all_data(
    projects_dir: Path,
    concurrency: int = DEFAULT_CONCURRENCY,
    *,
    prs: PrSnapshot | None = None,
) -> dict[str, Any]:
    """Every project under ``projects_dir`` with its worktrees, as rows.

    ``{"projects": [...]}``, each project carrying ``name``, ``path``,
    ``stack_tip``, ``repo_url`` and ``worktrees``.
    A closed worktree is included with ``is_closed`` true and its counts
    zeroed.

    ``concurrency`` caps how many reads run at once across both fan-out
    levels — see :data:`DEFAULT_CONCURRENCY`.

    ``prs`` is the pull request state each row carries. A branch it does not
    name has no pull request on its row. A row's ``pr_stale`` says its branch
    is in :attr:`ProjectPrs.stale_branches`.
    """
    projects = find_all_projects(projects_dir)
    # One live-session sweep shared across every project/worktree row, plus a
    # memo so the per-session worktree-list lookup runs once, not per row.
    live_sessions = await session_discovery.LiveSessionSet().sweep()

    # One budget for both levels. Every acquire wraps a leaf read, never a
    # coroutine that waits on another: a project holding a permit while its
    # worktrees queue for one would deadlock the whole read.
    limit = asyncio.Semaphore(concurrency)
    dev_origin = get_dev_origin()

    # Each project's reads are independent, so they run together. Read one
    # after another, 16 projects took 19s here, and every route waits on this.
    read = await asyncio.gather(
        *(
            _project_data(
                project_path,
                live_sessions,
                limit,
                dev_origin,
                (prs or {}).get(project_path.name, ProjectPrs()),
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
    return {"projects": projects_data}


@dataclass(frozen=True)
class _ProjectContext:
    """What every worktree row in one project needs, read once for all of them.

    Each field is a per-project batch — the PR state, the closed set, the
    bases, the repo URL — so a row reads only what is specific to itself.
    ``limit`` is shared with every other project too, so the two fan-out
    levels draw on one budget.
    """

    path: Path
    name: str
    closed_paths: set[Path]
    bases: dict[str, str]
    prs: ProjectPrs
    repo_url: str | None
    live_sessions: session_discovery.LiveSessionSet
    #: The project's shared services, read once for every row's env state.
    shared_status: list[ServiceStatus] | None
    #: The scheme and host every reported URL starts with.
    dev_origin: str
    limit: asyncio.Semaphore


async def _project_data(
    project_path: Path,
    live_sessions: session_discovery.LiveSessionSet,
    limit: asyncio.Semaphore,
    dev_origin: str,
    prs: ProjectPrs,
) -> dict[str, Any]:
    """One project's row, with a row per worktree under it.

    ``limit`` caps this project's own reads and its worktrees' alike, and is
    shared with every other project so the two fan-out levels do not multiply.
    """
    project_name = project_path.name
    # Each leaf read takes a permit on its own, never the whole prologue: a
    # project holding one while its worktrees queue for the same budget would
    # deadlock the read.
    async with limit:
        worktrees = await list_worktrees_async(project_path)
    async with limit:
        # The closed check is one batch per project, not two subprocesses per
        # worktree.
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
        prs=prs,
        repo_url=repo_url,
        live_sessions=live_sessions,
        shared_status=await asyncio.to_thread(
            get_shared_status, JsonEnvStore(), project_name
        ),
        dev_origin=dev_origin,
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
            "pr_merged_at": None,
            "pr_head_oid": None,
            "pr_stale": False,
            "head_oid": None,
            "pushed_commits": None,
            "app_url": None,
            "app_running": False,
            "env": {"state": "stopped", "services": []},
            "session_count": 0,
        }

    base = ctx.bases.get(wt.branch or "")
    # The subprocess reads below are what the cap is for. Held together
    # rather than one permit each, so a row that takes a permit finishes and
    # gives it back, instead of queueing again between its own reads.
    async with ctx.limit:
        dirty_count = len(await get_worktree_dirty_files_async(wt.path))
        local_commits = await get_local_only_commits_async(wt.path, wt.branch)
        head_oid = await get_head_sha_async(wt.path)

        pr = ctx.prs.by_branch.get(wt.branch) if wt.branch else None
        # A PR read that carried no URL gets one joined from the repo.
        row_pr_url = (pr.url or pr_url(ctx.repo_url, pr.number)) if pr else None
        pushed_commits = None
        if not is_open_pr(pr) and wt.branch:
            pushed_commits = await get_pushed_commit_count_async(wt.path, wt.branch)

    session_count = ctx.live_sessions.count_for(wt.path)

    app_url = None
    app_running = False
    app_info = get_app_url(ctx.path, display_name, origin=ctx.dev_origin)
    if app_info:
        app_url, app_running = app_info
    env = await asyncio.to_thread(_env_summary, wt.path, display_name, ctx, app_url)

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
        "pr_merged_at": pr.merged_at if pr else None,
        "pr_head_oid": pr.head_oid if pr else None,
        "pr_stale": wt.branch in ctx.prs.stale_branches,
        "head_oid": head_oid,
        "pushed_commits": pushed_commits,
        "app_url": app_url,
        "app_running": app_running,
        "env": {
            "state": env.state,
            "services": [asdict(service) for service in env.services],
        },
        "session_count": session_count,
    }


def _web_urls(
    port_base: int | None,
    declared: list[ServiceDef],
    port_names: list[str],
    origin: str,
) -> dict[str, str]:
    """Each per-worktree service's URL, on its first web-facing port."""
    if port_base is None:
        return {}
    urls: dict[str, str] = {}
    for svc in declared:
        web = [p.name for p in svc.ports if is_web_port_name(p.name)]
        if not svc.shared and web and web[0] in port_names:
            urls[svc.name] = service_url(
                port_base * 10 + port_names.index(web[0]), origin
            )
    return urls


def _env_summary(
    worktree_path: Path, worktree: str, ctx: _ProjectContext, app_url: str | None
) -> EnvSummary:
    """The worktree's env state, from its config and the env state files.

    Liveness is the tracked pid, not a port probe. The config is the worktree's
    own, because a branch can declare a service main lacks. Blocking: it reads
    files, so the caller runs it off the loop.
    """
    config = load_config_or_default(worktree_path)
    declared = config.services
    port_base = get_port_allocation(ctx.path, worktree)
    urls = _web_urls(port_base, declared, service_port_names(config), ctx.dev_origin)
    if not declared and app_url:
        urls["app"] = app_url
    tracked = get_env_status(JsonEnvStore(), ctx.name, worktree)
    return summarise_env(declared, urls, tracked, ctx.shared_status)
