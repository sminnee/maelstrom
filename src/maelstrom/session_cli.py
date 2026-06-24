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
from .util import atomic_write_json, now_iso
from .shell import run_cmd


SESSIONS_SUBDIR = "sessions"


def _sessions_dir() -> Path:
    return get_maelstrom_dir() / SESSIONS_SUBDIR


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
        run_cmd(["bun", "run", str(script)], replace_process=True)
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


# Each hook is installed with its own `event` argument; the argument maps
# directly to a session state, or to the special `session-end` action.
#
# This keeps the record command stateless: it doesn't need to know which
# Claude Code hook fired or interpret payload fields — the hook installer
# in claude_integration.py picks the right argument per matcher.
_EVENT_TO_STATE: dict[str, str] = {
    "user-prompt-submit": "processing",
    "stop": "idle",
    "stop-failure": "idle",
    "permission-prompt": "awaiting-permission",
    "elicitation-prompt": "awaiting-permission",
    "idle-prompt": "idle",
    "ask-user-pre": "awaiting-user-input",
    "ask-user-post": "processing",
}

SESSION_END_EVENT = "session-end"
HEARTBEAT_EVENT = "heartbeat"


def _close_task_for_session(cwd: str | None) -> None:
    """Mark the launching task ``done`` when its agent session ends.

    The open session *is* the "in-progress" signal: `mael task run` exports
    ``MAEL_TASK_ID`` into the launched Claude process, and Claude Code fires
    hooks as child processes, so the `session-end` hook inherits that env var.
    Reading it here is what lets us close the task without the agent having to
    remember to run `mael task status done`.

    Defensive throughout: a non-task session (no ``MAEL_TASK_ID``) is a clean
    no-op, and any failure — unresolvable project, missing task store, task
    already gone — is swallowed so session teardown always completes. We only
    move tasks that are still ``in-progress``; a task already moved to
    ``done``/``cancelled``/``blocked`` (by the agent or the user) is left alone.
    """
    task_id = os.environ.get("MAEL_TASK_ID")
    if not task_id:
        return

    try:
        from maelstrom import task as model
        from maelstrom import task_actions
        from maelstrom.task_store import GitFileStore

        ctx = resolve_context(
            None,
            require_project=True,
            cwd=Path(cwd) if cwd else None,
        )
        project = ctx.project
        if not project:
            return  # require_project guarantees this, but narrows the type

        store = GitFileStore()
        key = model.find_key(store, project, task_id)
        if key is None:
            return  # task already deleted — nothing to close
        if model.status_from_key(key) != model.STATUS_IN_PROGRESS:
            return  # already terminal or back in todo — don't clobber
        task_actions.move_with_actions(store, project, task_id, model.STATUS_DONE)
        click.echo(
            f"Session ended: closed task {project}/{task_id} -> "
            f"{model.STATUS_DONE}",
            err=True,
        )
    except Exception:
        # A hook must never crash session teardown.
        pass


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

    if event not in {SESSION_END_EVENT, HEARTBEAT_EVENT, *_EVENT_TO_STATE}:
        click.echo(f"Unknown event: {event}", err=True)
        sys.exit(2)

    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    pid = payload.get("pid")

    path = _find_session_file(session_id, cwd, pid if isinstance(pid, int) else None)
    if path is None:
        return

    if event == SESSION_END_EVENT:
        # The ending session is the completion signal for the task it launched:
        # close that task before tearing down the session file.
        _close_task_for_session(cwd)
        try:
            path.unlink()
        except OSError:
            pass
        return

    data = _read_session_file(path)
    if data is None:
        return

    # heartbeat events bump updated_at without changing state, so they can
    # safely fire alongside state-setting hooks regardless of ordering.
    if event != HEARTBEAT_EVENT:
        data["state"] = _EVENT_TO_STATE[event]
    data["updated_at"] = now_iso()
    atomic_write_json(path, data)


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


# Claude Code doesn't fire a hook on ESC / user-interrupt, so a session
# stuck in `processing` would never resolve on its own. If updated_at is
# older than this threshold, treat the state as idle (and rewrite the
# file so subsequent listings agree).
#
# The heartbeat hooks (matcher "" on PreToolUse/PostToolUse) bump
# updated_at every tool call, so a session genuinely doing work keeps
# ticking. The threshold needs to be longer than the slowest single tool
# call — 5 minutes accommodates long Bash runs and Task sub-agents.
STALE_PROCESSING_SECS = 300


def _is_stale_processing(state: str, updated_at: str) -> bool:
    if state != "processing" or not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age > STALE_PROCESSING_SECS


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

        state = data.get("state", "")
        updated_at = data.get("updated_at", "")
        if _is_stale_processing(state, updated_at):
            state = "idle"
            data["state"] = state
            atomic_write_json(f, data)

        cwd = data.get("cwd", "")
        project, worktree = _derive_project_worktree(cwd)
        pw = f"{project}/{worktree}" if project and worktree else (project or "")

        rows.append({
            "STATE": state,
            "PROJECT/WORKTREE": pw,
            "CWD": cwd,
            "AGE": _format_age(data.get("started_at", "")),
            "PID": str(data.get("pid", "")),
        })

    if not rows:
        click.echo("No active Claude Code sessions.")
        return

    draw_table(rows, ["STATE", "PROJECT/WORKTREE", "CWD", "AGE", "PID"])
