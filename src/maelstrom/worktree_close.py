"""Everything a worktree teardown does beyond the git work.

The model half of ``mael close`` and ``mael remove``, shared by the CLI and the
orchestrator server. :func:`maelstrom.worktree.close_worktree` does the git half
— sync, verify, detach, free the ports. This module is the sequence around it:
stop the environment, stop the agents and sessions living in the worktree,
rescue new ``.env`` vars back to the parent, then close the cmux workspace.

Close and remove are the same sequence with a different git step, so they are
written once here over :mod:`maelstrom.worktree_steps`. Remove differs only in
dropping the ``.env`` rescue — the worktree is being deleted, not parked.

It sits above the adapters rather than inside one, as ``task_launch.py`` does
for a launch. ``env.py`` already imports ``worktree.py``, so the sequence
cannot live in either without a cycle.

The collaborators arrive as a :class:`CloseSteps` bundle, defaulting to the
real ones. A caller that has no cmux, no daemon or no environment store swaps
the step rather than the module.
"""

from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field
from pathlib import Path

from .agent_stop import stop_agents_in_worktree
from .cmux import mael_layout
from .env import ServiceStatus, get_env_status, stop_env, stop_sessions
from .env_store import JsonEnvStore
from .session_discovery import LiveSession, LiveSessionSet
from .worktree import (
    CloseResult,
    close_worktree,
    copy_back_new_env_vars,
    get_worktree_dirty_files,
    remove_worktree_by_path,
)
from .worktree_model import CopyBackResult
from .worktree_steps import Scope, Step, StepOutcome, run_sequence

# Each default adapts one collaborator's signature to the CloseSteps shape:
# injecting the store, naming the `force` keyword, coercing a sequence.


def _env_status(project: str, worktree: str) -> Sequence[ServiceStatus] | None:
    return get_env_status(JsonEnvStore(), project, worktree)


def _stop_env(project: str, worktree: str) -> list[str]:
    return stop_env(JsonEnvStore(), project, worktree)


async def _stop_agents(worktree_path: Path) -> list[str]:
    return await stop_agents_in_worktree(worktree_path)


def _live_sessions(worktree_path: Path) -> Sequence[LiveSession]:
    return LiveSessionSet().all_for(worktree_path)


def _stop_sessions(sessions: Sequence[LiveSession]) -> list[str]:
    return stop_sessions(list(sessions))


def _copy_back(project_path: Path, worktree_path: Path) -> CopyBackResult:
    return copy_back_new_env_vars(project_path, worktree_path)


def _close(worktree_path: Path, force: bool, discard: bool) -> CloseResult:
    return close_worktree(worktree_path, force=force, discard=discard)


def _close_workspace(project: str, worktree: str) -> bool:
    return mael_layout.close_workspace(project, worktree)


def _remove(project_path: Path, folder_name: str) -> None:
    remove_worktree_by_path(project_path, folder_name)


def _dirty_files(worktree_path: Path) -> list[str]:
    return get_worktree_dirty_files(worktree_path)


@dataclass
class CloseSteps:
    """The collaborators the teardown sequence drives. Swapped whole in tests."""

    env_status: Callable[[str, str], Sequence[ServiceStatus] | None] = _env_status
    stop_env: Callable[[str, str], list[str]] = _stop_env
    #: The one awaited step: the agent host is reached over its socket.
    stop_agents: Callable[[Path], Awaitable[list[str]]] = _stop_agents
    live_sessions: Callable[[Path], Sequence[LiveSession]] = _live_sessions
    stop_sessions: Callable[[Sequence[LiveSession]], list[str]] = _stop_sessions
    copy_back: Callable[[Path, Path], CopyBackResult] = _copy_back
    close: Callable[[Path, bool, bool], CloseResult] = _close
    close_workspace: Callable[[str, str], bool] = _close_workspace
    #: Removes the checkout outright: ``(project_path, folder_name) -> None``.
    remove: Callable[[Path, str], None] = _remove
    #: What a removal would destroy, so it can refuse instead.
    dirty_files: Callable[[Path], list[str]] = _dirty_files


