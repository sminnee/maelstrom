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
import re
import shutil
import subprocess
from pathlib import Path

import click

LABEL = "nz.tangerinelabs.maelstrom.schedule"
CMUX_SOCKET_PATH = "/tmp/cmux.sock"

# Accepts a 24-hour ``HH:MM`` with leading zeros optional on the hour.
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _maelstrom_dir() -> Path:
    return Path.home() / ".maelstrom"


def marker_path() -> Path:
    """Path of the opt-in marker. Presence = "this machine wants the agent"."""
    return _maelstrom_dir() / "schedule.enabled"


def validate_hhmm(value: str) -> str:
    """Normalise an ``HH:MM`` string to zero-padded form, raising on bad input."""
    m = _HHMM_RE.match(value.strip())
    if not m:
        raise ValueError(f"Invalid time {value!r}; expected 24-hour HH:MM.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def wake_time() -> str | None:
    """Return the marker's wake time (validated ``HH:MM``), or ``None``.

    An empty marker body means awake-only (no wake). A malformed body is treated
    as no wake rather than raising, so a hand-corrupted marker never wedges
    reconciliation.
    """
    marker = marker_path()
    if not marker.exists():
        return None
    body = marker.read_text().strip()
    if not body:
        return None
    try:
        return validate_hhmm(body)
    except ValueError:
        return None


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


# --- pmset wake glue (macOS, needs sudo — interactive at install only) ---


def _minute_before(hhmm: str) -> str:
    """Return the ``HH:MM`` one minute earlier (wrapping past midnight)."""
    hh, mm = (int(p) for p in hhmm.split(":"))
    total = (hh * 60 + mm - 1) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _schedule_wake(hhmm: str) -> None:
    """Schedule a daily ``pmset`` wake one minute before ``hhmm``.

    Runs ``sudo pmset repeat wakeorpoweron MTWRFSU HH:MM:00`` so the next launchd
    tick at ``hhmm`` finds the Mac awake. Only one repeating ``pmset`` wake exists
    system-wide, so this replaces any prior one. Stdin is left attached so the
    ``sudo`` password prompt reaches the user.
    """
    wake = _minute_before(hhmm)
    subprocess.run(
        ["sudo", "pmset", "repeat", "wakeorpoweron", "MTWRFSU", f"{wake}:00"],
        check=True,
    )


def _clear_wake() -> None:
    """Cancel any repeating ``pmset`` wake (best-effort).

    Idempotent: cancelling when none is set is harmless. Failures (e.g. no sudo)
    are swallowed so reconciliation of the launchd agent still completes.
    """
    subprocess.run(
        ["sudo", "pmset", "repeat", "cancel"],
        capture_output=True,
        text=True,
    )


def ensure_schedule_agent() -> list[str]:
    """Reconcile the loaded launchd agent to the opt-in marker. Idempotent.

    - Non-macOS: no-op (returns a skip message).
    - Marker absent: ensure no agent — ``bootout`` and remove any stale plist so
      ``uninstall`` fully takes effect even if it only cleared the marker. Also
      clears any ``pmset`` wake.
    - Marker present: render the plist with the *current* absolute ``mael`` path
      and ``bootstrap`` it (replacing any existing one) — self-healing a stale
      path after a ``self-update``. If the marker carries a wake time, re-assert
      the ``pmset`` wake; otherwise clear it.
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
        _clear_wake()
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

    msgs = [f"Schedule agent: loaded ({mael})."]
    wake = wake_time()
    if wake:
        # Compare against the actual pmset target (_minute_before(wake)), not
        # `wake` itself — _pmset_wake_hhmm() reports the wake pmset holds, which
        # is one minute before the fire time.
        desired = _minute_before(wake)
        if _pmset_wake_hhmm() == desired:
            # Already exactly what we'd set — skip the sudo pmset call (and its
            # password prompt) on the common "nothing changed" path.
            msgs.append(
                f"Schedule wake: pmset already set for {desired} (unchanged)."
            )
        else:
            try:
                _schedule_wake(wake)
            except (subprocess.CalledProcessError, OSError) as e:
                # The launchd agent is loaded regardless; report the wake failure
                # without aborting. The marker keeps the wake intent, so the next
                # install / self-update re-attempts it.
                msgs.append(
                    f"Schedule wake: pmset failed ({e}); agent loaded but no wake "
                    f"set. Re-run `mael schedule install --wake-at {wake}` with sudo."
                )
            else:
                msgs.append(
                    f"Schedule wake: pmset set for {desired} "
                    f"(one minute before {wake})."
                )
    else:
        _clear_wake()
        msgs.append("Schedule wake: none configured.")
    return msgs


def install_marker(wake_at: str | None = None) -> None:
    """Create/update the opt-in marker (idempotent).

    The marker body carries the optional wake time: empty for awake-only, or a
    validated ``HH:MM`` to request a daily ``pmset`` wake. ``wake_at`` is
    validated/normalised here so a bad value fails before any launchd work.
    """
    marker = marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(validate_hhmm(wake_at) if wake_at else "")


def uninstall_marker() -> None:
    """Remove the opt-in marker if present (idempotent)."""
    marker = marker_path()
    if marker.exists():
        marker.unlink()


# --- status reporting (read-only; the missing diagnostic that hid this bug) ---


def _job_loaded() -> bool:
    """Return whether launchd reports our label loaded in the GUI domain."""
    result = subprocess.run(
        ["launchctl", "print", f"{_domain_target()}/{LABEL}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _pmset_wake_line() -> str | None:
    """Return the *repeating* wake line from ``pmset -g sched``, if any.

    Only the "Repeating power events" section is reported — the "Scheduled power
    events" section lists transient one-off system alarms we did not set and that
    would otherwise masquerade as our wake.
    """
    result = subprocess.run(
        ["pmset", "-g", "sched"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    in_repeating = False
    for line in result.stdout.splitlines():
        low = line.lower()
        if low.startswith("repeating power events"):
            in_repeating = True
            continue
        if low.endswith("power events:"):
            # Any other section header ends the repeating section.
            in_repeating = False
            continue
        if in_repeating and line.strip():
            return line.strip()
    return None


def _pmset_wake_hhmm() -> str | None:
    """Return the current repeating-wake time as 24-hour ``HH:MM``, if any.

    Parses the ``pmset -g sched`` repeating line (e.g. ``wakepoweron at 7:59AM
    every day``) and normalises the ``H:MMAM`` token to ``HH:MM`` so it can be
    compared against :func:`_minute_before`. Returns ``None`` when there is no
    repeating wake or the line doesn't parse (unknown -> treat as "re-apply").
    """
    line = _pmset_wake_line()
    if line is None:
        return None
    m = re.search(r"\bat\s+(\d{1,2}):(\d{2})\s*([AP]M)\b", line, re.IGNORECASE)
    if not m:
        return None
    hh, mm, meridiem = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if hh == 12:
        hh = 0
    if meridiem == "PM":
        hh += 12
    return f"{hh:02d}:{mm:02d}"


def _log_tail(n: int = 5) -> list[str]:
    """Return the last ``n`` non-empty lines of the schedule log (newest last)."""
    log = log_path()
    if not log.exists():
        return []
    lines = [ln.rstrip("\n") for ln in log.read_text().splitlines()]
    return lines[-n:]


def status_lines() -> list[str]:
    """Build a side-effect-free status report of the schedule agent.

    Reports marker presence (+ wake time), plist presence, whether launchd has
    the job loaded, the ``pmset`` repeating-wake line, and the log path + tail.
    """
    if platform.system() != "Darwin":
        return ["Schedule status: skipped (not macOS)."]

    out: list[str] = []
    marker = marker_path()
    if marker.exists():
        wake = wake_time()
        out.append(
            f"Marker: present ({marker})"
            + (f" — wake at {wake}" if wake else " — no wake configured")
        )
    else:
        out.append(f"Marker: absent ({marker})")

    plist = plist_path()
    out.append(f"Plist: {'present' if plist.exists() else 'absent'} ({plist})")
    out.append(f"Job loaded: {'yes' if _job_loaded() else 'no'}")

    wake_line = _pmset_wake_line()
    out.append(f"pmset wake: {wake_line if wake_line else 'none'}")

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
@click.option(
    "--wake-at",
    metavar="HH:MM",
    default=None,
    help=(
        "Schedule a daily pmset wake so a sleeping Mac runs the job (needs "
        "sudo). HH:MM is the machine's LOCAL time (pmset uses local time, as "
        "does the launchd hourly tick it lines up with). One system-wide "
        "repeating wake only — replaces any prior one; the wake is set one "
        "minute before HH:MM. Clamshell-on-battery laptops may ignore it."
    ),
)
def schedule_install(wake_at: str | None) -> None:
    """Opt this machine in: write the marker and load the launchd agent."""
    if wake_at is not None:
        try:
            wake_at = validate_hhmm(wake_at)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="--wake-at")
    install_marker(wake_at)
    for msg in ensure_schedule_agent():
        click.echo(msg)


@schedule_group.command("uninstall")
def schedule_uninstall() -> None:
    """Opt this machine out: remove the marker and tear the agent down."""
    uninstall_marker()
    for msg in ensure_schedule_agent():
        click.echo(msg)


@schedule_group.command("status")
def schedule_status() -> None:
    """Report agent state (marker, plist, loaded job, pmset wake, log tail)."""
    for msg in status_lines():
        click.echo(msg)
