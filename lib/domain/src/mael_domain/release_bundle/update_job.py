"""The record of the last **Update job**: ``<install root>/update.json``.

See "Update job" in CONTEXT.md and ``docs/dev/host-services.md``.

``mael self update --as-job`` writes it; ``mael self status`` and the
orchestrator's ``GET /api/system`` read it.

The job holds an flock on ``update.lock`` for its whole run. The lock, not the
pid, says whether a job is alive: a pid can be reused after a reboot.
"""

import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

RECORD = "update.json"
LOCK = "update.lock"

#: What the job writes, plus ``interrupted``, which only :func:`read_record` derives.
State = Literal["running", "succeeded", "failed", "interrupted"]


@dataclass(frozen=True)
class UpdateRecord:
    """One update job: its process, its times, the tags around it, and its result."""

    state: State
    pid: int
    started_at: str
    log: str
    from_tag: str | None = None
    to_tag: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pid": self.pid,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "fromTag": self.from_tag,
            "toTag": self.to_tag,
            "exitCode": self.exit_code,
            "log": self.log,
        }

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "UpdateRecord":
        return cls(
            state=data["state"],
            pid=int(data["pid"]),
            started_at=data["startedAt"],
            log=data["log"],
            from_tag=data.get("fromTag"),
            to_tag=data.get("toTag"),
            finished_at=data.get("finishedAt"),
            exit_code=data.get("exitCode"),
        )


def record_path(install_root: Path) -> Path:
    return install_root / RECORD


def lock_path(install_root: Path) -> Path:
    return install_root / LOCK


def lock_held(install_root: Path) -> bool:
    """Whether a process holds ``update.lock``: an update runs."""
    path = lock_path(install_root)
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


#: A reader's :func:`lock_held` holds the lock for an instant, so a job retries
#: before it decides that another update runs.
_LOCK_TRIES = 10
_LOCK_WAIT_SECS = 0.2


@contextmanager
def update_lock(
    install_root: Path, sleep: Callable[[float], None] = time.sleep
) -> Iterator[bool]:
    """Hold ``update.lock`` for the block. Yields ``False`` when another update holds it."""
    install_root.mkdir(parents=True, exist_ok=True)
    with open(lock_path(install_root), "a") as lock:
        for attempt in range(_LOCK_TRIES):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt + 1 < _LOCK_TRIES:
                    sleep(_LOCK_WAIT_SECS)
        else:
            yield False
            return
        yield True


def read_record(
    install_root: Path, held: Callable[[Path], bool] = lock_held
) -> UpdateRecord | None:
    """The last job's record, or ``None`` when no job ran or the file is unreadable.

    A ``running`` record whose lock is free reads as ``interrupted``: a reboot
    or a kill stopped the job before it wrote its result.
    """
    try:
        record = UpdateRecord.from_wire(
            json.loads(record_path(install_root).read_text())
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if record.state == "running" and not held(install_root):
        return replace(record, state="interrupted")
    return record


def write_record(install_root: Path, record: UpdateRecord) -> None:
    """Write ``record`` through a temp file and a rename, so a reader never sees half."""
    path = record_path(install_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    staging.write_text(json.dumps(record.to_wire(), indent=2) + "\n")
    os.replace(staging, path)
