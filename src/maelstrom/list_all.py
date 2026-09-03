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

from pathlib import Path
from typing import Any

from . import session_discovery
from . import task as task_model
from .base_store import GitConfigBaseStore
from .github import get_open_prs, get_pr_number_and_commits
from .ports import get_app_url
from .task_store import GitFileStore
from .worktree import (
    closed_worktrees,
    find_all_projects,
    get_local_only_commits,
    get_pushed_commit_count,
    get_worktree_dirty_files,
    list_worktrees,
)
from .worktree_model import extract_worktree_name_from_folder, has_claude_transcript


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


def resolve_pr(open_prs, project_path, branch):
    """Resolve ``branch`` to ``(pr_number, commit_count)`` for the PR column.

    ``open_prs`` is the whole-repo batch from :func:`get_open_prs`, or ``None``
    when that call failed. A successful batch is authoritative: a branch missing
    from it has no open PR, so we answer without a second network call. A failed
    batch falls back to the per-branch lookup, which keeps a broken ``gh`` no
    worse than it was before batching — one blank row rather than a blank column.
    """
    if not branch:
        return (None, None)
    if open_prs is not None:
        return open_prs.get(branch, (None, None))
    return get_pr_number_and_commits(project_path, branch)


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


def build_list_all_data(projects_dir: Path) -> dict[str, Any]:
    """Every project under ``projects_dir`` with its worktrees, as ``list-all`` data.

    The shape is what ``mael --json list-all`` prints: ``{"projects": [...]}``,
    each project carrying ``name``, ``path``, ``stack_tip`` and ``worktrees``.
    A closed worktree is included with ``is_closed`` true and its counts
    zeroed. ``session_stopped`` says a task on the row's branch ran here and
    stopped; the table renders it as the stopped marker.
    """
    projects = find_all_projects(projects_dir)
    # One live-session sweep shared across every project/worktree row, plus a
    # memo so the per-session worktree-list lookup runs once, not per row.
    live_sessions = session_discovery.LiveSessionSet()

    projects_data = []
    for project_path in projects:
        project_name = project_path.name
        worktrees = list_worktrees(project_path)
        worktree_data = []
        # Branch → task session ids for this project (stopped-marker detection).
        branch_sessions = branch_session_ids(project_name)
        # One PR lookup per project, not per worktree. The batch is repo-scoped,
        # so it belongs inside this loop. A project whose worktrees are all
        # detached has no branch to ask about, and `list-all` visits every
        # project — so skip the round trip rather than spend one per project.
        open_prs = (
            get_open_prs(project_path) if any(wt.branch for wt in worktrees) else {}
        )
        # Likewise the closed check: one batch per project, not two subprocesses
        # per worktree.
        closed_paths = closed_worktrees(project_path, worktrees)
        # One store read per project answers the base for every row.
        base_store = GitConfigBaseStore(project_path)
        bases = base_store.all()

        for wt in worktrees:
            # Skip the project root (bare repo). Resolved, because git reports
            # the real path and a symlinked projects dir would never match.
            if wt.path.resolve() == project_path.resolve():
                continue

            display_name = (
                extract_worktree_name_from_folder(project_name, wt.path.name)
                or wt.path.name
            )

            if wt.path in closed_paths:
                worktree_data.append(
                    {
                        "name": display_name,
                        "folder": wt.path.name,
                        "path": str(wt.path),
                        "branch": wt.branch or None,
                        "base": None,
                        "is_closed": True,
                        "dirty_files": 0,
                        "local_commits": 0,
                        "pr_number": None,
                        "pr_commits": None,
                        "pushed_commits": None,
                        "app_url": None,
                        "app_running": False,
                        "session_count": 0,
                        "session_stopped": False,
                    }
                )
                continue

            base = bases.get(wt.branch or "")
            dirty_count = len(get_worktree_dirty_files(wt.path))
            local_commits = get_local_only_commits(wt.path, wt.branch)

            pr_num, pr_commits = resolve_pr(open_prs, project_path, wt.branch)
            pushed_commits = None
            if not pr_num and wt.branch:
                pushed_commits = get_pushed_commit_count(wt.path, wt.branch)

            session_count = live_sessions.count_for(wt.path)
            stopped = not session_count and session_stopped(
                wt.path, wt.branch, branch_sessions
            )

            app_url = None
            app_running = False
            app_info = get_app_url(project_path, display_name)
            if app_info:
                app_url, app_running = app_info

            worktree_data.append(
                {
                    "name": display_name,
                    "folder": wt.path.name,
                    "path": str(wt.path),
                    "branch": wt.branch or None,
                    "base": base,
                    "is_closed": False,
                    "dirty_files": dirty_count,
                    "local_commits": local_commits,
                    "pr_number": pr_num,
                    "pr_commits": pr_commits,
                    "pushed_commits": pushed_commits,
                    "app_url": app_url,
                    "app_running": app_running,
                    "session_count": session_count,
                    "session_stopped": stopped,
                }
            )

        projects_data.append(
            {
                "name": project_name,
                "path": str(project_path),
                "stack_tip": base_store.read_stack_tip(),
                "worktrees": worktree_data,
            }
        )

    return {"projects": projects_data}
