"""CLI commands for managing the workspace status display."""

import click

from .cmux import mael_layout


@click.group("status")
def status():
    """Manage workspace status display."""


@status.command("set")
@click.argument("text")
def status_set(text):
    """Set the workspace status text."""
    mael_layout.set_status(text)  # no-op outside cmux


@status.command("clear")
def status_clear():
    """Clear the workspace status."""
    mael_layout.clear_status()  # no-op outside cmux
