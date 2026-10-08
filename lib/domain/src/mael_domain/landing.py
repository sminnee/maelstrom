"""A task's landing: its status past done, and the sync that records each step.

See ``CONTEXT.md``, "Landing". :func:`status_of` derives the status;
:func:`sync` refreshes the cached pull requests and records each new step.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .config import DEPLOY_STEPS, MaelstromConfig, load_config_or_default
from .landing_store import (
    STEPS,
    EnvLanding,
    InMemoryPullRequestStore,
    InMemoryTaskStepStore,
    PullRequest,
    PullRequestStore,
    StepEvent,
    TaskStepStore,
)
from .protocol import EnvLandingState, TaskLanding
from .task import STATUS_DONE, Task
from .task_table import TaskTable

log = logging.getLogger(__name__)

LANDED = "landed"
NOT_YET = "not_yet"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrackedTask:
    """A task whose landing is followed: one with a registered pull request."""

    project: str
    task_id: str
    status: str
    pr_number: int


@dataclass(frozen=True)
class Merge:
    """What GitHub says about one pull request. ``merged_at`` is empty until it merges.

    ``state`` is GitHub's: ``OPEN``, ``CLOSED`` or ``MERGED``.
    """

    url: str
    title: str
    merged_at: str
    merge_sha: str
    state: str = ""


@dataclass(frozen=True)
class Deploy:
    """The newest successful deploy to one environment."""

    sha: str
    created_at: str


class LandingSignals(ABC):
    """The reads that say how far a pull request has landed.

    A read that fails answers "unknown" — a missing entry or ``None`` — and
    never "not yet".
    """

    @abstractmethod
    async def merges(self, project: str, numbers: list[int]) -> dict[int, Merge]:
        """Each of ``numbers`` that GitHub answered for.

        Raises:
            RateLimited: If the GraphQL budget is spent.
        """

    @abstractmethod
    async def deploy(self, project: str, environment: str) -> Deploy | None:
        """The newest successful deploy to ``environment``, or ``None``."""

    @abstractmethod
    async def contains(
        self, project: str, merge_sha: str, deploy_sha: str
    ) -> bool | None:
        """Whether ``deploy_sha`` holds ``merge_sha``, or ``None`` when unread."""


class NoLandingSignals(LandingSignals):
    """Signals for a server with no GitHub: every read is unknown."""

    async def merges(self, project: str, numbers: list[int]) -> dict[int, Merge]:
        return {}

    async def deploy(self, project: str, environment: str) -> Deploy | None:
        return None

    async def contains(
        self, project: str, merge_sha: str, deploy_sha: str
    ) -> bool | None:
        return None


def status_of(
    task: TrackedTask, pr: PullRequest | None, config: MaelstromConfig
) -> str | None:
    """The highest step ``task`` has reached, or ``None`` before it is done."""
    reached = _reached(task, pr, config)
    return reached[-1] if reached else None


def _reached(
    task: TrackedTask, pr: PullRequest | None, config: MaelstromConfig
) -> list[str]:
    """Each step ``task`` has reached, in order.

    A step is reached only after the one before it. A deploy step the project
    does not deploy to is passed over, so a project with no ``deploy:`` block
    stops at ``merged``. An ``unknown`` environment is not reached.
    """
    if task.status != STATUS_DONE:
        return []
    reached = ["done"]
    if pr is None or not pr.merged_at:
        return reached
    reached.append("merged")
    for step in STEPS[2:]:
        if step not in config.deploy_environments:
            continue
        env = pr.envs.get(step)
        if env is None or env.state != LANDED:
            break
        reached.append(step)
    return reached


async def sync(
    prs: PullRequestStore,
    steps: TaskStepStore,
    signals: LandingSignals,
    tracked: Iterable[TrackedTask],
    config_for: Callable[[str], MaelstromConfig],
    now: str,
) -> list[StepEvent]:
    """Refresh each tracked task's pull request, then record the steps it reached.

    Recording is state-based: each step a task has reached and has no row for
    is written. So a server that was down catches up on its next tick, and no
    step is written twice. Returns the steps written.

    Raises:
        RateLimited: If the GraphQL budget is spent.
    """
    tracked = list(tracked)
    configs: dict[str, MaelstromConfig] = {}
    numbers: dict[str, set[int]] = {}
    for task in tracked:
        if task.project not in configs:
            configs[task.project] = config_for(task.project)
        if task.pr_number:
            numbers.setdefault(task.project, set()).add(task.pr_number)
    for project, project_numbers in numbers.items():
        await _refresh(
            prs, signals, project, sorted(project_numbers), configs[project], now
        )

    written: list[StepEvent] = []
    for task in tracked:
        pr = await prs.read(task.project, task.pr_number) if task.pr_number else None
        have = {e.step for e in await steps.for_task(task.project, task.task_id)}
        for step in _reached(task, pr, configs[task.project]):
            if step in have:
                continue
            event = StepEvent(
                project=task.project,
                task_id=task.task_id,
                step=step,
                pr_number=task.pr_number,
                at=_step_at(step, pr, now),
                recorded_at=now,
            )
            await steps.add(event)
            written.append(event)
    return written


def _step_at(step: str, pr: PullRequest | None, now: str) -> str:
    """When ``step`` happened: GitHub's time where it gives one, else ``now``."""
    if pr is not None and step == "merged":
        return pr.merged_at
    if pr is not None and step in pr.envs:
        return pr.envs[step].first_seen_at or now
    return now


