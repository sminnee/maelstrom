"""The finished GitHub Actions runs a template's trigger reads.

A trigger is ``gh-action/<workflow>[@<branch>] [<conclusion>,...]``. The
workflow is its file name, such as ``nightly.yml``, because the runs API
addresses a workflow by file. The branch defaults to ``main``, and the
conclusions to :data:`FAILED`.

Everything here is pure. The ``gh`` transport is
:class:`~mael_domain.build_runs_github.GhBuildRuns`.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

#: The trigger kind for a GitHub Actions workflow.
KIND = "gh-action"

#: The conclusions the alias ``failed`` stands for, and the default list.
FAILED = ("failure", "timed_out", "startup_failure")

#: How many completed runs one read looks back through.
_RUNS_PAGE = 10


@dataclass(frozen=True)
class BuildRun:
    """One completed workflow run."""

    id: int
    conclusion: str
    completed_at: datetime
    url: str
    head_sha: str


@dataclass(frozen=True)
class BuildTrigger:
    """A parsed ``gh-action/`` trigger: which runs it reads, and which fire."""

    workflow: str
    branch: str
    conclusions: frozenset[str]


def parse_trigger(value: str) -> BuildTrigger | None:
    """The trigger ``value`` names, or ``None`` when it does not parse."""
    kind, sep, spec = value.strip().partition("/")
    if kind != KIND or not sep:
        return None
    parts = spec.split()
    if not parts or len(parts) > 2:
        return None
    workflow, at, branch = parts[0].partition("@")
    if not workflow or (at and not branch):
        return None
    conclusions: set[str] = set()
    for name in (parts[1] if len(parts) == 2 else "failed").split(","):
        if not name:
            return None
        conclusions.update(FAILED if name == "failed" else (name,))
    return BuildTrigger(workflow, branch or "main", frozenset(conclusions))


def runs_path(trigger: BuildTrigger) -> str:
    """The ``gh api`` path that lists the trigger's completed runs."""
    workflow = quote(trigger.workflow, safe="")
    branch = quote(trigger.branch, safe="")
    return (
        f"repos/:owner/:repo/actions/workflows/{workflow}/runs"
        f"?branch={branch}&status=completed&per_page={_RUNS_PAGE}"
    )


def parse_runs(payload: str) -> list[BuildRun]:
    """The completed runs in a ``workflow_runs`` answer, newest first.

    A row with no conclusion has not finished, and is left out. A completed run
    has no ``completed_at`` field: its ``updated_at`` is when it finished.
    """
    runs = [
        BuildRun(
            id=int(row["id"]),
            conclusion=str(row["conclusion"]),
            completed_at=datetime.fromisoformat(row["updated_at"]),
            url=str(row.get("html_url") or ""),
            head_sha=str(row.get("head_sha") or ""),
        )
        for row in json.loads(payload).get("workflow_runs") or []
        if row.get("conclusion")
    ]
    return sorted(runs, key=lambda run: run.completed_at, reverse=True)


def run_section(trigger: BuildTrigger, run: BuildRun) -> str:
    """The ``## Build run`` section a fired run's content gains."""
    return (
        "## Build run\n\n"
        f"{trigger.workflow} on {trigger.branch}: {run.conclusion}. "
        f"{run.url} — commit {run.head_sha[:7]}."
        + (
            ""
            if run.conclusion == "success"
            else f"\nRead the log: `mael gh check-log {run.id} --failed-only`."
        )
    )


class BuildRuns(ABC):
    """Where the completed runs of a trigger come from."""

    @abstractmethod
    async def completed(
        self, project: str, trigger: BuildTrigger
    ) -> list[BuildRun] | None:
        """The trigger's completed runs, newest first, or ``None`` when the read failed."""
