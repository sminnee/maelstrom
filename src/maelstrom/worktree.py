"""Worktree management for maelstrom projects."""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .claude_integration import get_shared_dir
from .config import load_config_or_default
from .ports import (
    allocate_port_base,
    generate_port_env_vars,
    get_allocated_port_bases,
    get_port_allocation,
    load_port_allocations,
    record_port_allocation,
    remove_port_allocation,
)

# Fixed worktree names (NATO phonetic alphabet)
WORKTREE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
]

# Single-letter shortcodes for worktree names (all 26 first letters are unique)
WORKTREE_SHORTCODES = {name[0]: name for name in WORKTREE_NAMES}


def resolve_worktree_shortcode(name: str) -> str:
    """Resolve a single-letter shortcode to its full NATO worktree name.

    Args:
        name: A worktree name or single-letter shortcode.

    Returns:
        The full NATO name if input is a single letter, otherwise the input unchanged.
    """
    if len(name) == 1 and name in WORKTREE_SHORTCODES:
        return WORKTREE_SHORTCODES[name]
    return name

# Files managed by maelstrom that should be ignored when checking for dirty files
MAELSTROM_MANAGED_FILES = {".env"}

# Section markers for managed .env content
ENV_SECTION_START = "# Maelstrom port allocations"
ENV_SECTION_END = "# End Maelstrom port allocations"

# Main branch name (hardcoded - no master support)
MAIN_BRANCH = "main"



@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    commit: str
    is_dirty: bool = False
    commits_ahead: int = 0


def sanitize_branch_name(branch: str) -> str:
    """Convert branch name to directory-safe name (slashes → dashes)."""
    return branch.replace("/", "-")


def get_worktree_folder_name(project_name: str, worktree_name: str) -> str:
    """Get the folder name for a worktree.

    Args:
        project_name: The project name (e.g., 'askastro').
        worktree_name: The NATO phonetic worktree name (e.g., 'alpha').

    Returns:
        The folder name (e.g., 'askastro-alpha').
    """
    return f"{project_name}-{worktree_name}"


def extract_worktree_name_from_folder(project_name: str, folder_name: str) -> str | None:
    """Extract the worktree name from a folder name.

    Args:
        project_name: The project name (e.g., 'askastro').
        folder_name: The folder name (e.g., 'askastro-alpha').

    Returns:
        The worktree name (e.g., 'alpha') or None if not a valid worktree folder.
    """
    prefix = f"{project_name}-"
    if folder_name.startswith(prefix):
        potential_name = folder_name[len(prefix):]
        if potential_name in WORKTREE_NAMES:
            return potential_name
    return None


def run_cmd(cmd: list[str], cwd: Path | None = None, quiet: bool = False, check: bool = True, stream: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    if not quiet:
        print(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=not stream,
        text=True,
        check=check,
    )


def run_git(args: list[str], cwd: Path | None = None, quiet: bool = False) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    return run_cmd(["git"] + args, cwd=cwd, quiet=quiet, check=True)


class UpdateMainResult:
    """Result of updating the local main branch."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status  # "updated", "skipped", "warning"
        self.message = message


def update_local_main(project_path: Path) -> UpdateMainResult:
    """Fast-forward local main to match origin/main after a fetch.

    Uses ``git update-ref`` when main is not checked out in any worktree.
    If main is checked out somewhere, skips silently (the worktree's own
    rebase handles it).  If local main is ahead of origin/main, returns
    a warning.

    Args:
        project_path: Path to the project root (bare-ish repo).

    Returns:
        UpdateMainResult with status and message.
    """
    # Check if local main exists
    result = run_cmd(
        ["git", "rev-parse", "--verify", f"refs/heads/{MAIN_BRANCH}"],
        cwd=project_path, quiet=True, check=False,
    )
    if result.returncode != 0:
        return UpdateMainResult("skipped", f"No local {MAIN_BRANCH} branch")

    local_sha = result.stdout.strip()

    # Check if origin/main exists
    result = run_cmd(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{MAIN_BRANCH}"],
        cwd=project_path, quiet=True, check=False,
    )
    if result.returncode != 0:
        return UpdateMainResult("skipped", f"No origin/{MAIN_BRANCH}")

    origin_sha = result.stdout.strip()

    # Already in sync
    if local_sha == origin_sha:
        return UpdateMainResult("skipped", f"{MAIN_BRANCH} already up to date")

    # Check if local main is ahead of origin/main
    result = run_cmd(
        ["git", "rev-list", "--count", f"origin/{MAIN_BRANCH}..{MAIN_BRANCH}"],
        cwd=project_path, quiet=True, check=False,
    )
    if result.returncode == 0:
        ahead = int(result.stdout.strip())
        if ahead > 0:
            return UpdateMainResult(
                "warning",
                f"Local {MAIN_BRANCH} is {ahead} commit(s) ahead of origin/{MAIN_BRANCH}",
            )

    # Check if main is checked out in any worktree
    worktrees = list_worktrees(project_path)
    for wt in worktrees:
        if wt.branch == MAIN_BRANCH:
            return UpdateMainResult(
                "skipped",
                f"{MAIN_BRANCH} is checked out in {wt.path.name}",
            )

    # Safe to fast-forward via update-ref
    try:
        run_git(
            ["update-ref", f"refs/heads/{MAIN_BRANCH}", origin_sha, local_sha],
            cwd=project_path,
        )
        return UpdateMainResult("updated", f"Fast-forwarded {MAIN_BRANCH} to origin/{MAIN_BRANCH}")
    except subprocess.CalledProcessError:
        return UpdateMainResult("skipped", f"Could not update {MAIN_BRANCH} ref")


def get_worktree_dirty_files(worktree_path: Path) -> list[str]:
    """Get modified/untracked files in worktree, excluding maelstrom-managed files.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        List of file paths that are modified or untracked (excluding maelstrom-managed files).
    """
    if not worktree_path.is_dir():
        return []

    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []

    dirty_files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # git status --porcelain format: XY filename
        # where XY is the status code (2 chars) followed by a space
        filename = line[3:].strip()
        # Handle renamed files (format: "old -> new")
        if " -> " in filename:
            filename = filename.split(" -> ")[1]
        # Skip maelstrom-managed files
        if filename not in MAELSTROM_MANAGED_FILES:
            dirty_files.append(filename)

    return dirty_files


def get_commits_ahead(worktree_path: Path, base_branch: str = "origin/main") -> int:
    """Get the number of commits ahead of the base branch.

    Args:
        worktree_path: Path to the worktree directory.
        base_branch: Base branch to compare against.

    Returns:
        Number of commits ahead, or 0 if unable to determine.
    """
    if not worktree_path.is_dir():
        return 0

    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def get_local_only_commits(worktree_path: Path, branch: str | None) -> int:
    """Get commits that are local but not pushed to the remote branch.

    Args:
        worktree_path: Path to the worktree directory.
        branch: Branch name (or None if detached).

    Returns:
        Number of local-only commits.
    """
    if not branch:
        return 0

    # Check if remote branch exists
    remote_branch = f"origin/{branch}"
    result = subprocess.run(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        # Remote exists, count commits not on remote
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{remote_branch}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            try:
                return int(result.stdout.strip())
            except ValueError:
                return 0
        return 0
    else:
        # No remote branch - count all commits ahead of main
        return get_commits_ahead(worktree_path)


def get_pushed_commit_count(worktree_path: Path, branch: str) -> int | None:
    """Get the number of commits on the remote branch (ahead of main).

    Args:
        worktree_path: Path to the worktree directory.
        branch: Branch name.

    Returns:
        Number of pushed commits, or None if branch not pushed.
    """
    remote_branch = f"origin/{branch}"

    # Check if remote branch exists
    result = subprocess.run(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        return None  # Not pushed

    # Count commits on remote branch ahead of main
    result = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{MAIN_BRANCH}..{remote_branch}"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        try:
            return int(result.stdout.strip())
        except ValueError:
            return 0
    return 0


def has_root_worktree(project_path: Path) -> bool:
    """Check if the project has files checked out at the root level.

    Args:
        project_path: Path to the project root.

    Returns:
        True if there are tracked files at the root level.
    """
    git_dir = project_path / ".git"
    if not git_dir.exists():
        return False

    try:
        result = run_git(["ls-files"], cwd=project_path, quiet=True)
        return result.stdout.strip() != ""
    except subprocess.CalledProcessError:
        return False


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch name."""
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path, quiet=True)
    return result.stdout.strip()


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    branch: str
    message: str
    had_conflicts: bool = False
    merge_base: str | None = None  # SHA of merge-base before rebase
    upstream_head: str | None = None  # SHA of origin/main
    pushed: bool = False  # Whether the branch was pushed to remote
    push_message: str | None = None  # Push status message


