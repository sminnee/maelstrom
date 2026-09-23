"""Linear task management integration for maelstrom."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import load_config_or_default
from ..context import resolve_context
from ._auth import resolve_secret
from ._http import request_bytes, request_json
from .errors import IntegrationError

LINEAR_API_URL = "https://api.linear.app/graphql"

# Matches a markdown image whose target is an auth-gated Linear upload, e.g.
# ``![image.png](https://uploads.linear.app/7b3f.../32ea...)``. Group 1 is the
# alt text, group 2 the URL. Only description images from this host are
# localized — comments and attachments are out of scope.
_LINEAR_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https://uploads\.linear\.app/[^)]+)\)")


def get_linear_api_key() -> str:
    """Get the Linear API key.

    Checks in order:
    1. LINEAR_API_KEY environment variable
    2. LINEAR_API_KEY in .env file
    3. linear.api_key in ~/.maelstrom/config.yaml

    Raises:
        IntegrationError: If the key is not found.
    """
    key = resolve_secret("LINEAR_API_KEY", config_attr="linear_api_key")
    if key:
        return key

    raise IntegrationError(
        "LINEAR_API_KEY not found. Set it via:\n"
        "  - Environment variable: export LINEAR_API_KEY=lin_api_xxx\n"
        "  - Project .env file: LINEAR_API_KEY=lin_api_xxx\n"
        "  - Global config ~/.maelstrom/config.yaml:\n"
        "      linear:\n"
        "        api_key: lin_api_xxx"
    )


def get_team_id() -> str:
    """Get the Linear team ID from config."""
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=False)
        if ctx.worktree_path:
            config = load_config_or_default(ctx.worktree_path)
            if config.linear_team_id:
                return config.linear_team_id
    except ValueError:
        pass

    # Try loading from cwd
    config = load_config_or_default(Path.cwd())
    if config.linear_team_id:
        return config.linear_team_id

    # Fetch available teams to show in error message
    teams_info = _fetch_teams_for_error()
    raise IntegrationError(
        f"linear.team_id not configured. Add to .maelstrom.yaml:\n"
        f"  linear:\n"
        f'    team_id: "<team-uuid>"\n\n'
        f"Available teams:\n{teams_info}"
    )


def _fetch_teams_for_error() -> str:
    """Fetch teams from Linear API for error message."""
    try:
        query = """
        query {
            teams {
                nodes {
                    id
                    name
                    key
                }
            }
        }
        """
        result = graphql_request(query)
        lines = []
        for team in result["teams"]["nodes"]:
            lines.append(f"  {team['key']}: {team['name']}\n    -> {team['id']}")
        return "\n".join(lines) if lines else "  (no teams found)"
    except Exception:
        return "  (could not fetch teams - check your API key)"


def graphql_request(query: str, variables: dict | None = None) -> dict:
    """Make a GraphQL request to Linear API.

    Args:
        query: GraphQL query string.
        variables: Optional query variables.

    Returns:
        The response data.

    Raises:
        IntegrationError: On API errors.
    """
    api_key = get_linear_api_key()

    payload: dict[str, str | dict] = {"query": query}
    if variables:
        payload["variables"] = variables

    result = request_json(
        LINEAR_API_URL,
        method="POST",
        headers={"Authorization": api_key},
        json_body=payload,
    )
    if "errors" in result:
        raise IntegrationError(f"GraphQL errors: {result['errors']}")
    return result["data"]


def graphql_paginated(
    query: str,
    variables: dict | None = None,
    *,
    connection: str,
    page_size: int = 100,
    max_pages: int = 100,
) -> list[dict]:
    """Fetch every node of a top-level connection, following ``pageInfo`` cursors.

    Linear returns only the first 50 nodes when a query passes no ``first:``
    argument, so any query that can match more than that must paginate.

    Args:
        query: GraphQL query declaring ``$first: Int`` / ``$after: String`` and
            selecting ``pageInfo { hasNextPage endCursor }`` on the connection.
        variables: Optional query variables (``first``/``after`` are added here).
        connection: Name of the top-level connection field, e.g. ``"issues"``.
        page_size: Nodes to request per page. Linear caps this at 250; the
            default of 100 stays comfortably inside that.
        max_pages: Defensive cap so a server that never clears ``hasNextPage``
            cannot spin forever.

    Returns:
        All nodes across every page.

    Raises:
        IntegrationError: If the page cap is hit with pages still pending.
    """
    nodes: list[dict] = []
    cursor: str | None = None

    for _ in range(max_pages):
        page_vars = dict(variables or {})
        page_vars["first"] = page_size
        page_vars["after"] = cursor

        result = graphql_request(query, page_vars)
        page = result[connection]
        nodes.extend(page["nodes"])

        page_info = page.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")

    raise IntegrationError(
        f"Pagination exceeded {max_pages} pages fetching '{connection}' "
        f"({len(nodes)} nodes so far) — aborting."
    )


def get_current_cycle(team_id: str | None = None) -> dict | None:
    """Get the current cycle for ``team_id``, defaulting to the configured team.

    A caller that already knows the team passes it. The orchestrator does: it is
    one long-lived process serving every project, so the team resolved from its
    own cwd would be some other project's.
    """
    team_id = team_id or get_team_id()
    query = """
    query GetCurrentCycle($teamId: String!) {
        team(id: $teamId) {
            activeCycle {
                id
                name
                number
            }
        }
    }
    """
    result = graphql_request(query, {"teamId": team_id})
    return result["team"]["activeCycle"]


def get_issue(issue_id: str) -> dict:
    """Get full issue details by ID.

    Args:
        issue_id: Linear issue identifier (e.g., NORT-123).

    Returns:
        Issue data dictionary.

    Raises:
        IntegrationError: If issue not found.
    """
    query = """
    query GetIssue($id: String!) {
        issue(id: $id) {
            id
            identifier
            title
            description
            state {
                id
                name
                type
            }
            parent {
                id
                identifier
                title
            }
            children {
                nodes {
                    id
                    identifier
                    title
                    state {
                        id
                        name
                        type
                    }
                }
            }
            labels {
                nodes {
                    id
                    name
                }
            }
            cycle {
                id
                name
                number
            }
            comments {
                nodes {
                    id
                    body
                    user {
                        name
                        displayName
                    }
                    createdAt
                }
            }
            attachments {
                nodes {
                    id
                    url
                    title
                    sourceType
                }
            }
        }
    }
    """
    result = graphql_request(query, {"id": issue_id})
    if not result.get("issue"):
        raise IntegrationError(f"Issue {issue_id} not found")
    return result["issue"]


def get_workflow_states() -> dict[str, str]:
    """Get workflow states for the team, returning a map of state name to ID."""
    team_id = get_team_id()
    query = """
    query GetWorkflowStates($teamId: String!) {
        team(id: $teamId) {
            states {
                nodes {
                    id
                    name
                    type
                }
            }
        }
    }
    """
    result = graphql_request(query, {"teamId": team_id})
    return {state["name"]: state["id"] for state in result["team"]["states"]["nodes"]}


def get_labels() -> dict[str, str]:
    """Get all labels, returning a map of label name to ID."""
    team_id = get_team_id()
    query = """
    query GetLabels($teamId: String!) {
        team(id: $teamId) {
            labels {
                nodes {
                    id
                    name
                }
            }
        }
    }
    """
    result = graphql_request(query, {"teamId": team_id})
    return {label["name"]: label["id"] for label in result["team"]["labels"]["nodes"]}


def update_issue(issue_id: str, **kwargs) -> None:
    """Update an issue with the given fields.

    Args:
        issue_id: The issue's internal ID.
        **kwargs: Fields to update (stateId, labelIds, description, etc.).

    Raises:
        IntegrationError: If update fails.
    """
    mutation = """
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
        issueUpdate(id: $id, input: $input) {
            success
        }
    }
    """
    result = graphql_request(mutation, {"id": issue_id, "input": kwargs})
    if not result["issueUpdate"]["success"]:
        raise IntegrationError("Failed to update issue")


def create_issue(
    title: str,
    parent_id: str | None = None,
    description: str = "",
    cycle_id: str | None = None,
    state_id: str | None = None,
    label_ids: list[str] | None = None,
) -> dict:
    """Create a new issue, optionally as a subtask.

    Args:
        title: Issue title.
        parent_id: Optional parent issue's internal ID (makes it a subtask).
        description: Optional description.
        cycle_id: Optional cycle ID (inherits from parent if not specified).
        state_id: Optional workflow state ID.
        label_ids: Optional list of label IDs to apply.

    Returns:
        Created issue data with id, identifier, and title.

    Raises:
        IntegrationError: If creation fails.
    """
    team_id = get_team_id()
    mutation = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue {
                id
                identifier
                title
            }
        }
    }
    """
    input_data: dict[str, str | list[str]] = {
        "title": title,
        "teamId": team_id,
    }
    if parent_id:
        input_data["parentId"] = parent_id
    if description:
        input_data["description"] = description
    if cycle_id:
        input_data["cycleId"] = cycle_id
    if state_id:
        input_data["stateId"] = state_id
    if label_ids:
        input_data["labelIds"] = label_ids

    result = graphql_request(mutation, {"input": input_data})
    if not result["issueCreate"]["success"]:
        raise IntegrationError("Failed to create issue")
    return result["issueCreate"]["issue"]