@dataclass
class FullCloseResult:
    """What the whole close amounted to.

    ``close`` is the git half's own result, so a caller reads the refusal and
    its flags exactly as it would from :func:`close_worktree`. ``messages`` are
    the progress lines, in the order they happened, for a CLI to echo.
    """

    close: CloseResult
    messages: list[str] = field(default_factory=list)
    #: What the ``.env`` rescue found. Reported, never a reason to fail.
    copy_back: CopyBackResult = field(default_factory=CopyBackResult)
    #: How many of ``messages`` came before the rescue ran. A caller that
    #: renders ``copy_back`` splits ``messages`` here, so the rescue is
    #: reported where it happened rather than after the close.
    messages_before_copy_back: int = 0


def _teardown_steps(
    steps: CloseSteps,
    project: str,
    worktree: str,
    worktree_path: Path,
) -> list[Step]:
    """The steps every teardown runs before its git step.

    Two orderings are encoded here rather than remembered at each caller.
    ``stop_agents`` runs before ``stop_sessions``: signalling the pids first
    makes the daemon record a deliberate stop as a crash — see ``agent_stop``.
    And the environment stops before either, so nothing is restarted by a
    supervisor as its session dies.
    """

    def env() -> StepOutcome:
        status = steps.env_status(project, worktree)
        if not status or not any(service.alive for service in status):
            return StepOutcome()
        lines = [f"Stopping environment for '{worktree}'..."]
        lines += [f"  {line}" for line in steps.stop_env(project, worktree)]
        return StepOutcome(messages=lines)

    async def agents() -> StepOutcome:
        stopped = await steps.stop_agents(worktree_path)
        return StepOutcome(messages=[f"  {line}" for line in stopped])

    def sessions() -> StepOutcome:
        live = steps.live_sessions(worktree_path)
        if not live:
            return StepOutcome()
        lines = [f"Stopping {len(live)} Claude session(s) in '{worktree}'..."]
        lines += [f"  {line}" for line in steps.stop_sessions(live)]
        return StepOutcome(messages=lines)

    return [
        Step(name="stop_env", run=env, scopes=(Scope.WORKTREE,)),
        Step(name="stop_agents", run=agents, scopes=(Scope.WORKTREE,)),
        Step(name="stop_sessions", run=sessions, scopes=(Scope.WORKTREE,)),
    ]


async def close_worktree_fully(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path | None,
    *,
    force: bool = False,
    discard: bool = False,
    steps: CloseSteps | None = None,
    announce: Callable[[str], None] = lambda line: None,
    executor: Executor | None = None,
) -> FullCloseResult:
    """Close ``worktree`` and everything living in it.

    The order matters twice. The daemon is asked to stop its own agents before
    any pid is signalled, so a normal close is not recorded as a crash. The
    cmux workspace closes only after the git close succeeded, so a refused
    close leaves the user's workspace where it was.

    Never raises for a refusal: read ``result.close.success``.
    """
    steps = steps or CloseSteps()
    rescue = CopyBackResult()
    #: How many lines had been said when the rescue ran. Counted here rather
    #: than found in the text afterwards: the sequence knows where it was.
    split = 0
    closed: list[CloseResult] = []
    said: list[str] = []

    def record(line: str) -> None:
        said.append(line)
        announce(line)

    def copy_back() -> StepOutcome:
        # Rescue vars added to this worktree's .env back to the parent before
        # the close takes the worktree away. A warning here never fails it.
        nonlocal rescue, split
        split = len(said)
        if project_path is not None:
            rescue = steps.copy_back(project_path, worktree_path)
        return StepOutcome()

    def git_close() -> StepOutcome:
        outcome = steps.close(worktree_path, force, discard)
        closed.append(outcome)
        # A refusal stops the sequence, so close_workspace never runs on one
        # and the user's workspace stays where it was.
        if not outcome.success:
            return StepOutcome(
                messages=[f"Closing worktree '{worktree}'..."],
                blocked=outcome.message,
            )
        return StepOutcome(
            messages=[f"Closing worktree '{worktree}'...", outcome.message]
        )

    def workspace() -> StepOutcome:
        if not steps.close_workspace(project, worktree):
            return StepOutcome()
        name = mael_layout.workspace_name(project, worktree)
        return StepOutcome(messages=[f"Closed cmux workspace '{name}'."])

    sequence = _teardown_steps(steps, project, worktree, worktree_path)
    sequence += [
        Step(name="rescue_env_vars", run=copy_back, scopes=(Scope.WORKTREE,)),
        # Worktree only. close_worktree fetches, but it takes the repo scope
        # itself, at the fetch — which is the right granularity, because the
        # rest of the close touches this checkout alone. Declaring it here too
        # would deadlock: `flock` is per open file description, so the inner
        # acquire blocks against the outer one even in the same thread.
        Step(name="git_close", run=git_close, scopes=(Scope.WORKTREE,)),
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
        # A sequence blocked before the git step never produced a CloseResult.
        # Its own refusal stands in, so a caller reads one shape either way.
        close=closed[0]
        if closed
        else CloseResult(success=False, message=ran.blocked or ""),
        messages=ran.messages,
        copy_back=rescue,
        messages_before_copy_back=split,
    )