def _merged(pr: PullRequest | None) -> bool:
    """Whether ``pr`` merged with a commit to compare. Until then the merge is re-read."""
    return pr is not None and bool(pr.merged_at) and bool(pr.merge_sha)


def _settled(pr: PullRequest | None, config: MaelstromConfig) -> bool:
    """Whether nothing about ``pr`` can move: closed unmerged, or landed everywhere."""
    if pr is not None and pr.state == "CLOSED":
        return True
    return (
        pr is not None
        and _merged(pr)
        and all(
            pr.envs.get(step, EnvLanding(UNKNOWN)).state == LANDED
            for step in config.deploy_environments
        )
    )


async def _refresh(
    prs: PullRequestStore,
    signals: LandingSignals,
    project: str,
    numbers: list[int],
    config: MaelstromConfig,
    now: str,
) -> None:
    """Re-read the unsettled pull requests among ``numbers``, and store them.

    See ``docs/dev/orchestrator-server.md``, "What a landing read costs".
    """
    stored = {n: await prs.read(project, n) for n in numbers}
    unsettled = {n: pr for n, pr in stored.items() if not _settled(pr, config)}
    if not unsettled:
        return

    unmerged = [n for n, pr in unsettled.items() if not _merged(pr)]
    if unmerged:
        merges = await signals.merges(project, unmerged)
        for number in unmerged:
            if (merge := merges.get(number)) is None:
                continue
            base = unsettled[number] or PullRequest(project=project, number=number)
            unsettled[number] = replace(
                base,
                url=merge.url,
                title=merge.title,
                merged_at=merge.merged_at,
                merge_sha=merge.merge_sha,
                state=merge.state,
            )

    refreshed = {n: pr for n, pr in unsettled.items() if pr is not None}
    for step, environment in config.deploy_environments.items():
        waiting = [
            n
            for n, pr in refreshed.items()
            if _merged(pr) and pr.envs.get(step, EnvLanding(UNKNOWN)).state != LANDED
        ]
        if not waiting:
            continue
        deploy = await signals.deploy(project, environment)
        for number in waiting:
            pr = refreshed[number]
            env = await _judge(signals, project, pr, pr.envs.get(step), deploy)
            refreshed[number] = replace(pr, envs={**pr.envs, step: env})

    for pr in refreshed.values():
        await prs.write(replace(pr, fetched_at=now))


