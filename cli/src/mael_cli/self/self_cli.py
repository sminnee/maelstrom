"""``mael self``: install, upgrade and look after this host's maelstrom.

Thin commands over :mod:`.setup`, :mod:`.update` and :mod:`.host_service`.
See ``docs/dev/host-services.md``.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from mael_common.util import get_maelstrom_dir
from mael_domain.context import (
    GLOBAL_CONFIG_FILENAME,
    UPDATE_CHANNELS,
    load_global_config,
    set_update_channel,
)
from mael_domain.release_bundle.layout import ManifestError, current_manifest
from mael_domain.state_db.types import StateDbError

from . import schedule_launchd
from .host_check import HostCheck
from .host_service import AGENT_DAEMON, ORCHESTRATOR, run_quietly, supervisor
from .production import SHIM_MARKER, Production, agent_path
from .setup import Setup, SetupError, all_units, configured_services
from .update import UpdateError, main_checkout, update_main, update_stable


def _units(prod: Production):
    """Every unit, as the global config names it."""
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
def cmd_setup(changed: str, source: Path | None) -> None:
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
        interactive=sys.stdin.isatty(),
        prompt=lambda text, default: click.prompt(text, default=default),
    )
    try:
        run()
    except (StateDbError, SetupError) as exc:
        raise click.ClickException(str(exc)) from exc


def _run_interactive(argv) -> int:
    """Run ``argv`` on the user's terminal. 127 when it is not installed."""
    try:
        return subprocess.run(argv, check=False).returncode
    except FileNotFoundError:
        click.echo(f"  ✗ {argv[0]} is not installed.")
        return 127


@self_group.command("check-host")
@click.option(
    "--dev", is_flag=True, help="Also check node and pnpm, for a source install."
)
def cmd_check_host(dev: bool) -> None:
    """Check this host's tools, sign-ins and Tailscale, and offer to fix each.

    Each install or sign-in runs only after a yes. Without a terminal, it only
    reports, and tightens the secret files.
    `install.sh` runs it on a first install. Every step is safe to repeat.
    """
    prod = _production()
    caller_path = os.environ.get("PATH", "")
    # Probe on the agents' PATH, and run on it, so a later step finds what an
    # earlier step installed. See docs/dev/host-services.md.
    path = os.environ["PATH"] = agent_path(prod, caller_path)
    unmet = HostCheck(
        prod=prod,
        platform="darwin" if sys.platform == "darwin" else "linux",
        interactive=sys.stdin.isatty(),
        which=lambda name: shutil.which(name, path=path),
        run=run_quietly,
        run_interactive=_run_interactive,
        confirm=lambda text: click.confirm(text, default=False),
        echo=click.echo,
        dev=dev,
        caller_path=caller_path,
    )()
    if unmet:
        raise SystemExit(1)


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
