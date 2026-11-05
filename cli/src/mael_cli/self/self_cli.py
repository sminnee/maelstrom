"""``mael self``: install, upgrade and look after this host's maelstrom.

Thin commands over :mod:`.setup`, :mod:`.update` and :mod:`.host_service`.
See ``docs/dev/host-services.md``.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import click

from mael_common.util import get_maelstrom_dir, now_iso
from mael_domain.context import (
    GLOBAL_CONFIG_FILENAME,
    UPDATE_CHANNELS,
    load_global_config,
    set_update_channel,
)
from mael_domain.release_bundle.layout import ManifestError, current_manifest
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.types import StateDbError
from mael_domain.task import task_key
from mael_domain.task_export import Queued, SqliteExportQueue
from mael_domain.task_table import TABLE as TASKS_TABLE

from . import schedule_launchd
from .host_service import AGENT_DAEMON, ORCHESTRATOR, run_quietly, supervisor
from .production import SHIM_MARKER, Production
from .setup import Setup, SetupError, all_units, configured_services
from .update import UpdateError, main_checkout, update_main, update_stable


def _units(prod: Production):
    """Every unit, as the notebook's config names it."""
    try:
        return all_units(prod, os.environ.get("PATH", ""))
    except SetupError as exc:
        raise click.ClickException(str(exc)) from exc


def _production() -> Production:
    return Production(Path.home())


@click.group("self")
def self_group() -> None:
    """Install, upgrade and look after maelstrom on this host."""


@self_group.command("setup")
@click.option(
    "--changed",
    default="",
    help="The components that changed, comma-separated. Their host services "
    "restart. Default: none, so nothing restarts.",
)
@click.option(
    "--source",
    type=click.Path(file_okay=False, path_type=Path),
    help="The checkout the release was built from. Links its jig plugin.",
)
@click.option(
    "--rebuild-export",
    is_flag=True,
    help="Queue every task for the markdown export, whatever the queue holds.",
)
def cmd_setup(changed: str, source: Path | None, rebuild_export: bool) -> None:
    """Bring this host to the state the current release wants.

    `install.sh` runs it after each install. Run it yourself to repair a host.
    Every step is safe to repeat.
    """
    prod = _production()
    run = Setup(
        prod=prod,
        supervisor=supervisor(run_quietly, prod.home),
        run=run_quietly,
        echo=click.echo,
        changed=[c for c in changed.split(",") if c],
        source=source,
    )
    try:
        run()
    except (StateDbError, SetupError) as exc:
        raise click.ClickException(str(exc)) from exc
    if rebuild_export:
        queued = asyncio.run(_rebuild_export_queue(prod.state / "state.db"))
        click.echo(f"Queued {queued} task(s) for export.")


async def _rebuild_export_queue(path: Path) -> int:
    """Queue every task row, and return how many were queued.

    A task write queues its own export, so this is for the file that went
    missing without the row moving. The path is each task's current one.
    """
    db = open_state_db(path)
    try:
        await db.check()
        entries = [
            Queued(
                id=row["id"],
                path=task_key(row["project"], row["status"], row["task_id"]),
                deleted=False,
                queued_at=now_iso(),
            )
            for row in await db.read_all(TASKS_TABLE)
        ]
        await SqliteExportQueue(db).queue_all(entries)
    finally:
        db.close()
    return len(entries)


@self_group.command("update")
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
def cmd_update(channel: str | None, tag: str | None) -> None:
    """Install the latest release of maelstrom, then run its setup.

    A restart of the agent daemon stops the agents it runs, so run this from a
    shell, not from an agent session.
    """
    stored = channel is not None
    channel = channel or load_global_config().update_channel
    if tag is not None and channel != "stable":
        raise click.UsageError("--tag names a GitHub Release: use --channel stable.")
    if stored:
        set_update_channel(get_maelstrom_dir() / GLOBAL_CONFIG_FILENAME, channel)
        click.echo(f"Update channel: {channel}")
    try:
        if channel == "stable":
            code = update_stable(tag, subprocess.run)
        else:
            code = update_main(
                main_checkout(load_global_config().projects_dir), subprocess.run
            )
    except UpdateError as exc:
        raise click.ClickException(str(exc)) from exc
    if code != 0:
        raise click.ClickException(f"install.sh failed (exit {code}).")


