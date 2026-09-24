"""The ``mael schedule`` group: install, uninstall and report the launchd agent."""

import click

from . import schedule_launchd


@click.group("schedule")
def schedule_group() -> None:
    """Install/uninstall the background scheduled-task launchd agent (macOS)."""


@schedule_group.command("install")
def schedule_install() -> None:
    """Opt this machine in: write the marker and load the launchd agent."""
    schedule_launchd.install_marker()
    for msg in schedule_launchd.ensure_schedule_agent():
        click.echo(msg)


@schedule_group.command("uninstall")
def schedule_uninstall() -> None:
    """Opt this machine out: remove the marker and tear the agent down.

    Also clears a repeating ``pmset`` wake left by an older ``--wake-at``
    install. That step needs ``sudo``, and prompts only on a machine that has
    such a wake.
    """
    schedule_launchd.uninstall_marker()
    for msg in schedule_launchd.ensure_schedule_agent():
        click.echo(msg)
    for msg in schedule_launchd.clear_leftover_wake():
        click.echo(msg)


@schedule_group.command("status")
def schedule_status() -> None:
    """Report agent state (marker, plist, loaded job, log tail)."""
    for msg in schedule_launchd.status_lines():
        click.echo(msg)
