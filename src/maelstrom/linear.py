"""Linear task management integration for maelstrom."""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import click

from .config import load_config_or_default
from .context import resolve_context

LINEAR_API_URL = "https://api.linear.app/graphql"


def get_env_var(name: str) -> str:
    """Get environment variable from os.environ or .env file.

    Args:
        name: Environment variable name.

    Returns:
        The environment variable value.

    Raises:
        click.ClickException: If the variable is not found.
    """
    if value := os.environ.get(name):
        return value

    # Find .env file in current directory or parents
    current = Path.cwd()
    while current != current.parent:
        env_path = current / ".env"
        if env_path.exists():
            content = env_path.read_text()
            pattern = rf"^{re.escape(name)}\s*=\s*[\"']?([^\"'\n]+)[\"']?"
            if match := re.search(pattern, content, re.MULTILINE):
                return match.group(1)
            break
        current = current.parent

    raise click.ClickException(f"{name} environment variable not set")


def get_linear_api_key() -> str:
    """Get the Linear API key."""
    return get_env_var("LINEAR_API_KEY")


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

    raise click.ClickException(
        "linear_team_id not configured. Add to .maelstrom.yaml:\n"
        "  linear_team_id: \"your-team-uuid\""
    )


def graphql_request(query: str, variables: dict | None = None) -> dict:
    """Make a GraphQL request to Linear API.

    Args:
        query: GraphQL query string.
        variables: Optional query variables.

    Returns:
        The response data.

    Raises:
        click.ClickException: On API errors.
    """
    api_key = get_linear_api_key()

    payload: dict[str, str | dict] = {"query": query}
    if variables:
        payload["variables"] = variables

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        LINEAR_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,
        },
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
            if "errors" in result:
                raise click.ClickException(f"GraphQL errors: {result['errors']}")
            return result["data"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise click.ClickException(f"HTTP Error {e.code}: {error_body}")


def get_current_cycle() -> dict | None:
    """Get the current cycle for the team."""
    team_id = get_team_id()
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
        click.ClickException: If issue not found.
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
        raise click.ClickException(f"Issue {issue_id} not found")
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
        click.ClickException: If update fails.
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
        raise click.ClickException("Failed to update issue")


def create_issue(
    title: str, parent_id: str, description: str = "", cycle_id: str | None = None
) -> dict:
    """Create a new issue as a subtask.

    Args:
        title: Subtask title.
        parent_id: Parent issue's internal ID.
        description: Optional description.
        cycle_id: Optional cycle ID (inherits from parent if not specified).

    Returns:
        Created issue data with id, identifier, and title.

    Raises:
        click.ClickException: If creation fails.
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
    input_data = {
        "title": title,
        "teamId": team_id,
        "parentId": parent_id,
    }
    if description:
        input_data["description"] = description
    if cycle_id:
        input_data["cycleId"] = cycle_id

    result = graphql_request(mutation, {"input": input_data})
    if not result["issueCreate"]["success"]:
        raise click.ClickException("Failed to create issue")
    return result["issueCreate"]["issue"]


def create_attachment(
    issue_id: str, url: str, title: str, subtitle: str = ""
) -> dict:
    """Create an attachment on an issue.

    Args:
        issue_id: The issue's internal ID.
        url: URL for the attachment.
        title: Display title for the attachment.
        subtitle: Optional subtitle text.

    Returns:
        Created attachment data with id.

    Raises:
        click.ClickException: If creation fails.
    """
    mutation = """
    mutation CreateAttachment($input: AttachmentCreateInput!) {
        attachmentCreate(input: $input) {
            success
            attachment {
                id
            }
        }
    }
    """
    input_data: dict[str, str] = {
        "issueId": issue_id,
        "url": url,
        "title": title,
    }
    if subtitle:
        input_data["subtitle"] = subtitle

    result = graphql_request(mutation, {"input": input_data})
    if not result["attachmentCreate"]["success"]:
        raise click.ClickException("Failed to create attachment")
    return result["attachmentCreate"]["attachment"]


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
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
        "golf", "hotel", "india", "juliet", "kilo", "lima",
        "mike", "november", "oscar", "papa", "quebec", "romeo",
        "sierra", "tango", "uniform", "victor", "whiskey", "xray",
        "yankee", "zulu",
    ]


# --- Click Commands ---


@click.group("linear")
def linear():
    """Linear task management commands."""
    pass


@linear.command("list-tasks")
@click.option("--status", default=None, help="Filter by status name (partial match)")
def cmd_list_tasks(status):
    """List tasks in the current cycle."""
    cycle = get_current_cycle()
    if not cycle:
        raise click.ClickException("No active cycle found")

    team_id = get_team_id()
    query = """
    query ListIssues($teamId: ID!, $cycleId: ID!, $status: String) {
        issues(
            filter: {
                team: { id: { eq: $teamId } }
                cycle: { id: { eq: $cycleId } }
                state: { name: { containsIgnoreCase: $status } }
            }
            orderBy: updatedAt
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
        }
    }
    """
    variables = {
        "teamId": team_id,
        "cycleId": cycle["id"],
        "status": status or "",
    }

    result = graphql_request(query, variables)
    issues = result["issues"]["nodes"]

    click.echo(f"# Tasks in Cycle {cycle['number']}: {cycle['name']}\n")

    if not issues:
        click.echo("No tasks found.")
        return

    # Group by parent
    parent_issues = [i for i in issues if not i.get("parent")]
    child_issues = [i for i in issues if i.get("parent")]

    for issue in parent_issues:
        issue_status = issue["state"]["name"]
        click.echo(f"- **{issue['identifier']}**: {issue['title']} [{issue_status}]")

        # Find children of this issue
        children = [
            c
            for c in child_issues
            if c.get("parent", {}).get("identifier") == issue["identifier"]
        ]
        for child in children:
            child_status = child["state"]["name"]
            click.echo(
                f"  - **{child['identifier']}**: {child['title']} [{child_status}]"
            )

    # Any orphan children (parent not in current cycle)
    shown_children = {
        c["identifier"]
        for c in child_issues
        if any(
            c.get("parent", {}).get("identifier") == p["identifier"]
            for p in parent_issues
        )
    }
    orphans = [c for c in child_issues if c["identifier"] not in shown_children]
    for child in orphans:
        child_status = child["state"]["name"]
        parent_id = child.get("parent", {}).get("identifier", "?")
        click.echo(
            f"- **{child['identifier']}**: {child['title']} [{child_status}] "
            f"(parent: {parent_id})"
        )


@linear.command("read-task")
@click.argument("issue_id")
def cmd_read_task(issue_id):
    """Read task details as markdown."""
    issue = get_issue(issue_id)

    click.echo(f"# {issue['identifier']}: {issue['title']}\n")
    click.echo(f"**Status**: {issue['state']['name']}")

    if issue.get("parent"):
        parent = issue["parent"]
        click.echo(f"**Parent**: {parent['identifier']} - {parent['title']}")

    if issue.get("cycle"):
        click.echo(f"**Cycle**: {issue['cycle']['number']} - {issue['cycle']['name']}")

    labels = [label["name"] for label in issue.get("labels", {}).get("nodes", [])]
    if labels:
        click.echo(f"**Labels**: {', '.join(labels)}")

    click.echo()

    if issue.get("description"):
        click.echo("## Description\n")
        click.echo(issue["description"])
        click.echo()

    children = issue.get("children", {}).get("nodes", [])
    if children:
        click.echo("## Subtasks\n")
        for child in children:
            child_status = child["state"]["name"]
            checkbox = "x" if child["state"]["type"] in ["completed", "canceled"] else " "
            click.echo(
                f"- [{checkbox}] **{child['identifier']}**: {child['title']} "
                f"[{child_status}]"
            )
        click.echo()

    comments = issue.get("comments", {}).get("nodes", [])
    if comments:
        click.echo("## Comments\n")
        for comment in comments:
            user = comment.get("user", {})
            author = user.get("displayName") or user.get("name") or "Unknown"
            created_at = comment.get("createdAt", "")[:10]
            body = comment.get("body", "")
            click.echo(f"**{author}** ({created_at}):")
            click.echo(body)
            click.echo()

    attachments = issue.get("attachments", {}).get("nodes", [])
    sentry_issue_ids: list[str] = []
    if attachments:
        click.echo("## Attachments\n")
        for attachment in attachments:
            title = attachment.get("title") or "Unnamed"
            url = attachment.get("url", "")
            source_type = attachment.get("sourceType", "")
            # Detect Sentry links and collect issue IDs
            if "sentry.io" in url or source_type == "sentry":
                click.echo(f"- [{title}]({url}) (Sentry)")
                # Extract issue ID from URL like https://org.sentry.io/issues/123/
                match = re.search(r"/issues/(\d+)", url)
                if match:
                    sentry_issue_ids.append(match.group(1))
            else:
                click.echo(f"- [{title}]({url})")
        click.echo()

    # Fetch and display Sentry issue details
    for sentry_id in sentry_issue_ids:
        click.echo(f"## Sentry Issue {sentry_id}\n")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "maelstrom", "sentry", "get-issue", sentry_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                click.echo(result.stdout)
            else:
                click.echo(f"Failed to fetch Sentry issue: {result.stderr}")
        except subprocess.TimeoutExpired:
            click.echo("Timeout fetching Sentry issue details")
        except Exception as e:
            click.echo(f"Error fetching Sentry issue: {e}")
        click.echo()


@linear.command("start-task")
@click.argument("issue_id")
def cmd_start_task(issue_id):
    """Start a task: set to In Progress and add workspace label."""
    issue = get_issue(issue_id)
    workspace_label = detect_workspace_label()

    # Get state and label IDs
    states = get_workflow_states()
    labels_map = get_labels()

    if "In Progress" not in states:
        raise click.ClickException("'In Progress' state not found")

    # Build new label list: keep non-workspace labels, add current workspace
    workspace_labels = get_workspace_labels()
    current_labels = [
        label["name"] for label in issue.get("labels", {}).get("nodes", [])
    ]
    new_labels = [label for label in current_labels if label not in workspace_labels]
    if workspace_label:
        new_labels.append(workspace_label)

    # Convert to IDs
    label_ids = [
        labels_map[label_name]
        for label_name in new_labels
        if label_name in labels_map
    ]

    # Update the issue
    update_issue(
        issue["id"],
        stateId=states["In Progress"],
        labelIds=label_ids,
    )

    click.echo(f"Started task {issue['identifier']}: {issue['title']}")
    click.echo("- Status: In Progress")
    if workspace_label:
        click.echo(f"- Workspace: {workspace_label}")

    # Also update parent if this is a subtask
    if issue.get("parent"):
        parent = get_issue(issue["parent"]["id"])
        parent_labels = [
            label["name"] for label in parent.get("labels", {}).get("nodes", [])
        ]
        parent_new_labels = [
            label for label in parent_labels if label not in workspace_labels
        ]
        if workspace_label:
            parent_new_labels.append(workspace_label)
        parent_label_ids = [
            labels_map[label] for label in parent_new_labels if label in labels_map
        ]

        update_issue(
            parent["id"],
            stateId=states["In Progress"],
            labelIds=parent_label_ids,
        )
        click.echo(f"\nAlso updated parent {parent['identifier']}:")
        click.echo("- Status: In Progress")
        if workspace_label:
            click.echo(f"- Workspace: {workspace_label}")


@linear.command("complete-task")
@click.argument("issue_id")
def cmd_complete_task(issue_id):
    """Complete a task: set to Done (subtask) or Unreleased (standalone/parent)."""
    issue = get_issue(issue_id)
    states = get_workflow_states()

    is_subtask = bool(issue.get("parent"))

    # Determine target status
    if is_subtask:
        target_status = "Done"
    else:
        target_status = "Unreleased"

    if target_status not in states:
        raise click.ClickException(f"'{target_status}' state not found")

    # Update the issue
    update_issue(issue["id"], stateId=states[target_status])

    click.echo(f"Completed task {issue['identifier']}: {issue['title']}")
    click.echo(f"- Status: {target_status}")

    # If this is a subtask, check if all siblings are complete
    if is_subtask:
        parent = get_issue(issue["parent"]["id"])
        siblings = parent.get("children", {}).get("nodes", [])

        all_complete = all(
            s["state"]["type"] in ["completed", "canceled"] or s["id"] == issue["id"]
            for s in siblings
        )

        if all_complete and "Unreleased" in states:
            update_issue(parent["id"], stateId=states["Unreleased"])
            click.echo(f"\nAll subtasks complete - updated parent {parent['identifier']}:")
            click.echo("- Status: Unreleased")
        else:
            incomplete = [
                s
                for s in siblings
                if s["state"]["type"] not in ["completed", "canceled"]
                and s["id"] != issue["id"]
            ]
            if incomplete:
                click.echo(
                    f"\nParent {parent['identifier']} not updated "
                    f"({len(incomplete)} subtask(s) still incomplete)"
                )


@linear.command("create-subtask")
@click.argument("parent_id")
@click.argument("title")
@click.argument("description", default="", required=False)
def cmd_create_subtask(parent_id, title, description):
    """Create a subtask on a parent issue."""
    parent = get_issue(parent_id)

    cycle_id = parent.get("cycle", {}).get("id") if parent.get("cycle") else None

    new_issue = create_issue(
        title=title,
        parent_id=parent["id"],
        description=description or "",
        cycle_id=cycle_id,
    )

    click.echo(f"Created subtask {new_issue['identifier']}: {new_issue['title']}")
    click.echo(f"- Parent: {parent['identifier']}")
    if cycle_id:
        click.echo(f"- Cycle: {parent['cycle']['number']} - {parent['cycle']['name']}")


@linear.command("add-plan")
@click.argument("issue_id")
@click.argument("plan_content")
def cmd_add_plan(issue_id, plan_content):
    """Add an implementation plan section to a task."""
    issue = get_issue(issue_id)

    current_description = issue.get("description") or ""

    # Append the implementation plan section
    plan_section = f"\n\n## Implementation Plan\n\n{plan_content}"
    new_description = current_description + plan_section

    update_issue(issue["id"], description=new_description)

    click.echo(f"Added implementation plan to {issue['identifier']}: {issue['title']}")


@linear.command("submit-pr")
@click.argument("issue_id")
def cmd_submit_pr(issue_id):
    """Submit a PR for review: attach PR URL and set status to In Review.

    This command:
    1. Gets the PR URL from the current branch using `gh pr view`
    2. Attaches the PR URL to the Linear task
    3. Sets the task status to "In Review"
    4. If this is a subtask, also updates the parent task to "In Review"
    """
    from pathlib import Path

    from .github import get_pr_url

    cwd = Path.cwd()
    try:
        pr_url = get_pr_url(cwd)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    issue = get_issue(issue_id)
    states = get_workflow_states()

    if "In Review" not in states:
        raise click.ClickException("'In Review' state not found in workflow")

    # Extract PR number for title
    pr_number = pr_url.rstrip("/").split("/")[-1]

    # Attach PR (warn if duplicate)
    try:
        create_attachment(
            issue["id"], pr_url, f"Pull Request #{pr_number}", "Open"
        )
        click.echo(f"Attached PR to {issue['identifier']}: {pr_url}")
    except click.ClickException as e:
        click.echo(
            f"Warning: Could not attach PR (may already exist): {e.message}", err=True
        )

    # Update status to In Review
    update_issue(issue["id"], stateId=states["In Review"])
    click.echo(f"Updated {issue['identifier']} status to: In Review")

    # Update parent if subtask and parent not in terminal state
    if issue.get("parent"):
        parent = get_issue(issue["parent"]["id"])
        terminal_states = {"Done", "Unreleased", "Canceled", "Completed"}
        if parent["state"]["name"] not in terminal_states:
            update_issue(parent["id"], stateId=states["In Review"])
            click.echo(f"Updated parent {parent['identifier']} status to: In Review")
        else:
            click.echo(
                f"Parent {parent['identifier']} already in "
                f"'{parent['state']['name']}' - not updating"
            )
