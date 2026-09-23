"""The ``mael uptimerobot`` commands."""

import time

import click

from . import uptimerobot
from ._format import format_datetime, format_relative_time, parse_since
from .group_cli import IntegrationGroup


@click.group("uptimerobot", cls=IntegrationGroup)
def uptimerobot_group():
    """UptimeRobot monitor and outage commands."""
    pass


UPTIME_WINDOWS = "1-7-30"
UPTIME_WINDOW_HEADERS = ("24h", "7d", "30d")


@uptimerobot_group.command("status")  # type: ignore[attr-defined]
def cmd_status() -> None:
    """Show current status and uptime of configured monitors."""
    monitor_ids = uptimerobot.get_uptimerobot_monitors()
    # logs=1 with logs_limit=1 is what populates last_event_datetime; without
    # it that field reflects creation/config time, not the most recent event.
    monitors = uptimerobot.fetch_monitors(
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
        status = uptimerobot.format_status(int(monitor.get("status", -1)))
        # Prefer the timestamp of the most recent log entry — last_event_datetime
        # in the bare response often reflects creation/config time, not events.
        logs = monitor.get("logs") or []
        log_ts = int(logs[0].get("datetime", 0)) if logs else 0
        last_event_ts = log_ts or monitor.get("last_event_datetime")
        if last_event_ts:
            last_event = format_relative_time(
                uptimerobot.epoch_to_iso(int(last_event_ts))
            )
        else:
            last_event = "-"
        ratios = uptimerobot.parse_uptime_ratios(monitor.get("custom_uptime_ratio"))
        while len(ratios) < len(UPTIME_WINDOW_HEADERS):
            ratios.append("-")
        rows.append(
            (
                monitor_id,
                name,
                status,
                last_event,
                *ratios[: len(UPTIME_WINDOW_HEADERS)],
            )
        )

    headers = ("ID", "Name", "Status", "Last Event", *UPTIME_WINDOW_HEADERS)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_row = (
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    )
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        click.echo(
            "| "
            + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
            + " |"
        )


@uptimerobot_group.command("outages")  # type: ignore[attr-defined]
@click.option(
    "--since", default="24h", help="Time window (e.g. 30m, 24h, 7d). Default: 24h"
)
@click.option(
    "--limit", default=20, type=int, help="Max log entries per monitor. Default: 20"
)
def cmd_outages(since: str, limit: int) -> None:
    """List recent outage log entries across configured monitors."""
    window_seconds = parse_since(since)
    cutoff = int(time.time()) - window_seconds

    monitor_ids = uptimerobot.get_uptimerobot_monitors()
    monitors = uptimerobot.fetch_monitors(monitor_ids, logs=True, logs_limit=limit)

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
                    format_datetime(uptimerobot.epoch_to_iso(ts)),
                    uptimerobot.format_duration(duration),
                    reason,
                )
            )

    if not entries:
        click.echo(f"No outages in the last {since}.")
        return

    entries.sort(key=lambda row: row[0], reverse=True)

    click.echo(f"# UptimeRobot Outages (last {since})")
    click.echo("")

    rows = [
        (name, started, duration, reason)
        for _, name, started, duration, reason in entries
    ]
    headers = ("Monitor", "Started", "Duration", "Reason")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell.replace("|", "\\|")))

    header_row = (
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    )
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        safe = tuple(cell.replace("|", "\\|") for cell in row)
        click.echo(
            "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(safe)) + " |"
        )


@uptimerobot_group.command("monitors")  # type: ignore[attr-defined]
def cmd_monitors() -> None:
    """List all monitors on the account, regardless of project config."""
    monitors = uptimerobot.fetch_monitors(None)

    if not monitors:
        click.echo("No monitors found on this account.")
        return

    click.echo("# UptimeRobot Monitors (account)")
    click.echo("")

    rows: list[tuple[str, str, str, str]] = []
    for monitor in monitors:
        monitor_id = str(monitor.get("id", ""))
        name = str(monitor.get("friendly_name", ""))[:50].replace("|", "\\|")
        status = uptimerobot.format_status(int(monitor.get("status", -1)))
        url = str(monitor.get("url", ""))
        rows.append((monitor_id, name, status, url))

    headers = ("ID", "Name", "Status", "URL")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_row = (
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    )
    separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    click.echo(header_row)
    click.echo(separator)
    for row in rows:
        click.echo(
            "| "
            + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
            + " |"
        )