def create_comment(issue_id: str, body: str) -> dict:
    """Create a comment on an issue.

    Args:
        issue_id: The issue's internal ID.
        body: Markdown body of the comment.

    Returns:
        Created comment data with id.

    Raises:
        IntegrationError: If creation fails.
    """
    mutation = """
    mutation CreateComment($input: CommentCreateInput!) {
        commentCreate(input: $input) {
            success
            comment {
                id
            }
        }
    }
    """
    input_data = {
        "issueId": issue_id,
        "body": body,
    }

    result = graphql_request(mutation, {"input": input_data})
    if not result["commentCreate"]["success"]:
        raise IntegrationError("Failed to create comment")
    return result["commentCreate"]["comment"]


def detect_workspace_label() -> str | None:
    """Detect workspace label from current worktree name.

    Returns:
        Worktree name (e.g., 'alpha', 'bravo') or None if not in a worktree.
    """
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=True)
        return ctx.worktree
    except ValueError:
        return None


def get_product_label() -> str | None:
    """Get the configured product label from project config.

    Returns:
        Product label name or None if not configured.
    """
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=False)
        if ctx.worktree_path:
            config = load_config_or_default(ctx.worktree_path)
            if config.linear_product_label:
                return config.linear_product_label
    except ValueError:
        pass

    config = load_config_or_default(Path.cwd())
    return config.linear_product_label