@dataclass
class CloseResult:
    """Result of a close operation."""

    success: bool
    message: str
    had_dirty_files: bool = False
    had_unpushed_commits: bool = False


@dataclass
class TidyBranchResult:
    """Result of tidying a single branch."""

    branch: str
    action: str  # "deleted", "pushed", "rebased", "skipped_conflicts", "skipped_checked_out", "skipped_error"
    success: bool
    message: str
    deleted_local: bool = False
    deleted_remote: bool = False


def is_worktree_closed(worktree_info: WorktreeInfo) -> bool:
    """Check if a worktree is in 'closed' state (detached, clean, at or behind origin/main).

    A closed worktree is available for recycling when creating a new worktree.
    A worktree is considered closed if:
    - It is in detached HEAD state (no branch checked out)
    - It has no dirty files
    - It has no commits ahead of origin/main (HEAD is at or behind origin/main)

    Args:
        worktree_info: WorktreeInfo for the worktree.

    Returns:
        True if the worktree is closed and available for recycling.
    """
    # Must be in detached HEAD state (no branch)
    if worktree_info.branch:
        return False

    if get_worktree_dirty_files(worktree_info.path):
        return False

    if get_commits_ahead(worktree_info.path) > 0:
        return False

    return True


def find_closed_worktree(project_path: Path) -> WorktreeInfo | None:
    """Find a closed worktree available for recycling.

    Args:
        project_path: Path to the project root.

    Returns:
        WorktreeInfo for a closed worktree, or None if none available.
    """
    worktrees = list_worktrees(project_path)

    for wt in worktrees:
        # Skip the project root itself
        if wt.path == project_path:
            continue
        if is_worktree_closed(wt):
            return wt

    return None


def sync_worktree(worktree_path: Path, skip_fetch: bool = False) -> SyncResult:
    """Sync a worktree by rebasing against origin/main.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step (useful when syncing multiple
            worktrees that share the same repo, where fetch was already done).

    Returns:
        SyncResult with status and message.
    """
    worktree_path = worktree_path.resolve()

    # Get current branch
    branch = get_current_branch(worktree_path)

    # Fetch from origin (unless skipped)
    if not skip_fetch:
        try:
            run_git(["fetch", "origin"], cwd=worktree_path)
            # Fast-forward local main to match origin/main
            update_local_main(worktree_path.parent)
        except subprocess.CalledProcessError as e:
            return SyncResult(
                success=False,
                branch=branch,
                message=f"Failed to fetch from origin: {e.stderr}",
            )

    # Get merge-base and origin/main SHA before rebasing (for conflict instructions)
    merge_base: str | None = None
    upstream_head: str | None = None
    try:
        # Get merge-base (where branch diverged from origin/main)
        base_result = run_cmd(
            ["git", "merge-base", "HEAD", "origin/main"],
            cwd=worktree_path,
            quiet=True,
            check=False,
        )
        if base_result.returncode == 0 and base_result.stdout.strip():
            merge_base = base_result.stdout.strip()[:7]  # Short SHA

        # Get origin/main SHA
        head_result = run_cmd(
            ["git", "rev-parse", "--short", "origin/main"],
            cwd=worktree_path,
            quiet=True,
            check=False,
        )
        if head_result.returncode == 0 and head_result.stdout.strip():
            upstream_head = head_result.stdout.strip()
    except Exception:
        pass

    # Rebase with autostash
    result = run_cmd(
        ["git", "rebase", "--autostash", "origin/main"],
        cwd=worktree_path,
        check=False,
    )

    if result.returncode != 0:
        # Rebase failed - likely conflicts
        return SyncResult(
            success=False,
            branch=branch,
            message=result.stderr or result.stdout,
            had_conflicts=True,
            merge_base=merge_base,
            upstream_head=upstream_head,
        )

    # Rebase succeeded - check if remote branch exists and push
    pushed = False
    push_message = None

    # Check if remote branch exists
    remote_branch = f"origin/{branch}"
    remote_check = run_cmd(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=worktree_path,
        quiet=True,
        check=False,
    )

    if remote_check.returncode == 0:
        # Remote branch exists - push with force-with-lease
        push_result = run_cmd(
            ["git", "push", "--force-with-lease", "origin", branch],
            cwd=worktree_path,
            check=False,
        )
        if push_result.returncode == 0:
            pushed = True
            push_message = f"Pushed {branch} to origin"
        else:
            push_message = f"Push failed: {push_result.stderr or push_result.stdout}"

    return SyncResult(
        success=True,
        branch=branch,
        message=f"Successfully rebased {branch} onto origin/main",
        pushed=pushed,
        push_message=push_message,
    )


