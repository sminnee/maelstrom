"""Worktree management for maelstrom projects."""

import dataclasses
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .base_store import BaseStore, GitConfigBaseStore
from .claude_integration import get_shared_dir
from .rebase_repair import run_resolve_rebase_session
from .config import (
    load_config_or_default,
    service_port_names,
    shared_service_port_names,
)
from .shell import run_cmd
from .util import locked_file
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
    MAIN_WORKTREE_FOLDER,
    WORKTREE_NAMES,
    BaseRef,
    CopyBackResult,
    RebasePlan,
    StackTip,
    plan_rebase,
    resolve_stack_tip,
    validate_base,
    print_flushed,
    EnvConflict,
    _build_managed_section,
    _format_copy_back_block,
    _resolve_template_lines,
    _substitute_vars,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    parse_env_text,
    sanitise_path_for_claude,
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

    result = run_cmd(["git", "status", "--porcelain"], cwd=worktree_path, quiet=True, check=False)
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

    result = run_cmd(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=worktree_path, quiet=True, check=False,
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
    result = run_cmd(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=worktree_path, quiet=True, check=False,
    )

    if result.returncode == 0:
        # Remote exists, count commits not on remote
        result = run_cmd(
            ["git", "rev-list", "--count", f"{remote_branch}..HEAD"],
            cwd=worktree_path, quiet=True, check=False,
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
    result = run_cmd(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=worktree_path, quiet=True, check=False,
    )

    if result.returncode != 0:
        return None  # Not pushed

    # Count commits on remote branch ahead of main
    result = run_cmd(
        ["git", "rev-list", "--count", f"origin/{MAIN_BRANCH}..{remote_branch}"],
        cwd=worktree_path, quiet=True, check=False,
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
    repaired: bool = False  # conflicts were resolved by a headless Claude session
    base: str = MAIN_BRANCH  # the branch this one is stacked on
    base_collapsed: bool = False  # the base's remote branch was gone; flattened onto main


@dataclass
class CloseResult:
    """Result of a close operation."""

    success: bool
    message: str
    had_dirty_files: bool = False
    had_unpushed_commits: bool = False
    branch: str = ""  # branch checked out before close (for reopen tasks under --force)
    had_unmerged_work: bool = False  # --force closed over dirty/unmerged/conflicting work


@dataclass
class TidyBranchResult:
    """Result of tidying a single branch."""

    branch: str
    action: str  # "deleted", "pushed", "rebased", "skipped_conflicts", "skipped_checked_out", "skipped_base", "skipped_error"
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


def _has_origin_main(project_path: Path) -> bool:
    """True when ``origin/main`` resolves in ``project_path``.

    Distinguishes a project that was never pushed (no such ref) from a genuine
    failure of the commits-ahead batch, which the two cases must not share.
    """
    result = run_cmd(
        ["git", "rev-parse", "--verify", "--quiet", f"origin/{MAIN_BRANCH}"],
        cwd=project_path, quiet=True, check=False,
    )
    return result.returncode == 0


def _commits_ahead_batch(project_path: Path, commits: list[str]) -> dict[str, int]:
    """Classify each commit as ahead of ``origin/main`` or not, in one call.

    ``git rev-list <shas> --not origin/main`` lists the given commits that
    ``origin/main`` does not already contain. Anything it prints has work on it;
    anything it omits is contained. That is one subprocess for every worktree,
    rather than one ``rev-list`` each.

    This answers "ahead or not", not "how far ahead" — the returned ``1`` is a
    marker, not a count. :func:`is_worktree_closed` only compares against zero,
    which is why the cheaper question is enough. Do not use this where the
    magnitude matters; use :func:`get_commits_ahead`.

    Returns:
        ``{commit: 0 | 1}`` for each classified commit. A commit missing from the
        map could not be classified, and callers must treat that as unknown
        rather than as zero.
    """
    # ``git worktree list`` reports the bare project root with no HEAD. An empty
    # string reaching rev-list fails the whole call ("ambiguous argument"), which
    # would leave every other worktree in the project unclassified.
    unique = [c for c in dict.fromkeys(commits) if c]
    if not unique:
        return {}
    result = run_cmd(
        ["git", "rev-list", *unique, "--not", f"origin/{MAIN_BRANCH}"],
        cwd=project_path, quiet=True, check=False,
    )
    if result.returncode != 0:
        # A project that was never pushed has no origin/main to compare against.
        # get_commits_ahead answers 0 there, so a clean detached worktree counts
        # as closed; match that rather than showing the whole project as open.
        if not _has_origin_main(project_path):
            return {commit: 0 for commit in unique}
        # Anything else is a real failure. Classify nothing; the caller falls
        # back to "not closed", which keeps the row visible.
        return {}

    ahead = set(result.stdout.split())
    # rev-list walks history, so it prints ancestors of the given commits too.
    # Only the commits we asked about are ours to classify.
    return {commit: (1 if commit in ahead else 0) for commit in unique}


def closed_worktrees(project_path: Path, worktrees: list[WorktreeInfo]) -> set[Path]:
    """Return the paths of every closed worktree in ``worktrees``.

    The batch form of :func:`is_worktree_closed`, for callers that check a whole
    project at once. It applies the same three rules and reaches the same verdict,
    but resolves the commits-ahead rule for every worktree in one ``rev-list``
    instead of one per worktree. ``mael list`` spent more time on that check than
    on anything else once the PR lookup was batched.

    The dirty-file check stays per-worktree: ``git status`` reads a working tree,
    so there is nothing to batch. It runs last here, rather than second as it does
    per-worktree, so a worktree the batch has already ruled out costs no
    subprocess at all. The rules are independent, so the order does not change
    which worktrees come back — only how many ``git status`` calls it takes.
    """
    detached = [wt for wt in worktrees if not wt.branch]
    if not detached:
        return set()

    ahead = _commits_ahead_batch(project_path, [wt.commit for wt in detached])

    closed = set()
    for wt in detached:
        # The commits-ahead rule is already answered, so test it first: a
        # worktree with work on it cannot be closed whatever its working tree
        # holds, and skipping it here saves the `git status` below. Missing
        # means unclassified. Read that as "has work": calling a worktree closed
        # drops it from the table and offers it up for recycling.
        if ahead.get(wt.commit, 1) > 0:
            continue
        if get_worktree_dirty_files(wt.path):
            continue
        closed.add(wt.path)
    return closed


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
        # _main is a reference checkout, not a workspace. It normally has main
        # checked out (so it is not "closed"), but never recycle it even when
        # detached — the project would lose its main checkout.
        if wt.path.name == MAIN_WORKTREE_FOLDER:
            continue
        if is_worktree_closed(wt):
            return wt

    return None


def rebase_in_progress(worktree_path: Path) -> bool:
    """True if a rebase is currently in progress in the worktree.

    Git records an in-progress rebase in one of two state directories, depending
    on which backend it used. ``git rev-parse --git-path`` resolves both against
    the worktree's own git dir.
    """
    for state_dir in ("rebase-merge", "rebase-apply"):
        result = run_cmd(
            ["git", "rev-parse", "--git-path", state_dir],
            cwd=worktree_path,
            quiet=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = worktree_path / path
        if path.exists():
            return True
    return False


def _abort_rebase(worktree_path: Path) -> None:
    """Abort an in-progress rebase, restoring the worktree to its previous state."""
    run_cmd(
        ["git", "rebase", "--abort"],
        cwd=worktree_path,
        quiet=True,
        check=False,
    )


def _ref_exists(worktree_path: Path, ref: str) -> bool:
    """True when ``ref`` resolves in ``worktree_path``."""
    return run_cmd(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=worktree_path, quiet=True, check=False,
    ).returncode == 0


def _is_ancestor(worktree_path: Path, commit: str, of: str = "HEAD") -> bool:
    """True when ``commit`` is an ancestor of ``of`` (or is ``of``)."""
    return run_cmd(
        ["git", "merge-base", "--is-ancestor", commit, of],
        cwd=worktree_path, quiet=True, check=False,
    ).returncode == 0


def resolve_rebase_plan(
    worktree_path: Path, branch: str, store: BaseStore, *, skip_fetch: bool
) -> tuple[BaseRef, RebasePlan]:
    """Read ``branch``'s base and decide the rebase, guarding against data loss.

    Two IO facts feed the pure :func:`plan_rebase`, and both need care:

    - **Does ``origin/<base>`` still exist?** An absent ref cannot be rebased onto,
      so the plan always falls back to main. Whether that is a *permanent* collapse
      is a separate question: only a fetch that pruned can answer it. When the fetch
      was skipped (``sync-all``, the autorepair second pass), the ref may just be
      unfetched, so ``collapsed`` stays off and the store keeps its base — the
      collapse defers to the next real sync. Flattening a live stack for good is far
      worse than collapsing one sync later.
    - **Is the recorded tip actually in this branch's history?**
      ``git rebase --onto X <upstream>`` replays only ``<upstream>..HEAD``. A tip
      that is not an ancestor of HEAD makes that range something other than this
      branch's own work, and commits disappear with no error. Dropping to a plain
      rebase there is degraded but safe.

    Returns:
        ``(base, plan)`` — the base as stored, and the plan to execute.
    """
    base = store.read(branch)
    if base.is_default:
        return base, plan_rebase(base, base_exists=True)

    base_exists = _ref_exists(worktree_path, f"origin/{base.branch}")
    plan = plan_rebase(base, base_exists=base_exists)
    if plan.collapsed and skip_fetch:
        plan = replace(plan, collapsed=False)

    if plan.upstream is not None and not _is_ancestor(worktree_path, plan.upstream):
        plan = replace(plan, upstream=None)
    return base, plan


def squash_worktree(
    worktree_path: Path,
    skip_fetch: bool = False,
    squash: bool = True,
    abort_on_conflict: bool = False,
    store: BaseStore | None = None,
) -> SyncResult:
    """Fetch and rebase a worktree onto its base, optionally autosquashing
    ``fixup!`` commits.

    The base is ``main`` unless the branch was stacked on another branch, in which
    case the rebase targets that branch instead and cascades its new work into this
    one. A branch with no stored base runs the identical argv it always has.

    This is the single funnel every sync path reaches, so it is the one place that
    records ``base_tip`` — including after an autorepair, whose second pass
    re-enters here.

    Does NOT push — callers that need to publish the rebased branch call
    :func:`sync_worktree`, which builds on this function.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step (useful when syncing multiple
            worktrees that share the same repo, where fetch was already done). A
            missing base ref is then treated as unfetched rather than deleted.
        squash: If True, autosquash ``fixup!`` commits into their targets while
            rebasing (``git rebase --autosquash``).
        abort_on_conflict: If True, on a rebase conflict run ``git rebase --abort``
            to restore the worktree to its pre-rebase state instead of leaving the
            rebase in progress.
        store: Where branch bases live. Defaults to the project's git config.

    Returns:
        SyncResult with status and message. On success ``pushed``/``push_message``
        are left at their defaults — this function never pushes.
    """
    worktree_path = worktree_path.resolve()

    # Get current branch
    branch = get_current_branch(worktree_path)
    resolved_store: BaseStore = (
        store if store is not None else GitConfigBaseStore(worktree_path)
    )

    # A stacked branch needs pruned remote refs to tell a merged base from a live
    # one. --prune is gated to that case: it deletes stale remote-tracking refs,
    # a visible side effect the default path must not acquire.
    stacked = not resolved_store.read(branch).is_default

    # Fetch from origin (unless skipped)
    if not skip_fetch:
        try:
            run_git(
                ["fetch", "origin"] + (["--prune"] if stacked else []),
                cwd=worktree_path,
            )
            # Fast-forward local main to match origin/main
            update_local_main(worktree_path.parent)
        except subprocess.CalledProcessError as e:
            return SyncResult(
                success=False,
                branch=branch,
                message=f"Failed to fetch from origin: {e.stderr}",
            )

    base, plan = resolve_rebase_plan(
        worktree_path, branch, resolved_store, skip_fetch=skip_fetch
    )

    # Get merge-base and base-tip SHA before rebasing (for conflict instructions).
    # Both name the resolved base, so the help a conflict prints — and the prompt
    # a headless repair session gets — describe the rebase that actually ran.
    merge_base: str | None = None
    upstream_head: str | None = None
    try:
        # Get merge-base (where branch diverged from its base)
        base_result = run_cmd(
            ["git", "merge-base", "HEAD", plan.onto],
            cwd=worktree_path,
            quiet=True,
            check=False,
        )
        if base_result.returncode == 0 and base_result.stdout.strip():
            merge_base = base_result.stdout.strip()[:7]  # Short SHA

        # Get the base's SHA
        head_result = run_cmd(
            ["git", "rev-parse", "--short", plan.onto],
            cwd=worktree_path,
            quiet=True,
            check=False,
        )
        if head_result.returncode == 0 and head_result.stdout.strip():
            upstream_head = head_result.stdout.strip()
    except Exception:
        pass

    # Rebase with autostash (optionally autosquashing fixup! commits). The default
    # base has no upstream, so it builds the pre-stacking argv exactly.
    if plan.upstream is None:
        rebase_cmd = ["git", "rebase", "--autostash", plan.onto]
    else:
        rebase_cmd = ["git", "rebase", "--autostash", "--onto", plan.onto, plan.upstream]
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
            _abort_rebase(worktree_path)
            return SyncResult(
                success=False,
                branch=branch,
                message=(
                    f"Rebase of {branch} onto {plan.label} hit conflicts; "
                    "aborted and restored worktree to its previous state."
                ),
                had_conflicts=True,
                aborted=True,
                merge_base=merge_base,
                upstream_head=upstream_head,
                base=plan.effective_base,
            )
        return SyncResult(
            success=False,
            branch=branch,
            message=result.stderr or result.stdout,
            had_conflicts=True,
            merge_base=merge_base,
            upstream_head=upstream_head,
            base=plan.effective_base,
        )

    _record_base_after_rebase(worktree_path, branch, base, plan, resolved_store)

    return SyncResult(
        success=True,
        branch=branch,
        message=f"Successfully rebased {branch} onto {plan.label}",
        # The branch the rebase landed on, not the one the store holds. Under a
        # deferred collapse those differ, and callers build origin/<base> from
        # this — a stored name whose ref is gone would give them a ref that does
        # not resolve.
        base=plan.effective_base,
        base_collapsed=plan.collapsed,
    )


def _record_base_after_rebase(
    worktree_path: Path,
    branch: str,
    base: BaseRef,
    plan: RebasePlan,
    store: BaseStore,
) -> None:
    """Persist the base's new tip, or clear the base if the stack collapsed.

    Re-recording the tip on every successful rebase is what keeps the amended-parent
    case working: the tip must be where *this* rebase left the base, not where an
    earlier one did. A stale tip degrades silently into a naive rebase that then
    conflicts on review churn — the very case the tip exists for.

    A default base writes nothing at all, so an unstacked branch leaves no trace in
    config.
    """
    if base.is_default:
        return

    if plan.collapsed:
        store.clear(branch)
        return

    tip = run_cmd(
        ["git", "rev-parse", f"origin/{base.branch}"],
        cwd=worktree_path, quiet=True, check=False,
    )
    if tip.returncode != 0:
        # The base ref did not resolve, so this rebase landed on main and the
        # deferred-collapse rule kept the base stored. There is no new tip to
        # record, and the old one is now wrong — clear it rather than let a stale
        # value silently pick the wrong replay point next time.
        store.write(branch, replace(base, tip=None))
        return
    store.write(branch, replace(base, tip=tip.stdout.strip()))


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
    # "Empty" is measured against the resolved base, not main: a child identical to
    # its parent has nothing of its own, even when main has moved on beneath them.
    base_label = f"origin/{result.base}"
    if close_if_empty and is_branch_merged(worktree_path, branch, base=base_label):
        project_path = worktree_path.parent

        # A branch another branch is stacked on must survive its own emptiness:
        # deleting it would strand its children on a ref that no longer resolves.
        # Close the worktree, keep the branch.
        if branch in base_branches(project_path):
            detach_result = _detach_and_free_ports(worktree_path)
            if not detach_result.success:
                return SyncResult(success=False, branch=branch, message=detach_result.message)
            return SyncResult(
                success=True,
                branch=branch,
                message=(
                    f"{branch} is empty (merged into {base_label}) and the worktree was "
                    f"closed; the branch was kept because another branch is based on it."
                ),
                closed=True,
                base=result.base,
                base_collapsed=result.base_collapsed,
            )

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
                    f"{branch} is empty (merged into {base_label}) and the worktree was closed, "
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
                    f"{branch} is empty (merged into {base_label}); deleted the local branch and "
                    f"closed the worktree, but deleting origin/{branch} failed; it may need "
                    f"removing by hand."
                ),
                closed=True,
                deleted_remote=False,
            )
        msg = f"{branch} is empty (merged into {base_label}); deleted branch"
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


def _repair_conflicted_rebase(
    worktree_path: Path,
    first: SyncResult,
    repair_runner: Callable[[Path], subprocess.CompletedProcess] | None,
    announce: Callable[[str], None],
) -> SyncResult | None:
    """Run one repair session over a conflicted rebase.

    Shared by the sync and squash autorepair variants: both leave the conflict in
    place, hand the tree to one session, and check the same three things. They
    differ only in the rebase that came before and what follows a repair.

    Returns:
        ``None`` when the rebase is repaired and the caller may continue. A
        ``SyncResult`` when the repair did not happen or did not work — the
        caller returns it unchanged.
    """
    # ``had_conflicts`` means "the rebase exited non-zero", which covers failures
    # with nothing to resolve — a refused rebase, a bad --autosquash target, a
    # locked index. Only a rebase left in progress has conflicts to repair;
    # anything else would spend a whole repair session to reach the same error.
    if not rebase_in_progress(worktree_path):
        return first

    # The session streams its own output. Say what started it first, so the
    # console does not go from a sync line straight to a Claude session with
    # nothing to explain why an agent is now running.
    announce(
        f"Rebase conflict on {first.branch}. Starting autorepair: a headless "
        f"Claude session resolves the conflicts and continues the rebase."
    )

    runner = repair_runner or run_resolve_rebase_session
    try:
        proc = runner(worktree_path)
    except (OSError, subprocess.TimeoutExpired) as e:
        _abort_rebase(worktree_path)
        return SyncResult(
            success=False,
            branch=first.branch,
            message=f"Autorepair session failed: {e}; rebase aborted.",
            had_conflicts=True,
            aborted=True,
        )

    if proc.returncode != 0 or rebase_in_progress(worktree_path):
        _abort_rebase(worktree_path)
        return SyncResult(
            success=False,
            branch=first.branch,
            message="Autorepair did not complete the rebase; aborted and restored.",
            had_conflicts=True,
            aborted=True,
        )

    landed_on = get_current_branch(worktree_path)
    if landed_on != first.branch:
        return SyncResult(
            success=False,
            branch=first.branch,
            message=(
                f"Autorepair finished the rebase but left the worktree on "
                f"{landed_on}, not {first.branch}. Check out {first.branch} "
                f"again by hand, then re-run the sync."
            ),
            had_conflicts=True,
        )

    return None


def squash_worktree_with_autorepair(
    worktree_path: Path,
    *,
    skip_fetch: bool = False,
    squash: bool = True,
    repair_runner: Callable[[Path], subprocess.CompletedProcess] | None = None,
    announce: Callable[[str], None] = print_flushed,
) -> SyncResult:
    """Rebase a worktree, resolving a conflict with a headless Claude session.

    The no-push counterpart of :func:`sync_worktree_with_autorepair`, built on
    :func:`squash_worktree`. A repaired rebase is the whole job here: nothing is
    published, so the branch is left rebased and unpushed for the caller to
    inspect.

    Only one repair attempt is made, and every failure path aborts the rebase.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step.
        squash: If True, autosquash ``fixup!`` commits while rebasing.
        repair_runner: Callable taking the worktree path and returning a
            ``CompletedProcess``. Defaults to the real headless session; tests
            substitute their own.
        announce: Callable taking one line of progress text. Defaults to a
            flushed ``print``; the CLI passes ``click.echo``.

    Returns:
        SyncResult. ``repaired`` is True when a repair session fixed the rebase.
        ``pushed`` is always False — this function never pushes.
    """
    first = squash_worktree(
        worktree_path,
        skip_fetch=skip_fetch,
        squash=squash,
        abort_on_conflict=False,
    )
    if first.success or not first.had_conflicts:
        return first  # success, or a fetch failure there is no repairing

    failure = _repair_conflicted_rebase(worktree_path, first, repair_runner, announce)
    if failure is not None:
        return failure

    # The rebase is finished and nothing needs pushing, so the repair is the
    # whole result. Build it fresh rather than from ``first``: that result
    # carries the conflict flags of the rebase the repair just resolved.
    return SyncResult(
        success=True,
        branch=first.branch,
        message=f"Rebased {first.branch} onto origin/main",
        repaired=True,
    )


def sync_worktree_with_autorepair(
    worktree_path: Path,
    *,
    skip_fetch: bool = False,
    squash: bool = False,
    close_if_empty: bool = False,
    repair_runner: Callable[[Path], subprocess.CompletedProcess] | None = None,
    announce: Callable[[str], None] = print_flushed,
) -> SyncResult:
    """Sync a worktree, resolving a rebase conflict with a headless Claude session.

    Runs :func:`sync_worktree` first. A conflict is left in place — not aborted —
    so the repair session sees the conflicted tree. The session
    (``/resolve-rebase-conflicts``) resolves the conflicts and continues the
    rebase; a second sync then completes the push.

    Only one repair attempt is made. Every failure path aborts the rebase, so the
    worktree is never left mid-rebase for the caller to trip over. The exception
    is a repair session that finished the rebase but left the worktree on another
    branch: there is nothing to abort, and the state needs a human.

    Args:
        worktree_path: Path to the worktree directory.
        skip_fetch: If True, skip the fetch step.
        squash: If True, autosquash ``fixup!`` commits while rebasing.
        close_if_empty: If True, delete an empty branch and close the worktree.
        repair_runner: Callable taking the worktree path and returning a
            ``CompletedProcess``. Defaults to the real headless session; tests
            substitute their own.
        announce: Callable taking one line of progress text. Defaults to a
            flushed ``print``; the CLI passes ``click.echo``.

    Returns:
        SyncResult. ``repaired`` is True when a repair session fixed the rebase.
    """
    first = sync_worktree(
        worktree_path,
        skip_fetch=skip_fetch,
        squash=squash,
        abort_on_conflict=False,
        close_if_empty=close_if_empty,
    )
    if first.success or not first.had_conflicts:
        return first  # success, or a fetch failure there is no repairing

    failure = _repair_conflicted_rebase(worktree_path, first, repair_runner, announce)
    if failure is not None:
        return failure

    # The rebase is done, so this second pass is a no-op rebase that pushes.
    final = sync_worktree(
        worktree_path,
        skip_fetch=True,
        squash=squash,
        abort_on_conflict=True,
        close_if_empty=close_if_empty,
    )
    return dataclasses.replace(final, repaired=True) if final.success else final


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

    # 2. fast-forward local main to the rebased branch tip. main is normally
    #    checked out in _main, so merge there to move the ref *and* the working
    #    tree — update-ref alone would leave that checkout showing the merged
    #    files as pending deletions. Fall back to update-ref when nothing holds
    #    main (same approach as update_local_main).
    branch_sha = run_git(["rev-parse", "HEAD"], cwd=worktree_path, quiet=True).stdout.strip()
    main_worktree = find_worktree_by_branch(
        project_path, MAIN_BRANCH, include_main=True
    )
    if main_worktree is not None:
        run_git(["merge", "--ff-only", branch_sha], cwd=main_worktree)
    else:
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


def close_worktree(worktree_path: Path, *, force: bool = False) -> CloseResult:
    """Close a worktree by syncing and resetting to origin/main.

    A branch other branches are stacked on keeps its branch: only HEAD detaches.

    This operation:
    1. Syncs the worktree (rebase against the branch's base)
    2. Verifies no uncommitted changes
    3. Verifies no unmerged commits
    4. Resets HEAD to origin/main

    After closing, the worktree's HEAD will point to the same commit as
    origin/main, making it available for recycling via is_worktree_closed().

    With ``force=True`` the close becomes an escape hatch for *incomplete* work:
    a conflicting sync is aborted (not left mid-rebase), and the worktree is torn
    down even with unmerged commits or a dirty tree. Nothing is discarded — any
    uncommitted/untracked changes are committed onto the branch as
    ``wip: uncommitted changes`` first, so they ride along on the preserved branch
    and reappear when it is reopened. The branch (and its PR) is never deleted; the
    returned ``branch`` / ``had_unmerged_work`` let the caller create a reopen task.

    Args:
        worktree_path: Path to the worktree directory.
        force: If True, close even with unmerged/dirty/conflicting work (see above).

    Returns:
        CloseResult with status and message.
    """
    worktree_path = worktree_path.resolve()
    branch = get_current_branch(worktree_path)  # capture before any detach

    # --force never loses work: commit any dirty/untracked changes onto the branch
    # FIRST, so they survive the close and reappear when the branch is reopened.
    committed_wip = False
    if force and get_worktree_dirty_files(worktree_path):
        run_git(["add", "-A"], cwd=worktree_path)
        run_git(["commit", "-m", "wip: uncommitted changes"], cwd=worktree_path)
        committed_wip = True

    # Sync. With --force, abort a conflicting rebase instead of leaving it in progress.
    sync_result = sync_worktree(worktree_path, abort_on_conflict=force)
    if not sync_result.success and not force:
        return CloseResult(
            success=False,
            message=f"Sync failed: {sync_result.message}",
        )
    # With force, a failed/aborted sync still falls through to teardown.

    # Check for dirty files (empty now under force — wip was committed above).
    dirty_files = get_worktree_dirty_files(worktree_path)
    commits_ahead = get_commits_ahead(worktree_path)
    had_unmerged = force and (committed_wip or commits_ahead > 0 or sync_result.had_conflicts)

    if not force:
        if dirty_files:
            return CloseResult(
                success=False,
                message="Worktree has uncommitted changes",
                had_dirty_files=True,
            )
        if commits_ahead > 0:
            return CloseResult(
                success=False,
                message=f"Worktree has {commits_ahead} commit(s) not merged to origin/main",
                had_unpushed_commits=True,
            )

    # --force (or clean) → tear down. Branch is preserved (only HEAD detaches).
    # Tree is clean by now (wip committed), so the normal detach works.
    result = _detach_and_free_ports(worktree_path)
    # Surface what the caller needs to create a reopen task.
    result.branch = branch
    result.had_unmerged_work = had_unmerged
    return result


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


def recycle_worktree(worktree_path: Path, branch: str, *, base: str = MAIN_BRANCH) -> Path:
    """Recycle a closed worktree for a new branch.

    Assumes the worktree is already on main and clean.

    Args:
        worktree_path: Path to the closed worktree.
        branch: Branch name to switch to.
        base: Branch a brand-new branch starts from. Ignored when the branch
            already exists locally or on origin.

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
            # Create new branch from its base (HEAD may be behind if recycled), so
            # a stacked child starts with its parent's work already under it.
            base_ref = _first_existing_ref(
                worktree_path, [f"origin/{base}", f"origin/{MAIN_BRANCH}"]
            ) or f"origin/{MAIN_BRANCH}"
            run_git(["checkout", "-b", branch, base_ref], cwd=worktree_path)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to switch to branch {branch}: {e.stderr}")

    # Update .claude/CLAUDE.local.md
    project_path = worktree_path.parent
    wt_name = extract_worktree_name_from_folder(project_path.name, worktree_path.name)
    if wt_name:
        update_claude_local_md(project_path, worktree_path, wt_name)

    _setup_claude_settings_symlink(worktree_path)
    _write_agents_md(worktree_path)

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


@dataclass
class ProjectInfo:
    """A maelstrom-aware project and its worktree count."""

    name: str
    path: Path
    worktree_count: int


def list_projects(projects_dir: Path) -> list[ProjectInfo]:
    """Every maelstrom-aware project under projects_dir, with its worktree count.

    The count excludes the project root itself (the bare repo), which is how
    ``mael list-all`` skips that row.

    Args:
        projects_dir: Path to the projects directory (e.g., ~/Projects).

    Returns:
        One ProjectInfo per project, in the order find_all_projects gives.
    """
    projects = []
    for project_path in find_all_projects(projects_dir):
        worktrees = list_worktrees(project_path)
        count = sum(1 for wt in worktrees if wt.path != project_path)
        projects.append(
            ProjectInfo(name=project_path.name, path=project_path, worktree_count=count)
        )

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
        ~/Projects/<project>/.git            (bare clone)
        ~/Projects/<project>/_main           (main, for reference)
        ~/Projects/<project>/<project>-alpha (first worktree, detached)

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

    # Populate origin/* — the bare clone predates the refspec above, so it
    # wrote refs/heads/* only. Every object is local, so this is a ref exchange.
    run_git(["fetch", "origin"], cwd=project_path)

    # Keep a git note on its commit through a rebase. /code-review tags a reviewed
    # commit with a note; without this every rebase drops it. mael doctor repairs
    # projects created before this line.
    run_git(["config", "notes.rewriteRef", "refs/notes/*"], cwd=project_path)

    # Get the default branch
    result = run_git(["symbolic-ref", "--short", "HEAD"], cwd=project_path, quiet=True)
    default_branch = result.stdout.strip()

    # Detach HEAD so the default branch isn't "checked out" in the project root,
    # which would prevent git worktree add from using it.
    # Use update-ref --no-deref instead of checkout --detach to avoid touching the working tree.
    head_sha = run_git(["rev-parse", "HEAD"], cwd=project_path, quiet=True).stdout.strip()
    run_git(["update-ref", "--no-deref", "HEAD", head_sha], cwd=project_path, quiet=True)

    # Check the default branch out into _main. It is a reference checkout, not a
    # workspace: keeping main there leaves every NATO worktree free for work.
    main_path = project_path / MAIN_WORKTREE_FOLDER
    run_git(["worktree", "add", str(main_path), default_branch], cwd=project_path)

    # A bare clone sets no upstream; without this a human in _main gets no
    # ahead/behind count and a bare `git pull` fails. mael doctor repairs
    # projects created before this line.
    run_git(
        ["branch", "--set-upstream-to", f"origin/{default_branch}", default_branch],
        cwd=project_path,
    )

    # Create the alpha worktree. Detached, because main is checked out in _main
    # and git allows one worktree per branch.
    alpha_folder = get_worktree_folder_name(project_name, "alpha")
    alpha_path = project_path / alpha_folder
    run_git(
        ["worktree", "add", "--detach", str(alpha_path), default_branch],
        cwd=project_path,
    )

    # Generate .env for the initial worktree
    write_env_file(alpha_path, {"WORKTREE": "alpha", "WORKTREE_NUM": "0"})

    # Unify Claude Code memory across worktrees
    setup_claude_memory_symlink(project_path, alpha_path)

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

    # Derive the flat port-name lists. Structured `services:` (when present) owns
    # the ports; otherwise fall back to the legacy flat `port_names` fields. The
    # allocator mechanism below is identical for both.
    if config.services:
        local_port_names = service_port_names(config)
        shared_port_names = shared_service_port_names(config)
    else:
        local_port_names = config.port_names
        shared_port_names = config.shared_port_names

    # Add port variables if configured
    if local_port_names:
        port_base = None
        if reuse_ports:
            port_base = get_port_allocation(project_path, worktree_name)
        if port_base is None:
            port_base = allocate_port_base(project_path, len(local_port_names))
            record_port_allocation(project_path, worktree_name, port_base)
        generated_vars.update(generate_port_env_vars(port_base, local_port_names))

    # Add shared port variables if configured
    if shared_port_names:
        shared_base = get_port_allocation(project_path, "_shared")
        if shared_base is None:
            shared_base = allocate_port_base(project_path, len(shared_port_names))
            record_port_allocation(project_path, "_shared", shared_base)
        generated_vars["SHARED_PORT_BASE"] = str(shared_base)
        generated_vars.update(generate_port_env_vars(shared_base, shared_port_names))

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


def setup_claude_memory_symlink(project_path: Path, worktree_path: Path) -> None:
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

        project_sanitised = sanitise_path_for_claude(project_path)
        worktree_sanitised = sanitise_path_for_claude(worktree_path)

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
    setup_claude_memory_symlink(project_path, worktree_path)
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

    # Re-add the worktree's own values for blank-sentinel vars. The regenerated
    # template no longer emits a blank ``KEY=`` line for these (it is dropped as
    # a parent-side sentinel), so the preserved value would otherwise be lost.
    if preserved:
        _restore_blank_sentinel_values(env_file, preserved)


def _restore_blank_sentinel_values(env_file: Path, preserved: dict[str, str]) -> None:
    """Re-add preserved values for blank-sentinel keys in *env_file*.

    The regenerated template drops blank ``KEY=`` sentinel lines, so each
    preserved key is normally missing and gets appended as ``KEY=value``. Legacy
    ``.env`` files that still contain a literal blank ``KEY=`` line have it
    rewritten in place instead, so no duplicate is produced.
    """
    if not env_file.exists():
        return
    out: list[str] = []
    rewritten: set[str] = set()
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in preserved:
                out.append(f"{key}={preserved[key]}")
                rewritten.add(key)
                continue
        out.append(line)
    # Append any preserved key not already present (the common post-fix path,
    # where the template emitted no blank line to rewrite).
    for key, value in preserved.items():
        if key not in rewritten:
            out.append(f"{key}={value}")
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


def create_worktree(
    project_path: Path, branch: str, *, detached: bool = False, base: str = MAIN_BRANCH
) -> Path:
    """Create a new worktree for the given branch.

    Args:
        project_path: Path to the project root (bare repo).
        branch: Branch name to create worktree for.
        detached: If True, create a detached HEAD worktree at origin/main
            instead of checking out the branch. Useful when the branch
            (e.g., main) is already checked out elsewhere.
        base: Branch a brand-new branch starts from, so a child begins stacked
            rather than needing an immediate re-stack. Ignored when the branch
            already exists locally or on origin, which have their own tips.

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
        # Create new branch from its base (or origin's default branch, or HEAD if
        # no remote). Starting from the base is what makes a stacked child begin
        # with its parent's work already under it.
        base_ref = _first_existing_ref(
            project_path, [f"origin/{base}", "origin/main", "origin/master"]
        ) or "HEAD"
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

    # One locked, 0o600 read-modify-write replaces the previous unlocked
    # default-mode read_text()+write_text() pair. txn.text holds the current
    # contents (empty string when the file is freshly created).
    with locked_file(env_file) as txn:
        existing_content = txn.text

        if existing_content == "":
            # First-time creation
            parts = [managed_section]
            if template_text:
                parts.append("")  # blank line separator
                parts.append(
                    _resolve_template_lines(template_text.rstrip("\n"), generated_vars)
                )
            new_content = "\n".join(parts) + "\n"
        elif ENV_SECTION_START in existing_content and ENV_SECTION_END in existing_content:
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

        txn.text = new_content


def find_worktree_by_branch(
    project_path: Path, branch: str, *, include_main: bool = False
) -> Path | None:
    """Find a maelstrom-managed worktree by its branch name.

    Only managed worktrees count: a direct child of the project root named
    ``<project>-<nato>``. Git also lists worktrees created by hand elsewhere on
    disk, and one of those can hold the branch we want. Callers use the result to
    derive a NATO name and a port allocation, so returning a foreign path fails
    later and further from the cause.

    Args:
        project_path: Path to the project root.
        branch: Branch name to search for.
        include_main: Also match the ``_main`` reference checkout. It has no NATO
            name, so only a caller that just needs the path may ask for it.

    Returns:
        Path to the worktree directory, or None if not found.
    """
    # git reports resolved paths, so resolve ours before comparing. Callers may
    # pass a symlinked path (macOS /var -> /private/var; config only expands ~),
    # which would otherwise match nothing and look like the bug this filter fixes.
    project_path = project_path.resolve()
    project_name = project_path.name
    for wt in list_worktrees(project_path):
        if wt.branch != branch or wt.path.parent != project_path:
            continue
        if extract_worktree_name_from_folder(project_name, wt.path.name) is not None:
            return wt.path
        if include_main and wt.path.name == MAIN_WORKTREE_FOLDER:
            return wt.path
    return None


@dataclass
class WorktreeSetup:
    """Result of :func:`setup_worktree_for_branch`.

    ``action`` is one of ``"reused"`` (an existing worktree for the branch was
    returned untouched), ``"recycled"`` (a closed worktree was repurposed), or
    ``"created"`` (a fresh worktree was created).

    ``sync`` is the result of the sync that runs when the worktree is opened. It
    is ``None`` on the ``"reused"`` path, where no sync runs. A ``sync`` that
    failed means the branch was not rebased: the caller must block the launch
    rather than start a session on stale code. The worktree itself is still set
    up, so a repair in place and a re-run will pick it up.
    """

    path: Path
    name: str  # NATO name, e.g. "bravo"
    action: str  # "reused" | "recycled" | "created"
    sync: SyncResult | None = None  # None ⇒ no sync ran (reused)


def check_base_exists(project_path: Path, base: str) -> None:
    """Raise if ``base`` names no branch that could be rebased onto.

    The single owner of "is this a real base?", shared by every path that sets one
    — ``mael sync --base``, ``mael add --base``, and ``mael stack-tip``. Without it
    a typo is accepted, the next sync finds ``origin/<base>`` missing, treats that
    as a collapse, rebases onto main and clears the store: the user is told the
    base was set and it is silently gone one sync later.

    ``main`` is always acceptable — it means "not stacked", so it is never checked.

    Raises:
        ValueError: If ``base`` resolves neither on origin nor locally.
    """
    if base == MAIN_BRANCH:
        return
    if _first_existing_ref(
        project_path, [f"refs/remotes/origin/{base}", f"refs/heads/{base}"]
    ) is None:
        raise ValueError(
            f"No such branch to stack on: {base}. "
            f"It must exist on origin or locally."
        )


def _branch_exists_anywhere(project_path: Path, branch: str) -> bool:
    """True when ``branch`` already exists locally or on origin.

    Such a branch is checked out at its own tip rather than created from a base,
    so it keeps whatever base it already had.
    """
    return _first_existing_ref(
        project_path, [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
    ) is not None


def _resolve_new_branch_base(
    project_path: Path,
    branch: str,
    base: str | None,
    store: BaseStore,
    *,
    announce: Callable[[str], None] = print_flushed,
) -> str:
    """Decide what a brand-new ``branch`` should stack on.

    An explicit ``base`` wins and is validated. Otherwise the project's stack tip
    decides, self-healing to ``main`` if its branch is gone and warning — never
    blocking — if its branch is stale. Blocking would stall an unattended agent
    session on a judgement call.

    Raises:
        ValueError: If an explicit ``base`` is the branch itself or closes a cycle.
    """
    if base is not None:
        if base != MAIN_BRANCH:
            check_base_exists(project_path, base)
            validate_base(branch, base, store.all())
        return base

    tip = current_stack_tip(project_path, store)
    if tip.stale_days is not None:
        announce(
            f"Warning: the stack tip {tip.branch} has had no commits for "
            f"{tip.stale_days} days. {branch} will stack on it anyway; "
            f"run `mael stack-tip main` to start unrelated work instead."
        )
    if tip.branch == branch:
        # The tip already names this branch (a re-create after a close). Stacking
        # it on itself is not a thing, so fall back to main.
        return MAIN_BRANCH
    return tip.branch


def setup_worktree_for_branch(
    project_path: Path,
    project_name: str,
    branch: str,
    *,
    no_recycle: bool = False,
    run_install: bool = True,
    base: str | None = None,
    announce: Callable[[str], None] = print_flushed,
) -> WorktreeSetup:
    """Ensure a fully set-up worktree exists for ``branch``; return path+name+action.

    New work stacks on the project's stack tip unless ``base`` says otherwise, and
    the tip then advances to ``branch`` — so stacks form a genuine chain rather
    than a fan of siblings. A project whose tip is ``main`` (the default) gets the
    behaviour it always had.

    Does NOT launch anything. Idempotent: an existing worktree for ``branch`` is
    returned as-is (no recycle/create, no install, no CLAUDE.local.md rewrite, and
    no move of the stack tip — reuse must not silently re-point where new work
    lands).

    Args:
        base: Branch to stack ``branch`` on. ``None`` uses the stack tip;
            ``main`` opts this one worktree out.
        announce: Callable taking one line of progress text, for the stale-tip
            warning.

    Raises:
        RuntimeError: If a worktree name cannot be derived from the folder name.
        ValueError: If ``base`` is the branch itself, or closes a cycle.
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

    store = GitConfigBaseStore(project_path)
    # Decided before anything creates the branch: a pre-existing branch keeps the
    # base it already has, because it is checked out at its own tip.
    branch_existed = _branch_exists_anywhere(project_path, branch)
    resolved_base = _resolve_new_branch_base(
        project_path, branch, base, store, announce=announce
    )

    worktree_path: Path | None = None
    action = "created"

    # Recycle a closed worktree if allowed.
    if not no_recycle:
        closed_wt = find_closed_worktree(project_path)
        if closed_wt is not None:
            try:
                worktree_path = recycle_worktree(closed_wt.path, branch, base=resolved_base)
                action = "recycled"
                wt_name = extract_worktree_name_from_folder(
                    project_name, closed_wt.path.name
                )
                if wt_name:
                    reclaim_or_allocate_ports(project_path, worktree_path, wt_name)
                # Recycled worktrees skip _finalize_worktree; set up memory symlink.
                setup_claude_memory_symlink(project_path, worktree_path)
            except Exception as e:
                print(
                    f"Warning: Could not recycle worktree: {e}; creating new one.",
                    file=sys.stderr,
                )
                worktree_path = None
                action = "created"

    # Create a new worktree if not recycled.
    if worktree_path is None:
        worktree_path = create_worktree(
            project_path, branch, detached=False, base=resolved_base
        )
        action = "created"

    # Record the base and advance the tip only once the branch really exists, so a
    # failed create never leaves the project pointing at a branch that is not there.
    #
    # A branch that already existed is checked out at its own tip, not the base's —
    # create_worktree and recycle_worktree both ignore `base` for it. Recording the
    # base anyway would claim a relationship its history does not have, and the next
    # sync would rebase it onto an unrelated branch. The `mael close --force` →
    # reopen-branch path reaches here for exactly such a branch.
    if branch_existed:
        if base == MAIN_BRANCH:
            store.clear(branch)
    elif resolved_base == MAIN_BRANCH:
        store.clear(branch)
    else:
        store.write(branch, BaseRef(branch=resolved_base))

    # Advancing the tip is advisory: it decides where the *next* worktree stacks,
    # and this one is already built. A store that cannot be written must not turn
    # a working `mael add` into a failure, so this failure is reported and passed
    # over. The base write above is not advisory and is left to raise.
    try:
        store.write_stack_tip(branch)
    except RuntimeError as e:
        announce(f"Warning: could not advance the stack tip to {branch}: {e}")

    name = extract_worktree_name_from_folder(project_name, worktree_path.name)
    if name is None:
        raise RuntimeError(
            f"Could not derive worktree name from '{worktree_path.name}'."
        )

    # An opened worktree starts on rebased code. A branch that already existed —
    # locally or on origin — is checked out at its own tip, which can be many
    # commits behind origin/main. Sync before install so install runs against the
    # rebased tree. close_if_empty stays off: a brand-new branch is "empty" and
    # must never be deleted here.
    sync = sync_worktree_with_autorepair(worktree_path)

    # Finalize (recycle + create): write CLAUDE.local.md, run install command.
    # CLAUDE.local.md is written even when the sync failed. The worktree exists
    # either way, and the caller's advice for a failed sync is to repair it in
    # the worktree — which never writes the file, so skipping it here would
    # leave it missing until the next `mael add`. Install is skipped instead:
    # it must not run against a tree that was never rebased.
    update_claude_local_md(project_path, worktree_path, name)
    if run_install and sync.success:
        run_install_cmd(worktree_path)

    return WorktreeSetup(path=worktree_path, name=name, action=action, sync=sync)


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


IMPORT_PATTERN = re.compile(r"^@(\S+)\s*$", re.MULTILINE)
MAX_IMPORT_DEPTH = 3


def _inline_imports(text: str, worktree_path: Path, depth: int, seen: set[Path]) -> str:
    """Replace ``@path`` import lines with the referenced files' contents.

    Missing files are noted in an HTML comment rather than raising, matching
    Claude Code's tolerance of a dangling import (e.g. before the first
    ``mael add`` writes ``CLAUDE.local.md``).
    """

    def replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        target = (worktree_path / rel).resolve()
        if not target.is_file():
            return f"<!-- @{rel}: not found -->"
        if target in seen or depth >= MAX_IMPORT_DEPTH:
            return f"<!-- @{rel}: skipped (nested too deeply or circular) -->"
        inlined = "<!-- from @{0} -->\n{1}".format(
            rel, _inline_imports(target.read_text(), worktree_path, depth + 1, seen | {target})
        )
        return inlined.rstrip("\n")

    return IMPORT_PATTERN.sub(replace, text)


def _write_agents_md(worktree_path: Path) -> None:
    """Generate ``AGENTS.md`` from ``CLAUDE.md`` with ``@`` imports inlined.

    OpenCode reads only ``AGENTS.md`` and does not resolve Claude Code's
    ``@file`` imports, so the generated file inlines each import (recursively,
    depth-limited, cycle-safe). ``AGENTS.md`` is derived output: regenerated
    whenever the worktree's local context is rewritten, and gitignored.
    """
    claude_md = worktree_path / "CLAUDE.md"
    if not claude_md.exists():
        return

    agents_md = worktree_path / "AGENTS.md"
    agents_md.write_text(
        _inline_imports(claude_md.read_text(), worktree_path, 0, set()).rstrip("\n") + "\n"
    )

    _ensure_gitignore_entry(worktree_path, "AGENTS.md")


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

    # Regenerate AGENTS.md so it picks up the local context just written
    _write_agents_md(worktree_path)

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


def _first_existing_ref(repo_path: Path, refs: list[str]) -> str | None:
    """The first ref in ``refs`` that resolves in ``repo_path``, or ``None``."""
    for ref in refs:
        if _ref_exists(repo_path, ref):
            return ref
    return None


def remote_branch_ages(project_path: Path) -> dict[str, int]:
    """``branch -> days since its last commit`` for every branch on ``origin``.

    One local ``for-each-ref`` answers both questions the stack tip needs — does
    the branch still exist, and has anyone touched it lately — with no network
    call and no per-branch cost. ``refs/remotes/origin/HEAD`` is a symref rather
    than a branch, so it is dropped: nobody can stack on it.
    """
    result = run_cmd(
        [
            "git", "for-each-ref",
            "--format=%(refname:short)%09%(committerdate:unix)",
            "refs/remotes/origin",
        ],
        cwd=project_path, quiet=True, check=False,
    )
    if result.returncode != 0:
        return {}

    now = int(time.time())
    ages: dict[str, int] = {}
    for line in result.stdout.splitlines():
        short, _, when = line.partition("\t")
        if not when.strip():
            continue
        branch = short[len("origin/"):] if short.startswith("origin/") else short
        if not branch or branch == "HEAD":
            continue
        try:
            ages[branch] = max(0, (now - int(when.strip())) // 86400)
        except ValueError:
            continue
    return ages


def current_stack_tip(
    project_path: Path, store: BaseStore | None = None
) -> StackTip:
    """The branch new worktrees stack on, validated against what still exists.

    A tip whose branch has been deleted self-heals to ``main`` and the heal is
    persisted, so the next caller does not re-derive it and no ``mael add`` can
    ever base on a dead ref. A stale-but-live tip is returned as it is, with its
    age, for the caller to warn about.
    """
    resolved_store: BaseStore = (
        store if store is not None else GitConfigBaseStore(project_path)
    )
    tip = resolve_stack_tip(resolved_store.read_stack_tip(), remote_branch_ages(project_path))
    if tip.healed:
        resolved_store.write_stack_tip(MAIN_BRANCH)
    return tip


def base_branches(project_path: Path, store: BaseStore | None = None) -> set[str]:
    """Every branch that some other branch is stacked on.

    Deleting one of these strands its children on a ref that no longer resolves,
    so both branch-deleting paths — ``tidy_branches`` and sync's ``close_if_empty``
    — consult it first. One ``BaseStore.all()`` call answers it for the whole
    project.
    """
    resolved: BaseStore = store if store is not None else GitConfigBaseStore(project_path)
    return {base for base in resolved.all().values() if base != MAIN_BRANCH}


def stacked_branches(project_path: Path, store: BaseStore | None = None) -> set[str]:
    """Every branch involved in a stack, as either a base or a child.

    ``tidy_branches`` must leave all of them alone. Its rebase is hardcoded to
    ``origin/main``, which flattens a child off its base and strands the child's
    recorded tip on a commit no longer in the branch — the exact state ``base_tip``
    exists to prevent, and one no error reports. Its delete is equally wrong for a
    base. One ``BaseStore.all()`` call answers both sides.
    """
    resolved: BaseStore = store if store is not None else GitConfigBaseStore(project_path)
    bases = resolved.all()
    stacked = set(bases)
    stacked.update(base for base in bases.values() if base != MAIN_BRANCH)
    return stacked


def tidy_branch(
    project_path: Path,
    branch: str,
    temp_worktree_path: Path,
    checked_out_branches: set[str],
    bases: set[str] | None = None,
) -> TidyBranchResult:
    """Tidy a single branch: rebase, then delete if merged or push if not.

    Args:
        project_path: Path to the project root.
        branch: Branch name to tidy.
        temp_worktree_path: Path to temporary worktree for operations.
        checked_out_branches: Set of branches currently checked out in worktrees.
        bases: Branches involved in a stack, on either side. These are left alone
            entirely: rebasing one onto main would flatten the stack, and
            deleting a base would strand its children.

    Returns:
        TidyBranchResult with outcome.
    """
    # Skip a branch that is part of a stack, before anything touches it. This runs
    # first because both of tidy's actions — the rebase onto main and the delete —
    # are wrong here, and they are wrong on both sides of a stack link: the rebase
    # flattens a child, the delete strands a base's children.
    if bases and branch in bases:
        return TidyBranchResult(
            branch=branch,
            action="skipped_base",
            success=True,
            message=f"Branch '{branch}' is part of a stack",
        )

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
    0. Skip if the branch is part of a stack, on either side
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

    # One store call for the whole project; tidy must not flatten a stack.
    bases = stacked_branches(project_path)

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
            result = tidy_branch(
                project_path, branch, temp_worktree_path, checked_out_branches, bases,
            )
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
