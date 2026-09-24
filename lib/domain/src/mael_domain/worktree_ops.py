"""The worktree operations that are not a teardown: sync, and the environment.

``worktree_close.py`` holds close and remove. These two are the rest of what
the orchestrator offers over a worktree, and they are sequences for the same
reason: a step declares the scope it needs, so an operation cannot reach a
checkout another operation is already rewriting.

Both are one step. A sync is one git algorithm, whole — see
``docs/dev/worktree-steps.md``. A restart is two, because it is stop then start
rather than a third code path.

They live here rather than as closures in ``mael_orchestrator.cli`` so they can
be driven directly: the mode dispatch and the restart ordering are decisions,
and a decision no test can reach is a decision nothing holds.
"""

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from pathlib import Path

from .env import start_env, stop_env
from .env_store import JsonEnvStore
from .worktree import SyncResult, sync_worktree, sync_worktree_with_autorepair
from .worktree_steps import Scope, SequenceResult, Step, StepOutcome, run_sequence

#: The three settings ``mael sync`` has, which the one sync operation chooses
#: between. Mirrored by ``validate.SYNC_MODES``.
AUTOREPAIR = "autorepair"
SQUASH = "squash"

#: What an environment can be asked to do. Mirrored by ``validate.ENV_ACTIONS``.
START = "start"
STOP = "stop"
RESTART = "restart"


def _sync(worktree_path: Path, squash: bool, abort: bool) -> SyncResult:
    return sync_worktree(worktree_path, squash=squash, abort_on_conflict=abort)


def _autorepair(worktree_path: Path) -> SyncResult:
    return sync_worktree_with_autorepair(worktree_path)


def _start(project: str, worktree: str, worktree_path: Path) -> list[str]:
    state = start_env(JsonEnvStore(), project, worktree, worktree_path)
    return [f"{service.name}: started" for service in state.services]


def _stop(project: str, worktree: str) -> list[str]:
    return stop_env(JsonEnvStore(), project, worktree)


@dataclass
class SyncSteps:
    """The collaborators the sync drives. Swapped whole in tests."""

    sync: Callable[[Path, bool, bool], SyncResult] = _sync
    autorepair: Callable[[Path], SyncResult] = _autorepair


@dataclass
class EnvSteps:
    """The collaborators the environment operation drives."""

    start: Callable[[str, str, Path], list[str]] = _start
    stop: Callable[[str, str], list[str]] = _stop


async def run_sync(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path,
    mode: str,
    *,
    steps: SyncSteps | None = None,
    announce: Callable[[str], None] = lambda line: None,
    executor: Executor | None = None,
) -> SequenceResult:
    """Rebase ``worktree`` onto its base.

    ``mode`` is ``plain``, ``autorepair`` or ``squash``. ``abort_on_conflict``
    is implied on the two non-repairing modes: only autorepair wants the
    conflicted tree left standing, because its repair session is what reads it.

    The worktree scope, not the repo scope: the sync's own fetch takes the repo
    scope where it belongs, and declaring it here as well would deadlock — see
    ``worktree_steps``.

    Never raises for a refusal: read ``result.ok``.
    """
    steps = steps or SyncSteps()

    def rebase() -> StepOutcome:
        if mode == AUTOREPAIR:
            result = steps.autorepair(worktree_path)
        else:
            result = steps.sync(worktree_path, mode == SQUASH, True)
        if not result.success:
            return StepOutcome(blocked=result.message)
        return StepOutcome(messages=[result.message])

    return await run_sequence(
        [Step(name="rebase", run=rebase, scopes=(Scope.WORKTREE,))],
        announce=announce,
        repo=project_path,
        worktree=worktree_path,
        executor=executor,
    )


async def run_env(
    project: str,
    worktree: str,
    worktree_path: Path,
    project_path: Path,
    action: str,
    *,
    steps: EnvSteps | None = None,
    announce: Callable[[str], None] = lambda line: None,
    executor: Executor | None = None,
) -> SequenceResult:
    """Start, stop or restart the environment in ``worktree``.

    ``restart`` is the other two in order. A stop that fails stops the
    sequence, so a half-restart never happens: starting over a process that
    would not die hides the fault behind a running service.

    Never raises for a failure: read ``result.ok``.
    """
    steps = steps or EnvSteps()

    def stop() -> StepOutcome:
        if action not in (STOP, RESTART):
            return StepOutcome()
        try:
            return StepOutcome(messages=list(steps.stop(project, worktree)))
        except Exception as exc:  # noqa: BLE001 — the caller reads the refusal
            return StepOutcome(blocked=f"Could not stop the environment: {exc}")

    def start() -> StepOutcome:
        if action not in (START, RESTART):
            return StepOutcome()
        try:
            return StepOutcome(
                messages=list(steps.start(project, worktree, worktree_path))
            )
        except Exception as exc:  # noqa: BLE001 — the caller reads the refusal
            return StepOutcome(blocked=f"Could not start the environment: {exc}")

    return await run_sequence(
        [
            Step(name="stop_env", run=stop, scopes=(Scope.WORKTREE,)),
            Step(name="start_env", run=start, scopes=(Scope.WORKTREE,)),
        ],
        announce=announce,
        repo=project_path,
        worktree=worktree_path,
        executor=executor,
    )