def close_worktree(worktree_path: Path) -> CloseResult:
    """Close a worktree by syncing and resetting to origin/main.

    This operation:
    1. Syncs the worktree (rebase against origin/main)
    2. Verifies no uncommitted changes
    3. Verifies no unmerged commits
    4. Resets HEAD to origin/main

    After closing, the worktree's HEAD will point to the same commit as
    origin/main, making it available for recycling via is_worktree_closed().

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        CloseResult with status and message.
    """
    worktree_path = worktree_path.resolve()

    # First sync the worktree
    sync_result = sync_worktree(worktree_path)
    if not sync_result.success:
        return CloseResult(
            success=False,
            message=f"Sync failed: {sync_result.message}",
        )

    # Check for dirty files
    dirty_files = get_worktree_dirty_files(worktree_path)
    if dirty_files:
        return CloseResult(
            success=False,
            message="Worktree has uncommitted changes",
            had_dirty_files=True,
        )

    # Check for unmerged commits
    commits_ahead = get_commits_ahead(worktree_path)
    if commits_ahead > 0:
        return CloseResult(
            success=False,
            message=f"Worktree has {commits_ahead} commit(s) not merged to origin/main",
            had_unpushed_commits=True,
        )

    # Detach HEAD at origin/main to mark as closed
    # This avoids branch conflicts when the branch might be checked out elsewhere
    try:
        run_git(["checkout", "--detach", f"origin/{MAIN_BRANCH}"], cwd=worktree_path)
    except subprocess.CalledProcessError as e:
        return CloseResult(
            success=False,
            message=f"Failed to detach at origin/{MAIN_BRANCH}: {e.stderr}",
        )

    # Free the port allocation so the ports can be reused
    project_path = worktree_path.parent
    project_name = project_path.name
    nato_name = extract_worktree_name_from_folder(project_name, worktree_path.name)
    if nato_name:
        remove_port_allocation(project_path, nato_name)

    return CloseResult(
        success=True,
        message=f"Worktree closed (detached at origin/{MAIN_BRANCH})",
    )


def recycle_worktree(worktree_path: Path, branch: str) -> Path:
    """Recycle a closed worktree for a new branch.

    Assumes the worktree is already on main and clean.

    Args:
        worktree_path: Path to the closed worktree.
        branch: Branch name to switch to.

    Returns:
        Path to the recycled worktree (same as input).

    Raises:
        RuntimeError: If recycling fails.
    """
    worktree_path = worktree_path.resolve()

    # Fetch latest
    try:
        run_git(["fetch", "origin"], cwd=worktree_path)
        # Fast-forward local main to match origin/main
        update_local_main(worktree_path.parent)
    except subprocess.CalledProcessError:
        pass  # Continue even if fetch fails (might be offline)

    # Check if branch exists on remote
    remote_branch = f"origin/{branch}"
    try:
        run_git(["rev-parse", "--verify", remote_branch], cwd=worktree_path, quiet=True)
        remote_exists = True
    except subprocess.CalledProcessError:
        remote_exists = False

    # Check if branch exists locally
    try:
        run_git(["rev-parse", "--verify", branch], cwd=worktree_path, quiet=True)
        local_exists = True
    except subprocess.CalledProcessError:
        local_exists = False

    # Switch to the new branch
    try:
        if remote_exists:
            # Reset local branch to match remote
            run_git(["checkout", "-B", branch, remote_branch], cwd=worktree_path)
        elif local_exists:
            # Switch to existing local branch
            run_git(["checkout", branch], cwd=worktree_path)
        else:
            # Create new branch from origin/main (HEAD may be behind if recycled)
            run_git(["checkout", "-b", branch, f"origin/{MAIN_BRANCH}"], cwd=worktree_path)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to switch to branch {branch}: {e.stderr}")

    # Update .claude/CLAUDE.local.md
    project_path = worktree_path.parent
    wt_name = extract_worktree_name_from_folder(project_path.name, worktree_path.name)
    if wt_name:
        update_claude_local_md(project_path, worktree_path, wt_name)

    _setup_claude_settings_symlink(worktree_path)

    return worktree_path


def list_worktrees(project_path: Path) -> list[WorktreeInfo]:
    """List all worktrees in the project.

    Args:
        project_path: Path to the project root (bare repo).

    Returns:
        List of WorktreeInfo objects.
    """
    try:
        result = run_git(["worktree", "list", "--porcelain"], cwd=project_path, quiet=True)
    except subprocess.CalledProcessError:
        return []

    worktrees = []
    current: dict[str, str] = {}

    for line in result.stdout.strip().split("\n"):
        if not line:
            if current:
                worktrees.append(
                    WorktreeInfo(
                        path=Path(current.get("worktree", "")),
                        branch=current.get("branch", "").replace("refs/heads/", ""),
                        commit=current.get("HEAD", ""),
                    )
                )
                current = {}
            continue

        if line.startswith("worktree "):
            current["worktree"] = line[9:]
        elif line.startswith("HEAD "):
            current["HEAD"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]

    if current:
        worktrees.append(
            WorktreeInfo(
                path=Path(current.get("worktree", "")),
                branch=current.get("branch", "").replace("refs/heads/", ""),
                commit=current.get("HEAD", ""),
            )
        )

    # Filter out stale worktrees whose directories no longer exist
    valid_worktrees = []
    for wt in worktrees:
        if wt.path.is_dir():
            valid_worktrees.append(wt)
        else:
            print(
                f"Warning: worktree '{wt.path}' is registered in git but its "
                f"directory is missing. Run 'git worktree prune' in "
                f"{project_path} to clean up.",
                file=sys.stderr,
            )

    return valid_worktrees