def ensure_product_label(
    issue_id: str, labels_map: dict[str, str], current_label_names: list[str]
) -> list[str] | None:
    """Add product label to an issue's label list if configured and not already present.

    Args:
        issue_id: The issue's internal ID (for update).
        labels_map: Map of label name to ID.
        current_label_names: Current label names on the issue.

    Returns:
        Updated label IDs list if product label was added, None if no change needed.
    """
    product_label = get_product_label()
    if not product_label or product_label in current_label_names:
        return None

    if product_label not in labels_map:
        return None

    new_label_names = current_label_names + [product_label]
    return [labels_map[name] for name in new_label_names if name in labels_map]


def get_workspace_labels() -> list[str]:
    """Get configured workspace labels or default to common worktree names.

    Returns:
        List of valid workspace label names.
    """
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=False)
        if ctx.worktree_path:
            config = load_config_or_default(ctx.worktree_path)
            if config.linear_workspace_labels:
                return config.linear_workspace_labels
    except ValueError:
        pass

    # Default to NATO phonetic alphabet names used by maelstrom
    return [
        "alpha",
        "bravo",
        "charlie",
        "delta",
        "echo",
        "foxtrot",
        "golf",
        "hotel",
        "india",
        "juliet",
        "kilo",
        "lima",
        "mike",
        "november",
        "oscar",
        "papa",
        "quebec",
        "romeo",
        "sierra",
        "tango",
        "uniform",
        "victor",
        "whiskey",
        "xray",
        "yankee",
        "zulu",
    ]


