"""CLI commands for maelstrom self-management (install, self-update, self-env, admin)."""

import os
import shutil
import subprocess
from pathlib import Path

import click

from .agent_transport import ROOT_ENV
from .claude_integration import install_claude_integration
from .cli_async import AsyncGroup
from .context import get_maelstrom_dir, harden_global_config
from .env_cli import env
from .shell import mael_path
from .state_db.migrate import open_state_db
from .state_db.paths import get_state_db_path
from .state_db.types import StateDbError
from .util import sanitise_child_env
from .worktree_model import MAIN_WORKTREE_FOLDER


@click.command("install")
def cmd_install():
    """Install maelstrom's Claude Code skills and hooks."""
    messages = install_claude_integration()
    for msg in messages:
        click.echo(msg)


#: Marks a `mael` that is already a shim, so a second update replaces it
#: rather than wrapping it again.
SHIM_MARKER = "# maelstrom daemon-root shim"

#: A `mael` under this directory belongs to a virtualenv, not to the PATH
#: install. Under `uv run` that is what the name resolves to.
VENV_DIR = ".venv"


def _write_daemon_root_shim() -> str:
    """Give the `mael` on PATH the everyday daemon's root.

    That `mael` is a uv entrypoint, which reads no `.env`. `UV_ENV_FILE` only
    reaches `uv run` in a directory that has one, so a bare `mael agent list`
    anywhere else names no root and has no daemon to reach.

    The shim supplies `_main`'s root only when nothing else has: a worktree's
    `uv run mael` and a command inside a driven session both arrive with a root
    already set, and overriding either would send it to the wrong daemon.

    A `mael` inside a virtualenv is left alone. Under `uv run` the name resolves
    to the worktree's own `.venv/bin/mael`, and shimming there would corrupt
    that venv while leaving the real PATH entrypoint rootless.

    The lookup runs against a sanitised environment. `mael` lives in
    `_main/.venv/bin`, and an inherited activation puts that directory first on
    PATH, so a raw lookup names the venv copy wherever the command is run — and
    the test above then skips every `mael`, including the one on the real PATH.

    Best-effort, like the dependency sync above it: the update has already
    landed by this point, so a `mael` that cannot be rewritten warns rather than
    aborting, and the entrypoint is put back the way it was.

    Returns what to tell the user.
    """
    path = Path(mael_path(sanitise_child_env(os.environ)))
    root = get_maelstrom_dir() / "daemons" / MAIN_WORKTREE_FOLDER
    if VENV_DIR in path.parts:
        return (
            f"`mael` at {path} is a virtualenv entrypoint, not the `mael` on "
            "your PATH, so it was left alone. Run `mael self-update` outside a "
            "worktree to point your PATH `mael` at a daemon root."
        )
    advice = (
        f"Warning: could not point `mael` at {root}. "
        f"Set {ROOT_ENV} in your shell instead."
    )
    real = path.with_name(f"{path.name}-real")
    try:
        moved = SHIM_MARKER not in path.read_text(errors="replace")
        if moved:
            # First time: move the entrypoint aside, so the shim can exec it.
            path.replace(real)
    except OSError:
        return advice
    shim = (
        "#!/bin/sh\n"
        f"{SHIM_MARKER} — written by `mael self-update`.\n"
        "# The everyday daemon's root, unless something already named one.\n"
        f'export {ROOT_ENV}="${{{ROOT_ENV}:-{root}}}"\n'
        f'exec "{real}" "$@"\n'
    )
    try:
        path.write_text(shim)
        path.chmod(0o755)
    except OSError:
        # The rename already happened, so returning here would leave the user
        # with no `mael` at all — and no way to act on the advice.
        if moved:
            real.replace(path)
        return advice
    return f"`mael` reaches the daemon on {root}"


def resolve_install_root(module_dir: Path) -> Path:
    """The checkout ``self-update`` reinstalls: ``_main``, not the caller's worktree.

    The editable install is shared by the whole machine, so its target must not
    depend on which copy of the source happens to be running. Deriving it from
    ``__file__`` did exactly that: ``mael self-update`` run from a worktree
    repointed the install at that worktree, and every bare ``mael`` then ran
    its in-progress code.

    A checkout with no ``_main`` beside it is an ordinary clone rather than a
    maelstrom project, and updates in place.

    Args:
        module_dir: The ``maelstrom`` package directory, i.e. ``__file__``'s parent.

    Returns:
        The checkout to pull and reinstall.
    """
    repo_root = module_dir.parent.parent
    if repo_root.name == MAIN_WORKTREE_FOLDER:
        return repo_root
    main = repo_root.parent / MAIN_WORKTREE_FOLDER
    return main if main.is_dir() else repo_root


@click.command("self-update")
def cmd_self_update():
    """Update maelstrom to the latest version from git."""
    # Always `_main`, whichever worktree's copy of this code is running.
    repo_root = resolve_install_root(Path(__file__).parent)
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

    click.echo(f"  {_write_daemon_root_shim()}")

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


@click.group("admin", cls=AsyncGroup)
def cmd_admin() -> None:
    """Look after maelstrom's own state."""


@cmd_admin.command("migrate")
async def cmd_migrate() -> None:
    """Bring the state database up to this build's schema.

    The only thing that writes a schema, and the command every refusal names.
    The desk ladder's second rung brings an existing ``desk.json`` in, so a
    user's canvas survives the move; the file is left on disk as a fallback.
    """
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        await db.migrate()
    except StateDbError as exc:
        # A database from a newer build refuses here too, and the message
        # already says what to do. A traceback would bury it.
        raise click.ClickException(str(exc)) from exc
    finally:
        db.close()
    click.echo(f"The state database at {path} is up to date.")
