"""CLI commands for maelstrom self-management (install, self-update, self-env, admin)."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import click

from mael_agent.agent_transport import ROOT_ENV
from mael_common.cli_async import AsyncGroup
from mael_common.util import get_maelstrom_dir, now_iso
from mael_domain.context import (
    GLOBAL_CONFIG_FILENAME,
    UPDATE_CHANNELS,
    harden_global_config,
    load_global_config,
    set_update_channel,
)
from mael_domain.env import ServiceVersionError, restart_changed, service_versions
from mael_domain.notebook_root import NOTEBOOK_ROOT_ENV
from mael_domain.release_bundle.install import (
    InstallError,
    current_manifest,
    format_report,
    install_bundle,
)
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


#: The repository whose GitHub Releases carry the stable channel's bundles.
RELEASE_REPO = "sminnee/maelstrom"
TARBALL = "maelstrom.tar.gz"

#: The tangier SHA bucket that hashes the jig's Vite plugin, and its package.
JIG_BUCKET = "jig"
JIG_PACKAGE = "vite-plugin-mael-jig"

#: The self-env service whose restart interrupts running agents.
AGENT_DAEMON_SERVICE = "agent-daemon"


def install_root() -> Path:
    """Where releases install: ``~/.maelstrom/install``."""
    return get_maelstrom_dir() / "install"


def shim_path() -> Path:
    """The `mael` on PATH: ``~/.local/bin/mael``, where uv puts tool entry points."""
    return Path.home() / ".local" / "bin" / "mael"


@click.command("self-update")
@click.option(
    "--channel",
    type=click.Choice(UPDATE_CHANNELS),
    help="Where to update from, stored in config.yaml for later updates: "
    "`stable` (the latest GitHub Release) or `main` (a bundle built from "
    "_main). Default: the stored channel, else stable.",
)
@click.option(
    "--tag",
    help="Install this GitHub Release, such as a pre-release or an older "
    "version to roll back to. Stable channel only.",
)
def cmd_self_update(channel: str | None, tag: str | None):
    """Install the latest release bundle of maelstrom."""
    if channel is None:
        channel = load_global_config().update_channel
        stored = False
    else:
        stored = True
    if tag is not None and channel != "stable":
        raise click.UsageError("--tag names a GitHub Release: use --channel stable.")
    if stored:
        set_update_channel(get_maelstrom_dir() / GLOBAL_CONFIG_FILENAME, channel)
        click.echo(f"Update channel: {channel}")

    root = install_root()
    main: Path | None = None
    jig_moved = False
    with tempfile.TemporaryDirectory() as tmp:
        if channel == "stable":
            bundle = _fetch_stable(root, Path(tmp), tag)
        else:
            main = load_global_config().projects_dir / SELF_ENV_PROJECT
            main = main / MAIN_WORKTREE_FOLDER
            bundle, jig_moved = _build_main(root, main, Path(tmp))
        if bundle is not None:
            try:
                report = install_bundle(bundle, root)
            except (InstallError, OSError, subprocess.CalledProcessError) as e:
                raise click.ClickException(f"The install failed: {e}") from e
            for line in format_report(report):
                click.echo(line)

    click.echo("Updating Claude Code integration...")
    for msg in install_claude_integration():
        click.echo(f"  {msg}")

    # Tighten any loose perms on the global config / ~/.maelstrom while we're
    # touching the install. The config carries plaintext API keys; doctor is the
    # other place this runs, but self-update is a natural "tidy my install" hook.
    for msg in harden_global_config():
        click.echo(f"  {msg}")

    click.echo(f"  {_write_shim(shim_path(), root)}")

    if main is not None:
        # The jig is a developer override, and the npm release serves
        # everyone else, so only the main channel links it.
        if jig_moved:
            _link_jig(main / JIG_PACKAGE)
        _restart_self_env(main)
    else:
        click.echo(
            "  The services still run from _main; restart them with "
            "`mael self-env restart` to pick up a changed component."
        )

    click.echo("Update complete.")


def _current_tag(root: Path) -> str | None:
    """The tag of the release that runs now, or ``None`` before the first install."""
    current = current_manifest(root)
    return current.tag if current else None


def _gh(*args: str) -> str:
    try:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True
        )
    except FileNotFoundError as e:
        raise click.ClickException("gh is not on PATH") from e
    except subprocess.CalledProcessError as e:
        raise click.ClickException(
            f"gh {args[0]} {args[1]} failed: {(e.stderr or e.stdout).strip()}"
        ) from e
    return result.stdout.strip()


def _fetch_stable(root: Path, tmp: Path, tag: str | None) -> Path | None:
    """Download the GitHub Release ``tag``, or the latest, unless it runs now."""
    if tag is None:
        tag = _gh(
            "release",
            "view",
            "--repo",
            RELEASE_REPO,
            "--json",
            "tagName",
            "--jq",
            ".tagName",
        )
    if tag == _current_tag(root):
        click.echo(f"{tag} is installed already.")
        return None
    click.echo(f"Downloading {tag}...")
    _gh(
        "release",
        "download",
        tag,
        "--repo",
        RELEASE_REPO,
        "--pattern",
        TARBALL,
        "--dir",
        str(tmp),
    )
    return tmp / TARBALL


def _build_main(root: Path, main: Path, tmp: Path) -> tuple[Path | None, bool]:
    """Pull ``_main`` and build its bundle, unless that commit runs now.

    It runs ``_main``'s own ``bin/build-bundle``, not the installed copy, so a
    change to the build lands with the commit that makes it.

    Returns the bundle, and whether the pull moved the jig.
    """
    if not (main / ".git").exists():
        raise click.ClickException(
            f"The main channel builds from {main}, which is not a checkout."
        )
    jig_before = _jig_hash(main)
    click.echo(f"Updating {main}...")
    try:
        pull = subprocess.run(
            ["git", "pull"], cwd=main, capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Git pull failed: {e.stderr or e.stdout or e}")
    if pull.stdout.strip():
        click.echo(pull.stdout.strip())
    jig_after = _jig_hash(main)
    jig_moved = jig_after is not None and jig_after != jig_before

    # The build needs _main's dependencies, and the install migrates the state
    # database, which new code refuses unmigrated.
    click.echo("Installing dependencies in _main...")
    try:
        run_install_cmd(main)
    except (OSError, subprocess.CalledProcessError) as e:
        raise click.ClickException(
            f"install_cmd failed in {main}: {e}. Nothing was installed."
        ) from e

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=main,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"git rev-parse failed in {main}: {e.stderr}")
    tag = f"main-{commit[:12]}"
    if tag == _current_tag(root):
        click.echo(f"{tag} is installed already.")
        return None, jig_moved
    click.echo(f"Building {tag}...")
    bundle = tmp / TARBALL
    try:
        subprocess.run(
            [str(main / "bin" / "build-bundle"), "--tag", tag, "--out", str(bundle)],
            cwd=main,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        raise click.ClickException(f"bin/build-bundle failed: {e}") from e
    return bundle, jig_moved


def _jig_hash(main: Path) -> str | None:
    """The jig's SHA bucket hash, or ``None`` when tangier cannot give it."""
    try:
        return service_versions(main, [JIG_BUCKET]).get(JIG_BUCKET)
    except ServiceVersionError:
        return None


