"""Sentry issue and event query integration for maelstrom."""

import json
from pathlib import Path

from ..config import load_config_or_default
from ..context import resolve_context
from ._auth import resolve_secret
from ._http import request_json
from .errors import IntegrationError

SENTRY_API_URL = "https://sentry.io/api/0"


def get_sentry_api_key() -> str:
    """Get the Sentry API key from env var, .env file, or global config."""
    key = resolve_secret("SENTRY_API_KEY", config_attr="sentry_api_key")
    if key:
        return key

    raise IntegrationError(
        "Sentry API key not found. Set SENTRY_API_KEY env var or add to ~/.maelstrom/config.yaml:\n"
        "  sentry:\n"
        '    api_key: "your-api-key"'
    )


def get_sentry_config() -> tuple[str, str]:
    """Get the Sentry org and project from config.

    Returns:
        Tuple of (sentry_org, sentry_project).

    Raises:
        IntegrationError: If config is missing.
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

    raise IntegrationError(
        "Sentry not configured. Add to .maelstrom.yaml:\n"
        "  sentry:\n"
        '    org: "your-org"\n'
        '    project_id: "your-project-id"'
    )


def api_request(
    endpoint: str,
    params: dict | None = None,
    method: str = "GET",
    body: dict | None = None,
) -> dict | list:
    """Make a REST API request to Sentry.

    Args:
        endpoint: API endpoint path.
        params: Optional query parameters.
        method: HTTP method (default: GET).
        body: Optional request body (will be JSON-encoded).

    Returns:
        The response data.

    Raises:
        IntegrationError: On API errors.
    """
    api_key = get_sentry_api_key()
    url = f"{SENTRY_API_URL}{endpoint}"

    return request_json(
        url,
        method=method,
        headers={"Authorization": f"Bearer {api_key}"},
        json_body=body,
        params=params,
    )


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

    stacktrace = exception.get("stacktrace") or {}
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
                for _, code in context[:suspect_idx]:
                    lines.append(f"    {code}")
                # Suspect line
                _, suspect_code = context[suspect_idx]
                lines.append(f"    {suspect_code}  <-- SUSPECT LINE")
                # Post-context lines
                for _, code in context[suspect_idx + 1 :]:
                    lines.append(f"    {code}")
            else:
                # No suspect line found, just show all context
                for _, code in context:
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


def resolve_issue(issue_id: str) -> str:
    """Mark a Sentry issue resolved in the next release. Raises on failure.

    The reusable core shared by ``mael sentry resolve-issue`` and the task
    lifecycle action ``sentry.resolve``. Returns the two-line result.
    """
    sentry_org, _ = get_sentry_config()

    endpoint = f"/organizations/{sentry_org}/issues/{issue_id}/"
    body = {"status": "resolved", "statusDetails": {"inNextRelease": True}}

    result = api_request(endpoint, method="PUT", body=body)

    if not isinstance(result, dict):
        raise IntegrationError(f"Unexpected Sentry API response: {result!r}")
    status = result.get("status", "unknown")
    title = result.get("title", issue_id)
    return f"Resolved: {title}\nStatus: {status}"
