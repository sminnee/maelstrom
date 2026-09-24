"""The ``mael linear`` commands."""

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from mael_domain.context import resolve_project
from mael_domain.integrations import linear

from .. import task_cli
from ..task_cli import block_task_options
from .group_cli import IntegrationGroup


def _warn(line: str) -> None:
    click.echo(line, err=True)


@click.group("linear", cls=IntegrationGroup)
def linear_group():
    """Linear task management commands."""
    pass


@linear_group.command("list-tasks")
@click.option("--status", default=None, help="Filter by status name (partial match)")
def cmd_list_tasks(status):
    """List tasks in the current cycle, or all active tasks if no cycle."""
    team_id = linear.get_team_id()
    cycle = linear.get_current_cycle()
    issues = linear.fetch_cycle_issues(team_id, status)
    header = (
        f"# Tasks in Cycle {cycle['number']}: {cycle['name']}\n"
        if cycle
        else "# Active Tasks (no active cycle)\n"
    )

    click.echo(header)

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


@linear_group.command("plan")
@click.argument("issue_id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@block_task_options(distinguish_unset=True)
@click.option(
    "--run/--no-run",
    default=True,
    help="Launch the planning session immediately (default: run; --no-run creates only).",
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
async def cmd_plan(
    issue_id: str,
    project: str | None,
    command: str | None,
    mode: str | None,
    model: str | None,
    base: str | None,
    execute_model: str | None,
    branch: str | None,
    parent: str | None,
    pre_action: str | None,
    post_action: str | None,
    priority: str | None,
    follows: tuple[str, ...],
    follow_ends: tuple[str, ...],
    run: bool,
    here: bool,
) -> None:
    """Seed a notebook planning task from a Linear issue.

    Thin wrapper over ``mael task add``: fetches the issue brief and creates a
    ``plan-task`` task whose content is the brief, parented under
    ``linear.<identifier>``. Runs by default — the planning session launches
    immediately; pass ``--no-run`` to create the task without launching. All
    worktree/launch behaviour comes from the shared ``task add`` path — this
    command adds only the brief fetch and argument assembly.

    Every block-settable field is exposed via the shared ``block_task_options``
    decorator, so this command's vocabulary can't drift from ``task add``'s. The
    planning-specific values (``plan-task``/``normal`` mode/``opus``/the
    ``linear.<ID>`` parent/``linear.planned``) are *defaults* the matching flag
    overrides; only ``title`` stays fixed at ``Plan <identifier>``. The options
    default to ``None`` (``distinguish_unset``) rather than ``''``, so passing an
    explicit empty value — ``--post-action ''`` — clears the field instead of
    falling back to the planning default, matching ``task add``'s semantics.
    """
    # add_task re-resolves internally; passing the resolved name is idempotent.
    resolved_project = resolve_project(project)
    planned = linear.build_plan_task(
        issue_id, resolved_project, branch=branch, warn=_warn
    )

    await task_cli.add_task(
        title=planned["title"],
        project=resolved_project,
        command=planned["command"] if command is None else command,
        mode=planned["mode"] if mode is None else mode,
        model=planned["model"] if model is None else model,
        base=base or "",
        execute_model=execute_model or "",
        parent=planned["parent"] if parent is None else parent,
        branch=planned["branch"],
        pre_action=pre_action or "",
        post_action=planned["post_action"] if post_action is None else post_action,
        priority=priority,
        follows=follows,
        follow_ends=follow_ends,
        content=planned["content"],
        run=run,
        here=here,
    )


@linear_group.command("read-task")
@click.argument("issue_id")
def cmd_read_task(issue_id):
    """Read task details as markdown."""
    issue = linear.get_issue(issue_id)

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
            checkbox = (
                "x" if child["state"]["type"] in ["completed", "canceled"] else " "
            )
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
                [
                    sys.executable,
                    "-m",
                    "mael_cli.cli",
                    "sentry",
                    "get-issue",
                    sentry_id,
                ],
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


@linear_group.command("start-task")
@click.argument("issue_id")
def cmd_start_task(issue_id):
    """Start a task: set to In Progress and add workspace label."""
    issue = linear.get_issue(issue_id)
    workspace_label = linear.detect_workspace_label()

    # Get state and label IDs
    states = linear.get_workflow_states()
    labels_map = linear.get_labels()

    if "In Progress" not in states:
        raise click.ClickException("'In Progress' state not found")

    # Build new label list: keep non-workspace labels, add current workspace + product
    workspace_labels = linear.get_workspace_labels()
    product_label = linear.get_product_label()
    current_labels = [
        label["name"] for label in issue.get("labels", {}).get("nodes", [])
    ]
    new_labels = [label for label in current_labels if label not in workspace_labels]
    if workspace_label:
        new_labels.append(workspace_label)
    if product_label and product_label not in new_labels:
        new_labels.append(product_label)

    # Convert to IDs
    label_ids = [
        labels_map[label_name] for label_name in new_labels if label_name in labels_map
    ]

    # Update the issue
    linear.update_issue(
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
        parent = linear.get_issue(issue["parent"]["id"])
        parent_labels = [
            label["name"] for label in parent.get("labels", {}).get("nodes", [])
        ]
        parent_new_labels = [
            label for label in parent_labels if label not in workspace_labels
        ]
        if workspace_label:
            parent_new_labels.append(workspace_label)
        if product_label and product_label not in parent_new_labels:
            parent_new_labels.append(product_label)
        parent_label_ids = [
            labels_map[label] for label in parent_new_labels if label in labels_map
        ]

        # Only promote parent to In Progress from early states
        early_states = {"Todo", "Planned", "Backlog"}
        update_kwargs: dict[str, Any] = {"labelIds": parent_label_ids}
        if parent["state"]["name"] in early_states:
            update_kwargs["stateId"] = states["In Progress"]

        linear.update_issue(parent["id"], **update_kwargs)
        click.echo(f"\nAlso updated parent {parent['identifier']}:")
        if parent["state"]["name"] in early_states:
            click.echo("- Status: In Progress")
        if workspace_label:
            click.echo(f"- Workspace: {workspace_label}")


@linear_group.command("set-status")
@click.argument("issue_id")
@click.argument("status", type=click.Choice(list(linear.STATUS_STATES)))
def cmd_set_status(issue_id, status):
    """Set a Linear issue's status: planned, in-progress, or done.

    The canonical status-transition command. ``done`` maps to the ``Unreleased``
    workflow state (promote to ``Done`` later with ``mael linear release``).
    Applies to the issue as-is — no special subtask/parent handling.
    """
    click.echo(linear.set_issue_status(issue_id, status))


@linear_group.command("create-subtask")
@click.argument("parent_id")
@click.argument("title")
@click.argument("description", default="", required=False)
def cmd_create_subtask(parent_id, title, description):
    """Create a subtask on a parent issue."""
    parent = linear.get_issue(parent_id)

    cycle_id = parent.get("cycle", {}).get("id") if parent.get("cycle") else None

    new_issue = linear.create_issue(
        title=title,
        parent_id=parent["id"],
        description=description or "",
        cycle_id=cycle_id,
    )

    click.echo(f"Created subtask {new_issue['identifier']}: {new_issue['title']}")
    click.echo(f"- Parent: {parent['identifier']}")
    if cycle_id:
        click.echo(f"- Cycle: {parent['cycle']['number']} - {parent['cycle']['name']}")

    # Add product label to parent if configured
    labels_map = linear.get_labels()
    parent_labels = [
        label["name"] for label in parent.get("labels", {}).get("nodes", [])
    ]
    parent_label_ids = linear.ensure_product_label(
        parent["id"], labels_map, parent_labels
    )
    if parent_label_ids:
        linear.update_issue(parent["id"], labelIds=parent_label_ids)
        click.echo(f"Added product label to parent: {linear.get_product_label()}")

    # Transition parent to Planned if currently Todo
    if parent["state"]["name"] == "Todo":
        states = linear.get_workflow_states()
        if "Planned" in states:
            linear.update_issue(parent["id"], stateId=states["Planned"])
            click.echo(f"Updated parent {parent['identifier']} status: Todo -> Planned")


@linear_group.command("create-task")
@click.argument("title")
@click.argument("description", default="", required=False)
def cmd_create_task(title: str, description: str) -> None:
    """Create a new task in the project backlog."""
    states = linear.get_workflow_states()
    if "Backlog" not in states:
        raise click.ClickException("Backlog state not found in workflow states")

    state_id = states["Backlog"]
    label_ids: list[str] | None = None
    product_label = linear.get_product_label()

    if product_label:
        labels_map = linear.get_labels()
        if product_label in labels_map:
            label_ids = [labels_map[product_label]]

    new_issue = linear.create_issue(
        title=title,
        description=description,
        state_id=state_id,
        label_ids=label_ids,
    )

    click.echo(f"Created task {new_issue['identifier']}: {new_issue['title']}")
    click.echo("- Status: Backlog")
    if product_label and label_ids:
        click.echo(f"- Label: {product_label}")


@linear_group.command("write-plan")
@click.argument("issue_id")
@click.argument("plan_file", type=click.Path(exists=True))
def cmd_write_plan(issue_id, plan_file):
    """Write an implementation plan to a Linear task's description.

    Reads a markdown plan file and stores it in the issue description between
    '# Implementation Plan' and '(end of plan)' markers. Updates status to
    'Planned' if currently 'Todo'.
    """
    plan_path = Path(plan_file)
    plan_content = plan_path.read_text().strip()
    if not plan_content:
        raise click.ClickException("Plan file is empty")

    issue = linear.get_issue(issue_id)
    description = issue.get("description") or ""

    # Build the plan section with markers and surrounding HRs for visual separation
    plan_section = (
        f"---\n\n# Implementation Plan\n\n{plan_content}\n\n(end of plan)\n\n---"
    )

    # Replace existing plan or append
    start_marker = "# Implementation Plan"
    end_marker = "(end of plan)"
    start_idx = description.find(start_marker)
    end_idx = description.find(end_marker)

    if start_idx != -1 and end_idx != -1:
        # Expand range to include surrounding HRs and whitespace
        replace_start = start_idx
        replace_end = end_idx + len(end_marker)
        # Look backwards for a preceding HR
        prefix = description[:replace_start].rstrip()
        if prefix.endswith("---"):
            replace_start = len(prefix) - 3
        # Look forwards for a trailing HR
        suffix = description[replace_end:].lstrip()
        if suffix.startswith("---"):
            replace_end = len(description) - len(suffix) + 3
        new_description = (
            description[:replace_start].rstrip()
            + "\n\n"
            + plan_section
            + "\n\n"
            + description[replace_end:].lstrip()
        )
    elif start_idx != -1:
        # Malformed - has start but no end, replace from start onward
        new_description = description[:start_idx].rstrip() + "\n\n" + plan_section
    else:
        new_description = description.rstrip() + "\n\n" + plan_section

    linear.update_issue(issue["id"], description=new_description)
    click.echo(f"Wrote implementation plan to {issue['identifier']}: {issue['title']}")

    # Add product label if configured
    labels_map = linear.get_labels()
    current_labels = [
        label["name"] for label in issue.get("labels", {}).get("nodes", [])
    ]
    product_label_ids = linear.ensure_product_label(
        issue["id"], labels_map, current_labels
    )
    if product_label_ids:
        linear.update_issue(issue["id"], labelIds=product_label_ids)
        click.echo(f"Added product label: {linear.get_product_label()}")

        # Also add to parent if this is a subtask
        if issue.get("parent"):
            parent = linear.get_issue(issue["parent"]["id"])
            parent_labels = [
                label["name"] for label in parent.get("labels", {}).get("nodes", [])
            ]
            parent_label_ids = linear.ensure_product_label(
                parent["id"], labels_map, parent_labels
            )
            if parent_label_ids:
                linear.update_issue(parent["id"], labelIds=parent_label_ids)

    # Update status to Planned if currently Todo
    if issue["state"]["name"] == "Todo":
        states = linear.get_workflow_states()
        if "Planned" in states:
            linear.update_issue(issue["id"], stateId=states["Planned"])
            click.echo("Updated status: Todo -> Planned")
        else:
            click.echo(
                "Warning: 'Planned' state not found in workflow. Status not updated.",
                err=True,
            )


@linear_group.command("read-plan")
@click.argument("issue_id")
def cmd_read_plan(issue_id):
    """Read the implementation plan from a Linear task's description.

    Extracts content between '# Implementation Plan' and '(end of plan)'
    markers in the issue description.
    """
    issue = linear.get_issue(issue_id)
    description = issue.get("description") or ""

    start_marker = "# Implementation Plan"
    end_marker = "(end of plan)"
    start_idx = description.find(start_marker)
    end_idx = description.find(end_marker)

    if start_idx == -1:
        raise click.ClickException(
            f"No implementation plan found on {issue['identifier']}. "
            f"Use 'mael linear write-plan' to add one."
        )

    # Extract content between markers (excluding the markers themselves)
    content_start = start_idx + len(start_marker)
    if end_idx != -1:
        plan_content = description[content_start:end_idx].strip()
    else:
        plan_content = description[content_start:].strip()

    click.echo(plan_content)


@linear_group.command("edit-plan")
@click.argument("issue_id")
@click.argument("old_arg")
@click.argument("new_arg")
@click.option(
    "-s",
    "--string",
    is_flag=True,
    help="Treat arguments as literal strings instead of file paths.",
)
def cmd_edit_plan(issue_id, old_arg, new_arg, string):
    """Search/replace within the plan section of a Linear issue description.

    In default (file-based) mode, OLD_ARG and NEW_ARG are file paths containing
    the search and replace text. With -s/--string, they are literal strings.
    """
    if string:
        old_string = old_arg
        new_string = new_arg
    else:
        old_path = Path(old_arg)
        new_path = Path(new_arg)
        if not old_path.exists():
            raise click.ClickException(f"File not found: {old_arg}")
        if not new_path.exists():
            raise click.ClickException(f"File not found: {new_arg}")
        old_string = old_path.read_text()
        new_string = new_path.read_text()

    if not old_string:
        raise click.ClickException("Search string is empty")

    issue = linear.get_issue(issue_id)
    description = issue.get("description") or ""

    start_marker = "# Implementation Plan"
    end_marker = "(end of plan)"
    start_idx = description.find(start_marker)
    end_idx = description.find(end_marker)

    if start_idx == -1:
        raise click.ClickException(
            f"No implementation plan found on {issue['identifier']}. "
            f"Use 'mael linear write-plan' to add one."
        )

    # Extract plan section
    if end_idx != -1:
        plan_section = description[start_idx : end_idx + len(end_marker)]
    else:
        plan_section = description[start_idx:]

    # Verify exactly one match
    count = plan_section.count(old_string)
    if count == 0:
        raise click.ClickException("Search string not found in plan section")
    if count > 1:
        raise click.ClickException(
            f"Search string found {count} times in plan section (must be unique)"
        )

    # Replace within plan section and reconstruct
    new_plan_section = plan_section.replace(old_string, new_string, 1)
    new_description = (
        description[:start_idx]
        + new_plan_section
        + description[start_idx + len(plan_section) :]
    )

    linear.update_issue(issue["id"], description=new_description)
    click.echo(f"Updated plan on {issue['identifier']}: {issue['title']}")


@linear_group.command("add-comment")
@click.argument("issue_id")
@click.argument("comment_file", type=click.Path(exists=True))
def cmd_add_comment(issue_id, comment_file):
    """Add a comment to a Linear issue from a markdown file.

    Reads markdown content from the file and creates a comment on the issue.
    """
    comment_path = Path(comment_file)
    body = comment_path.read_text().strip()
    if not body:
        raise click.ClickException("Comment file is empty")

    issue = linear.get_issue(issue_id)
    linear.create_comment(issue["id"], body)
    click.echo(f"Added comment to {issue['identifier']}: {issue['title']}")


@linear_group.command("release")
@click.option(
    "--dry-run",
    is_flag=True,
    help="List the tasks that would be released without changing anything.",
)
def cmd_release(dry_run):
    """Promote all 'Unreleased' tasks with the product label to 'Done'.

    Finds all issues with status "Unreleased" that have the configured product label,
    and transitions them to "Done". Requires linear.product_label to be configured.
    """
    product_label = linear.get_product_label()
    if not product_label:
        raise click.ClickException(
            "linear.product_label not configured. Add to .maelstrom.yaml:\n"
            "  linear:\n"
            '    product_label: "YourProduct"'
        )

    team_id = linear.get_team_id()
    states = linear.get_workflow_states()

    if "Unreleased" not in states:
        raise click.ClickException("'Unreleased' state not found in workflow")
    if "Done" not in states:
        raise click.ClickException("'Done' state not found in workflow")

    # Query for issues with "Unreleased" status and the product label. Paginated:
    # without `first:` Linear caps the result at 50, which would silently release
    # only part of a large backlog.
    query = """
    query GetUnreleasedIssues(
        $teamId: ID!
        $stateName: String!
        $labelName: String!
        $first: Int
        $after: String
    ) {
        issues(
            filter: {
                team: { id: { eq: $teamId } }
                state: { name: { eq: $stateName } }
                labels: { name: { eq: $labelName } }
            }
            first: $first
            after: $after
        ) {
            nodes {
                id
                identifier
                title
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """
    issues = linear.graphql_paginated(
        query,
        {
            "teamId": team_id,
            "stateName": "Unreleased",
            "labelName": product_label,
        },
        connection="issues",
    )

    if not issues:
        click.echo(f"No unreleased tasks found with label '{product_label}'.")
        return

    if dry_run:
        click.echo(f"Would release {len(issues)} task(s):\n")
        for issue in issues:
            click.echo(f"- {issue['identifier']}: {issue['title']} -> Done")
        click.echo("\nDry run — no tasks changed.")
        return

    done_state_id = states["Done"]
    click.echo(f"Releasing {len(issues)} task(s):\n")

    # A single bad ticket must not strand the rest half-released: report and
    # carry on, then exit non-zero at the end so the failure is still visible.
    failures: list[str] = []
    for issue in issues:
        try:
            linear.update_issue(issue["id"], stateId=done_state_id)
        except Exception as exc:
            # Deliberately broad: update_issue raises IntegrationError for a
            # `success: false` response, but a transport error or a malformed
            # payload surfaces as OSError/KeyError. Letting those escape would
            # abort mid-run — exactly what this block exists to prevent.
            reason = str(exc) or exc.__class__.__name__
            failures.append(issue["identifier"])
            click.echo(
                f"- {issue['identifier']}: {issue['title']} -> FAILED ({reason})"
            )
            continue
        click.echo(f"- {issue['identifier']}: {issue['title']} -> Done")

    released = len(issues) - len(failures)
    click.echo(f"\nReleased {released} task(s).")
    if failures:
        raise click.ClickException(
            f"{len(failures)} task(s) failed to release: {', '.join(failures)}"
        )
