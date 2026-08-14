"""Headless Claude session that resolves an in-progress rebase conflict.

A leaf module: it imports only the standard library and ``shell``, which is
itself a leaf, so ``worktree`` can call it without pulling in the launcher layer.
"""

import subprocess
from pathlib import Path

from .shell import run_cmd

_REPAIR_TIMEOUT = 600  # seconds; a conflict resolution is not a long job


def run_resolve_rebase_session(worktree_path: Path) -> subprocess.CompletedProcess:
    """Run ``/resolve-rebase-conflicts`` headlessly in ``worktree_path``.

    The session runs in the worktree so it sees the mid-rebase tree. It uses the
    same auto permission mode as an unattended task session, and skips project
    MCP servers.

    Its output streams to the console as the session works. The repair can take
    minutes, so capturing it would leave the console silent throughout, with no
    sign of progress and nothing to read when it goes wrong. Nothing needs the
    text afterwards: the caller decides on the exit code and the state of the
    rebase.

    Raises:
        OSError: The ``claude`` binary is missing or cannot be run.
        subprocess.TimeoutExpired: The session ran past ``_REPAIR_TIMEOUT``.
    """
    return run_cmd(
        [
            "claude",
            "-p",
            "/resolve-rebase-conflicts",
            "--permission-mode",
            "auto",
            "--strict-mcp-config",
        ],
        cwd=worktree_path,
        stream=True,
        timeout=_REPAIR_TIMEOUT,
        check=False,
    )
