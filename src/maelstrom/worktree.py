"""Worktree management for maelstrom projects."""

import fcntl
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .claude_integration import get_shared_dir
from .config import load_config_or_default
from .shell import run_cmd
from .ports import (
    allocate_port_base,
    generate_port_env_vars,
    get_allocated_port_bases,
    get_port_allocation,
    load_port_allocations,
    record_port_allocation,
    remove_port_allocation,
)
from .worktree_model import (
    ENV_SECTION_END,
    ENV_SECTION_START,
    MAELSTROM_MANAGED_FILES,
    MAIN_BRANCH,
    WORKTREE_NAMES,
    CopyBackResult,
    EnvConflict,
    _build_managed_section,
    _format_copy_back_block,
    _resolve_template_lines,
    _sanitise_path_for_claude,
    _substitute_vars,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    parse_env_text,
)

@dataclass
class WorktreeInfo:
    """Information about a git worktree."""

    path: Path
    branch: str
    commit: str
    is_dirty: bool = False
    commits_ahead: int = 0


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
    If main is checked out in a worktree, runs ``git merge --ff-only`` in
    that worktree to update both the ref and the working tree.  If local
    main is ahead of origin/main, returns a warning.

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

    # If main is checked out in a worktree, fast-forward via merge there
    worktrees = list_worktrees(project_path)
    for wt in worktrees:
        if wt.branch == MAIN_BRANCH:
            try:
                run_git(["merge", "--ff-only", f"origin/{MAIN_BRANCH}"], cwd=wt.path)
                return UpdateMainResult(
                    "updated",
                    f"Fast-forwarded {MAIN_BRANCH} in worktree {wt.path.name}",
                )
            except subprocess.CalledProcessError:
                return UpdateMainResult(
                    "warning",
                    f"Could not fast-forward {MAIN_BRANCH} in worktree {wt.path.name}",
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
    aborted: bool = False  # rebase aborted on conflict (--abort)
    closed: bool = False  # branch was empty: deleted + worktree closed (--close)
    deleted_remote: bool = False  # remote branch also deleted


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


def squash_worktree(
    worktree_path: Path,
    skip_fetch: bool = False,
    squash: bool = True,
    abort_on_conflict: bool = False,
) -> SyncResult:
    """Fetch and rebase a worktree onto origin/main, optionally autosquashing
    ``fixup!`` commits.

    Does NOT push — callers that need to publish the rebased branch call
    :func:`sync_worktree`, which builds on this function.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step (useful when syncing multiple
            worktrees that share the same repo, where fetch was already done).
        squash: If True, autosquash ``fixup!`` commits into their targets while
            rebasing (``git rebase --autosquash``).
        abort_on_conflict: If True, on a rebase conflict run ``git rebase --abort``
            to restore the worktree to its pre-rebase state instead of leaving the
            rebase in progress.

    Returns:
        SyncResult with status and message. On success ``pushed``/``push_message``
        are left at their defaults — this function never pushes.
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

    # Rebase with autostash (optionally autosquashing fixup! commits)
    rebase_cmd = ["git", "rebase", "--autostash", "origin/main"]
    rebase_env: dict | None = None
    if squash:
        rebase_cmd.insert(2, "--autosquash")
        # --autosquash triggers an interactive rebase; run it non-interactively
        # by stubbing out the sequence editor (and the commit editor as a safety
        # net) so the generated todo list is applied verbatim.
        rebase_env = {"GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}

    result = run_cmd(
        rebase_cmd,
        cwd=worktree_path,
        check=False,
        env=rebase_env,
    )

    if result.returncode != 0:
        # Rebase failed - likely conflicts
        if abort_on_conflict:
            run_cmd(
                ["git", "rebase", "--abort"],
                cwd=worktree_path,
                quiet=True,
                check=False,
            )
            return SyncResult(
                success=False,
                branch=branch,
                message=(
                    f"Rebase of {branch} onto origin/main hit conflicts; "
                    "aborted and restored worktree to its previous state."
                ),
                had_conflicts=True,
                aborted=True,
                merge_base=merge_base,
                upstream_head=upstream_head,
            )
        return SyncResult(
            success=False,
            branch=branch,
            message=result.stderr or result.stdout,
            had_conflicts=True,
            merge_base=merge_base,
            upstream_head=upstream_head,
        )

    return SyncResult(
        success=True,
        branch=branch,
        message=f"Successfully rebased {branch} onto origin/main",
    )


def sync_worktree(
    worktree_path: Path,
    skip_fetch: bool = False,
    squash: bool = False,
    abort_on_conflict: bool = False,
    close_if_empty: bool = False,
) -> SyncResult:
    """Sync a worktree by rebasing against origin/main, then pushing.

    Builds on :func:`squash_worktree` (the fetch + rebase primitive) and adds the
    force-with-lease push of the rebased branch.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step (useful when syncing multiple
            worktrees that share the same repo, where fetch was already done).
        squash: If True, autosquash ``fixup!`` commits into their targets while
            rebasing (``git rebase --autosquash``).
        abort_on_conflict: If True, abort the rebase on conflict and restore the
            worktree (passed through to :func:`squash_worktree`).
        close_if_empty: If True and the branch is empty after a successful rebase
            (HEAD == origin/main), delete the branch (local + remote) and close the
            worktree instead of pushing.

    Returns:
        SyncResult with status and message.
    """
    result = squash_worktree(
        worktree_path,
        skip_fetch=skip_fetch,
        squash=squash,
        abort_on_conflict=abort_on_conflict,
    )
    if not result.success:
        return result  # conflicts / fetch failure already populated

    worktree_path = worktree_path.resolve()
    branch = result.branch

    # If the branch is now empty (fully merged), close it out before any push so a
    # local-only empty branch is never pushed to origin just to be deleted.
    if close_if_empty and is_branch_merged(worktree_path, branch, base=f"origin/{MAIN_BRANCH}"):
        project_path = worktree_path.parent
        delete_remote = branch_exists_on_remote(project_path, branch)  # compute before detach
        detach_result = _detach_and_free_ports(worktree_path)  # frees the branch + ports first
        if not detach_result.success:
            return SyncResult(success=False, branch=branch, message=detach_result.message)
        # delete_branch uses check=False and never raises; an orphaned branch left
        # behind by a failed delete must be reported, not silently claimed as deleted.
        local_deleted, remote_deleted = delete_branch(project_path, branch, delete_remote=delete_remote)
        if not local_deleted:
            return SyncResult(
                success=False,
                branch=branch,
                message=(
                    f"{branch} is empty (merged into origin/main) and the worktree was closed, "
                    f"but deleting the local branch failed; it may need removing by hand."
                ),
                closed=True,
                deleted_remote=remote_deleted,
            )
        if delete_remote and not remote_deleted:
            return SyncResult(
                success=False,
                branch=branch,
                message=(
                    f"{branch} is empty (merged into origin/main); deleted the local branch and "
                    f"closed the worktree, but deleting origin/{branch} failed; it may need "
                    f"removing by hand."
                ),
                closed=True,
                deleted_remote=False,
            )
        msg = f"{branch} is empty (merged into origin/main); deleted branch"
        msg += " (local + remote)" if remote_deleted else " (local)"
        msg += " and closed worktree."
        return SyncResult(
            success=True,
            branch=branch,
            message=msg,
            closed=True,
            deleted_remote=remote_deleted,
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
        message=result.message,
        pushed=pushed,
        push_message=push_message,
    )


def merge_to_main(worktree_path: Path, *, squash: bool = True, close: bool = False) -> SyncResult:
    """Merge the current feature branch back into ``main`` for local workflows.

    Rebases the branch onto an up-to-date ``origin/main`` (autosquashing
    ``fixup!`` commits by default), fast-forwards local ``main`` to the rebased
    branch tip, and pushes ``main`` to origin. With ``close=True`` it then tears
    down the worktree and deletes the feature branch.

    Built on the existing primitives — :func:`squash_worktree`,
    :func:`close_worktree`, :func:`delete_branch` — so there are no new
    subprocess idioms here.

    Args:
        worktree_path: Path to the feature worktree directory.
        squash: If True, autosquash ``fixup!`` commits during the rebase.
        close: If True, close the worktree and delete the feature branch
            (local + remote) after the merge succeeds.

    Returns:
        SyncResult with status and message. On a rebase conflict the result
        carries ``had_conflicts`` plus the merge-base/upstream SHAs for guidance.
    """
    worktree_path = worktree_path.resolve()
    project_path = worktree_path.parent
    branch = get_current_branch(worktree_path)

    # 1. fetch + sync main + rebase/autosquash onto origin/main
    result = squash_worktree(worktree_path, squash=squash)
    if not result.success:
        return result  # conflicts / fetch failure already populated

    # 2. fast-forward local main to the rebased branch tip. main is not checked
    #    out in the feature worktree, so manipulate the ref directly from the
    #    project root (same approach as update_local_main).
    branch_sha = run_git(["rev-parse", "HEAD"], cwd=worktree_path, quiet=True).stdout.strip()
    run_git(["update-ref", f"refs/heads/{MAIN_BRANCH}", branch_sha], cwd=project_path)

    # 3. push main (carries any local-only commits)
    push = run_cmd(["git", "push", "origin", MAIN_BRANCH], cwd=project_path, check=False)
    if push.returncode != 0:
        return SyncResult(
            success=False,
            branch=branch,
            message=f"Merged to local {MAIN_BRANCH} but push failed: {push.stderr or push.stdout}",
        )

    pushed, push_message = True, f"Pushed {MAIN_BRANCH} to origin"

    # 4. optional teardown (close + delete branch together)
    close_suffix = ""
    if close:
        close_result = close_worktree(worktree_path)
        if not close_result.success:
            return SyncResult(
                success=False,
                branch=branch,
                message=f"Merged and pushed, but close failed: {close_result.message}",
                pushed=pushed,
                push_message=push_message,
            )

        # delete_branch uses check=False and never raises; a failed delete
        # (local or remote) would otherwise leave an orphaned branch unreported.
        local_deleted, remote_deleted = delete_branch(project_path, branch, delete_remote=True)
        if not local_deleted:
            return SyncResult(
                success=False,
                branch=branch,
                message=f"Merged, pushed, and closed worktree, but failed to delete local branch {branch}",
                pushed=pushed,
                push_message=push_message,
            )
        close_suffix = (
            " and closed worktree"
            if remote_deleted
            else f" and closed worktree (origin/{branch} not deleted)"
        )

    return SyncResult(
        success=True,
        branch=branch,
        message=f"Merged {branch} into {MAIN_BRANCH}{close_suffix}",
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

    return _detach_and_free_ports(worktree_path)


def _detach_and_free_ports(worktree_path: Path) -> CloseResult:
    """Detach HEAD at origin/main and free the worktree's port allocation.

    Shared tail of close_worktree() and the sync --close path. Assumes the caller
    has already verified the worktree is safe to close (clean / empty).
    """
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

    # Unify Claude Code memory across worktrees
    _setup_claude_memory_symlink(project_path, alpha_path)

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


def _setup_claude_memory_symlink(project_path: Path, worktree_path: Path) -> None:
    """Unify Claude Code memory across worktrees by symlinking to a shared dir.

    Claude Code stores memories in ~/.claude/projects/<sanitised-path>/memory/.
    Each worktree gets its own sanitised path, fragmenting knowledge. This function
    creates a central memory dir at the project level and symlinks each worktree's
    memory dir to it, migrating any existing files first.

    Failures are logged as warnings rather than raised, since this is a
    non-critical enhancement that should not break worktree operations.

    Args:
        project_path: Path to the project root (bare repo).
        worktree_path: Path to the worktree.
    """
    try:
        claude_projects_dir = Path.home() / ".claude" / "projects"

        # Only proceed if ~/.claude/projects exists (Claude Code has been used)
        if not claude_projects_dir.is_dir():
            return

        project_sanitised = _sanitise_path_for_claude(project_path)
        worktree_sanitised = _sanitise_path_for_claude(worktree_path)

        central_memory = claude_projects_dir / project_sanitised / "memory"
        worktree_claude_dir = claude_projects_dir / worktree_sanitised
        worktree_memory = worktree_claude_dir / "memory"

        # Ensure central memory dir exists
        central_memory.mkdir(parents=True, exist_ok=True)

        # Ensure worktree's claude project dir exists
        worktree_claude_dir.mkdir(parents=True, exist_ok=True)

        # If worktree memory is already a symlink to the right place, nothing to do
        if worktree_memory.is_symlink():
            if worktree_memory.resolve() == central_memory.resolve():
                return
            # Stale symlink pointing elsewhere — remove it
            worktree_memory.unlink()

        # If worktree memory exists as a real directory, migrate its contents
        if worktree_memory.is_dir():
            for item in worktree_memory.iterdir():
                target = central_memory / item.name
                if not target.exists():
                    shutil.move(str(item), str(target))
            # Remove the now-empty (or emptied) directory
            shutil.rmtree(str(worktree_memory))

        # Create symlink
        worktree_memory.symlink_to(central_memory)
    except OSError as e:
        print(f"Warning: Could not set up unified Claude memory: {e}", file=sys.stderr)


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
    _setup_claude_memory_symlink(project_path, worktree_path)
    return worktree_path


def _blank_sentinel_keys(project_path: Path) -> set[str]:
    """Return keys the parent ``.env`` declares as blank-value sentinels (``KEY=``).

    A blank value in the parent marks a var the worktree manages independently:
    it is copied neither back (worktree -> parent) nor forward (parent ->
    worktree), so each worktree keeps its own value across a reset.
    """
    parent_env = project_path / ".env"
    if not parent_env.exists():
        return set()
    return {k for k, v in parse_env_text(parent_env.read_text()).items() if v == ""}


def regenerate_env_file(project_path: Path, worktree_path: Path, worktree_name: str) -> None:
    """Regenerate the .env file for a worktree, reusing the existing PORT_BASE.

    Used when .maelstrom.yaml has been updated (e.g., new port names added)
    and the .env file needs to reflect the current config.

    Args:
        project_path: Path to the project root.
        worktree_path: Path to the worktree.
        worktree_name: NATO name of the worktree.
    """
    # Capture worktree-managed values (parent declares them blank) before the
    # clean recreate wipes them — these are copied neither back nor forward, so
    # the worktree must keep its own value across the reset.
    env_file = worktree_path / ".env"
    sentinel_keys = _blank_sentinel_keys(project_path)
    preserved = {
        k: v
        for k, v in read_env_file(worktree_path).items()
        if k in sentinel_keys
    }

    # Clean recreate: drop the existing .env so _build_env_file rebuilds it
    # purely from the parent template. Callers (e.g. `mael env reset`) copy any
    # new worktree vars back to the parent first, so nothing user-authored is
    # lost — and the only difference between worktrees becomes the managed
    # section.
    if env_file.exists():
        env_file.unlink()
    _build_env_file(project_path, worktree_path, worktree_name, reuse_ports=True)

    # Restore the worktree's own values for blank-sentinel vars, replacing the
    # blank line the parent template produced.
    if preserved:
        _restore_blank_sentinel_values(env_file, preserved)


def _restore_blank_sentinel_values(env_file: Path, preserved: dict[str, str]) -> None:
    """Replace blank ``KEY=`` lines in *env_file* with their preserved values."""
    if not env_file.exists():
        return
    out: list[str] = []
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in preserved:
                out.append(f"{key}={preserved[key]}")
                continue
        out.append(line)
    env_file.write_text("\n".join(out) + "\n")


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
    return parse_env_text(env_file.read_text())


# --- Locked file transactions -------------------------------------------------


class _Txn:
    """Buffer for a :func:`locked_file` transaction.

    ``text`` starts as the file's current contents; assigning to it buffers a
    rewrite that is flushed only on clean exit of the ``with`` block, and only
    if the value actually changed.
    """

    def __init__(self, initial: str) -> None:
        self._initial = initial
        self.text = initial


@contextmanager
def locked_file(
    path: Path, *, timeout: float = 10.0, create: bool = True
) -> Iterator[_Txn]:
    """Open *path* under an exclusive advisory lock as a read/rewrite transaction.

    The file is its own lockfile: we ``flock`` its open fd directly (no separate
    lockfile, no atomic rename). The yielded transaction exposes the current
    ``text``; assigning ``txn.text`` buffers a rewrite that is flushed in place on
    clean exit, and only when the contents changed. On an exception inside the
    block nothing is written. The lock is always released in ``finally``.

    Because we lock the target fd, the rewrite is truncate-in-place rather than
    temp+``os.replace`` — replacing the inode would orphan a second waiter's
    already-open fd.

    Args:
        path: File to lock and (optionally) rewrite.
        timeout: Seconds to wait for the lock before raising ``TimeoutError``.
        create: Create the file if missing (open ``a+`` never truncates).

    Raises:
        TimeoutError: If the lock cannot be acquired within *timeout* seconds.
        FileNotFoundError: If the file is missing and *create* is False.
    """
    if not create and not path.exists():
        raise FileNotFoundError(path)

    # "a+" creates the file if missing and never truncates on open. Seek to 0 to
    # read existing contents.
    fd = open(path, "a+")
    try:
        deadline = time.monotonic() + timeout
        # Non-blocking acquire + sleep-poll so the deadline is portable; a plain
        # blocking LOCK_EX can't be time-bounded across platforms. Mirrors
        # task_store._locked.
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{path} is locked by another process; "
                        f"gave up after {int(timeout)}s."
                    )
                time.sleep(0.1)

        fd.seek(0)
        txn = _Txn(initial=fd.read())
        try:
            yield txn
            if txn.text != txn._initial:
                fd.seek(0)
                fd.truncate()
                fd.write(txn.text)
                fd.flush()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


# --- Copy-back of new worktree env vars to the parent .env --------------------


def managed_keys_in_env(worktree_path: Path) -> set[str]:
    """Return the set of maelstrom-managed keys in a worktree's ``.env``.

    These are exactly the keys inside the ``ENV_SECTION_START`` /
    ``ENV_SECTION_END`` markers (ports, ``WORKTREE``, etc.). Deriving them
    structurally from the file avoids re-running the port allocation logic.

    Args:
        worktree_path: Path to the worktree.

    Returns:
        Set of managed key names (empty if the file or markers are absent).
    """
    env_file = worktree_path / ".env"
    if not env_file.exists():
        return set()

    text = env_file.read_text()
    if ENV_SECTION_START not in text or ENV_SECTION_END not in text:
        return set()

    start = text.index(ENV_SECTION_START)
    end = text.index(ENV_SECTION_END)
    if start >= end:
        # Malformed: end marker before start marker — don't trust the slice.
        return set()
    section = text[start:end]
    keys: set[str] = set()
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def copy_back_new_env_vars(
    project_path: Path, worktree_path: Path
) -> CopyBackResult:
    """Copy genuinely-new worktree ``.env`` vars back to the parent ``.env``.

    The parent ``.env`` (``project_path/.env``) is the template every worktree
    ``.env`` is generated from. A var added to a worktree first would be lost on
    the next recreate; this rescues such vars into the parent so the parent stays
    the source of truth.

    Only **new** keys are copied: present in the worktree, absent from the parent,
    and not maelstrom-managed. Copy-back is purely additive — a key present in
    both with a differing value is reported as a conflict and left untouched.

    Args:
        project_path: Path to the project root (holds the parent ``.env``).
        worktree_path: Path to the worktree to copy from.

    Returns:
        A :class:`CopyBackResult` listing added keys and conflicts.
    """
    worktree_vars = read_env_file(worktree_path)
    managed = managed_keys_in_env(worktree_path)
    user_vars = {k: v for k, v in worktree_vars.items() if k not in managed}

    parent_env = project_path / ".env"
    result = CopyBackResult()

    # The read and the write are one critical section under the lock.
    with locked_file(parent_env) as env:
        parent_vars = parse_env_text(env.text)

        added: dict[str, str] = {}
        conflicts: list[EnvConflict] = []
        for key, value in user_vars.items():
            if key not in parent_vars:
                added[key] = value
                continue
            parent_val = parent_vars[key]
            if parent_val == "":
                # Blank parent value = install-managed sentinel; never copy back.
                continue
            if parent_val != value:
                resolved_parent = _substitute_vars(parent_val, worktree_vars)
                if resolved_parent == value:
                    # Parent holds the unresolved template that resolves to the
                    # worktree value — equivalent, not a real conflict.
                    continue
                conflicts.append(
                    EnvConflict(key, parent_val, value, resolved_parent)
                )

        result.added = added
        result.conflicts = conflicts

        if added:
            block = _format_copy_back_block(added)
            existing = env.text.rstrip("\n")
            # Append directly after existing content (one newline); if the parent
            # is empty, start cleanly at the top.
            env.text = f"{existing}\n{block}" if existing else block

    return result


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


@dataclass
class WorktreeSetup:
    """Result of :func:`setup_worktree_for_branch`.

    ``action`` is one of ``"reused"`` (an existing worktree for the branch was
    returned untouched), ``"recycled"`` (a closed worktree was repurposed), or
    ``"created"`` (a fresh worktree was created).
    """

    path: Path
    name: str  # NATO name, e.g. "bravo"
    action: str  # "reused" | "recycled" | "created"


def setup_worktree_for_branch(
    project_path: Path,
    project_name: str,
    branch: str,
    *,
    no_recycle: bool = False,
    run_install: bool = True,
) -> WorktreeSetup:
    """Ensure a fully set-up worktree exists for ``branch``; return path+name+action.

    Does NOT launch anything. Idempotent: an existing worktree for ``branch`` is
    returned as-is (no recycle/create, no install, no CLAUDE.local.md rewrite).

    Raises:
        RuntimeError: If a worktree name cannot be derived from the folder name.
    """
    project_path = project_path.resolve()

    # Reuse: an existing worktree for the branch is returned untouched.
    existing = find_worktree_by_branch(project_path, branch)
    if existing is not None:
        name = extract_worktree_name_from_folder(project_name, existing.name)
        if name is None:
            raise RuntimeError(
                f"Could not derive worktree name from '{existing.name}'."
            )
        return WorktreeSetup(path=existing, name=name, action="reused")

    worktree_path: Path | None = None
    action = "created"

    # Recycle a closed worktree if allowed.
    if not no_recycle:
        closed_wt = find_closed_worktree(project_path)
        if closed_wt is not None:
            try:
                worktree_path = recycle_worktree(closed_wt.path, branch)
                action = "recycled"
                wt_name = extract_worktree_name_from_folder(
                    project_name, closed_wt.path.name
                )
                if wt_name:
                    reclaim_or_allocate_ports(project_path, worktree_path, wt_name)
                # Recycled worktrees skip _finalize_worktree; set up memory symlink.
                _setup_claude_memory_symlink(project_path, worktree_path)
            except Exception as e:
                print(
                    f"Warning: Could not recycle worktree: {e}; creating new one.",
                    file=sys.stderr,
                )
                worktree_path = None
                action = "created"

    # Create a new worktree if not recycled.
    if worktree_path is None:
        worktree_path = create_worktree(project_path, branch, detached=False)
        action = "created"

    name = extract_worktree_name_from_folder(project_name, worktree_path.name)
    if name is None:
        raise RuntimeError(
            f"Could not derive worktree name from '{worktree_path.name}'."
        )

    # Finalize (recycle + create): write CLAUDE.local.md, run install command.
    update_claude_local_md(project_path, worktree_path, name)
    if run_install:
        run_install_cmd(worktree_path)

    return WorktreeSetup(path=worktree_path, name=name, action=action)


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
