"""Tiny OS-process helpers shared across the session subsystems.

Kept dependency-free (only ``os``) so :mod:`maelstrom.session_discovery`
(transcript→pid→live) has one liveness implementation to share rather than
rolling its own ``os.kill(pid, 0)``.
"""

import os


def is_process_running(pid: int) -> bool:
    """True if a process with ``pid`` currently exists.

    Uses the POSIX ``kill(pid, 0)`` probe: it sends no signal but performs the
    permission/existence check, so a live pid returns ``True`` and a dead one
    raises ``OSError`` (``ESRCH``). A pid we merely lack permission to signal
    (``EPERM``) still exists, so we treat that as running too.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
