"""Everything a worktree close does beyond the git work.

The model half of ``mael close``, shared by the CLI and the orchestrator
server. :func:`maelstrom.worktree.close_worktree` does the git half — sync,
verify, detach, free the ports. This module is the sequence around it: stop the
environment, stop the agents and sessions living in the worktree, rescue new
``.env`` vars back to the parent, then close the cmux workspace.

It sits above the adapters rather than inside one, as ``task_launch.py`` does
for a launch. ``env.py`` already imports ``worktree.py``, so the sequence
cannot live in either without a cycle.

The collaborators arrive as a :class:`CloseSteps` bundle, defaulting to the
real ones. A caller that has no cmux, no daemon or no environment store swaps
the step rather than the module.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .agent_stop import stop_agents_in_worktree
from .cmux import mael_layout
from .env import ServiceStatus, get_env_status, stop_env, stop_sessions
from .env_store import JsonEnvStore
from .session_discovery import LiveSession, LiveSessionSet
from .worktree import CloseResult, close_worktree, copy_back_new_env_vars
from .worktree_model import CopyBackResult

# Each default adapts one collaborator's signature to the CloseSteps shape:
# injecting the store, naming the `force` keyword, coercing a sequence.


def _env_status(project: str, worktree: str) -> Sequence[ServiceStatus] | None:
    return get_env_status(JsonEnvStore(), project, worktree)


def _stop_env(project: str, worktree: str) -> list[str]:
    return stop_env(JsonEnvStore(), project, worktree)


def _stop_agents(worktree_path: Path) -> list[str]:
    return stop_agents_in_worktree(worktree_path)


def _live_sessions(worktree_path: Path) -> Sequence[LiveSession]:
    return LiveSessionSet().all_for(worktree_path)


def _stop_sessions(sessions: Sequence[LiveSession]) -> list[str]:
    return stop_sessions(list(sessions))


def _copy_back(project_path: Path, worktree_path: Path) -> CopyBackResult:
    return copy_back_new_env_vars(project_path, worktree_path)


def _close(worktree_path: Path, force: bool) -> CloseResult:
    return close_worktree(worktree_path, force=force)


def _close_workspace(project: str, worktree: str) -> bool:
    return mael_layout.close_workspace(project, worktree)


@dataclass
class CloseSteps:
    """The collaborators the close sequence drives. Swapped whole in tests."""

    env_status: Callable[[str, str], Sequence[ServiceStatus] | None] = _env_status
    stop_env: Callable[[str, str], list[str]] = _stop_env
    stop_agents: Callable[[Path], list[str]] = _stop_agents
    live_sessions: Callable[[Path], Sequence[LiveSession]] = _live_sessions
    stop_sessions: Callable[[Sequence[LiveSession]], list[str]] = _stop_sessions
    copy_back: Callable[[Path, Path], CopyBackResult] = _copy_back
    close: Callable[[Path, bool], CloseResult] = _close
    close_workspace: Callable[[str, str], bool] = _close_workspace


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


def close_worktree_fully(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path | None,
    *,
    force: bool = False,
    steps: CloseSteps | None = None,
) -> FullCloseResult:
    """Close ``worktree`` and everything living in it.

    The order matters twice. The daemon is asked to stop its own agents before
    any pid is signalled, so a normal close is not recorded as a crash. The
    cmux workspace closes only after the git close succeeded, so a refused
    close leaves the user's workspace where it was.

    Never raises for a refusal: read ``result.close.success``.
    """
    steps = steps or CloseSteps()
    messages: list[str] = []

    status = steps.env_status(project, worktree)
    if status and any(service.alive for service in status):
        messages.append(f"Stopping environment for '{worktree}'...")
        messages.extend(f"  {line}" for line in steps.stop_env(project, worktree))

    # The daemon first: signalling the pids instead would record a normal close
    # as a crash — see agent_stop.
    messages.extend(f"  {line}" for line in steps.stop_agents(worktree_path))

    sessions = steps.live_sessions(worktree_path)
    if sessions:
        messages.append(
            f"Stopping {len(sessions)} Claude session(s) in '{worktree}'..."
        )
        messages.extend(f"  {line}" for line in steps.stop_sessions(sessions))

    # Rescue vars added to this worktree's .env back to the parent before the
    # close takes the worktree away. A warning here never fails the close.
    copy_back = CopyBackResult()
    before_copy_back = len(messages)
    if project_path is not None:
        copy_back = steps.copy_back(project_path, worktree_path)

    messages.append(f"Closing worktree '{worktree}'...")
    close = steps.close(worktree_path, force)
    if close.success:
        messages.append(close.message)
        if steps.close_workspace(project, worktree):
            name = mael_layout.workspace_name(project, worktree)
            messages.append(f"Closed cmux workspace '{name}'.")
    return FullCloseResult(
        close=close,
        messages=messages,
        copy_back=copy_back,
        messages_before_copy_back=before_copy_back,
    )
