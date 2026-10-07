"""Where landings are kept: GitHub's data per pull request, and each step a task reached.

See ``CONTEXT.md``, "Landing". :mod:`mael_domain.landing` decides what to
write; this module only stores it.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .config import DEPLOY_STEPS
from .state_db.db import StateDb

#: A task's status past done, in order. The index is the rank.
STEPS = ("done", "merged", *DEPLOY_STEPS)

PULL_REQUESTS = "pull_requests"
TASK_STEPS = "task_steps"


@dataclass(frozen=True)
class EnvLanding:
    """Whether a pull request's merge has reached one deploy environment.

    ``state`` is ``landed``, ``not_yet`` or ``unknown``. ``first_seen_at`` is
    the time of the deploy in which the server first saw the merge.
    """

    state: str
    deploy_sha: str = ""
    first_seen_at: str = ""


@dataclass(frozen=True)
class PullRequest:
    """GitHub's answer about one pull request. ``merged_at`` is empty until it merges.

    ``state`` is GitHub's: ``OPEN``, ``CLOSED`` or ``MERGED``.
    """

    project: str
    number: int
    url: str = ""
    title: str = ""
    merged_at: str = ""
    merge_sha: str = ""
    state: str = ""
    #: Deploy step -> how far the merge got there.
    envs: dict[str, EnvLanding] = field(default_factory=dict)
    fetched_at: str = ""


@dataclass(frozen=True)
class StepEvent:
    """One step a task reached.

    ``at`` is when the step happened. ``recorded_at`` is when this row was
    written, which is what a flush compares.
    """

    project: str
    task_id: str
    step: str
    pr_number: int
    at: str
    recorded_at: str


def _pr_id(project: str, number: int) -> str:
    return f"{project}/{number}"


def _step_id(event: StepEvent) -> str:
    return f"{event.project}/{event.task_id}/{event.step}"


def _event_order(event: StepEvent) -> tuple[str, int]:
    return event.recorded_at, STEPS.index(event.step)


class PullRequestStore(ABC):
    """Where GitHub's data about each pull request is kept."""

    @abstractmethod
    async def read(self, project: str, number: int) -> PullRequest | None:
        """The stored pull request, or ``None`` when nothing has read it."""

    @abstractmethod
    async def write(self, pr: PullRequest) -> None:
        """Store ``pr``, stamped with its ``fetched_at``."""


class TaskStepStore(ABC):
    """Where the steps each task reached are kept."""

    @abstractmethod
    async def for_task(self, project: str, task_id: str) -> list[StepEvent]:
        """The steps one task has reached."""

    @abstractmethod
    async def add(self, event: StepEvent) -> None:
        """Record a step. A step is recorded once per task."""

    @abstractmethod
    async def list(self, since: str = "") -> list[StepEvent]:
        """Every step recorded after ``since``, oldest first."""


class InMemoryPullRequestStore(PullRequestStore):
    """Pull requests for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, PullRequest] = {}

    async def read(self, project: str, number: int) -> PullRequest | None:
        return self._rows.get(_pr_id(project, number))

    async def write(self, pr: PullRequest) -> None:
        self._rows[_pr_id(pr.project, pr.number)] = pr


class InMemoryTaskStepStore(TaskStepStore):
    """Task steps for a test or a server with no state database."""

    def __init__(self) -> None:
        self._rows: dict[str, StepEvent] = {}

    async def for_task(self, project: str, task_id: str) -> list[StepEvent]:
        return [
            e
            for e in self._rows.values()
            if (e.project, e.task_id) == (project, task_id)
        ]

    async def add(self, event: StepEvent) -> None:
        self._rows.setdefault(_step_id(event), event)

    async def list(self, since: str = "") -> list[StepEvent]:
        return sorted(
            (e for e in self._rows.values() if e.recorded_at > since), key=_event_order
        )


class SqlitePullRequestStore(PullRequestStore):
    """Pull requests in the state database's cached ``pull_requests`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def read(self, project: str, number: int) -> PullRequest | None:
        row = await self._db.read(PULL_REQUESTS, _pr_id(project, number))
        return _pr_from_row(row) if row is not None else None

    async def write(self, pr: PullRequest) -> None:
        await self._db.upsert(
            PULL_REQUESTS,
            _pr_id(pr.project, pr.number),
            fetched_at=pr.fetched_at or None,
            project=pr.project,
            number=pr.number,
            url=pr.url,
            title=pr.title,
            merged_at=pr.merged_at,
            merge_sha=pr.merge_sha,
            state=pr.state,
            envs=json.dumps(
                {
                    step: {
                        "state": env.state,
                        "deploy_sha": env.deploy_sha,
                        "first_seen_at": env.first_seen_at,
                    }
                    for step, env in sorted(pr.envs.items())
                }
            ),
        )


class SqliteTaskStepStore(TaskStepStore):
    """Task steps in the state database's canonical ``task_steps`` table."""

    def __init__(self, db: StateDb) -> None:
        self._db = db

    async def for_task(self, project: str, task_id: str) -> list[StepEvent]:
        rows = await self._db.read_where(TASK_STEPS, "task_id", task_id)
        return [_step_from_row(r) for r in rows if r["project"] == project]

    async def add(self, event: StepEvent) -> None:
        if await self._db.read(TASK_STEPS, _step_id(event)) is not None:
            return
        await self._db.upsert(
            TASK_STEPS,
            _step_id(event),
            project=event.project,
            task_id=event.task_id,
            step=event.step,
            pr_number=event.pr_number,
            at=event.at,
            recorded_at=event.recorded_at,
        )

    async def list(self, since: str = "") -> list[StepEvent]:
        events = [_step_from_row(r) for r in await self._db.read_all(TASK_STEPS)]
        return sorted((e for e in events if e.recorded_at > since), key=_event_order)


def _pr_from_row(row: Any) -> PullRequest:
    return PullRequest(
        project=row["project"],
        number=row["number"],
        url=row["url"],
        title=row["title"],
        merged_at=row["merged_at"],
        merge_sha=row["merge_sha"],
        state=row["state"],
        envs={step: EnvLanding(**env) for step, env in json.loads(row["envs"]).items()},
        fetched_at=row["fetched_at"] or "",
    )


def _step_from_row(row: Any) -> StepEvent:
    return StepEvent(
        project=row["project"],
        task_id=row["task_id"],
        step=row["step"],
        pr_number=row["pr_number"],
        at=row["at"],
        recorded_at=row["recorded_at"],
    )
