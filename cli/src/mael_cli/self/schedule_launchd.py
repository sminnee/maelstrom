"""The scheduler's macOS leftovers: the wake an older install set.

The scheduler itself is a host service unit, in :mod:`.host_service`. It
touches no cmux: each scheduled run starts on the agent daemon.

Nothing wakes a sleeping Mac, and nothing needs to: launchd starts a job missed
during sleep on the next wake, coalescing missed intervals into one event. That
single fire is what ``schedule.fire_due`` expects — one run per due template, no
backfill. See ``docs/dev/scheduled-tasks.md``.

The one remaining ``pmset`` call is :func:`clear_leftover_wake`, which cleans up
after the superseded ``--wake-at`` design. It runs from ``mael self uninstall``
only — never from setup, which must stay non-interactive.
"""

import platform
import subprocess


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

    Maelstrom no longer sets a wake, but a machine that once ran the old
    ``--wake-at`` install keeps its wake until something cancels it.

    Returns no message when there is nothing to clear, which is the common case.
    The read is free; only an actual cancel needs ``sudo``, so the password
    prompt appears only on the machines that need it.

    **Call this from ``mael self uninstall`` only.** Setup runs unattended, and
    a sudo prompt would block it. ``pmset repeat cancel`` clears the one
    system-wide repeating wake, so it also clears a wake the user set
    themselves. That is why only an explicit teardown does this, and why it
    says what it did.
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
