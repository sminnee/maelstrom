"""The orchestrator's one door onto Linear.

Everything the server knows about Linear is here. Both functions block — they
reach the Linear API — so the server runs them on its executor, as it does every
other blocking source call.

The Linear integration is expected to go once the task notebook covers the same
ground. Keeping it to this module and two routes is what makes that a deletion
rather than an unpicking.
"""

from typing import Any

from ..config import linear_team_id
from ..context import load_global_config
from ..integrations.linear import build_plan_task, fetch_cycle_issues
from ..worktree import list_worktrees


def _team_id(project: str) -> str | None:
    """``project``'s configured Linear team, or ``None`` if it names none."""
    project_path = load_global_config().projects_dir / project
    return linear_team_id(
        project_path, [wt.path for wt in list_worktrees(project_path)]
    )


def cycle_issues(project: str) -> list[dict[str, Any]]:
    """``project``'s Linear issues for the current cycle.

    The raw issue nodes, as the CLI's ``list-tasks`` sees them. The route turns
    them into the wire rows; this keeps the Linear shape in one place.
    """
    team_id = _team_id(project)
    if not team_id:
        raise ValueError(f"{project} names no Linear team")
    return fetch_cycle_issues(team_id)


def plan_fields(
    project: str, issue_id: str, branch: str | None = None
) -> dict[str, Any]:
    """The task fields that plan ``issue_id`` — what ``mael linear plan`` writes.

    ``branch`` names the branch rather than generating one — see
    :func:`maelstrom.integrations.linear.build_plan_task`.
    """
    return build_plan_task(issue_id, project, branch=branch)
