"""The ``mael sentry`` commands."""

from typing import Any

import click

from mael_domain.integrations import sentry
from mael_domain.integrations._format import (
    format_datetime,
    format_relative_time,
    parse_since,
)

from .group_cli import IntegrationGroup


@click.group("sentry", cls=IntegrationGroup)
def sentry_group():
    """Sentry issue and event query commands."""
    pass


@sentry_group.command("list-issues")  # type: ignore[attr-defined]
@click.option(
    "--env", "environment", default="prod", help="Environment filter (default: prod)"
)
@click.option(
    "--since",
    default=None,
    help="Only issues last seen within this window (e.g. 30m, 24h, 7d). Default: all unresolved issues",
)
def cmd_list_issues(environment, since):
    """List unresolved issues for the project."""
    sentry_org, sentry_project = sentry.get_sentry_config()

    query = f"is:unresolved environment:{environment}"
    if since is not None:
        # Validate the duration (raises the standard 'Invalid --since' error on
        # bad input). parse_since permits surrounding whitespace, so normalize to
        # the bare token before interpolating it into the query / display strings.
        parse_since(since)
        since = "".join(since.split())
        query = f"{query} lastSeen:-{since}"

    endpoint = f"/projects/{sentry_org}/{sentry_project}/issues/"
    params = {
        "query": query,
        "statsPeriod": "24h",
    }

    issues = sentry.api_request(endpoint, params)

    window = f" in the last {since}" if since is not None else ""
    if not issues:
        click.echo(
            f"No unresolved issues found in environment '{environment}'{window}."
        )
        return

    heading_window = f", last {since}" if since is not None else ""
    click.echo(f"# Unresolved Issues (environment: {environment}{heading_window})")
    click.echo("")
    click.echo("- **Count**: Total number of events for this issue (all time)")
    click.echo("- **Trend**: Change in events over last 12h vs previous 12h")
    click.echo("")

    # Pre-process all rows to calculate column widths
    rows: list[tuple[str, str, str, str, str]] = []
    for issue in issues:
        short_id = str(issue.get("shortId", ""))
        title = issue.get("title", "")[:70]
        if len(issue.get("title", "")) > 70:
            title += ".."
        # Escape pipe characters in title for markdown table
        title = title.replace("|", "\\|")
        last_seen = format_relative_time(issue.get("lastSeen", ""))
        count = str(issue.get("count", "0"))
        trend = sentry.calculate_trend(issue.get("stats", {}))
        rows.append((short_id, title, last_seen, count, trend))

    # Calculate column widths (minimum widths for headers)
    headers = ("Short ID", "Title", "Last Seen", "Count", "Trend")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    # Print markdown table with aligned columns
    header_row = (
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    )
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)

    for row in rows:
        data_row = (
            "| "
            + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
            + " |"
        )
        click.echo(data_row)


@sentry_group.command("get-issue")  # type: ignore[attr-defined]
@click.argument("issue_id")
def cmd_get_issue(issue_id):
    """Get issue details as markdown."""
    sentry_org, _ = sentry.get_sentry_config()

    # Fetch the latest event
    endpoint = f"/organizations/{sentry_org}/issues/{issue_id}/events/latest/"
    params = {"full": "true"}

    response = sentry.api_request(endpoint, params)

    if not response or not isinstance(response, dict):
        raise click.ClickException(f"No events found for issue {issue_id}")

    event: dict[str, Any] = response

    # Extract data
    date_created = event.get("dateCreated", "")
    tags = event.get("tags", [])

    # Get exception info
    exception_values = []
    entries = event.get("entries", [])
    for entry in entries:
        if entry.get("type") == "exception":
            exception_values = entry.get("data", {}).get("values", [])
            break

    # Build title from first exception
    if exception_values:
        first_exc = exception_values[0]
        title = f"{first_exc.get('type', 'Error')}: {first_exc.get('value', '')}"
    else:
        title = event.get("title", "Unknown Error")

    # Get project name from config
    _, sentry_project = sentry.get_sentry_config()

    # Output markdown
    click.echo(f"# {title}")
    click.echo("")
    click.echo(f"**Issue ID:** {issue_id}")
    click.echo(f"**Project:** {sentry_project}")
    if date_created:
        click.echo(f"**Date:** {format_datetime(date_created)}")
    click.echo("")

    # Tags
    if tags:
        click.echo("## Tags")
        click.echo("")
        for tag in tags:
            if isinstance(tag, dict):
                key = tag.get("key", "")
                value = tag.get("value", "")
            elif isinstance(tag, list) and len(tag) >= 2:
                key, value = tag[0], tag[1]
            else:
                continue
            click.echo(f"- **{key}:** {value}")
        click.echo("")

    # Exceptions
    if exception_values:
        click.echo("## Exception")
        click.echo("")
        for i, exc in enumerate(exception_values, 1):
            if len(exception_values) > 1:
                click.echo(f"### Exception {i}")
                click.echo("")
            click.echo(sentry.format_stacktrace(exc))
            click.echo("")


@sentry_group.command("resolve-issue")  # type: ignore[attr-defined]
@click.argument("issue_id")
def cmd_resolve_issue(issue_id: str) -> None:
    """Mark an issue as resolved in the next release."""
    click.echo(sentry.resolve_issue(issue_id))
