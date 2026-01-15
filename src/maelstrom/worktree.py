"""Worktree management for maelstrom projects."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import load_config_or_default
from .ports import allocate_port_base, generate_port_env_vars

# Fixed worktree names (NATO phonetic alphabet)
WORKTREE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
]


@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    commit: str


def sanitize_branch_name(branch: str) -> str:
    """Convert branch name to directory-safe name (slashes → dashes)."""
    return branch.replace("/", "-")


def run_cmd(cmd: list[str], cwd: Path | None = None, quiet: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    if not quiet:
        print(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def run_git(args: list[str], cwd: Path | None = None, quiet: bool = False) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    return run_cmd(["git"] + args, cwd=cwd, quiet=quiet, check=True)


def is_bare_repo(project_path: Path) -> bool:
    """Check if the repository is a bare repository.

    A bare repo has no worktree at the root level. We check this by:
    1. Looking for .git directory (indicates non-bare with worktree)
    2. Or checking git config core.bare

    Args:
        project_path: Path to the project root.

    Returns:
        True if the repo is bare (no root worktree).
    """
    git_dir = project_path / ".git"

    # If .git is a file, it's a worktree pointer, not a bare repo
    if git_dir.is_file():
        return False

    # If .git is a directory, check if there are tracked files in root
    if git_dir.is_dir():
        try:
            result = run_git(["ls-files"], cwd=project_path, quiet=True)
            # If there are files listed, this is a non-bare repo with root worktree
            return result.stdout.strip() == ""
        except subprocess.CalledProcessError:
            return False

    # No .git at all - not a git repo
    return False


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


def get_next_worktree_name(project_path: Path) -> str:
    """Get the first unused worktree name from the fixed list.

    Args:
        project_path: Path to the project root.

    Returns:
        The first available worktree name from WORKTREE_NAMES.

    Raises:
        RuntimeError: If all 26 worktree names are in use.
    """
    existing = {wt.path.name for wt in list_worktrees(project_path)}
    for name in WORKTREE_NAMES:
        if name not in existing:
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
    alpha_path = project_path / "alpha"
    run_git(["worktree", "add", str(alpha_path), default_branch], cwd=project_path)

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


def create_pr(cwd: Path | None = None, draft: bool = False) -> tuple[str, bool]:
    """Create a pull request for the current worktree branch, or push if PR exists.

    Args:
        cwd: Current working directory (default: actual cwd).
        draft: Create as draft PR (only if creating new PR).

    Returns:
        Tuple of (PR URL, created) where created is True if new PR was created.

    Raises:
        RuntimeError: If PR creation or push fails.
    """
    if cwd is None:
        cwd = Path.cwd()

    # Check if PR already exists
    pr_exists = False
    existing_url = ""
    try:
        result = run_cmd(
            ["gh", "pr", "view", "--json", "url", "-q", ".url"],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            pr_exists = True
            existing_url = result.stdout.strip()
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")

    # Push the branch
    try:
        result = run_cmd(
            ["git", "push", "-u", "origin", "HEAD"],
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to push branch: {result.stderr}")
        # Print push output for visibility
        if result.stderr:
            print(result.stderr.strip())
    except FileNotFoundError:
        raise RuntimeError("git is not installed")

    # If PR exists, just return the URL
    if pr_exists:
        return existing_url, False

    # Get current branch name for --head flag
    branch_result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, quiet=True)
    branch_name = branch_result.stdout.strip()

    # Try to get the first commit message for title
    try:
        log_result = run_git(["log", "-1", "--format=%s"], cwd=cwd, quiet=True)
        title = log_result.stdout.strip()
    except subprocess.CalledProcessError:
        title = branch_name

    # Create the PR with explicit title (--fill can fail if base branch not fetched)
    cmd = ["gh", "pr", "create", "--title", title, "--body", "", "--head", branch_name]
    if draft:
        cmd.append("--draft")

    result = run_cmd(cmd, cwd=cwd, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create PR: {result.stderr}")

    return result.stdout.strip(), True


def create_worktree(project_path: Path, branch: str) -> Path:
    """Create a new worktree for the given branch.

    Args:
        project_path: Path to the project root (bare repo).
        branch: Branch name to create worktree for.

    Returns:
        Path to the created worktree.

    Raises:
        RuntimeError: If worktree creation fails.
    """
    project_path = project_path.resolve()
    worktree_name = get_next_worktree_name(project_path)
    worktree_path = project_path / worktree_name

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

    # Load config and handle .env file
    config = load_config_or_default(worktree_path)

    # Read .env from project root if present (e.g., /Projects/myapp/.env)
    existing_env = read_env_file(project_path)

    # Generate port variables if configured
    if config.port_names:
        port_base = allocate_port_base(project_path, len(config.port_names))
        generated_vars = generate_port_env_vars(port_base, config.port_names)
    else:
        generated_vars = {}

    # Write .env if there's anything to write
    if existing_env or generated_vars:
        write_env_file(worktree_path, generated_vars, existing_env)

    # Run install command if configured
    if config.install_cmd:
        run_cmd(["sh", "-c", config.install_cmd], cwd=worktree_path)

    return worktree_path


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

    # Remove the worktree using git
    run_git(["worktree", "remove", str(worktree_path)], cwd=project_path)


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

    # Remove the worktree using git
    run_git(["worktree", "remove", str(worktree_path)], cwd=project_path)
