"""The audit log: one **Audit record** per state-changing action, in a rotating file.

The orchestrator server and the agent daemon each keep one. A record says who
changed what; ``docs/dev/orchestrator-server.md`` and ``docs/dev/agent-daemon.md``
list the fields each writes. The files are owner-only: a record holds command
lines and paths.
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The size at which the file rolls over to ``<name>.1``.
MAX_BYTES = 10 * 1024 * 1024
#: How many rolled-over files are kept: ``<name>.1`` to ``<name>.5``.
BACKUPS = 5


class AuditLog:
    """Appends **Audit records** to ``path``, and rolls it over by size.

    :meth:`write` never raises: an action must not fail because its record
    could not be written.
    """

    def __init__(
        self, path: Path, *, max_bytes: int = MAX_BYTES, backups: int = BACKUPS
    ) -> None:
        self.path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        """Append ``record`` with a ``ts``, and log one INFO line of it."""
        line = json.dumps(
            {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), **record},
            default=str,
        )
        log.info("audit %s", line)
        data = (line + "\n").encode()
        try:
            with self._lock:
                self._roll_over(len(data))
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
        except OSError as exc:
            log.warning("could not write the audit record to %s: %s", self.path, exc)

    def _roll_over(self, adding: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return
        if size + adding <= self._max_bytes:
            return
        for n in range(self._backups - 1, 0, -1):
            older = self.path.with_name(f"{self.path.name}.{n}")
            if older.exists():
                older.replace(self.path.with_name(f"{self.path.name}.{n + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))
