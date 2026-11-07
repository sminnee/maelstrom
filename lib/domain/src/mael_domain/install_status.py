"""The result of a worktree's last ``install_cmd`` run: its log and its status.

Both files sit with the service logs, in ``~/.maelstrom/logs/<project>/<worktree>/``,
so git does not see them. ``install.status`` holds ``running <pid>``, or the exit
code of the run that ended. The install's own shell writes both, so a start that
fails leaves no stale ``running``.
"""

import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from . import context
from .worktree_model import extract_worktree_name_from_folder

#: Seconds between two reads of a running install's status.
POLL_INTERVAL = 0.5


@dataclass(frozen=True)
class InstallStatus:
    """The last install. ``code`` is ``None`` while it runs."""

    code: int | None
    log: Path
    #: The install's shell, while it runs.
    pid: int | None = None

    @property
    def passed(self) -> bool:
        return self.code == 0


def names_from_path(worktree_path: Path) -> tuple[str, str]:
    """The project and worktree names the logs use for ``worktree_path``."""
    project = worktree_path.parent.name
    folder = worktree_path.name
    return project, extract_worktree_name_from_folder(project, folder) or folder


def log_dir(project: str, worktree: str) -> Path:
    """The directory that holds a worktree's service logs and install files."""
    return context.get_maelstrom_dir() / "logs" / project / worktree


def install_log_path(project: str, worktree: str) -> Path:
    return log_dir(project, worktree) / "install.log"


def _status_path(project: str, worktree: str) -> Path:
    return log_dir(project, worktree) / "install.status"


def read_install_status(project: str, worktree: str) -> InstallStatus | None:
    """The last install's status. ``None`` when no install has run.

    A ``running`` install whose shell is gone, such as after a reboot, ended
    without a code. It reads as exit 1.
    """
    log = install_log_path(project, worktree)
    try:
        text = _status_path(project, worktree).read_text().split()
    except OSError:
        return None
    try:
        if text[:1] == ["running"]:
            pid = int(text[1])
            if _alive(pid):
                return InstallStatus(None, log, pid)
            return InstallStatus(1, log)
        return InstallStatus(int(text[0]), log)
    except (IndexError, ValueError):
        return None


def wait_for_install(project: str, worktree: str) -> InstallStatus | None:
    """The last install's status, after a running install ends."""
    while (status := read_install_status(project, worktree)) and status.code is None:
        time.sleep(POLL_INTERVAL)
    return status


def mark_running(project: str, worktree: str, pid: int) -> None:
    """Record that ``pid`` holds an install that is about to start."""
    path = _status_path(project, worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"running {pid}\n")


def install_argv(
    project: str, worktree: str, install_cmd: str, *, tee: bool = False
) -> list[str]:
    """``sh`` argv that runs ``install_cmd`` and writes the install status.

    The command runs in a subshell, so an ``exit`` in it still lets the status
    be written. With ``tee``, the output goes to the install log and to stdout.
    """
    dir_ = log_dir(project, worktree)
    dir_.mkdir(parents=True, exist_ok=True)
    status = shlex.quote(str(_status_path(project, worktree)))
    body = f"echo running $$ > {status}; ( {install_cmd}\n) 2>&1; echo $? > {status}"
    if tee:
        log = shlex.quote(str(install_log_path(project, worktree)))
        body = f"{{ {body}; }} | tee {log}"
    return ["sh", "-c", body]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
