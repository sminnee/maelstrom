"""Trash: set a branch's unmerged work aside without deleting it.

The model half of ``mael close --trash``, shared by the CLI and the
orchestrator server. **Trash** is defined in ``CONTEXT.md``.

It is the teardown of :mod:`mael_domain.worktree_close` with a different git
half. Uncommitted work is committed rather than refused, and nothing syncs.

Three orderings are encoded here:

- The pull request closes while ``origin/<b>`` still exists. GitHub closes a
  PR whose head is deleted on its own, and the comment saying where the branch
  went would be lost.
- The steps that can fail on the network — the PR close and the push — run
  before the detach. A failure leaves the worktree on its branch, so the same
  trash can be run again.
- The push runs before the local rename, for the same reason.
"""

import subprocess
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from pathlib import Path

from .cmux import mael_layout
from .github import close_pr, find_open_pr
from .github_model import GitHubError, PrStatus
from .worktree import (
    CloseResult,
    commit_wip,
    detach_and_free_ports,
    get_current_branch,
    rename_branch,
    rename_remote_branch,
    trash_refusal,
)
from .worktree_close import CloseSteps, FullCloseResult, teardown_steps
from .worktree_model import (
    CopyBackResult,
    WorktreeError,
    is_worktree_closable,
    trash_name,
)
from .worktree_steps import Scope, Step, StepOutcome, run_sequence


@dataclass
class TrashSteps(CloseSteps):
    """The collaborators the trash sequence drives, beyond the teardown's."""

    current_branch: Callable[[Path], str] = get_current_branch
    #: Why a branch cannot be trashed: ``(project_path, branch) -> reason``.
    refusal: Callable[[Path, str], str | None] = trash_refusal
    commit_wip: Callable[[Path], bool] = commit_wip
    detach: Callable[[Path], CloseResult] = detach_and_free_ports
    #: Raises ``GitHubError`` when the lookup fails: "no PR" must be known.
    find_pr: Callable[[Path, str], Awaitable[PrStatus | None]] = find_open_pr
    close_pr: Callable[[Path, int, str], None] = close_pr
    #: ``(project_path, old, new)``: push ``new`` to origin, delete ``old`` there.
    rename_remote: Callable[[Path, str, str], None] = rename_remote_branch
    #: ``(project_path, old, new)``: the local branch and its working history.
    rename: Callable[[Path, str, str], None] = rename_branch


def _guard(steps: TrashSteps, project_path: Path, branch: str) -> Step:
    def guard() -> StepOutcome:
        return StepOutcome(blocked=steps.refusal(project_path, branch))

    return Step(name="guard", run=guard)


def _remote_steps(steps: TrashSteps, project_path: Path, branch: str) -> list[Step]:
    """Close the pull request, then move the branch on origin."""
    target = trash_name(branch)

    async def pr() -> StepOutcome:
        try:
            found = await steps.find_pr(project_path, branch)
            if found is None:
                return StepOutcome()
            steps.close_pr(
                project_path, found.number, f"Trashed: branch moved to {target}"
            )
        except GitHubError as e:
            return StepOutcome(blocked=f"Could not close the PR for {branch}: {e}")
        return StepOutcome(messages=[f"Closed PR #{found.number}."])

    def remote() -> StepOutcome:
        try:
            steps.rename_remote(project_path, branch, target)
        except WorktreeError as e:
            return StepOutcome(blocked=str(e))
        except subprocess.CalledProcessError as e:
            return StepOutcome(
                blocked=f"Could not move {branch} on origin: {(e.stderr or '').strip()}"
            )
        return StepOutcome(messages=[f"Pushed {target} to origin."])

    return [
        Step(name="close_pr", run=pr),
        Step(name="rename_remote", run=remote, scopes=(Scope.REPO,)),
    ]


def _rename_step(steps: TrashSteps, project_path: Path, branch: str) -> Step:
    target = trash_name(branch)

    def local() -> StepOutcome:
        try:
            steps.rename(project_path, branch, target)
        except subprocess.CalledProcessError as e:
            return StepOutcome(
                blocked=f"Could not rename {branch}: {(e.stderr or '').strip()}"
            )
        return StepOutcome(messages=[f"Moved {branch} to {target}."])

    return Step(name="rename", run=local, scopes=(Scope.REPO,))


async def trash_worktree_fully(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path,
    *,
    steps: TrashSteps | None = None,
    announce: Callable[[str], None] = lambda line: None,
    executor: Executor | None = None,
) -> FullCloseResult:
    """Trash the branch ``worktree`` holds, and close the worktree.

    The guard runs before the teardown, so a refusal leaves the agents running.

    The result has the shape of :func:`close_worktree_fully`'s, so one caller
    reports both. Never raises for a refusal: read ``result.close.success``.
    """
    steps = steps or TrashSteps()
    if not is_worktree_closable(worktree):
        return FullCloseResult(
            close=CloseResult(
                success=False,
                message=f"{worktree} holds the main checkout and cannot be trashed",
            )
        )
    branch = steps.current_branch(worktree_path)
    rescue = CopyBackResult()
    split = 0
    said: list[str] = []

    def record(line: str) -> None:
        said.append(line)
        announce(line)

    def copy_back() -> StepOutcome:
        nonlocal rescue, split
        split = len(said)
        rescue = steps.copy_back(project_path, worktree_path)
        return StepOutcome()

    def wip() -> StepOutcome:
        try:
            committed = steps.commit_wip(worktree_path)
        except subprocess.CalledProcessError as e:
            return StepOutcome(
                blocked=f"Could not commit the uncommitted changes: "
                f"{(e.stderr or '').strip()}"
            )
        if not committed:
            return StepOutcome()
        return StepOutcome(messages=["Committed uncommitted changes as wip."])

    def detach() -> StepOutcome:
        outcome = steps.detach(worktree_path)
        if not outcome.success:
            return StepOutcome(blocked=outcome.message)
        return StepOutcome(
            messages=[f"Closing worktree '{worktree}'...", outcome.message]
        )

    def workspace() -> StepOutcome:
        if not steps.close_workspace(project, worktree):
            return StepOutcome()
        name = mael_layout.workspace_name(project, worktree)
        return StepOutcome(messages=[f"Closed cmux workspace '{name}'."])

    sequence = [_guard(steps, project_path, branch)]
    sequence += teardown_steps(steps, project, worktree, worktree_path)
    sequence += [
        Step(name="rescue_env_vars", run=copy_back, scopes=(Scope.WORKTREE,)),
        Step(name="commit_wip", run=wip, scopes=(Scope.WORKTREE,)),
    ]
    sequence += _remote_steps(steps, project_path, branch)
    sequence += [
        Step(name="detach", run=detach, scopes=(Scope.WORKTREE,)),
        _rename_step(steps, project_path, branch),
        Step(name="close_workspace", run=workspace),
    ]
    ran = await run_sequence(
        sequence,
        announce=record,
        repo=project_path,
        worktree=worktree_path,
        executor=executor,
    )
    return FullCloseResult(
        close=CloseResult(
            success=ran.ok,
            message=ran.blocked or f"Trashed {branch} as {trash_name(branch)}.",
            branch=branch,
        ),
        messages=ran.messages,
        copy_back=rescue,
        messages_before_copy_back=split,
    )
