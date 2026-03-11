"""CLI commands for git operations."""

import json
import subprocess
from pathlib import Path

import click

from .context import resolve_context
from .worktree import (
    MAIN_BRANCH,
    get_commits_ahead,
    get_current_branch,
    get_local_only_commits,
)


def get_worktree_file_status(path: Path) -> dict[str, list[str]]:
    """Parse git status --porcelain into categorised file lists.

    Returns:
        Dict with keys 'staged', 'modified', 'untracked', each a list of file paths.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    staged = []
    modified = []
    untracked = []

    if result.returncode != 0:
        return {"staged": staged, "modified": modified, "untracked": untracked}

    for line in result.stdout.rstrip("\n").split("\n"):
        if not line:
            continue
        index_status = line[0]
        work_status = line[1]
        filename = line[3:]

        # Staged changes (index has a non-space status, not untracked)
        if index_status in "MADRC":
            staged.append(filename)
        # Working tree modifications (not staged)
        if work_status in "MD" and index_status != "?":
            modified.append(filename)
        # Untracked files
        if index_status == "?" and work_status == "?":
            untracked.append(filename)

    return {"staged": staged, "modified": modified, "untracked": untracked}


def get_diff_stat_summary(path: Path) -> tuple[int, int, int] | None:
    """Parse git diff --stat summary line.

    Returns:
        Tuple of (files_changed, insertions, deletions) or None if no changes.
    """
    # Diff of staged + unstaged against HEAD
    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    # The last line looks like: " 3 files changed, 10 insertions(+), 5 deletions(-)"
    lines = result.stdout.strip().split("\n")
    summary_line = lines[-1].strip()

    files = 0
    insertions = 0
    deletions = 0

    import re
    m_files = re.search(r"(\d+) files? changed", summary_line)
    m_ins = re.search(r"(\d+) insertions?\(\+\)", summary_line)
    m_del = re.search(r"(\d+) deletions?\(-\)", summary_line)

    if m_files:
        files = int(m_files.group(1))
    if m_ins:
        insertions = int(m_ins.group(1))
    if m_del:
        deletions = int(m_del.group(1))

    if files == 0:
        return None

    return (files, insertions, deletions)


def get_recent_commits(path: Path, count: int = 5) -> list[dict[str, str]]:
    """Get recent commits as list of {hash, message} dicts."""
    result = subprocess.run(
        ["git", "log", f"--oneline", f"-{count}"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.rstrip("\n").split("\n"):
        if not line:
            continue
        parts = line.split(" ", 1)
        commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
    return commits


def format_git_status(
    branch: str,
    commits_ahead: int,
    unpushed: int,
    file_status: dict[str, list[str]],
    diff_stat: tuple[int, int, int] | None,
    recent_commits: list[dict[str, str]],
) -> str:
    """Format git status as compact plain text."""
    lines = []

    # Branch section
    lines.append("## Branch")
    lines.append("")
    branch_info = branch
    details = []
    if commits_ahead > 0:
        details.append(f"{commits_ahead} ahead of {MAIN_BRANCH}")
    if unpushed > 0:
        details.append(f"{unpushed} unpushed")
    if details:
        branch_info += f" ({', '.join(details)})"
    lines.append(branch_info)

    # Check if working tree is clean
    has_changes = (
        file_status["staged"]
        or file_status["modified"]
        or file_status["untracked"]
    )

    if not has_changes and commits_ahead == 0:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"Clean working tree, no commits ahead of {MAIN_BRANCH}.")
        return "\n".join(lines)

    if not has_changes:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("Clean working tree.")
    else:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Working Tree")
        lines.append("")

        if file_status["staged"]:
            lines.append("Staged:")
            for f in file_status["staged"]:
                lines.append(f"  {f}")
        if file_status["modified"]:
            lines.append("Modified:")
            for f in file_status["modified"]:
                lines.append(f"  {f}")
        if file_status["untracked"]:
            lines.append("Untracked:")
            for f in file_status["untracked"]:
                lines.append(f"  {f}")

        if diff_stat:
            files, ins, dels = diff_stat
            lines.append(f"Diff: {files} files, +{ins} -{dels}")

    # Commits section
    if recent_commits:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Commits")
        lines.append("")
        for c in recent_commits:
            lines.append(f"{c['hash']} {c['message']}")

    return "\n".join(lines)


def build_status_dict(
    branch: str,
    commits_ahead: int,
    unpushed: int,
    file_status: dict[str, list[str]],
    diff_stat: tuple[int, int, int] | None,
    recent_commits: list[dict[str, str]],
) -> dict:
    """Build git status as a JSON-serialisable dict."""
    result = {
        "branch": branch,
        "commits_ahead": commits_ahead,
        "unpushed": unpushed,
        "staged": file_status["staged"],
        "modified": file_status["modified"],
        "untracked": file_status["untracked"],
        "diff_stat": None,
        "recent_commits": recent_commits,
    }
    if diff_stat:
        files, ins, dels = diff_stat
        result["diff_stat"] = {"files": files, "insertions": ins, "deletions": dels}
    return result


@click.group("git")
@click.pass_context
def git(ctx):
    """Git helper commands."""
    pass


@git.command("status")
@click.argument("target", required=False, default=None)
@click.pass_context
def git_status(ctx, target):
    """Show a compact git status summary."""
    output_json = ctx.obj.get("json", False) if ctx.obj else False

    # Resolve working directory
    try:
        context = resolve_context(target, require_project=False, require_worktree=False)
        cwd = context.worktree_path or Path.cwd()
    except ValueError:
        cwd = Path.cwd()

    branch = get_current_branch(cwd)
    commits_ahead = get_commits_ahead(cwd, f"origin/{MAIN_BRANCH}")
    unpushed = get_local_only_commits(cwd, branch)
    file_status = get_worktree_file_status(cwd)
    diff_stat = get_diff_stat_summary(cwd)
    recent_commits = get_recent_commits(cwd)

    if output_json:
        data = build_status_dict(branch, commits_ahead, unpushed, file_status, diff_stat, recent_commits)
        click.echo(json.dumps(data, indent=2))
    else:
        output = format_git_status(branch, commits_ahead, unpushed, file_status, diff_stat, recent_commits)
        click.echo(output)
