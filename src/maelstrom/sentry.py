"""Sentry issue and event query integration for maelstrom."""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from .config import load_config_or_default
from .context import resolve_context

SENTRY_API_URL = "https://sentry.io/api/0"


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


def get_sentry_api_key() -> str:
    """Get the Sentry API key."""
    return get_env_var("SENTRY_API_KEY")


def get_sentry_config() -> tuple[str, str]:
    """Get the Sentry org and project from config.

    Returns:
        Tuple of (sentry_org, sentry_project).

    Raises:
        click.ClickException: If config is missing.
    """
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=False)
        if ctx.worktree_path:
            config = load_config_or_default(ctx.worktree_path)
            if config.sentry_org and config.sentry_project:
                return config.sentry_org, config.sentry_project
    except ValueError:
        pass

    # Try loading from cwd
    config = load_config_or_default(Path.cwd())
    if config.sentry_org and config.sentry_project:
        return config.sentry_org, config.sentry_project

    raise click.ClickException(
        "Sentry not configured. Add to .maelstrom.yaml:\n"
        '  sentry_org: "your-org"\n'
        '  sentry_project: "your-project-id"'
    )


def api_request(endpoint: str, params: dict | None = None) -> dict | list:
    """Make a REST API request to Sentry.

    Args:
        endpoint: API endpoint path.
        params: Optional query parameters.

    Returns:
        The response data.

    Raises:
        click.ClickException: On API errors.
    """
    api_key = get_sentry_api_key()
    url = f"{SENTRY_API_URL}{endpoint}"

    if params:
        query_string = urllib.parse.urlencode(params)
        url = f"{url}?{query_string}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise click.ClickException(f"HTTP Error {e.code}: {error_body}")


def format_relative_time(iso_timestamp: str) -> str:
    """Convert ISO timestamp to relative time string.

    Args:
        iso_timestamp: ISO 8601 timestamp string.

    Returns:
        Human-readable relative time (e.g., "5m ago", "2d ago").
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    now = datetime.now(UTC)
    delta = now - dt

    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    return f"{months}mo ago"


def calculate_trend(stats: dict) -> str:
    """Calculate trend from 24h stats.

    Args:
        stats: Statistics dictionary with hourly data.

    Returns:
        Trend indicator string (e.g., "up 15", "down 3", "steady").
    """
    if not stats or "24h" not in stats:
        return "steady"

    hourly_data = stats["24h"]
    if not hourly_data or len(hourly_data) < 2:
        return "steady"

    # Sum recent 12h vs previous 12h
    mid = len(hourly_data) // 2
    recent = sum(count for _, count in hourly_data[mid:])
    previous = sum(count for _, count in hourly_data[:mid])

    diff = recent - previous
    if diff > 0:
        return f"up {diff}"
    if diff < 0:
        return f"down {abs(diff)}"
    return "steady"


def format_datetime(iso_timestamp: str) -> str:
    """Convert ISO timestamp to DD/MM/YYYY, HH:MM:SS format.

    Args:
        iso_timestamp: ISO 8601 timestamp string.

    Returns:
        Formatted datetime string.
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return dt.strftime("%d/%m/%Y, %H:%M:%S")


def format_stacktrace(exception: dict) -> str:
    """Format a single exception's stacktrace as markdown.

    Args:
        exception: Exception dictionary from Sentry.

    Returns:
        Markdown-formatted stacktrace.
    """
    lines = []

    exc_type = exception.get("type", "Unknown")
    exc_value = exception.get("value", "")

    lines.append(f"**Type:** {exc_type}")
    lines.append(f"**Value:** {exc_value}")
    lines.append("")
    lines.append("#### Stacktrace")
    lines.append("")
    lines.append("```")

    stacktrace = exception.get("stacktrace", {})
    frames = stacktrace.get("frames", [])

    # Reverse frames to show outermost first (matches Sentry UI)
    for i, frame in enumerate(reversed(frames)):
        function = frame.get("function", "<unknown>")
        filename = frame.get("filename", "<unknown>")
        lineno = frame.get("lineNo") or frame.get("lineno") or "?"
        in_app = frame.get("inApp", False)
        in_app_marker = "(In app)" if in_app else ""

        lines.append(f" {function} in {filename} [Line {lineno}] {in_app_marker}")

        # Parse context array: [[lineNo, codeLine], ...]
        context = frame.get("context", [])
        if context and lineno != "?":
            # Find the suspect line index and split into pre/post context
            suspect_idx = None
            for idx, (line_num, _) in enumerate(context):
                if line_num == lineno:
                    suspect_idx = idx
                    break

            if suspect_idx is not None:
                # Pre-context lines
                for _line_num, code in context[:suspect_idx]:
                    lines.append(f"    {code}")
                # Suspect line
                _, suspect_code = context[suspect_idx]
                lines.append(f"    {suspect_code}  <-- SUSPECT LINE")
                # Post-context lines
                for _line_num, code in context[suspect_idx + 1 :]:
                    lines.append(f"    {code}")
            else:
                # No suspect line found, just show all context
                for _line_num, code in context:
                    lines.append(f"    {code}")
        lines.append("---")

        # Variables
        variables = frame.get("vars", {})
        if variables:
            lines.append("Variable values:")
            lines.append(f"    {json.dumps(variables, indent=2, default=str)}")

        # Separator between frames
        if i < len(frames) - 1:
            lines.append("=======")

    lines.append("```")
    return "\n".join(lines)


# --- Click Commands ---


@click.group("sentry")
def sentry():
    """Sentry issue and event query commands."""
    pass


@sentry.command("list-issues")
@click.option("--env", "environment", default="prod", help="Environment filter (default: prod)")
def cmd_list_issues(environment):
    """List unresolved issues for the project."""
    sentry_org, sentry_project = get_sentry_config()

    endpoint = f"/projects/{sentry_org}/{sentry_project}/issues/"
    params = {
        "query": f"is:unresolved environment:{environment}",
        "statsPeriod": "24h",
    }

    issues = api_request(endpoint, params)

    if not issues:
        click.echo(f"No unresolved issues found in environment '{environment}'.")
        return

    click.echo(f"# Unresolved Issues (environment: {environment})")
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
        trend = calculate_trend(issue.get("stats", {}))
        rows.append((short_id, title, last_seen, count, trend))

    # Calculate column widths (minimum widths for headers)
    headers = ("Short ID", "Title", "Last Seen", "Count", "Trend")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    # Print markdown table with aligned columns
    header_row = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)

    for row in rows:
        data_row = "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"
        click.echo(data_row)


@sentry.command("get-issue")
@click.argument("issue_id")
def cmd_get_issue(issue_id):
    """Get issue details as markdown."""
    sentry_org, _ = get_sentry_config()

    # Fetch the latest event
    endpoint = f"/organizations/{sentry_org}/issues/{issue_id}/events/latest/"
    params = {"full": "true"}

    response = api_request(endpoint, params)

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
    _, sentry_project = get_sentry_config()

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
            click.echo(format_stacktrace(exc))
            click.echo("")
