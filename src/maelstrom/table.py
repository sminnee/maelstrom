"""Table rendering utilities."""

import click

from .integrations._format import format_table


def draw_table(rows: list[dict[str, str]], columns: list[str]) -> None:
    """Render a table with dynamic column widths and print it.

    Thin wrapper over :func:`maelstrom.integrations._format.format_table` — the
    single width algorithm — that prints the result via ``click.echo``. Prints
    nothing when there are no rows.

    Args:
        rows: List of dictionaries, each representing a row.
        columns: List of column names to display (in order).
    """
    if not rows:
        return
    click.echo(format_table(rows, columns))
