"""Worktree management for maelstrom projects."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .claude_integration import get_shared_dir
from .config import load_config_or_default
from .ports import allocate_port_base, generate_port_env_vars

# Fixed worktree names (NATO phonetic alphabet)
WORKTREE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
]

# Files managed by maelstrom that should be ignored when checking for dirty files
MAELSTROM_MANAGED_FILES = {".env"}

# Main branch name (hardcoded - no master support)
MAIN_BRANCH = "main"

# Markers for maelstrom workflow section in CLAUDE.md
# Support both old and new style start markers for detection
CLAUDE_HEADER_STARTS = ("# Maelstrom Workflow", "# Maelstrom-based workflow")
CLAUDE_HEADER_END = "(maelstrom instructions end)"


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


def get_worktree_dirty_files(worktree_path: Path) -> list[str]:
    """Get modified/untracked files in worktree, excluding maelstrom-managed files.

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        List of file paths that are modified or untracked (excluding maelstrom-managed files).
    """
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
    """Check if a worktree is in 'closed' state (detached at origin/main, clean).

    A closed worktree is available for recycling when creating a new worktree.
    A worktree is considered closed if:
    - It is in detached HEAD state (no branch checked out)
    - It has no dirty files
    - It has no commits ahead of origin/main
    - Its HEAD points to the same commit as origin/main

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

    # Check if HEAD matches origin/main
    try:
        head_result = run_cmd(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_info.path,
            quiet=True,
            check=False,
        )
        main_result = run_cmd(
            ["git", "rev-parse", f"origin/{MAIN_BRANCH}"],
            cwd=worktree_info.path,
            quiet=True,
            check=False,
        )
        if head_result.returncode != 0 or main_result.returncode != 0:
            return False
        if head_result.stdout.strip() != main_result.stdout.strip():
            return False
    except Exception:
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
            # Create new branch from current position (main)
            run_git(["checkout", "-b", branch], cwd=worktree_path)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to switch to branch {branch}: {e.stderr}")

    # Update CLAUDE.md if needed
    update_claude_md(worktree_path)

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

    return worktrees


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

    # Configure the bare repo to work with worktrees
    # Set core.bare to false so git commands work in worktrees
    run_git(["config", "core.bare", "false"], cwd=project_path)

    # Set up fetch refspec to create origin/* remote tracking refs
    run_git(["config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=project_path)

    # Get the default branch
    result = run_git(["symbolic-ref", "--short", "HEAD"], cwd=project_path, quiet=True)
    default_branch = result.stdout.strip()

    # Create the alpha worktree
    alpha_folder = get_worktree_folder_name(project_name, "alpha")
    alpha_path = project_path / alpha_folder
    run_git(["worktree", "add", str(alpha_path), default_branch], cwd=project_path)

    # Generate .env for the initial worktree
    write_env_file(alpha_path, {"WORKTREE": "alpha"})

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
    # Load config and handle .env file
    config = load_config_or_default(worktree_path)

    # Read .env from project root if present (e.g., /Projects/myapp/.env)
    existing_env = read_env_file(project_path)

    # Generate environment variables
    generated_vars = {"WORKTREE": worktree_name}

    # Add port variables if configured
    if config.port_names:
        port_base = allocate_port_base(project_path, len(config.port_names))
        generated_vars.update(generate_port_env_vars(port_base, config.port_names))

    # Write .env if there's anything to write
    if existing_env or generated_vars:
        write_env_file(worktree_path, generated_vars, existing_env)

    # Run install command if configured
    if config.install_cmd:
        run_cmd(["sh", "-c", config.install_cmd], cwd=worktree_path, stream=True)

    return worktree_path


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


def substitute_env_vars(value: str, env_vars: dict[str, str]) -> str:
    """Substitute $VAR and ${VAR} references in a value.

    Args:
        value: The string containing variable references.
        env_vars: Dictionary of environment variables to substitute.

    Returns:
        The value with all known variables substituted.
    """
    def replacer(match: re.Match) -> str:
        # Group 1 is ${VAR}, group 2 is $VAR
        var_name = match.group(1) or match.group(2)
        return env_vars.get(var_name, match.group(0))

    # Match ${VAR} or $VAR (where VAR is alphanumeric + underscore)
    pattern = r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
    return re.sub(pattern, replacer, value)


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
            env_vars[key.strip()] = value.strip()
    return env_vars


def write_env_file(
    worktree_path: Path,
    generated_vars: dict[str, str],
    existing_vars: dict[str, str] | None = None,
) -> None:
    """Write environment variables to .env file in worktree.

    Merges existing variables with generated ones, substituting variable
    references like $VAR and ${VAR} in existing values.

    Args:
        worktree_path: Path to the worktree.
        generated_vars: Generated environment variables (e.g., ports).
        existing_vars: Existing variables from user's .env file.
    """
    # Start with existing vars, substitute references, then merge generated
    merged = {}

    if existing_vars:
        for key, value in existing_vars.items():
            # Substitute variable references using generated vars
            merged[key] = substitute_env_vars(value, generated_vars)

    # Generated vars override existing (in case of conflicts)
    merged.update(generated_vars)

    env_file = worktree_path / ".env"
    lines = [f"{key}={value}" for key, value in sorted(merged.items())]
    env_file.write_text("\n".join(lines) + "\n")


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

    # Remove the worktree using git (--force needed for maelstrom-managed files like .env)
    run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=project_path)


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

    # Remove the worktree using git (--force needed for maelstrom-managed files like .env)
    run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=project_path)


