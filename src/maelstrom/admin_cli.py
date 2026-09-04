"""CLI commands for maelstrom self-management (install, self-update, self-env)."""

import shutil
import subprocess
from pathlib import Path

import click

from .claude_integration import install_claude_integration
from .context import harden_global_config
from .env_cli import env
from .worktree_model import MAIN_WORKTREE_FOLDER


@click.command("install")
@click.option(
    "--no-monitor",
    is_flag=True,
    help="Skip installing the session-tracking MCP channel, hooks, and channel dependencies.",
)
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
                uv,
                "tool",
                "install",
                "--editable",
                str(repo_root),
                "--reinstall",
                "--force",
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


# `mael self-env <verb>` is `mael env <verb>` aimed at the maelstrom project's
# own `_main` — see `docs/guide/worktrees.md`.
SELF_ENV_PROJECT = "maelstrom"
SELF_ENV_TARGET = f"{SELF_ENV_PROJECT}.{MAIN_WORKTREE_FOLDER}"

# `mael env`'s verbs, and how each one takes its target: through the `--worktree`
# option, or as a positional argument.
_TARGET_AS_OPTION = ("start", "stop", "restart", "logs")
_TARGET_AS_ARGUMENT = ("status", "reset", "open")


def _self_env_command(name: str) -> click.Command:
    """Wrap one `mael env` command so it always runs against `maelstrom._main`."""
    command = env.get_command(None, name)  # type: ignore[arg-type]
    assert command is not None, f"mael env has no {name!r} command"

    as_option = name in _TARGET_AS_OPTION
    target_param = "worktree_opt" if as_option else "target"

    class Targeted(click.Command):
        def parse_args(self, ctx, args):
            # Prepended, so a stray argument is the surplus one the error names.
            target = ["-w", SELF_ENV_TARGET] if as_option else [SELF_ENV_TARGET]
            return command.parse_args(ctx, target + list(args))

    # The target is fixed, so its parameter is hidden from --help. It stays on
    # the real command, which is what parses the arguments above.
    return Targeted(
        name=name,
        callback=command.callback,
        params=[p for p in command.params if p.name != target_param],
        help=command.help,
        short_help=command.short_help,
    )


class SelfEnvGroup(click.Group):
    """`mael env`'s commands, each aimed at maelstrom's fixed environment."""

    def list_commands(self, ctx):
        return sorted(_TARGET_AS_OPTION + _TARGET_AS_ARGUMENT)

    def get_command(self, ctx, name):
        if name not in _TARGET_AS_OPTION + _TARGET_AS_ARGUMENT:
            return None
        return _self_env_command(name)


@click.group("self-env", cls=SelfEnvGroup)
def cmd_self_env():
    """Manage maelstrom's own fixed environment (its `_main` worktree)."""
