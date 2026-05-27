"""Session-tracking CLI: `mael session record`, `mael session list`, and `mael session-channel`."""

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .context import get_maelstrom_dir, resolve_context
from .table import draw_table


SESSIONS_SUBDIR = "sessions"


def _sessions_dir() -> Path:
    return get_maelstrom_dir() / SESSIONS_SUBDIR


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically by writing to a temp file and renaming."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _read_session_file(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _find_session_file(
    session_id: str | None, cwd: str | None, pid: int | None,
) -> Path | None:
    """Find the session file matching session_id, falling back to cwd+pid."""
    sdir = _sessions_dir()
    if not sdir.is_dir():
        return None

    candidates: list[tuple[Path, dict]] = []
    for f in sdir.glob("*.json"):
        data = _read_session_file(f)
        if data is None:
            continue
        candidates.append((f, data))

    if session_id:
        for f, data in candidates:
            if data.get("session_id") == session_id:
                return f
            if data.get("session_key") == session_id:
                return f

    if cwd and pid is not None:
        for f, data in candidates:
            if data.get("cwd") == cwd and data.get("pid") == pid:
                return f

    if cwd:
        for f, data in candidates:
            if data.get("cwd") == cwd:
                return f

    return None


def _liveness_check(port: int) -> bool:
    """Return True if something is listening on 127.0.0.1:port."""
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- session-channel launcher ---


@click.command("session-channel")
def session_channel() -> None:
    """Launch the Bun-based session-tracking MCP channel.

    Invoked by Claude Code via the user-wide MCP server entry installed by
    `mael install`. Not meant for humans.
    """
    module_dir = Path(__file__).parent
    repo_root = module_dir.parent.parent
    script = repo_root / "tools" / "mael-session-channel" / "index.ts"

    if not script.exists():
        click.echo(
            f"mael-session-channel script not found at {script}.\n"
            "Reinstall maelstrom from a git checkout.",
            err=True,
        )
        sys.exit(1)

    try:
        os.execvp("bun", ["bun", "run", str(script)])
    except FileNotFoundError:
        click.echo(
            "bun is not installed or not on PATH. Install from https://bun.sh.",
            err=True,
        )
        sys.exit(127)


# --- session group ---


@click.group("session")
def session() -> None:
    """Inspect and update Claude Code session state."""


_EVENT_TO_STATE = {
    "user-prompt-submit": "processing",
    "stop": "idle",
}


def _state_for_notification(payload: dict) -> str | None:
    n_type = payload.get("type")
    if n_type == "permission_prompt":
        return "awaiting-permission"
    if n_type == "idle_prompt":
        return "waiting-for-input"
    return None


@session.command("record")
@click.argument("event")
def session_record(event: str) -> None:
    """Update session state from a Claude Code hook event.

    Reads the hook payload as JSON from stdin and rewrites the
    `state` and `updated_at` fields on the matching session file.
    """
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    if event in _EVENT_TO_STATE:
        new_state = _EVENT_TO_STATE[event]
    elif event == "notification":
        new_state = _state_for_notification(payload)
        if new_state is None:
            return
    else:
        click.echo(f"Unknown event: {event}", err=True)
        sys.exit(2)

    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    pid = payload.get("pid")

    path = _find_session_file(session_id, cwd, pid if isinstance(pid, int) else None)
    if path is None:
        # No registry file yet — silently ignore. The channel may not have
        # finished writing it, or this is a stray hook.
        return

    data = _read_session_file(path)
    if data is None:
        return

    data["state"] = new_state
    data["updated_at"] = _now_iso()
    _atomic_write_json(path, data)


def _format_age(started_at: str) -> str:
    try:
        start = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    now = datetime.now(timezone.utc)
    delta = now - start
    total = int(delta.total_seconds())
    if total < 0:
        return "0s"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _derive_project_worktree(cwd: str | None) -> tuple[str | None, str | None]:
    if not cwd:
        return (None, None)
    try:
        ctx = resolve_context(
            None,
            require_project=False,
            require_worktree=False,
            cwd=Path(cwd),
        )
    except ValueError:
        return (None, None)
    return (ctx.project, ctx.worktree)


@session.command("list")
def session_list() -> None:
    """List active Claude Code sessions."""
    sdir = _sessions_dir()
    if not sdir.is_dir():
        click.echo("No active Claude Code sessions.")
        return

    rows = []
    for f in sorted(sdir.glob("*.json")):
        data = _read_session_file(f)
        if data is None:
            # Corrupt file — remove it.
            try:
                f.unlink()
            except OSError:
                pass
            continue

        port = data.get("channel_port", 0)
        if not _liveness_check(int(port) if port else 0):
            try:
                f.unlink()
            except OSError:
                pass
            continue

        cwd = data.get("cwd", "")
        project, worktree = _derive_project_worktree(cwd)
        pw = f"{project}/{worktree}" if project and worktree else (project or "")

        rows.append({
            "STATE": data.get("state", ""),
            "PROJECT/WORKTREE": pw,
            "CWD": cwd,
            "AGE": _format_age(data.get("started_at", "")),
            "PID": str(data.get("pid", "")),
        })

    if not rows:
        click.echo("No active Claude Code sessions.")
        return

    draw_table(rows, ["STATE", "PROJECT/WORKTREE", "CWD", "AGE", "PID"])
