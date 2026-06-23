"""CLI commands for maelstrom self-management (install, self-update)."""

import subprocess
from pathlib import Path

import click

from .claude_integration import install_claude_integration


@click.command("install")
@click.option("--no-monitor", is_flag=True, help="Skip installing the session-tracking MCP channel, hooks, and channel dependencies.")
def cmd_install(no_monitor):
    """Install maelstrom's Claude Code skills and hooks."""
    messages = install_claude_integration(monitor=not no_monitor)
    for msg in messages:
        click.echo(msg)


@click.command("self-update")
def cmd_self_update():
    """Update maelstrom to the latest version from git."""
    # Get the maelstrom package root directory
    module_dir = Path(__file__).parent
    repo_root = module_dir.parent.parent
    git_dir = repo_root / ".git"

    # Check if it's a git checkout
    if not git_dir.exists():
        raise click.ClickException(
            "Cannot self-update: maelstrom is not installed from a git checkout. "
            "Please reinstall from git or use your package manager to update."
        )

    # Run git pull
    click.echo(f"Updating maelstrom from {repo_root}...")
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip():
            click.echo(result.stdout)
        if result.stderr.strip():
            click.echo(result.stderr, err=True)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Git pull failed: {e.stderr or e.stdout or str(e)}")

    click.echo("Updating Claude Code integration...")
    messages = install_claude_integration()
    for msg in messages:
        click.echo(f"  {msg}")

    click.echo("Update complete.")
