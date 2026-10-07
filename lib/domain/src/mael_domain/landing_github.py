"""The GitHub reads behind a landing: merge, deploy and ancestry.

The parsers are pure and take the raw payload. :class:`GhLandingSignals` is the
transport: each read is ``gh api`` run in the project's ``_main``, so ``gh``
fills ``:owner/:repo``.

Merges are rebase merges, so a PR's head commit is never on main. The merge
commit is: that is the sha the ancestry read compares.
"""

import json
import logging
from pathlib import Path
from urllib.parse import quote

from mael_common.shell import run_cmd_async

from .github_model import RateLimited, is_rate_limit
from .landing import Deploy, LandingSignals, Merge

log = logging.getLogger(__name__)

#: How many deployments one read looks back through for a successful one. Each
#: costs a statuses call, so a run of failed deploys costs one plus this many.
_DEPLOYMENTS_PAGE = 5


def merges_query(numbers: list[int]) -> tuple[str, dict[str, int]]:
    """One GraphQL document asking about each of ``numbers``, and alias -> number."""
    aliases = {f"p{number}": number for number in numbers}
    fields = "\n".join(
        f"    {alias}: pullRequest(number: {number}) "
        "{ url title state mergedAt mergeCommit { oid } }"
        for alias, number in aliases.items()
    )
    query = (
        "query($owner: String!, $repo: String!) {\n"
        "  repository(owner: $owner, name: $repo) {\n"
        f"{fields}\n"
        "  }\n"
        "}\n"
    )
    return query, aliases


def parse_merges(payload: str, aliases: dict[str, int]) -> dict[int, Merge]:
    """Each pull request the merge query answered for, by number.

    A pull request GitHub did not answer for is left out, which reads as
    unknown.

    Raises:
        RateLimited: If the payload has no data because the budget is spent.
        ValueError: If the payload has no data for any other reason, or is not JSON.
    """
    data = json.loads(payload)
    repository = (data.get("data") or {}).get("repository")
    if repository is None:
        errors = data.get("errors") or []
        if is_rate_limit(errors):
            raise RateLimited(f"GraphQL rate limit: {errors}")
        raise ValueError(f"GraphQL query failed: {errors}")
    merges: dict[int, Merge] = {}
    for alias, number in aliases.items():
        node = repository.get(alias)
        if not node:
            continue
        merges[number] = Merge(
            url=node.get("url") or "",
            title=node.get("title") or "",
            merged_at=node.get("mergedAt") or "",
            merge_sha=(node.get("mergeCommit") or {}).get("oid") or "",
            state=node.get("state") or "",
        )
    return merges


def parse_deployments(payload: str) -> list[tuple[int, str]]:
    """``(id, sha)`` of each deployment in the list, newest first as GitHub gives them."""
    return [(int(d["id"]), str(d["sha"])) for d in json.loads(payload)]


def parse_success(payload: str) -> str | None:
    """The newest status's time when it is ``success``, else ``None``."""
    statuses = json.loads(payload)
    if statuses and statuses[0].get("state") == "success":
        return statuses[0].get("created_at") or None
    return None


def parse_compare(payload: str) -> bool | None:
    """Whether ``compare/<merge>...<deploy>`` says the deploy holds the merge.

    ``ahead`` or ``identical`` is yes, ``behind`` or ``diverged`` is no, and
    anything else is unknown.
    """
    try:
        status = json.loads(payload).get("status")
    except (json.JSONDecodeError, AttributeError):
        return None
    if status in ("ahead", "identical"):
        return True
    if status in ("behind", "diverged"):
        return False
    return None


class GhLandingSignals(LandingSignals):
    """The landing reads, through ``gh`` in each project's ``_main``."""

    def __init__(self, projects_dir: Path) -> None:
        self._projects_dir = projects_dir

    async def _gh(
        self, project: str, *args: str, payload_decides: bool = False
    ) -> str | None:
        """``gh api`` output, or ``None`` when the read failed.

        ``payload_decides`` keeps the output of a non-zero exit: gh exits 1 on a
        GraphQL payload that is complete apart from one refused field.
        """
        try:
            result = await run_cmd_async(
                ["gh", "api", *args],
                cwd=self._projects_dir / project / "_main",
                quiet=True,
                check=False,
            )
        except OSError as exc:
            log.warning("gh api %s failed in %s: %s", args[0], project, exc)
            return None
        if result.returncode != 0 and not payload_decides:
            log.info("gh api %s refused in %s: %s", args[0], project, result.stdout)
            return None
        return result.stdout

    async def merges(self, project: str, numbers: list[int]) -> dict[int, Merge]:
        query, aliases = merges_query(numbers)
        payload = await self._gh(
            project,
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            "owner=:owner",
            "-F",
            "repo=:repo",
            payload_decides=True,
        )
        if payload is None:
            return {}
        try:
            return parse_merges(payload, aliases)
        except RateLimited:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("merge read failed in %s: %s", project, exc)
            return {}

    async def deploy(self, project: str, environment: str) -> Deploy | None:
        listed = await self._gh(
            project,
            f"repos/:owner/:repo/deployments?environment={quote(environment, safe='')}"
            f"&ref=main&per_page={_DEPLOYMENTS_PAGE}",
        )
        if listed is None:
            return None
        try:
            deployments = parse_deployments(listed)
        except (ValueError, KeyError, TypeError):
            return None
        for deployment_id, sha in deployments:
            statuses = await self._gh(
                project,
                f"repos/:owner/:repo/deployments/{deployment_id}/statuses?per_page=1",
            )
            if statuses is None:
                return None
            try:
                created_at = parse_success(statuses)
            except (ValueError, AttributeError):
                return None
            if created_at is not None:
                return Deploy(sha=sha, created_at=created_at)
        return None

    async def contains(
        self, project: str, merge_sha: str, deploy_sha: str
    ) -> bool | None:
        answer = await self._gh(
            project, f"repos/:owner/:repo/compare/{merge_sha}...{deploy_sha}"
        )
        return parse_compare(answer) if answer is not None else None