#: Marks a `mael` this command wrote, so the user can tell it from uv's.
SHIM_MARKER = "# maelstrom shim"


def _write_shim(path: Path, root: Path) -> str:
    """Point the `mael` on PATH at the ``current`` release, with its roots.

    ``current`` is a symlink, so the shim does not change between releases.

    The shim supplies `_main`'s daemon root and the notebook root only when
    nothing else has: a worktree's `uv run mael` and a command inside a driven
    session both arrive with a root already set, and overriding either would
    send it to the wrong daemon.

    The write goes through a temporary file and a rename. The rename replaces
    a symlink rather than writing through it, and a running `mael` keeps its
    file.

    Best-effort: the release is installed by this point, so a failed write
    warns rather than aborting. Returns what to tell the user.
    """
    daemon_root = get_maelstrom_dir() / "daemons" / MAIN_WORKTREE_FOLDER
    shim = (
        "#!/bin/sh\n"
        f"{SHIM_MARKER} — written by `mael self-update`.\n"
        "# The everyday daemon's root, unless something already named one.\n"
        f'export {ROOT_ENV}="${{{ROOT_ENV}:-{daemon_root}}}"\n'
        f'export {NOTEBOOK_ROOT_ENV}="${{{NOTEBOOK_ROOT_ENV}:-{get_maelstrom_dir()}}}"\n'
        "export MAEL_PRODUCTION=1\n"
        f'exec "{root / "current" / "venv" / "bin" / "mael"}" "$@"\n'
    )
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(shim)
        staging.chmod(0o755)
        os.replace(staging, path)
    except OSError:
        staging.unlink(missing_ok=True)
        return (
            f"Warning: could not write {path}. Run "
            f"{root / 'current' / 'venv' / 'bin' / 'mael'} with {ROOT_ENV} set."
        )
    return f"`mael` at {path} runs the current release"


def _link_jig(package: Path) -> None:
    """Build `_main`'s jig plugin and register it with `pnpm link --global`.

    A project then runs `pnpm link --global vite-plugin-mael-jig` to use it in
    place of the npm release. Best-effort, as the dependency sync is.
    """
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        click.echo(
            "  Warning: 'pnpm' not found; skipping the jig plugin link.", err=True
        )
        return
    click.echo("Building and linking the jig plugin...")
    for step in (
        ["install", "--frozen-lockfile"],
        ["run", "build"],
        ["link", "--global"],
    ):
        result = subprocess.run(
            [pnpm, *step], cwd=package, capture_output=True, text=True
        )
        if result.returncode != 0:
            click.echo(result.stderr or result.stdout, err=True)
            click.echo(
                f"  Warning: 'pnpm {' '.join(step)}' failed; the jig plugin is not linked.",
                err=True,
            )
            return


def _restart_self_env(repo_root: Path) -> None:
    """Restart `_main`'s changed services, best-effort: the release is installed."""
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
