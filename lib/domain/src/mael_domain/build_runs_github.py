"""The ``gh`` transport behind :class:`~mael_domain.build_runs.BuildRuns`.

One :func:`~mael_domain.github.gh_api` read in the project's ``_main``.
"""

import logging
from pathlib import Path

from .build_runs import BuildRun, BuildRuns, BuildTrigger, parse_runs, runs_path
from .github import gh_api

log = logging.getLogger(__name__)


class GhBuildRuns(BuildRuns):
    """The runs read, through ``gh`` in each project's ``_main``."""

    def __init__(self, projects_dir: Path) -> None:
        self._projects_dir = projects_dir

    async def completed(
        self, project: str, trigger: BuildTrigger
    ) -> list[BuildRun] | None:
        payload = await gh_api(
            self._projects_dir / project / "_main", runs_path(trigger)
        )
        if payload is None:
            return None
        try:
            return parse_runs(payload)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("build run read failed in %s: %s", project, exc)
            return None
