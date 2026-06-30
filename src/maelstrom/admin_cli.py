"""CLI commands for maelstrom self-management (install, self-update)."""

import shutil
import subprocess
from pathlib import Path

import click

from .claude_integration import install_claude_integration
from .context import harden_global_config


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

    # Re-sync dependencies. `git pull` updates the source, but a new dependency
    # in pyproject.toml is invisible to the installed environment until uv
    # re-resolves it — so commands that import the new package crash with
    # ModuleNotFoundError after an otherwise-successful self-update. Reinstall
    # the editable tool to pick up dependency changes.
    #
    # This is best-effort: the pull already landed, so a missing/failing uv must
    # warn rather than abort. Installs that aren't uv tools (plain `uv run`, a
    # system package manager) handle their own deps and simply skip this.
    uv = shutil.which("uv")
    if uv is None:
        click.echo(
            "  Warning: 'uv' not found; skipping dependency sync. If a new "
            "dependency was added, reinstall maelstrom to pick it up.",
            err=True,
        )
    else:
        click.echo("Syncing dependencies...")
        # --force overwrites the existing `mael` entrypoint: self-update always
        # reinstalls over a live install, and without it uv aborts with
        # "Executable already exists: mael".
        sync = subprocess.run(
            [
                uv, "tool", "install",
                "--editable", str(repo_root),
                "--reinstall", "--force",
            ],
            capture_output=True,
            text=True,
        )
        # uv writes its progress to stderr; surface it whatever the outcome.
        if sync.stderr.strip():
            click.echo(sync.stderr, err=True)
        if sync.returncode != 0:
            click.echo(
                "  Warning: dependency sync failed. The code updated, but new "
                "dependencies may be missing — reinstall maelstrom manually if "
                "commands fail.",
                err=True,
            )

    click.echo("Updating Claude Code integration...")
    messages = install_claude_integration()
    for msg in messages:
        click.echo(f"  {msg}")

    # Tighten any loose perms on the global config / ~/.maelstrom while we're
    # touching the install. The config carries plaintext API keys; doctor is the
    # other place this runs, but self-update is a natural "tidy my install" hook.
    for msg in harden_global_config():
        click.echo(f"  {msg}")

    click.echo("Update complete.")
