"""Claude Code IDE session tracking."""

import json
import os
from pathlib import Path


def get_active_ide_sessions() -> dict[Path, int]:
    """Return mapping of workspace folder path -> active session count."""
    ide_dir = Path.home() / ".claude" / "ide"
    sessions: dict[Path, int] = {}

    if not ide_dir.exists():
        return sessions

    for lock_file in ide_dir.glob("*.lock"):
        try:
            data = json.loads(lock_file.read_text())
            pid = data.get("pid")
            folders = data.get("workspaceFolders", [])

            # Check if process is running
            if pid and _is_process_running(pid):
                for folder in folders:
                    folder_path = Path(folder)
                    sessions[folder_path] = sessions.get(folder_path, 0) + 1
        except (json.JSONDecodeError, OSError):
            continue

    return sessions


def _is_process_running(pid: int) -> bool:
    """Check if a process is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
