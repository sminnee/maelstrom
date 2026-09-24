"""UptimeRobot monitor and outage query integration for maelstrom."""

from pathlib import Path

from ..config import load_config_or_default
from ..context import resolve_context
from ._auth import resolve_secret
from ._http import request_json
from .errors import IntegrationError

UPTIMEROBOT_API_URL = "https://api.uptimerobot.com/v2"


STATUS_LABELS = {
    0: "paused",
    1: "not-checked",
    2: "up",
    8: "seems-down",
    9: "down",
}

LOG_TYPE_LABELS = {
    1: "down",
    2: "up",
    98: "started",
    99: "paused",
}


def get_uptimerobot_api_key() -> str:
    """Get the UptimeRobot API key from env var, .env file, or global config."""
    key = resolve_secret("UPTIMEROBOT_API_KEY", config_attr="uptimerobot_api_key")
    if key:
        return key

    raise IntegrationError(
        "UptimeRobot API key not found. Set UPTIMEROBOT_API_KEY env var or add to ~/.maelstrom/config.yaml:\n"
        "  uptimerobot:\n"
        '    api_key: "your-api-key"'
    )


def get_uptimerobot_monitors() -> list[str] | None:
    """Get the configured UptimeRobot monitor IDs from project config.

    Returns:
        List of monitor IDs configured for this project, or None if unconfigured.
    """
    try:
        ctx = resolve_context(None, require_project=False, require_worktree=False)
        if ctx.worktree_path:
            config = load_config_or_default(ctx.worktree_path)
            if config.uptimerobot_monitors:
                return [str(m) for m in config.uptimerobot_monitors]
    except ValueError:
        pass

    config = load_config_or_default(Path.cwd())
    if config.uptimerobot_monitors:
        return [str(m) for m in config.uptimerobot_monitors]

    return None


def api_request(endpoint: str, body: dict | None = None) -> dict:
    """Make a POST request to the UptimeRobot v2 API.

    Args:
        endpoint: API endpoint path (e.g. "/getMonitors").
        body: Optional form fields. The api_key is injected automatically.

    Returns:
        The parsed JSON response.

    Raises:
        IntegrationError: On HTTP errors or `stat == "fail"` responses.
    """
    api_key = get_uptimerobot_api_key()
    url = f"{UPTIMEROBOT_API_URL}{endpoint}"

    form: dict = {"api_key": api_key, "format": "json"}
    if body:
        form.update(body)

    payload = request_json(
        url,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        form_body=form,
    )

    if payload.get("stat") == "fail":
        error = payload.get("error", {})
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or "unknown error"
        else:
            message = str(error)
        raise IntegrationError(f"UptimeRobot API error: {message}")

    return payload


def format_status(code: int) -> str:
    """Render an UptimeRobot monitor status code as a label."""
    return STATUS_LABELS.get(code, f"unknown({code})")


def format_log_type(code: int) -> str:
    """Render an UptimeRobot log type code as a label."""
    return LOG_TYPE_LABELS.get(code, f"unknown({code})")


def format_duration(seconds: int) -> str:
    """Render an outage duration in seconds as a compact human string."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        if secs:
            return f"{minutes}m {secs}s"
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    days, hours = divmod(hours, 24)
    if hours:
        return f"{days}d {hours}h"
    return f"{days}d"


def epoch_to_iso(ts: int) -> str:
    """Convert UptimeRobot epoch seconds to ISO 8601 UTC."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def fetch_monitors(
    monitor_ids: list[str] | None,
    *,
    logs: bool = False,
    logs_limit: int = 50,
    uptime_ratios: str | None = None,
) -> list[dict]:
    """Fetch monitors from the API, optionally restricted by ID."""
    body: dict = {"response_times": 0}
    if monitor_ids:
        body["monitors"] = "-".join(str(m) for m in monitor_ids)
    if logs:
        body["logs"] = 1
        body["logs_limit"] = logs_limit
    if uptime_ratios:
        body["custom_uptime_ratios"] = uptime_ratios

    payload = api_request("/getMonitors", body)
    monitors = payload.get("monitors", [])
    if not isinstance(monitors, list):
        return []
    return monitors


def parse_uptime_ratios(value: str | None) -> list[str]:
    """Parse a `custom_uptime_ratios` response string into per-window strings.

    UptimeRobot returns e.g. "99.987-99.991-99.823". Missing or empty fields
    become "-" so the table still aligns.
    """
    if not value:
        return []
    parts = []
    for chunk in str(value).split("-"):
        chunk = chunk.strip()
        if not chunk:
            parts.append("-")
            continue
        try:
            parts.append(f"{float(chunk):.2f}%")
        except ValueError:
            parts.append(chunk)
    return parts
