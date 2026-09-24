"""Print a table from a CLI command."""

import click

from mael_common.table import format_table


def draw_table(rows: list[dict[str, str]], columns: list[str]) -> None:
    """Print ``format_table(rows, columns)``; print nothing for no rows."""
    if not rows:
        return
    click.echo(format_table(rows, columns))
