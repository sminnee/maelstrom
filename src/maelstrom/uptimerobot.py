"""UptimeRobot monitor and outage query integration for maelstrom."""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import click

from .config import load_config_or_default
from .context import resolve_context
from .sentry import format_datetime, format_relative_time

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
    if value := os.environ.get("UPTIMEROBOT_API_KEY"):
        return value

    # Find .env file in current directory or parents
    current = Path.cwd()
    while current != current.parent:
        env_path = current / ".env"
        if env_path.exists():
            content = env_path.read_text()
            pattern = r"^UPTIMEROBOT_API_KEY\s*=\s*[\"']?([^\"'\n]+)[\"']?"
            if match := re.search(pattern, content, re.MULTILINE):
                return match.group(1)
            break
        current = current.parent

    # Fall back to global config
    from .context import load_global_config

    global_config = load_global_config()
    if global_config.uptimerobot_api_key:
        return global_config.uptimerobot_api_key

    raise click.ClickException(
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
        click.ClickException: On HTTP errors or `stat == "fail"` responses.
    """
    api_key = get_uptimerobot_api_key()
    url = f"{UPTIMEROBOT_API_URL}{endpoint}"

    form: dict = {"api_key": api_key, "format": "json"}
    if body:
        form.update(body)

    data = urllib.parse.urlencode(form).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    }

    req = urllib.request.Request(url, data=data, method="POST", headers=headers)

    try:
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise click.ClickException(f"HTTP Error {e.code}: {error_body}")

    if payload.get("stat") == "fail":
        error = payload.get("error", {})
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or "unknown error"
        else:
            message = str(error)
        raise click.ClickException(f"UptimeRobot API error: {message}")

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


def _parse_since(since: str) -> int:
    """Parse a `--since` duration like '24h' or '7d' into seconds.

    Supported suffixes: s, m, h, d.
    """
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", since)
    if not match:
        raise click.ClickException(
            f"Invalid --since value '{since}'. Use forms like '30m', '24h', '7d'."
        )
    value = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def _epoch_to_iso(ts: int) -> str:
    """Convert UptimeRobot epoch seconds to ISO 8601 UTC."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def _fetch_monitors(
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


# --- Click Commands ---


@click.group("uptimerobot")
def uptimerobot():
    """UptimeRobot monitor and outage commands."""
    pass


UPTIME_WINDOWS = "1-7-30"
UPTIME_WINDOW_HEADERS = ("24h", "7d", "30d")


@uptimerobot.command("status")  # type: ignore[attr-defined]
def cmd_status() -> None:
    """Show current status and uptime of configured monitors."""
    monitor_ids = get_uptimerobot_monitors()
    # logs=1 with logs_limit=1 is what populates last_event_datetime; without
    # it that field reflects creation/config time, not the most recent event.
    monitors = _fetch_monitors(
        monitor_ids,
        uptime_ratios=UPTIME_WINDOWS,
        logs=True,
        logs_limit=1,
    )

    if not monitors:
        click.echo("No monitors found.")
        return

    scope = "configured" if monitor_ids else "all account"
    click.echo(f"# UptimeRobot Status ({scope} monitors)")
    click.echo("")

    rows: list[tuple[str, ...]] = []
    for monitor in monitors:
        name = str(monitor.get("friendly_name", ""))[:50]
        name = name.replace("|", "\\|")
        monitor_id = str(monitor.get("id", ""))
        status = format_status(int(monitor.get("status", -1)))
        # Prefer the timestamp of the most recent log entry — last_event_datetime
        # in the bare response often reflects creation/config time, not events.
        logs = monitor.get("logs") or []
        log_ts = int(logs[0].get("datetime", 0)) if logs else 0
        last_event_ts = log_ts or monitor.get("last_event_datetime")
        if last_event_ts:
            last_event = format_relative_time(_epoch_to_iso(int(last_event_ts)))
        else:
            last_event = "-"
        ratios = parse_uptime_ratios(monitor.get("custom_uptime_ratio"))
        while len(ratios) < len(UPTIME_WINDOW_HEADERS):
            ratios.append("-")
        rows.append((monitor_id, name, status, last_event, *ratios[: len(UPTIME_WINDOW_HEADERS)]))

    headers = ("ID", "Name", "Status", "Last Event", *UPTIME_WINDOW_HEADERS)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_row = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        click.echo("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")


@uptimerobot.command("outages")  # type: ignore[attr-defined]
@click.option("--since", default="24h", help="Time window (e.g. 30m, 24h, 7d). Default: 24h")
@click.option("--limit", default=20, type=int, help="Max log entries per monitor. Default: 20")
def cmd_outages(since: str, limit: int) -> None:
    """List recent outage log entries across configured monitors."""
    window_seconds = _parse_since(since)
    cutoff = int(time.time()) - window_seconds

    monitor_ids = get_uptimerobot_monitors()
    monitors = _fetch_monitors(monitor_ids, logs=True, logs_limit=limit)

    if not monitors:
        click.echo("No monitors found.")
        return

    entries: list[tuple[int, str, str, str, str]] = []
    for monitor in monitors:
        name = str(monitor.get("friendly_name", ""))
        for log in monitor.get("logs", []) or []:
            log_type = int(log.get("type", -1))
            if log_type != 1:  # only "down" entries
                continue
            ts = int(log.get("datetime", 0))
            if ts < cutoff:
                continue
            duration = int(log.get("duration", 0) or 0)
            reason_raw = log.get("reason") or {}
            if isinstance(reason_raw, dict):
                reason = str(reason_raw.get("detail") or reason_raw.get("code") or "")
            else:
                reason = str(reason_raw)
            entries.append(
                (
                    ts,
                    name,
                    format_datetime(_epoch_to_iso(ts)),
                    format_duration(duration),
                    reason,
                )
            )

    if not entries:
        click.echo(f"No outages in the last {since}.")
        return

    entries.sort(key=lambda row: row[0], reverse=True)

    click.echo(f"# UptimeRobot Outages (last {since})")
    click.echo("")

    rows = [(name, started, duration, reason) for _, name, started, duration, reason in entries]
    headers = ("Monitor", "Started", "Duration", "Reason")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell.replace("|", "\\|")))

    header_row = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        safe = tuple(cell.replace("|", "\\|") for cell in row)
        click.echo("| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(safe)) + " |")


@uptimerobot.command("monitors")  # type: ignore[attr-defined]
def cmd_monitors() -> None:
    """List all monitors on the account, regardless of project config."""
    monitors = _fetch_monitors(None)

    if not monitors:
        click.echo("No monitors found on this account.")
        return

    click.echo("# UptimeRobot Monitors (account)")
    click.echo("")

    rows: list[tuple[str, str, str, str]] = []
    for monitor in monitors:
        monitor_id = str(monitor.get("id", ""))
        name = str(monitor.get("friendly_name", ""))[:50].replace("|", "\\|")
        status = format_status(int(monitor.get("status", -1)))
        url = str(monitor.get("url", ""))
        rows.append((monitor_id, name, status, url))

    headers = ("ID", "Name", "Status", "URL")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_row = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        click.echo("| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |")