def find_all_projects(projects_dir: Path) -> list[Path]:
    """Find all Maelstrom-managed projects in projects_dir.

    A valid project has a .mael marker file.

    Args:
        projects_dir: Path to the projects directory (e.g., ~/Projects).

    Returns:
        Sorted list of paths to valid Maelstrom projects.
    """
    projects = []
    if not projects_dir.is_dir():
        return projects

    for entry in sorted(projects_dir.iterdir()):
        if entry.is_dir() and (entry / ".mael").exists():
            projects.append(entry)

    return projects


def get_next_worktree_name(project_path: Path) -> str:
    """Get the first unused worktree name from the fixed list.

    Args:
        project_path: Path to the project root.

    Returns:
        The first available worktree name from WORKTREE_NAMES.

    Raises:
        RuntimeError: If all 26 worktree names are in use.
    """
    project_name = project_path.name
    existing_folders = {wt.path.name for wt in list_worktrees(project_path)}

    # Extract worktree names from folder names (e.g., "myproject-alpha" -> "alpha")
    existing_names = set()
    for folder in existing_folders:
        wt_name = extract_worktree_name_from_folder(project_name, folder)
        if wt_name:
            existing_names.add(wt_name)

    for name in WORKTREE_NAMES:
        if name not in existing_names:
            return name
    raise RuntimeError("All worktree names are in use (max 26)")


def extract_project_name(git_url: str) -> str:
    """Extract project name from a git URL.

    Args:
        git_url: Git URL (e.g., git@github.com:user/repo.git or https://github.com/user/repo.git)

    Returns:
        Project name (e.g., 'repo')
    """
    # Remove trailing .git if present
    url = git_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    # Extract the last path component
    if "/" in url:
        return url.rsplit("/", 1)[-1]
    if ":" in url:
        return url.rsplit(":", 1)[-1]

    return url


