"""Shared time formatting for service integrations.

``format_relative_time`` and ``format_datetime`` previously lived in
``sentry.py``; both ``sentry`` and ``uptimerobot`` need them, so they live here
to remove the ``uptimerobot → sentry`` cross-import. Bodies are unchanged.
"""

from datetime import UTC, datetime


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


def format_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    """Render a table with dynamic column widths and return it as a string.

    The single width algorithm shared with ``table.draw_table`` (which prints
    the result via ``click.echo``). Returns an empty string for no rows.

    Args:
        rows: List of dictionaries, each representing a row.
        columns: List of column names to display (in order).
    """
    if not rows:
        return ""

    # Calculate max width for each column (including header)
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            value = row.get(col, "")
            widths[col] = max(widths[col], len(value))

    # Build header with 2-space padding between columns
    header_parts = [f"{col:<{widths[col]}}" for col in columns]
    lines = ["  ".join(header_parts)]

    # Separator spanning the full table width
    total_width = sum(widths.values()) + 2 * (len(columns) - 1)
    lines.append("-" * total_width)

    for row in rows:
        row_parts = [f"{row.get(col, ''):<{widths[col]}}" for col in columns]
        lines.append("  ".join(row_parts))

    return "\n".join(lines)
