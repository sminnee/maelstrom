"""launchd glue for the scheduled-task agent (macOS only).

Isolated from the pure :mod:`maelstrom.schedule` module: everything here touches
the filesystem and shells out to ``launchctl``, so it is mocked rather than
exercised directly in tests (apart from :func:`render_plist`, which is pure).

The agent is opt-in per machine, gated by a marker file (so a background
scheduler is never imposed on every checkout / CI box). The marker's presence is
the single source of truth; :func:`ensure_schedule_agent` reconciles the loaded
agent to it idempotently and is wired into ``install_claude_integration`` so
``mael install`` / ``mael self-update`` keep an opted-in agent's ``mael`` path
current.

The launchd→cmux path needs no secret in the plist: a *user* LaunchAgent runs in
the logged-in GUI session and so reaches the same keychain the ``cmux`` CLI falls
through to. Only ``CMUX_SOCKET_PATH`` is set.
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path

import click

LABEL = "nz.tangerinelabs.maelstrom.schedule"
CMUX_SOCKET_PATH = "/tmp/cmux.sock"


def _maelstrom_dir() -> Path:
    return Path.home() / ".maelstrom"


def marker_path() -> Path:
    """Path of the opt-in marker. Presence = "this machine wants the agent"."""
    return _maelstrom_dir() / "schedule.enabled"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return _maelstrom_dir() / "schedule.log"


def _mael_path() -> str:
    """Absolute path to the ``mael`` binary (so launchd's bare env can find it)."""
    found = shutil.which("mael")
    if found:
        return found
    # Fall back to a conventional location; ensure_schedule_agent self-heals the
    # path on the next install/self-update if this guess is wrong.
    return str(Path.home() / ".local" / "bin" / "mael")


def _agent_path() -> str:
    """A PATH covering ``mael`` and ``cmux`` for the launchd job's bare env."""
    mael_dir = str(Path(_mael_path()).parent)
    candidates = [
        mael_dir,
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    # De-dupe while preserving order.
    seen: set[str] = set()
    ordered = [c for c in candidates if not (c in seen or seen.add(c))]
    return ":".join(ordered)


def render_plist(mael: str, *, agent_path: str, log: str) -> str:
    """Render the LaunchAgent plist XML.

    Pure (no I/O) so the exact output — label, absolute ``mael`` path,
    ``CMUX_SOCKET_PATH`` and **no** password — is asserted in tests.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{mael}</string>
        <string>task</string>
        <string>add-scheduled</string>
        <string>--all-projects</string>
        <string>--run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CMUX_SOCKET_PATH</key>
        <string>{CMUX_SOCKET_PATH}</string>
        <key>PATH</key>
        <string>{agent_path}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"""


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def _bootout() -> None:
    """Best-effort unload of any currently-loaded agent (ignore failures)."""
    subprocess.run(
        ["launchctl", "bootout", f"{_domain_target()}/{LABEL}"],
        capture_output=True,
        text=True,
    )


def _bootstrap(path: Path) -> None:
    subprocess.run(
        ["launchctl", "bootstrap", _domain_target(), str(path)],
        capture_output=True,
        text=True,
        check=True,
    )


def ensure_schedule_agent() -> list[str]:
    """Reconcile the loaded launchd agent to the opt-in marker. Idempotent.

    - Non-macOS: no-op (returns a skip message).
    - Marker absent: ensure no agent — ``bootout`` and remove any stale plist so
      ``uninstall`` fully takes effect even if it only cleared the marker.
    - Marker present: render the plist with the *current* absolute ``mael`` path
      and ``bootstrap`` it (replacing any existing one) — self-healing a stale
      path after a ``self-update``.
    """
    if platform.system() != "Darwin":
        return ["Schedule agent: skipped (not macOS)."]

    plist = plist_path()
    if not marker_path().exists():
        removed = False
        if plist.exists():
            _bootout()
            plist.unlink()
            removed = True
        return [
            "Schedule agent: removed (opt-out)."
            if removed
            else "Schedule agent: not enabled (no marker)."
        ]

    mael = _mael_path()
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(render_plist(mael, agent_path=_agent_path(), log=str(log)))
    # Replace any already-loaded copy so a changed path/args takes effect.
    _bootout()
    try:
        _bootstrap(plist)
    except subprocess.CalledProcessError as e:
        return [f"Schedule agent: bootstrap failed: {e.stderr or e.stdout or e}"]
    return [f"Schedule agent: loaded ({mael})."]


def install_marker() -> None:
    """Create the opt-in marker (idempotent)."""
    marker = marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def uninstall_marker() -> None:
    """Remove the opt-in marker if present (idempotent)."""
    marker = marker_path()
    if marker.exists():
        marker.unlink()


# --- thin install/uninstall CLI (the `mael schedule` group) ---


@click.group("schedule")
def schedule_group() -> None:
    """Install/uninstall the background scheduled-task launchd agent (macOS)."""


@schedule_group.command("install")
def schedule_install() -> None:
    """Opt this machine in: write the marker and load the launchd agent."""
    install_marker()
    for msg in ensure_schedule_agent():
        click.echo(msg)


@schedule_group.command("uninstall")
def schedule_uninstall() -> None:
    """Opt this machine out: remove the marker and tear the agent down."""
    uninstall_marker()
    for msg in ensure_schedule_agent():
        click.echo(msg)
