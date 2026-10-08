"""CLI commands for maelstrom self-management (install, self-update, self-env, admin)."""

import os
import shutil
import subprocess
from pathlib import Path

import click

from mael_agent.agent_transport import ROOT_ENV
from mael_common.cli_async import AsyncGroup
from mael_common.shell import mael_path
from mael_common.util import get_maelstrom_dir, now_iso, sanitise_child_env
from mael_domain.context import harden_global_config
from mael_domain.env import ServiceVersionError, restart_changed, service_versions
from mael_domain.notebook_root import NOTEBOOK_ROOT_ENV
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.state_db.types import StateDbError
from mael_domain.task import task_key
from mael_domain.task_export import Queued, SqliteExportQueue
from mael_domain.task_table import TABLE as TASKS_TABLE
from mael_domain.worktree import run_install_cmd
from mael_domain.worktree_model import MAIN_WORKTREE_FOLDER

from .claude_integration import install_claude_integration
from .env_cli import WORKTREE_PARAM, env, make_store, report_version_changes


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
        f'export {NOTEBOOK_ROOT_ENV}="${{{NOTEBOOK_ROOT_ENV}:-{get_maelstrom_dir()}}}"\n'
        "export MAEL_PRODUCTION=1\n"
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
        module_dir: The ``mael_cli`` package directory, i.e. ``__file__``'s parent.

    Returns:
        The checkout to pull and reinstall.
    """
    # cli/src/mael_cli/ -> the repository root.
    repo_root = module_dir.parents[2]
    if repo_root.name == MAIN_WORKTREE_FOLDER:
        return repo_root
    main = repo_root.parent / MAIN_WORKTREE_FOLDER
    return main if main.is_dir() else repo_root


#: The tangier SHA bucket that hashes the CLI and the libraries it bundles.
CLI_BUCKET = "cli"

#: The self-env service whose restart interrupts running agents.
AGENT_DAEMON_SERVICE = "agent-daemon"


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

    # The `cli` SHA bucket before the pull. Only a change to the CLI or its
    # libraries needs the slow reinstall.
    cli_before = _cli_hash(repo_root)

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
    cli_after = _cli_hash(repo_root)
    uv = shutil.which("uv")
    if cli_before is not None and cli_before == cli_after:
        # No version on either side fails open: the reinstall runs.
        click.echo("CLI unchanged; skipping its reinstall.")
    elif uv is None:
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
                # The workspace root builds no package.
                str(repo_root / "cli"),
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

    _update_self_env(repo_root)

    click.echo("Update complete.")


def _cli_hash(repo_root: Path) -> str | None:
    """The `cli` SHA bucket's hash, or None when tangier cannot give one."""
    try:
        return service_versions(repo_root, [CLI_BUCKET]).get(CLI_BUCKET)
    except ServiceVersionError:
        return None


def _update_self_env(repo_root: Path) -> None:
    """Install new dependencies in `_main`, then restart its changed services.

    Best-effort, as the dependency sync is: the pull has already landed.
    """
    click.echo("Installing dependencies in _main...")
    try:
        run_install_cmd(repo_root)
    except (OSError, subprocess.CalledProcessError) as e:
        click.echo(f"  Warning: install_cmd failed: {e}", err=True)

    click.echo("Restarting changed services...")
    try:
        changes = restart_changed(
            make_store(), SELF_ENV_PROJECT, MAIN_WORKTREE_FOLDER, repo_root
        )
    except (RuntimeError, ValueError, TimeoutError) as e:
        # A start can fail after its stop, so a changed service may be down.
        click.echo(
            f"  Warning: the restart failed: {e}. A changed service may be "
            "stopped; run `mael self-env start` to start it.",
            err=True,
        )
        return
    report_version_changes(changes)
    if any(c.name == AGENT_DAEMON_SERVICE for c in changes):
        click.echo("  The agent daemon restarted. Mid-turn agents resume.")


