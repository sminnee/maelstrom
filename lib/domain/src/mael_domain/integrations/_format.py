"""Shared time formatting for service integrations.

``format_relative_time`` and ``format_datetime`` previously lived in
``sentry.py``; both ``sentry`` and ``uptimerobot`` need them, so they live here
to remove the ``uptimerobot → sentry`` cross-import. Bodies are unchanged.
"""

import re
from datetime import UTC, datetime

from .errors import IntegrationError


def parse_since(since: str) -> int:
    """Parse a `--since` duration like '24h' or '7d' into seconds.

    Supported suffixes: s, m, h, d.
    """
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", since)
    if not match:
        raise IntegrationError(
            f"Invalid --since value '{since}'. Use forms like '30m', '24h', '7d'."
        )
    value = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


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


def format_datetime(iso_timestamp: str) -> str:
    """Convert ISO timestamp to DD/MM/YYYY, HH:MM:SS format.

    Args:
        iso_timestamp: ISO 8601 timestamp string.

    Returns:
        Formatted datetime string.
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return dt.strftime("%d/%m/%Y, %H:%M:%S")