async def _judge(
    signals: LandingSignals,
    project: str,
    pr: PullRequest,
    before: EnvLanding | None,
    deploy: Deploy | None,
) -> EnvLanding:
    """Whether ``deploy`` holds ``pr``'s merge, reusing a ``not_yet`` on the same sha."""
    if deploy is None:
        return EnvLanding(UNKNOWN, before.deploy_sha if before else "")
    if (
        before is not None
        and before.state == NOT_YET
        and before.deploy_sha == deploy.sha
    ):
        return before
    landed = await signals.contains(project, pr.merge_sha, deploy.sha)
    if landed is None:
        return EnvLanding(UNKNOWN, deploy.sha)
    if landed:
        return EnvLanding(LANDED, deploy.sha, deploy.created_at)
    return EnvLanding(NOT_YET, deploy.sha)


async def tracked_tasks(table: TaskTable, projects: Iterable[str]) -> list[TrackedTask]:
    """Every task in ``projects`` that has a **Registered PR**."""
    return [
        TrackedTask(project, task.id, task.status, task.pr_number)
        for project in projects
        for task in await table.list(project)
        if task.pr_number
    ]


def project_config(projects_dir: Path, project: str) -> MaelstromConfig:
    """``project``'s config from its ``_main``, or the default when it cannot be read.

    One broken ``.maelstrom.yaml`` must not stop the landing of every project.
    """
    try:
        return load_config_or_default(projects_dir / project / "_main")
    except (ValueError, yaml.YAMLError, OSError) as exc:
        log.warning("cannot read %s's config: %s", project, exc)
        return MaelstromConfig()


@dataclass(frozen=True)
class LandingRow:
    """One tracked task, its pull request, and the status they give."""

    task: TrackedTask
    pr: PullRequest | None
    status: str | None


async def _nothing_tracked() -> list[TrackedTask]:
    return []


@dataclass
class Landings:
    """Everything one sync needs: the stores, the reads, and what to follow.

    The defaults hold nothing and read nothing, for a test or a server with no
    state database.
    """

    prs: PullRequestStore = field(default_factory=InMemoryPullRequestStore)
    steps: TaskStepStore = field(default_factory=InMemoryTaskStepStore)
    signals: LandingSignals = field(default_factory=NoLandingSignals)
    tracked: Callable[[], Awaitable[list[TrackedTask]]] = _nothing_tracked
    config_for: Callable[[str], MaelstromConfig] = lambda project: MaelstromConfig()

    async def sync(self, now: str) -> list[StepEvent]:
        """One :func:`sync` over the tasks followed now.

        Raises:
            RateLimited: If the GraphQL budget is spent.
        """
        return await sync(
            self.prs,
            self.steps,
            self.signals,
            await self.tracked(),
            self.config_for,
            now,
        )

    async def landing_of(self, task: Task) -> TaskLanding | None:
        """``task``'s landing as the wire carries it, or ``None`` until done. Reads nothing from GitHub."""
        if task.status != STATUS_DONE:
            return None
        config = self.config_for(task.project)
        tracked = TrackedTask(task.project, task.id, task.status, task.pr_number)
        pr = (
            await self.prs.read(task.project, task.pr_number)
            if task.pr_number
            else None
        )
        envs: dict[str, EnvLandingState] = {}
        if task.pr_number:
            for step in DEPLOY_STEPS:
                if step not in config.deploy_environments:
                    continue
                if pr is None or not pr.merged_at:
                    envs[step] = NOT_YET
                else:
                    envs[step] = pr.envs.get(step, EnvLanding(UNKNOWN)).state  # type: ignore[assignment]
        return {"status": status_of(tracked, pr, config) or "done", "envs": envs}

    async def report(self) -> list[LandingRow]:
        """Each followed task, as the last sync left it. Reads nothing from GitHub."""
        configs: dict[str, MaelstromConfig] = {}
        rows: list[LandingRow] = []
        for task in await self.tracked():
            if task.project not in configs:
                configs[task.project] = self.config_for(task.project)
            pr = (
                await self.prs.read(task.project, task.pr_number)
                if task.pr_number
                else None
            )
            rows.append(
                LandingRow(task, pr, status_of(task, pr, configs[task.project]))
            )
        return rows