@self_group.command("status")
def cmd_status() -> None:
    """Report the release, the host services, the scheduler and the export queue."""
    prod = _production()
    try:
        manifest = current_manifest(prod.install_root)
    except (ManifestError, OSError) as exc:
        manifest = None
        click.echo(f"Release: unreadable ({exc})")
    if manifest is not None:
        click.echo(f"Release: {manifest.tag} ({manifest.commit[:12]})")
        for name, version in manifest.versions.items():
            click.echo(f"  {name}: {version}")
    elif not (prod.install_root / "current").exists():
        click.echo("Release: none installed")

    sup = supervisor(run_quietly, prod.home)
    units = _units(prod)
    click.echo("Host services:")
    for unit, status in zip(units, sup.status(units), strict=True):
        state = "loaded" if status.loaded else "not loaded"
        pid = f", pid {status.pid}" if status.pid else ""
        last = f", last exit {status.last_exit}" if status.last_exit is not None else ""
        click.echo(f"  {unit.name}: {state}{pid}{last}")
        click.echo(f"    log: {sup.log_hint(unit)}")
    click.echo(f"Shim: {prod.shim}")
    _echo_export_queue(prod.state / "state.db")


def _echo_export_queue(path: Path) -> None:
    """What the markdown export still owes the files.

    The orchestrator drains the queue, so a depth that does not fall means the
    server is down or its drain is failing. The oldest entry tells a busy
    notebook from a drain that stopped.
    """
    if not path.exists():
        click.echo(f"Export queue: no notebook at {path}")
        return

    async def read() -> tuple[int, str | None]:
        db = open_state_db(path)
        try:
            await db.check()
            queue = SqliteExportQueue(db)
            return await queue.depth(), await queue.oldest()
        finally:
            db.close()

    try:
        depth, oldest = asyncio.run(read())
    except StateDbError as exc:
        click.echo(f"Export queue: {exc}")
        return
    if not depth:
        click.echo("Export queue: up to date")
    else:
        click.echo(f"Export queue: owes {depth} task(s), the oldest since {oldest}")


@self_group.command("uninstall")
def cmd_uninstall() -> None:
    """Remove the host services, the scheduler and the `mael` shim.

    Keeps `~/.maelstrom/install` and the notebook. On macOS, it also clears a
    repeating wake an older scheduler set, which asks for sudo.
    """
    prod = _production()
    sup = supervisor(run_quietly, prod.home)
    for msg in sup.remove(_units(prod)):
        click.echo(msg)
    if prod.shim.is_file() and SHIM_MARKER in prod.shim.read_text():
        prod.shim.unlink()
        click.echo(f"Removed {prod.shim}")
    for msg in schedule_launchd.clear_leftover_wake():
        click.echo(msg)


@self_group.command("restart")
@click.argument("names", nargs=-1, type=click.Choice([ORCHESTRATOR, AGENT_DAEMON]))
def cmd_restart(names: tuple[str, ...]) -> None:
    """Restart host services through the supervisor. Default: both.

    A restart of the agent daemon stops its agents; they resume after it.
    """
    prod = _production()
    wanted = set(names or (ORCHESTRATOR, AGENT_DAEMON))
    try:
        specs = configured_services(prod, os.environ.get("PATH", ""))
    except SetupError as exc:
        raise click.ClickException(str(exc)) from exc
    restarted = supervisor(run_quietly, prod.home).restart(
        [s for s in specs if s.name in wanted]
    )
    for msg in restarted.messages:
        click.echo(msg)
    if restarted.failed:
        raise click.ClickException(f"{', '.join(restarted.failed)} did not restart.")
