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
through to. It needs no ``CMUX_SOCKET_PATH`` either — the CLI defaults to the
conventional socket path when the var is unset — so the plist sets only ``PATH``.

Nothing here wakes a sleeping Mac, and nothing needs to: launchd starts a job
missed during sleep on the next wake, coalescing missed intervals into one event.
That single fire is what ``schedule.due_templates`` expects — one run per due
template, no backfill. See ``docs/dev/scheduled-tasks.md``.

The one remaining ``pmset`` call is :func:`clear_leftover_wake`, which cleans up
after the superseded ``--wake-at`` design. It runs from ``mael schedule
uninstall`` only — never from :func:`ensure_schedule_agent`, which must stay
non-interactive for ``mael install`` / ``mael self-update``.
"""

import os
import platform
import subprocess
from pathlib import Path

import click

from .shell import mael_path

LABEL = "nz.tangerinelabs.maelstrom.schedule"


def _maelstrom_dir() -> Path:
    return Path.home() / ".maelstrom"


def marker_path() -> Path:
    """Path of the opt-in marker. Presence = "this machine wants the agent"."""
    return _maelstrom_dir() / "schedule.enabled"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return _maelstrom_dir() / "schedule.log"


def _agent_path() -> str:
    """A PATH covering ``mael`` and ``cmux`` for the launchd job's bare env."""
    mael_dir = str(Path(mael_path()).parent)
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

    Pure (no I/O) so the exact output — label, absolute ``mael`` path, and
    **no** cmux socket path or password — is asserted in tests.
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
    """Load the agent, tolerating the "already bootstrapped" race.

    ``_bootout`` is best-effort, so a teardown still in flight can leave the
    service loaded when ``bootstrap`` runs — launchd then reports "service
    already bootstrapped" / an I/O error. That is the desired end state (agent
    loaded), so treat it as success; re-raise anything else.
    """
    result = subprocess.run(
        ["launchctl", "bootstrap", _domain_target(), str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    blob = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
    if "already bootstrapped" in blob or "service already loaded" in blob:
        return
    raise subprocess.CalledProcessError(
        result.returncode, result.args, result.stdout, result.stderr
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

    mael = mael_path()
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
    """Create the opt-in marker (idempotent).

    The marker is a bare presence flag, so the file is written empty. Any body an
    older version left behind is overwritten and, either way, never read.
    """
    marker = marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("")


def uninstall_marker() -> None:
    """Remove the opt-in marker if present (idempotent)."""
    marker = marker_path()
    if marker.exists():
        marker.unlink()


def _has_repeating_wake() -> bool:
    """Return whether ``pmset`` holds a repeating wake.

    Reads only the "Repeating power events" section. The "Scheduled power events"
    section lists transient one-off system alarms we did not set, which would
    otherwise look like ours.
    """
    result = subprocess.run(["pmset", "-g", "sched"], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    in_repeating = False
    for line in result.stdout.splitlines():
        low = line.lower()
        if low.startswith("repeating power events"):
            in_repeating = True
            continue
        if low.endswith("power events:"):
            in_repeating = False
            continue
        if in_repeating and line.strip():
            return True
    return False


def clear_leftover_wake() -> list[str]:
    """Cancel a repeating ``pmset`` wake left by an older ``--wake-at`` install.

    Maelstrom no longer sets a wake, but a machine that once ran
    ``mael schedule install --wake-at HH:MM`` keeps its wake until something
    cancels it. Clean up the system state we created rather than leaving it to
    the user.

    Returns no message when there is nothing to clear, which is the common case.
    The read is free; only an actual cancel needs ``sudo``, so the password
    prompt appears only on the machines that need it.

    **Call this from the CLI only.** ``ensure_schedule_agent`` runs unattended
    from ``mael install`` / ``mael self-update``, and a sudo prompt would block
    them. A human is always present for ``mael schedule uninstall``.

    ``pmset repeat cancel`` clears the one system-wide repeating wake, so it also
    clears a wake the user set themselves. That is why only ``uninstall`` — an
    explicit teardown — does this, and why it says what it did.
    """
    if platform.system() != "Darwin" or not _has_repeating_wake():
        return []
    result = subprocess.run(["sudo", "pmset", "repeat", "cancel"])
    if result.returncode != 0:
        return [
            "Schedule wake: found a repeating pmset wake but could not clear it. "
            "Run `sudo pmset repeat cancel` yourself."
        ]
    return [
        "Schedule wake: cleared the repeating pmset wake (set by an old --wake-at)."
    ]


# --- status reporting (read-only; the missing diagnostic that hid this bug) ---


def _job_loaded() -> bool:
    """Return whether launchd reports our label loaded in the GUI domain."""
    result = subprocess.run(
        ["launchctl", "print", f"{_domain_target()}/{LABEL}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _log_tail(n: int = 5) -> list[str]:
    """Return the last ``n`` non-empty lines of the schedule log (newest last)."""
    log = log_path()
    if not log.exists():
        return []
    lines = [ln.rstrip("\n") for ln in log.read_text().splitlines()]
    return lines[-n:]


def status_lines() -> list[str]:
    """Build a side-effect-free status report of the schedule agent.

    Reports marker presence, plist presence, whether launchd has the job loaded,
    and the log path + tail.
    """
    if platform.system() != "Darwin":
        return ["Schedule status: skipped (not macOS)."]

    out: list[str] = []
    marker = marker_path()
    out.append(f"Marker: {'present' if marker.exists() else 'absent'} ({marker})")

    plist = plist_path()
    out.append(f"Plist: {'present' if plist.exists() else 'absent'} ({plist})")
    out.append(f"Job loaded: {'yes' if _job_loaded() else 'no'}")

    log = log_path()
    out.append(f"Log: {log}")
    tail = _log_tail()
    if tail:
        out.append("Log tail:")
        out.extend(f"  {line}" for line in tail)
    else:
        out.append("Log tail: (empty)")
    return out


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
    """Opt this machine out: remove the marker and tear the agent down.

    Also clears a repeating ``pmset`` wake left by an older ``--wake-at``
    install. That step needs ``sudo``, and prompts only on a machine that has
    such a wake.
    """
    uninstall_marker()
    for msg in ensure_schedule_agent():
        click.echo(msg)
    for msg in clear_leftover_wake():
        click.echo(msg)


@schedule_group.command("status")
def schedule_status() -> None:
    """Report agent state (marker, plist, loaded job, log tail)."""
    for msg in status_lines():
        click.echo(msg)