def open_worktree(worktree_path: Path, command: str, open_chat: bool = True) -> None:
    """Open a worktree using the configured command.

    Args:
        worktree_path: Path to the worktree directory.
        command: Command to run (e.g., "code", "cursor").
        open_chat: If True, also open Claude Code chat panel (VS Code only).

    Raises:
        RuntimeError: If the command fails to execute.
    """
    try:
        subprocess.run([command, str(worktree_path)], check=True)
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {command}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to open worktree: {e}")

    # Open Claude Code chat if requested
    if open_chat:
        try:
            open_claude_code(command)
        except RuntimeError as e:
            import sys

            print(f"Warning: Could not open Claude Code: {e}", file=sys.stderr)


def open_claude_code(command: str) -> None:
    """Open Claude Code panel in VS Code.

    Uses the claude-vscode extension commands to open and focus the chat panel.
    Only works with VS Code (code, code-insiders). Silently skips for other editors.

    Args:
        command: The editor command (e.g., "code", "cursor").

    Raises:
        RuntimeError: If the VS Code command fails to execute.
    """
    # Only VS Code supports the claude-vscode extension
    if command not in ("code", "code-insiders"):
        return

    try:
        # Open the Claude Code editor panel
        subprocess.run([command, "--command", "claude-vscode.editor.open"], check=True)
        # Focus the input field
        subprocess.run([command, "--command", "claude-vscode.focus"], check=True)
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {command}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to open Claude Code: {e}")


def _find_claude_header_start(content: str) -> tuple[str, int] | None:
    """Find the first occurrence of any valid header start marker.

    Returns tuple of (marker, index) or None if not found.
    """
    for marker in CLAUDE_HEADER_STARTS:
        idx = content.find(marker)
        if idx != -1:
            return (marker, idx)
    return None


def _validate_claude_snippet(content: str) -> None:
    """Validate that the snippet has expected start and end markers.

    Raises ValueError if the snippet doesn't start with one of the valid
    start markers or doesn't end with the expected end marker.
    """
    trimmed = content.strip()

    # Check start marker - must start with one of the valid markers
    starts_valid = any(trimmed.startswith(marker) for marker in CLAUDE_HEADER_STARTS)
    if not starts_valid:
        raise ValueError(
            f"Snippet must start with one of: {CLAUDE_HEADER_STARTS}, "
            f"but starts with: {trimmed[:50]!r}"
        )

    # Check end marker
    if not trimmed.endswith(CLAUDE_HEADER_END):
        raise ValueError(
            f"Snippet must end with {CLAUDE_HEADER_END!r}, "
            f"but ends with: {trimmed[-50:]!r}"
        )


def update_claude_md(worktree_path: Path) -> bool:
    """Update CLAUDE.md with maelstrom workflow instructions.

    Checks the worktree's CLAUDE.md file and ensures it contains the
    maelstrom workflow section. Handles three cases:
    - File doesn't exist: creates new file with header content
    - File exists without markers: appends header content
    - File exists with markers: replaces content between markers if outdated

    Args:
        worktree_path: Path to the worktree directory.

    Returns:
        True if the file was created or modified, False if already up to date.

    Raises:
        ValueError: If the header template doesn't have valid markers.
    """
    # Get the header template content
    try:
        shared_dir = get_shared_dir()
        header_file = shared_dir / "claude-header.md"
        if not header_file.exists():
            return False
        header_content = header_file.read_text()
    except FileNotFoundError:
        return False

    # Validate the snippet has correct markers
    _validate_claude_snippet(header_content)

    claude_md_path = worktree_path / "CLAUDE.md"

    # Case 1: File doesn't exist - create it with only the header
    if not claude_md_path.exists():
        claude_md_path.write_text(header_content)
        return True

    existing_content = claude_md_path.read_text()

    # Check if markers are present
    start_match = _find_claude_header_start(existing_content)
    has_start = start_match is not None
    has_end = CLAUDE_HEADER_END in existing_content

    # Case 2: No markers - append to end
    if not has_start and not has_end:
        new_content = existing_content.rstrip() + "\n\n" + header_content
        claude_md_path.write_text(new_content)
        return True

    # Case 3: Markers present - check if up to date and replace if needed
    if has_start and has_end:
        # Extract the existing section
        _, start_idx = start_match
        end_idx = existing_content.find(CLAUDE_HEADER_END) + len(CLAUDE_HEADER_END)

        existing_section = existing_content[start_idx:end_idx]
        new_section = header_content.strip()

        # Check if content is already up to date
        if existing_section.strip() == new_section:
            return False

        # Replace the section
        new_content = existing_content[:start_idx] + new_section + existing_content[end_idx:]
        claude_md_path.write_text(new_content)
        return True

    # Partial markers (unusual case) - append to be safe
    if has_start != has_end:
        new_content = existing_content.rstrip() + "\n\n" + header_content
        claude_md_path.write_text(new_content)
        return True

    return False


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
