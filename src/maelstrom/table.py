"""Table rendering utilities."""

import click


def draw_table(rows: list[dict[str, str]], columns: list[str]) -> None:
    """Render a table with dynamic column widths.

    Args:
        rows: List of dictionaries, each representing a row.
        columns: List of column names to display (in order).
    """
    if not rows:
        return

    # Calculate max width for each column (including header)
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = len(col)
        for row in rows:
            value = row.get(col, "")
            widths[col] = max(widths[col], len(value))

    # Build format string with 2-space padding between columns
    header_parts = [f"{col:<{widths[col]}}" for col in columns]
    header = "  ".join(header_parts)

    # Calculate total width for separator
    total_width = sum(widths.values()) + 2 * (len(columns) - 1)

    # Print header and separator
    click.echo(header)
    click.echo("-" * total_width)

    # Print rows
    for row in rows:
        row_parts = [f"{row.get(col, ''):<{widths[col]}}" for col in columns]
        click.echo("  ".join(row_parts))
