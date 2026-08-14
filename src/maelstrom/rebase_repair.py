"""Headless Claude session that resolves an in-progress rebase conflict.

A leaf module: it imports only the standard library, so ``worktree`` can call it
without pulling in the launcher layer.
"""

import subprocess
from pathlib import Path

_REPAIR_TIMEOUT = 600  # seconds; a conflict resolution is not a long job


def run_resolve_rebase_session(worktree_path: Path) -> subprocess.CompletedProcess:
    """Run ``/resolve-rebase-conflicts`` headlessly in ``worktree_path``.

    The session runs in the worktree so it sees the mid-rebase tree. It uses the
    same auto permission mode as an unattended task session, and skips project
    MCP servers.

    Raises:
        OSError: The ``claude`` binary is missing or cannot be run.
        subprocess.TimeoutExpired: The session ran past ``_REPAIR_TIMEOUT``.
    """
    return subprocess.run(
        [
            "claude",
            "-p",
            "/resolve-rebase-conflicts",
            "--permission-mode",
            "auto",
            "--strict-mcp-config",
        ],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        timeout=_REPAIR_TIMEOUT,
        check=False,
    )