async def remove_worktree_fully(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path,
    folder_name: str,
    *,
    force: bool = False,
    steps: CloseSteps | None = None,
    announce: Callable[[str], None] = lambda line: None,
    executor: Executor | None = None,
) -> FullCloseResult:
    """Remove ``worktree`` and everything living in it.

    The same teardown as :func:`close_worktree_fully` with two differences. The
    git step deletes the checkout rather than parking it, and there is no
    ``.env`` rescue: the worktree is going away, so there is nothing to carry
    back.

    Written as a sequence, it gains the step ``mael remove`` never had. The
    daemon is asked to stop its agents before any pid is signalled, so a
    removal is not recorded as a crash — see ``agent_stop``.

    ``force`` removes a worktree holding uncommitted work. Without it the
    removal is refused and the files are named, because ``git worktree remove
    --force`` destroys them and a caller with no prompt of its own — the
    server — must be told rather than obeyed.

    Never raises for a failure: read ``result.close.success``.
    """
    steps = steps or CloseSteps()
    said: list[str] = []

    def record(line: str) -> None:
        said.append(line)
        announce(line)

    def check_dirty() -> StepOutcome:
        if force:
            return StepOutcome()
        dirty = steps.dirty_files(worktree_path)
        if not dirty:
            return StepOutcome()
        listed = ", ".join(dirty[:5])
        if len(dirty) > 5:
            listed += f", and {len(dirty) - 5} more"
        return StepOutcome(
            blocked=(
                f"'{worktree}' has {len(dirty)} modified or untracked file(s) "
                f"that removing would destroy: {listed}"
            )
        )

    def git_remove() -> StepOutcome:
        opening = f"Removing worktree '{worktree}'..."
        try:
            steps.remove(project_path, folder_name)
        except Exception as exc:  # noqa: BLE001 — the caller reads the refusal
            return StepOutcome(
                messages=[opening],
                blocked=f"Could not remove '{worktree}': {exc}",
            )
        return StepOutcome(messages=[opening, "Worktree removed successfully."])

    def workspace() -> StepOutcome:
        if not steps.close_workspace(project, worktree):
            return StepOutcome()
        name = mael_layout.workspace_name(project, worktree)
        return StepOutcome(messages=[f"Closed cmux workspace '{name}'."])

    # The guard runs before the teardown: refusing after the agents are
    # stopped would leave the worktree worse off for a removal that never ran.
    sequence = [Step(name="check_dirty", run=check_dirty, scopes=(Scope.WORKTREE,))]
    sequence += _teardown_steps(steps, project, worktree, worktree_path)
    sequence += [
        # Both scopes, unlike `git_close`: the removal rewrites the shared
        # worktree list under `.git` and frees the port allocation, and it
        # takes no lock of its own to collide with.
        Step(name="git_remove", run=git_remove, scopes=(Scope.REPO, Scope.WORKTREE)),
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
        close=CloseResult(success=ran.ok, message=ran.blocked or "Worktree removed."),
        messages=ran.messages,
    )