def fetch_cycle_issues(team_id: str, status: str | None = None) -> list[dict]:
    """The team's issues for its current cycle, or its active ones with no cycle.

    Returns the raw issue nodes — ``identifier``, ``title``, ``state`` and
    ``parent`` — so the CLI can format them and the orchestrator can offer them
    without either owning the query. Paginated: a cycle can hold more than one
    page of issues.
    """
    cycle = get_current_cycle(team_id)

    if cycle:
        query = """
        query ListIssues($teamId: ID!, $cycleId: ID!, $status: String, $first: Int, $after: String) {
            issues(
                filter: {
                    team: { id: { eq: $teamId } }
                    cycle: { id: { eq: $cycleId } }
                    state: { name: { containsIgnoreCase: $status } }
                }
                orderBy: updatedAt
                first: $first
                after: $after
            ) {
                nodes {
                    identifier
                    title
                    state {
                        name
                        type
                    }
                    parent {
                        identifier
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        variables = {"teamId": team_id, "cycleId": cycle["id"], "status": status or ""}
    else:
        # No active cycle — show all non-backlog, non-done tasks.
        query = """
        query ListActiveIssues($teamId: ID!, $status: String, $first: Int, $after: String) {
            issues(
                filter: {
                    team: { id: { eq: $teamId } }
                    state: {
                        name: { containsIgnoreCase: $status }
                        type: { nin: ["backlog", "completed", "canceled"] }
                    }
                }
                orderBy: updatedAt
                first: $first
                after: $after
            ) {
                nodes {
                    identifier
                    title
                    state {
                        name
                        type
                    }
                    parent {
                        identifier
                    }
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
        """
        variables = {"teamId": team_id, "status": status or ""}

    return graphql_paginated(query, variables, connection="issues")


def localize_description_images(
    identifier: str, project: str, description: str, *, warn: Callable[[str], None]
) -> str:
    """Download ``uploads.linear.app`` images and rewrite refs to a portable token.

    Scans ``description`` for ``![alt](https://uploads.linear.app/...)`` refs.
    Each unique URL is downloaded with the Linear API key (the URLs are
    auth-gated) and written into the git-backed task repo under
    ``images/<identifier>/``; the ref's target is rewritten to a
    ``{{MAEL_TASK_DIR}}/images/<identifier>/<file>`` token that
    :func:`maelstrom.task.build_prompt` expands to an absolute path at launch.

    The image files are left untracked on disk — the caller's subsequent
    ``add_task`` commit sweeps them in via ``git add -A``.

    A single failed download (an HTTP error such as a 404 or a revoked key)
    goes to ``warn`` and that one ref is left as the original URL, so one bad
    image never aborts the whole plan. Alt text is preserved. A description with no matching images is
    returned unchanged and writes nothing.
    """
    from ..attachments import markdown_ref, save_attachment

    matches = list(_LINEAR_IMAGE_RE.finditer(description))
    if not matches:
        return description

    # url -> the token that replaces it (cached so a URL used twice = one file).
    replacements: dict[str, str] = {}

    for match in matches:
        url = match.group(2)
        if url in replacements:
            continue
        alt = match.group(1)
        try:
            data = request_bytes(url, headers={"Authorization": get_linear_api_key()})
        except IntegrationError as e:
            warn(
                f"warning: could not download image {url}: {e}; "
                "leaving the original URL in the brief"
            )
            continue

        # Name the file after the URL's last segment (a UUID) so re-localizing a
        # brief keeps stable filenames; the alt text backs up the extension
        # sniff. Writing, de-duping and minting the token are shared with every
        # other way an image reaches a task — see ``maelstrom.attachments``.
        stem = url.rstrip("/").rsplit("/", 1)[-1] or "image"
        hint = f"{stem}{Path(alt).suffix}"
        try:
            replacements[url] = save_attachment(project, identifier, data, name=hint)
        except ValueError as e:
            # An SVG, an oversized image, or an HTML error body served with
            # HTTP 200. Treated like a failed download: one bad image must not
            # abort the whole plan.
            warn(
                f"warning: could not store image {url}: {e}; "
                "leaving the original URL in the brief"
            )
            continue

    def _rewrite(match: "re.Match[str]") -> str:
        url = match.group(2)
        token = replacements.get(url)
        if token is None:
            return match.group(0)  # download failed — keep original.
        return markdown_ref(match.group(1), token)

    return _LINEAR_IMAGE_RE.sub(_rewrite, description)


def build_plan_task(
    issue_id: str,
    project: str,
    *,
    branch: str | None = None,
    warn: Callable[[str], None],
) -> dict[str, Any]:
    """The task fields that plan ``issue_id``, without creating the task.

    Fetches the issue, localizes its images into ``project``'s task repo, and
    returns the ``add_task`` kwargs ``mael linear plan`` uses. The values here
    are the planning *defaults*; ``cmd_plan`` lets its own flags override them,
    and the orchestrator takes them as they come.

    ``branch`` names the branch rather than generating one. Generation shells
    out to ``claude -p``, so a caller that has already inferred a branch — the
    orchestrator does — passes it here and spares the second model call.
    ``warn`` takes the line for each image that could not be localized.
    """
    from .. import branch_name

    issue = get_issue(issue_id)
    identifier = issue["identifier"]
    title = issue.get("title") or ""
    description = issue.get("description") or ""

    description = localize_description_images(
        identifier, project, description, warn=warn
    )

    # The meaningful title/description live on the *issue*, not on the "Plan
    # NORT-123" task, so the descriptive branch is computed from them. The bare
    # issue number leads the desc, and the branch is shared by all children of
    # this parent. An explicit '' falls through to create()'s own
    # sibling/parent inheritance, same as on `task add`.
    resolved_branch = (
        branch
        if branch is not None
        else branch_name.generate_branch_name(
            title, description, prefix=identifier.split("-")[-1]
        )
    )

    return {
        "title": f"Plan {identifier}",
        "command": "plan-task",
        # Planning runs in normal mode: the session's deliverable is draft task
        # files, and the plan-task skill (not plan mode) forbids code edits.
        "mode": "normal",
        # Planning runs on Opus by default: the plan is the leverage point, and
        # the sessions the chain goes on to launch inherit their own model.
        "model": "opus",
        "parent": f"linear.{identifier}",
        "branch": resolved_branch,
        # Finishing the planning session moves the Linear issue to Planned.
        "post_action": "linear.planned",
        "content": f"# {identifier}: {title}\n\n{description}",
    }


# The three logical statuses the task workflow uses, mapped to their Linear
# workflow-state names. ``done`` maps to ``Unreleased`` (completed work waiting
# on a release), not the literal ``Done`` state. This is the single canonical
# status-transition command; there is deliberately no special subtask handling.
STATUS_STATES = {
    "planned": "Planned",
    "in-progress": "In Progress",
    "done": "Unreleased",
}


def set_issue_status(issue_id: str, status: str) -> str:
    """Set a Linear issue to one of planned|in-progress|done. Raises on failure.

    The reusable core shared by ``mael linear set-status`` and the task
    lifecycle actions. ``done`` maps to the ``Unreleased`` workflow state.
    Raises ``IntegrationError`` if the target state is missing from the
    workflow. Returns the one-line result, a no-op included.
    """
    state_name = STATUS_STATES[status]
    issue = get_issue(issue_id)
    states = get_workflow_states()
    if state_name not in states:
        raise IntegrationError(f"'{state_name}' state not found in workflow.")
    current = issue["state"]["name"]
    if current == state_name:
        return f"{issue['identifier']} already {state_name}."
    update_issue(issue["id"], stateId=states[state_name])
    return f"{issue['identifier']}: {current} -> {state_name}"