# `mael self-env <verb>` is `mael env <verb>` aimed at the maelstrom project's
# own `_main` — see `docs/guide/worktrees.md`.
SELF_ENV_PROJECT = "maelstrom"
SELF_ENV_TARGET = f"{SELF_ENV_PROJECT}.{MAIN_WORKTREE_FOLDER}"


def _targets_a_worktree(command: click.Command) -> bool:
    """Whether a `mael env` command acts on one worktree, named with -w."""
    return any(p.name == WORKTREE_PARAM for p in command.params)


def _self_env_command(command: click.Command) -> click.Command:
    """Wrap one `mael env` command so it always runs against `maelstrom._main`."""
    # -w is fixed, so the user cannot give it. A deprecated alias would take a
    # stray argument as a second target, so it goes too.
    dropped = [p for p in command.params if p.name == WORKTREE_PARAM or p.deprecated]

    class Targeted(click.Command):
        def parse_args(self, ctx, args):
            rest = super().parse_args(ctx, args)
            for p in dropped:
                assert p.name is not None
                ctx.params[p.name] = None
            ctx.params[WORKTREE_PARAM] = SELF_ENV_TARGET
            return rest

    return Targeted(
        name=command.name,
        callback=command.callback,
        params=[p for p in command.params if p not in dropped],
        help=command.help,
        short_help=command.short_help,
    )


class SelfEnvGroup(click.Group):
    """`mael env`'s worktree commands, each aimed at maelstrom's fixed environment."""

    def list_commands(self, ctx):
        return sorted(
            name
            for name, command in env.commands.items()
            if _targets_a_worktree(command)
        )

    def get_command(self, ctx, name):
        command = env.commands.get(name)
        if command is None or not _targets_a_worktree(command):
            return None
        return _self_env_command(command)


@click.group("self-env", cls=SelfEnvGroup)
def cmd_self_env():
    """Manage maelstrom's own fixed environment (its `_main` worktree)."""


@click.group("admin", cls=AsyncGroup)
def cmd_admin() -> None:
    """Look after maelstrom's own state."""


@cmd_admin.command("export-queue")
@click.option(
    "--rebuild",
    is_flag=True,
    help="Queue every task for export, whatever the queue holds now.",
)
async def cmd_export_queue(rebuild: bool) -> None:
    """Report what the markdown export still owes the files.

    The orchestrator drains the queue, so a depth that does not fall means the
    server is down or its drain is failing. The oldest entry is the useful
    number: a deep queue that is seconds old is a busy notebook, and a shallow
    one that is hours old is a drain that stopped.

    ``--rebuild`` queues every task in the notebook. A task write queues its own
    export, so this is for the file that went missing without the row moving —
    deleted by hand, or lost to a git failure. The queue is one row per task, so
    rebuilding twice costs one export each.
    """
    db = open_state_db(get_state_db_path())
    try:
        await db.check()
        queue = SqliteExportQueue(db)
        if rebuild:
            queued = await _rebuild_export_queue(db, queue)
            click.echo(f"Queued {queued} task(s) for export.")
            return
        depth = await queue.depth()
        oldest = await queue.oldest()
    except StateDbError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        db.close()
    if not depth:
        click.echo("The markdown export is up to date.")
        return
    click.echo(f"The markdown export owes {depth} task(s).")
    click.echo(f"The oldest has waited since {oldest}.")


async def _rebuild_export_queue(db, queue: SqliteExportQueue) -> int:
    """Queue every task row, and return how many were queued.

    The path is each task's current one, so a rebuild writes each task where it
    belongs now. A file at a status the task has since left is not this
    command's to find: nothing records that it was ever written.
    """
    entries = [
        Queued(
            id=row["id"],
            path=task_key(row["project"], row["status"], row["task_id"]),
            deleted=False,
            queued_at=now_iso(),
        )
        for row in await db.read_all(TASKS_TABLE)
    ]
    await queue.queue_all(entries)
    return len(entries)
