"""Table rendering utilities."""


def format_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    """Render a table with dynamic column widths and return it as a string.

    Returns an empty string for no rows.

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