def add_project(git_url: str, projects_dir: Path | None = None) -> Path:
    """Clone a git repository in bare format for use with maelstrom.

    Creates the structure:
        ~/Projects/<project>/.git  (bare clone)
        ~/Projects/<project>/alpha (initial worktree)

    Args:
        git_url: Git URL to clone.
        projects_dir: Base directory for projects (default: ~/Projects).

    Returns:
        Path to the project directory.

    Raises:
        RuntimeError: If cloning fails.
    """
    if projects_dir is None:
        projects_dir = Path.home() / "Projects"

    project_name = extract_project_name(git_url)
    project_path = projects_dir / project_name

    if project_path.exists():
        raise RuntimeError(f"Project directory already exists: {project_path}")

    # Ensure projects directory exists
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Create project directory
    project_path.mkdir()

    # Clone as bare into .git subdirectory
    git_dir = project_path / ".git"
    run_cmd(["git", "clone", "--bare", git_url, str(git_dir)])

    # Set up fetch refspec to create origin/* remote tracking refs
    # (core.bare stays true from the bare clone — worktrees work fine with it)
    run_git(["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=project_path)

    # Get the default branch
    result = run_git(["symbolic-ref", "--short", "HEAD"], cwd=project_path, quiet=True)
    default_branch = result.stdout.strip()

    # Detach HEAD so the default branch isn't "checked out" in the project root,
    # which would prevent git worktree add from using it.
    # Use update-ref --no-deref instead of checkout --detach to avoid touching the working tree.
    head_sha = run_git(["rev-parse", "HEAD"], cwd=project_path, quiet=True).stdout.strip()
    run_git(["update-ref", "--no-deref", "HEAD", head_sha], cwd=project_path, quiet=True)

    # Create the alpha worktree
    alpha_folder = get_worktree_folder_name(project_name, "alpha")
    alpha_path = project_path / alpha_folder
    run_git(["worktree", "add", str(alpha_path), default_branch], cwd=project_path)

    # Generate .env for the initial worktree
    write_env_file(alpha_path, {"WORKTREE": "alpha", "WORKTREE_NUM": "0"})

    # Create .mael marker file to identify this as a Maelstrom project
    (project_path / ".mael").touch()

    return project_path


def get_current_worktree_info(cwd: Path | None = None) -> tuple[Path, str]:
    """Get the project path and branch for the current working directory.

    Args:
        cwd: Current working directory (default: actual cwd).

    Returns:
        Tuple of (project_path, branch_name).

    Raises:
        RuntimeError: If not in a git worktree.
    """
    if cwd is None:
        cwd = Path.cwd()

    cwd = cwd.resolve()

    # Get the git toplevel for this worktree
    try:
        result = run_git(["rev-parse", "--show-toplevel"], cwd=cwd, quiet=True)
        worktree_root = Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        raise RuntimeError(f"Not in a git repository: {cwd}")

    # Get current branch
    try:
        result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, quiet=True)
        branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not determine current branch")

    # The project path is the parent of the worktree (where .git lives)
    # Check if this is a linked worktree by looking for .git file
    git_path = worktree_root / ".git"
    if git_path.is_file():
        # This is a linked worktree, project root is parent
        project_path = worktree_root.parent
    else:
        # This might be the main worktree or a bare-ish repo
        project_path = worktree_root

    return project_path, branch


def _build_env_file(
    project_path: Path, worktree_path: Path, worktree_name: str,
    *, reuse_ports: bool = False,
) -> None:
    """Build and write the .env file for a worktree.

    Shared logic for both initial worktree setup and env regeneration.

    Args:
        project_path: Path to the project root.
        worktree_path: Path to the worktree.
        worktree_name: NATO name of the worktree.
        reuse_ports: If True, reuse existing port allocation before allocating new.
    """
    config = load_config_or_default(worktree_path)

    # Read .env from project root as raw text if present (e.g., /Projects/myapp/.env)
    project_env_file = project_path / ".env"
    template_text = project_env_file.read_text() if project_env_file.exists() else None

    # Generate environment variables
    generated_vars = {
        "WORKTREE": worktree_name,
        "WORKTREE_NUM": str(WORKTREE_NAMES.index(worktree_name)),
    }

    # Add port variables if configured
    if config.port_names:
        port_base = None
        if reuse_ports:
            port_base = get_port_allocation(project_path, worktree_name)
        if port_base is None:
            port_base = allocate_port_base(project_path, len(config.port_names))
            record_port_allocation(project_path, worktree_name, port_base)
        generated_vars.update(generate_port_env_vars(port_base, config.port_names))

    # Add shared port variables if configured
    if config.shared_port_names:
        shared_base = get_port_allocation(project_path, "_shared")
        if shared_base is None:
            shared_base = allocate_port_base(project_path, len(config.shared_port_names))
            record_port_allocation(project_path, "_shared", shared_base)
        generated_vars["SHARED_PORT_BASE"] = str(shared_base)
        generated_vars.update(generate_port_env_vars(shared_base, config.shared_port_names))

    # Write .env if there's anything to write
    if template_text or generated_vars:
        write_env_file(worktree_path, generated_vars, template_text)


def _setup_claude_settings_symlink(worktree_path: Path) -> None:
    """Create a symlink from .claude/settings.local.json to settings.json.

    This ensures tool-use approvals saved to settings.local.json land in the
    tracked settings.json, making them available across worktrees.

    Args:
        worktree_path: Path to the worktree.
    """
    claude_dir = worktree_path / ".claude"
    settings_json = claude_dir / "settings.json"
    settings_local = claude_dir / "settings.local.json"

    # If .claude/settings.json doesn't exist, nothing to do
    if not settings_json.exists():
        return

    # If settings.local.json exists and is not a symlink, skip
    if settings_local.exists() and not settings_local.is_symlink():
        print(
            "Warning: .claude/settings.local.json already exists and is not a symlink, skipping"
        )
        return

    # Remove existing symlink (idempotent) and create new one
    if settings_local.is_symlink():
        settings_local.unlink()

    settings_local.symlink_to("settings.json")


def _finalize_worktree(project_path: Path, worktree_path: Path, worktree_name: str) -> Path:
    """Finalize worktree setup after git worktree add.

    Handles .env file creation and install command execution.

    Args:
        project_path: Path to the project root.
        worktree_path: Path to the worktree.
        worktree_name: NATO name of the worktree.

    Returns:
        Path to the worktree.
    """
    _build_env_file(project_path, worktree_path, worktree_name)
    _setup_claude_settings_symlink(worktree_path)
    return worktree_path


def regenerate_env_file(project_path: Path, worktree_path: Path, worktree_name: str) -> None:
    """Regenerate the .env file for a worktree, reusing the existing PORT_BASE.

    Used when .maelstrom.yaml has been updated (e.g., new port names added)
    and the .env file needs to reflect the current config.

    Args:
        project_path: Path to the project root.
        worktree_path: Path to the worktree.
        worktree_name: NATO name of the worktree.
    """
    _build_env_file(project_path, worktree_path, worktree_name, reuse_ports=True)


def reclaim_or_allocate_ports(project_path: Path, worktree_path: Path, worktree_name: str) -> None:
    """Reclaim existing port allocation for a recycled worktree, or allocate new ports.

    When a closed worktree is recycled, this function tries to reclaim the old
    PORT_BASE from its .env file. If those ports have been allocated to another
    worktree, it allocates new ports and regenerates the .env file.

    Args:
        project_path: Path to the project root.
        worktree_path: Path to the recycled worktree.
        worktree_name: NATO name of the worktree.
    """
    config = load_config_or_default(worktree_path)
    if not config.port_names:
        return

    # Read old PORT_BASE from the worktree's existing .env
    existing_env = read_env_file(worktree_path)
    old_port_base_str = existing_env.get("PORT_BASE")

    if old_port_base_str is not None:
        try:
            old_port_base = int(old_port_base_str)
        except ValueError:
            old_port_base = None
    else:
        old_port_base = None

    if old_port_base is not None:
        # Check if the old port_base is still available (not allocated to another worktree)
        allocations = load_port_allocations()
        allocated_bases = get_allocated_port_bases(allocations)
        if old_port_base not in allocated_bases:
            # Reclaim the old ports
            record_port_allocation(project_path, worktree_name, old_port_base)
            return

    # Old ports are taken or unavailable - allocate new ports and regenerate .env
    _finalize_worktree(project_path, worktree_path, worktree_name)


def run_install_cmd(worktree_path: Path) -> None:
    """Run the project's install command if configured."""
    config = load_config_or_default(worktree_path)
    if config.install_cmd:
        run_cmd(["sh", "-c", config.install_cmd], cwd=worktree_path, stream=True)


def create_worktree(project_path: Path, branch: str, *, detached: bool = False) -> Path:
    """Create a new worktree for the given branch.

    Args:
        project_path: Path to the project root (bare repo).
        branch: Branch name to create worktree for.
        detached: If True, create a detached HEAD worktree at origin/main
            instead of checking out the branch. Useful when the branch
            (e.g., main) is already checked out elsewhere.

    Returns:
        Path to the created worktree.

    Raises:
        RuntimeError: If worktree creation fails.
    """
    project_path = project_path.resolve()
    worktree_name = get_next_worktree_name(project_path)
    folder_name = get_worktree_folder_name(project_path.name, worktree_name)
    worktree_path = project_path / folder_name

    # Ensure fetch refspec is configured (for repos created before this was added)
    try:
        result = run_git(["config", "--get", "remote.origin.fetch"], cwd=project_path, quiet=True)
        if not result.stdout.strip():
            run_git(["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=project_path)
    except subprocess.CalledProcessError:
        # Config doesn't exist, add it
        run_git(["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=project_path)

    # Fetch latest from origin
    try:
        run_git(["fetch", "origin"], cwd=project_path)
        # Fast-forward local main to match origin/main
        update_local_main(project_path)
    except subprocess.CalledProcessError:
        # Fetch failed, but we can continue - might be offline or no remote
        pass

    # Handle detached mode - create worktree at origin/main without checking out a branch
    if detached:
        run_git(
            ["worktree", "add", "--detach", str(worktree_path), f"origin/{MAIN_BRANCH}"],
            cwd=project_path,
        )
        # Skip to post-creation setup
        return _finalize_worktree(project_path, worktree_path, worktree_name)

    # Check if branch exists locally
    try:
        run_git(["rev-parse", "--verify", branch], cwd=project_path, quiet=True)
        local_branch_exists = True
    except subprocess.CalledProcessError:
        local_branch_exists = False

    # Check if branch exists on remote
    remote_branch = f"origin/{branch}"
    try:
        run_git(["rev-parse", "--verify", remote_branch], cwd=project_path, quiet=True)
        remote_branch_exists = True
    except subprocess.CalledProcessError:
        remote_branch_exists = False

    # Create the worktree - prioritize remote to get latest code
    if remote_branch_exists:
        # Use -B to create/reset local branch to match remote
        run_git(
            ["worktree", "add", "-B", branch, str(worktree_path), remote_branch],
            cwd=project_path,
        )
    elif local_branch_exists:
        # Fall back to local branch if no remote
        run_git(["worktree", "add", str(worktree_path), branch], cwd=project_path)
    else:
        # Create new branch from origin's default branch (or HEAD if no remote)
        try:
            run_git(["rev-parse", "--verify", "origin/main"], cwd=project_path, quiet=True)
            base_ref = "origin/main"
        except subprocess.CalledProcessError:
            try:
                run_git(["rev-parse", "--verify", "origin/master"], cwd=project_path, quiet=True)
                base_ref = "origin/master"
            except subprocess.CalledProcessError:
                base_ref = "HEAD"
        run_git(["worktree", "add", "-b", branch, str(worktree_path), base_ref], cwd=project_path)

    return _finalize_worktree(project_path, worktree_path, worktree_name)


def read_env_file(worktree_path: Path) -> dict[str, str]:
    """Read existing .env file if present.

    Args:
        worktree_path: Path to the worktree.

    Returns:
        Dictionary of environment variables from the file.
    """
    env_file = worktree_path / ".env"
    if not env_file.exists():
        return {}

    env_vars = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        # Parse KEY=value
        if "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            # Strip trailing source comment (double-space + #) that isn't
            # inside quotes.
            if "  #" in value:
                # Check if the value starts with a quote
                if value and value[0] in ('"', "'"):
                    quote = value[0]
                    # Find the closing quote
                    close = value.find(quote, 1)
                    if close != -1:
                        # Only strip comments after the closing quote
                        rest = value[close + 1 :]
                        pos = rest.find("  #")
                        if pos != -1:
                            value = value[: close + 1 + pos]
                else:
                    pos = value.find("  #")
                    value = value[:pos]
            # Strip surrounding quotes
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            env_vars[key.strip()] = value
    return env_vars


_VAR_PATTERN = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_SOURCE_PATTERN = re.compile(r"  # source: \[(.+)\]$")


def _resolve_env_line(line: str, generated_vars: dict[str, str]) -> str:
    """Resolve variable references in a single .env line.

    If the line has a ``# source: [...]`` suffix, the bracketed text is used as
    the template instead of the visible value.  After substitution the source
    comment is (re-)appended so that future rewrites can recover the template.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return line

    # If a source comment already exists, use it as the template
    source_match = _SOURCE_PATTERN.search(line)
    if source_match:
        template = source_match.group(1)
    else:
        template = line

    def _replace(m: re.Match[str]) -> str:
        var = m.group(1) or m.group(2) or m.group(0)
        return generated_vars.get(var, m.group(0))

    resolved = _VAR_PATTERN.sub(_replace, template)

    if resolved != template:
        # Substitution occurred – attach/update source comment
        return f"{resolved}  # source: [{template}]"

    # No substitution – return unchanged (strip old source comment if template
    # had nothing to resolve any more)
    if source_match:
        return template
    return line


def _resolve_template_lines(text: str, generated_vars: dict[str, str]) -> str:
    """Apply variable resolution to every line in *text*."""
    lines = text.splitlines()
    resolved = [_resolve_env_line(line, generated_vars) for line in lines]
    return "\n".join(resolved)


def _build_managed_section(generated_vars: dict[str, str]) -> str:
    """Build the managed section text for a .env file.

    Args:
        generated_vars: Generated environment variables (e.g., ports).

    Returns:
        The managed section text including start/end markers.
    """
    lines = [ENV_SECTION_START]
    for key, value in sorted(generated_vars.items()):
        lines.append(f"{key}={value}")
    lines.append(ENV_SECTION_END)
    return "\n".join(lines)


def write_env_file(
    worktree_path: Path,
    generated_vars: dict[str, str],
    template_text: str | None = None,
) -> None:
    """Write environment variables to .env file in worktree.

    Generated variables are placed in a managed section at the top of the file,
    delimited by marker comments. Content outside the section is preserved when
    updating an existing file.

    Since the managed section appears first, dotenv readers will natively expand
    $VAR references in user content below.

    Args:
        worktree_path: Path to the worktree.
        generated_vars: Generated environment variables (e.g., ports).
        template_text: Raw text from project root .env, used only on first creation.
    """
    managed_section = _build_managed_section(generated_vars)
    env_file = worktree_path / ".env"

    if env_file.exists():
        existing_content = env_file.read_text()

        if ENV_SECTION_START in existing_content and ENV_SECTION_END in existing_content:
            # Replace the managed section, preserve everything else
            start_idx = existing_content.index(ENV_SECTION_START)
            end_idx = existing_content.index(ENV_SECTION_END) + len(ENV_SECTION_END)
            # Consume the newline after end marker if present
            if end_idx < len(existing_content) and existing_content[end_idx] == "\n":
                end_idx += 1
            user_content = _resolve_template_lines(
                existing_content[end_idx:], generated_vars
            )
            new_content = (
                existing_content[:start_idx]
                + managed_section + "\n"
                + user_content
            )
        else:
            # Upgrade path: no markers found, prepend managed section
            # Strip keys from existing content that are now in the managed section
            filtered_lines = []
            for line in existing_content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in generated_vars:
                        continue
                filtered_lines.append(line)
            remaining = "\n".join(filtered_lines).strip()
            if remaining:
                remaining = _resolve_template_lines(remaining, generated_vars)
                new_content = managed_section + "\n\n" + remaining + "\n"
            else:
                new_content = managed_section + "\n"

        env_file.write_text(new_content)
    else:
        # First-time creation
        parts = [managed_section]
        if template_text:
            parts.append("")  # blank line separator
            parts.append(
                _resolve_template_lines(template_text.rstrip("\n"), generated_vars)
            )
        env_file.write_text("\n".join(parts) + "\n")


def find_worktree_by_branch(project_path: Path, branch: str) -> Path | None:
    """Find a worktree by its branch name.

    Args:
        project_path: Path to the project root.
        branch: Branch name to search for.

    Returns:
        Path to the worktree directory, or None if not found.
    """
    for wt in list_worktrees(project_path):
        if wt.branch == branch:
            return wt.path
    return None


def remove_worktree(project_path: Path, branch: str) -> None:
    """Remove a worktree by branch name.

    Args:
        project_path: Path to the project root.
        branch: Branch name of the worktree to remove.

    Raises:
        RuntimeError: If removal fails.
    """
    project_path = project_path.resolve()
    worktree_path = find_worktree_by_branch(project_path, branch)

    if worktree_path is None:
        raise RuntimeError(f"No worktree found for branch: {branch}")

    # Extract NATO name before removal for port deallocation
    project_name = project_path.name
    nato_name = extract_worktree_name_from_folder(project_name, worktree_path.name)

    # Remove the worktree using git (--force needed for maelstrom-managed files like .env)
    run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=project_path)

    # Free the port allocation
    if nato_name:
        remove_port_allocation(project_path, nato_name)


def remove_worktree_by_path(project_path: Path, worktree_name: str) -> None:
    """Remove a worktree by its directory name.

    Args:
        project_path: Path to the project root.
        worktree_name: Directory name of the worktree (already sanitized).

    Raises:
        RuntimeError: If worktree does not exist or removal fails.
    """
    project_path = project_path.resolve()
    worktree_path = project_path / worktree_name

    if not worktree_path.exists():
        raise RuntimeError(f"Worktree does not exist: {worktree_path}")

    # Extract NATO name before removal for port deallocation
    project_name = project_path.name
    nato_name = extract_worktree_name_from_folder(project_name, worktree_name)

    # Remove the worktree using git (--force needed for maelstrom-managed files like .env)
    run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=project_path)

    # Free the port allocation
    if nato_name:
        remove_port_allocation(project_path, nato_name)


def open_worktree(worktree_path: Path, command: str) -> None:
    """Open a worktree using the configured command.

    Args:
        worktree_path: Path to the worktree directory.
        command: Command to run (e.g., "code", "cursor").

    Raises:
        RuntimeError: If the command fails to execute.
    """
    try:
        subprocess.run([command, str(worktree_path)], check=True)
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {command}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to open worktree: {e}")


def start_claude_session(
    worktree_path: Path,
    project: str | None = None,
    worktree: str | None = None,
) -> None:
    """Start an interactive Claude Code CLI session in a worktree.

    When running inside cmux and project/worktree are provided, creates a
    cmux workspace instead of replacing the current process. Falls back to
    os.execvp if cmux setup fails or is unavailable.
    """
    from .cmux import create_cmux_workspace, is_cmux_mode

    if is_cmux_mode() and project and worktree:
        result = create_cmux_workspace(project, worktree, str(worktree_path))
        if result is not None:
            return

    os.chdir(worktree_path)
    os.execvp("claude", ["claude"])


CLAUDE_LOCAL_IMPORT = "@.claude/CLAUDE.local.md"


def _ensure_claude_md_import(worktree_path: Path) -> None:
    """Ensure CLAUDE.md has an @.claude/CLAUDE.local.md import on its first line.

    If CLAUDE.md doesn't exist, does nothing (the import only makes sense
    when there's already a CLAUDE.md to add it to).
    """
    claude_md = worktree_path / "CLAUDE.md"
    if not claude_md.exists():
        return

    content = claude_md.read_text()
    if CLAUDE_LOCAL_IMPORT in content:
        return

    claude_md.write_text(CLAUDE_LOCAL_IMPORT + "\n\n" + content)


def _ensure_gitignore_entry(worktree_path: Path, entry: str) -> None:
    """Ensure .gitignore contains the given entry.

    Appends the entry if it's not already present. Creates .gitignore if needed.
    """
    gitignore = worktree_path / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if entry in content.splitlines():
            return
        # Ensure trailing newline before appending
        if content and not content.endswith("\n"):
            content += "\n"
        gitignore.write_text(content + entry + "\n")
    else:
        gitignore.write_text(entry + "\n")


def update_claude_local_md(
    project_path: Path, worktree_path: Path, worktree_name: str
) -> bool:
    """Generate .claude/CLAUDE.local.md with maelstrom workflow instructions.

    Creates (or overwrites) a gitignored .claude/CLAUDE.local.md file containing
    the maelstrom workflow header and environment information (worktree path,
    app URL if applicable).

    Args:
        project_path: Path to the project root (bare repo).
        worktree_path: Path to the worktree directory.
        worktree_name: NATO phonetic name of the worktree (e.g., "alpha").

    Returns:
        True if the file was written, False if the header template is missing.
    """
    from .ports import get_app_url

    # Get the header template content
    try:
        shared_dir = get_shared_dir()
        header_file = shared_dir / "claude-header.md"
        if not header_file.exists():
            return False
        header_content = header_file.read_text().rstrip()
    except FileNotFoundError:
        return False

    # Build environment section
    env_lines = [
        "",
        "## Environment",
        "",
        f"The current working directory is {worktree_path}",
    ]

    app_url_result = get_app_url(project_path, worktree_name)
    if app_url_result is not None:
        url, _ = app_url_result
        env_lines.append("")
        env_lines.append(f"The app URL is {url}")

    content = header_content + "\n" + "\n".join(env_lines) + "\n"

    # Write .claude/CLAUDE.local.md
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    local_md_path = claude_dir / "CLAUDE.local.md"
    local_md_path.write_text(content)

    # Ensure CLAUDE.md imports the local file
    _ensure_claude_md_import(worktree_path)

    # Ensure .gitignore excludes the generated file
    _ensure_gitignore_entry(worktree_path, ".claude/CLAUDE.local.md")

    return True


def list_local_branches(project_path: Path) -> list[str]:
    """List all local branches in the repository.

    Args:
        project_path: Path to the project root.

    Returns:
        List of branch names.
    """
    result = run_cmd(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]


def branch_exists_on_remote(project_path: Path, branch: str) -> bool:
    """Check if a branch exists on the remote.

    Args:
        project_path: Path to the project root.
        branch: Branch name to check.

    Returns:
        True if branch exists on origin.
    """
    result = run_cmd(
        ["git", "rev-parse", "--verify", f"origin/{branch}"],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    return result.returncode == 0


def is_branch_merged(project_path: Path, branch: str, base: str = f"origin/{MAIN_BRANCH}") -> bool:
    """Check if a branch is at the same commit as the base (fully merged).

    Args:
        project_path: Path to the project root.
        branch: Branch name to check.
        base: Base ref to compare against.

    Returns:
        True if branch points to the same commit as base.
    """
    branch_result = run_cmd(
        ["git", "rev-parse", branch],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    base_result = run_cmd(
        ["git", "rev-parse", base],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    if branch_result.returncode != 0 or base_result.returncode != 0:
        return False
    return branch_result.stdout.strip() == base_result.stdout.strip()


def delete_branch(project_path: Path, branch: str, delete_remote: bool = False) -> tuple[bool, bool]:
    """Delete a local branch and optionally its remote counterpart.

    Args:
        project_path: Path to the project root.
        branch: Branch name to delete.
        delete_remote: If True, also delete origin/<branch>.

    Returns:
        Tuple of (local_deleted, remote_deleted).
    """
    local_deleted = False
    remote_deleted = False

    # Delete local branch
    result = run_cmd(
        ["git", "branch", "-D", branch],
        cwd=project_path,
        quiet=True,
        check=False,
    )
    local_deleted = result.returncode == 0

    # Delete remote branch if requested
    if delete_remote:
        result = run_cmd(
            ["git", "push", "origin", "--delete", branch],
            cwd=project_path,
            quiet=True,
            check=False,
        )
        remote_deleted = result.returncode == 0

    return local_deleted, remote_deleted


def tidy_branch(
    project_path: Path,
    branch: str,
    temp_worktree_path: Path,
    checked_out_branches: set[str],
) -> TidyBranchResult:
    """Tidy a single branch: rebase, then delete if merged or push if not.

    Args:
        project_path: Path to the project root.
        branch: Branch name to tidy.
        temp_worktree_path: Path to temporary worktree for operations.
        checked_out_branches: Set of branches currently checked out in worktrees.

    Returns:
        TidyBranchResult with outcome.
    """
    # Skip if checked out somewhere
    if branch in checked_out_branches:
        return TidyBranchResult(
            branch=branch,
            action="skipped_checked_out",
            success=True,
            message=f"Branch '{branch}' is checked out in a worktree",
        )

    # Check if remote branch exists (before we start modifying things)
    has_remote = branch_exists_on_remote(project_path, branch)

    # Checkout the branch in temp worktree
    checkout_result = run_cmd(
        ["git", "checkout", branch],
        cwd=temp_worktree_path,
        quiet=True,
        check=False,
    )
    if checkout_result.returncode != 0:
        return TidyBranchResult(
            branch=branch,
            action="skipped_error",
            success=False,
            message=f"Failed to checkout branch: {checkout_result.stderr}",
        )

    # If remote exists, reset to match remote (pull in any changes)
    if has_remote:
        run_cmd(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=temp_worktree_path,
            quiet=True,
            check=False,
        )

    # Attempt rebase against origin/main
    rebase_result = run_cmd(
        ["git", "rebase", f"origin/{MAIN_BRANCH}"],
        cwd=temp_worktree_path,
        quiet=True,
        check=False,
    )

    if rebase_result.returncode != 0:
        # Rebase failed - abort and skip
        run_cmd(["git", "rebase", "--abort"], cwd=temp_worktree_path, quiet=True, check=False)
        return TidyBranchResult(
            branch=branch,
            action="skipped_conflicts",
            success=True,  # Not a failure, just conflicts
            message=f"Branch '{branch}' has conflicts with origin/{MAIN_BRANCH}",
        )

    # Rebase succeeded - check if now merged (same as origin/main)
    if is_branch_merged(temp_worktree_path, "HEAD", f"origin/{MAIN_BRANCH}"):
        # Branch is fully merged - delete it
        # First checkout detached to allow deleting the branch
        run_cmd(
            ["git", "checkout", "--detach", f"origin/{MAIN_BRANCH}"],
            cwd=temp_worktree_path,
            quiet=True,
            check=False,
        )
        local_deleted, remote_deleted = delete_branch(project_path, branch, delete_remote=has_remote)
        return TidyBranchResult(
            branch=branch,
            action="deleted",
            success=True,
            message=f"Deleted merged branch '{branch}'",
            deleted_local=local_deleted,
            deleted_remote=remote_deleted,
        )

    # Branch has unmerged work - push if it has a remote
    if has_remote:
        push_result = run_cmd(
            ["git", "push", "--force-with-lease", "origin", branch],
            cwd=temp_worktree_path,
            quiet=True,
            check=False,
        )
        if push_result.returncode == 0:
            return TidyBranchResult(
                branch=branch,
                action="pushed",
                success=True,
                message=f"Rebased and pushed '{branch}'",
            )
        else:
            return TidyBranchResult(
                branch=branch,
                action="skipped_error",
                success=False,
                message=f"Failed to push '{branch}': {push_result.stderr}",
            )

    # Local-only branch with unmerged work - leave it rebased
    return TidyBranchResult(
        branch=branch,
        action="rebased",
        success=True,
        message=f"Rebased local branch '{branch}' (no remote)",
    )


def tidy_branches(project_path: Path) -> list[TidyBranchResult]:
    """Tidy all feature branches in a project.

    For each non-main branch:
    1. Skip if checked out in a worktree
    2. Pull remote changes if branch exists on origin
    3. Rebase against origin/main
    4. If conflicts, abort and skip
    5. If merged (at same commit as main), delete local and remote
    6. If not merged with remote, force push
    7. If not merged local-only, leave rebased

    Args:
        project_path: Path to the project root.

    Returns:
        List of TidyBranchResult for each processed branch.
    """
    project_path = project_path.resolve()
    results: list[TidyBranchResult] = []

    # Fetch latest from origin with prune to remove stale remote refs
    try:
        run_git(["fetch", "origin", "--prune"], cwd=project_path)
        # Fast-forward local main to match origin/main
        update_local_main(project_path)
    except subprocess.CalledProcessError:
        pass  # Continue even if fetch fails

    # Get all local branches
    branches = list_local_branches(project_path)
    feature_branches = [b for b in branches if b != MAIN_BRANCH]

    if not feature_branches:
        return results

    # Get branches currently checked out in worktrees
    worktrees = list_worktrees(project_path)
    checked_out_branches = {wt.branch for wt in worktrees if wt.branch}

    # Create temporary worktree for operations
    temp_name = "_tidy_temp"
    temp_worktree_path = project_path / temp_name

    try:
        # Create detached worktree at origin/main
        run_git(
            ["worktree", "add", "--detach", str(temp_worktree_path), f"origin/{MAIN_BRANCH}"],
            cwd=project_path,
        )

        # Process each branch
        for branch in feature_branches:
            result = tidy_branch(project_path, branch, temp_worktree_path, checked_out_branches)
            results.append(result)

            # Return to detached state before next branch
            run_cmd(
                ["git", "checkout", "--detach", f"origin/{MAIN_BRANCH}"],
                cwd=temp_worktree_path,
                quiet=True,
                check=False,
            )
    finally:
        # Clean up temporary worktree
        run_cmd(
            ["git", "worktree", "remove", "--force", str(temp_worktree_path)],
            cwd=project_path,
            quiet=True,
            check=False,
        )

    return results
