"""Integration with Claude Code for skills and hooks."""

import json
import os
import shutil
import subprocess
from pathlib import Path


def get_shared_dir() -> Path:
    """Get path to maelstrom's shared/ directory."""
    module_dir = Path(__file__).parent
    dev_path = module_dir.parent.parent / "shared"
    if dev_path.exists():
        return dev_path
    raise FileNotFoundError("Could not locate maelstrom shared directory")


def get_channel_dir() -> Path:
    """Get path to the bun-based session-channel project."""
    module_dir = Path(__file__).parent
    return module_dir.parent.parent / "tools" / "mael-session-channel"


def _symlink_items(source_dir: Path, target_dir: Path) -> list[str]:
    """Symlink all items from source_dir into target_dir. Returns messages."""
    messages = []
    if not source_dir.exists():
        return [f"Source not found: {source_dir}"]

    target_dir.mkdir(parents=True, exist_ok=True)

    for item in source_dir.iterdir():
        target = target_dir / item.name

        if target.is_symlink():
            if target.resolve() == item.resolve():
                continue  # Already correctly linked
            old_target = target.resolve()
            target.unlink()
            messages.append(f"Replaced old link {target.name} (was {old_target})")
        elif target.exists():
            backup = target.with_suffix(".backup")
            if backup.exists():
                shutil.rmtree(backup)
            target.rename(backup)
            messages.append(f"Backed up existing {target.name}")

        target.symlink_to(item)
        messages.append(f"Linked {target} -> {item}")

    # Clean up stale symlinks that point into source_dir but no longer exist
    for entry in target_dir.iterdir():
        if not entry.is_symlink():
            continue
        link_target = Path(os.readlink(entry))
        # Resolve relative symlinks against the symlink's parent
        if not link_target.is_absolute():
            link_target = entry.parent / link_target
        try:
            link_target.resolve().relative_to(source_dir.resolve())
        except ValueError:
            continue  # Points elsewhere, leave it alone
        if not link_target.exists():
            entry.unlink()
            messages.append(f"Removed stale link {entry.name}")

    return messages


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def install_session_channel() -> list[str]:
    """Register the mael-session MCP channel in ~/.claude.json."""
    path = Path.home() / ".claude.json"
    data = _read_json(path)

    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        return [f"Cannot install: {path} has non-object mcpServers"]

    entry = {"command": "mael", "args": ["session-channel"]}
    if servers.get("mael-session") == entry:
        return [f"MCP channel already registered in {path}"]

    servers["mael-session"] = entry
    _write_json(path, data)
    return [f"Registered MCP channel mael-session in {path}"]


# (Claude Code hook name, matcher, record-event-arg) for every hook we install.
# Matcher "" matches all firings of the event; otherwise it's an exact string
# (or regex for tool-name matchers). The third element is the argument passed
# to `mael session record`.
_SESSION_HOOKS: list[tuple[str, str, str]] = [
    ("UserPromptSubmit", "", "user-prompt-submit"),
    ("Stop", "", "stop"),
    ("StopFailure", "", "stop-failure"),
    ("Notification", "permission_prompt", "permission-prompt"),
    ("Notification", "elicitation_dialog", "elicitation-prompt"),
    ("Notification", "idle_prompt", "idle-prompt"),
    ("PreToolUse", "AskUserQuestion|ExitPlanMode", "ask-user-pre"),
    ("PostToolUse", "AskUserQuestion|ExitPlanMode", "ask-user-post"),
    # Heartbeats: bump updated_at on every tool call without changing state.
    # Lets `mael session list` detect ESC / interrupt while still tolerating
    # long-running tools.
    ("PreToolUse", "", "heartbeat"),
    ("PostToolUse", "", "heartbeat"),
    ("SessionEnd", "", "session-end"),
]


def _strip_mael_hooks(blocks: list) -> tuple[list, bool]:
    """Return (cleaned_blocks, removed_any) for a hook event's blocks list.

    Removes any `mael session record …` hook entry; keeps non-mael hooks
    intact, dropping a block only if it's empty afterwards.
    """
    cleaned: list = []
    removed = False
    for block in blocks:
        if not isinstance(block, dict):
            cleaned.append(block)
            continue
        block_hooks = block.get("hooks", [])
        if not isinstance(block_hooks, list):
            cleaned.append(block)
            continue
        non_mael = [
            h for h in block_hooks
            if not (isinstance(h, dict)
                    and isinstance(h.get("command"), str)
                    and h["command"].startswith("mael session record"))
        ]
        if len(non_mael) == len(block_hooks):
            cleaned.append(block)
            continue
        removed = True
        if non_mael:
            kept = dict(block)
            kept["hooks"] = non_mael
            cleaned.append(kept)
    return cleaned, removed


def install_session_hooks() -> list[str]:
    """Install all session-tracking hooks in ~/.claude/settings.json.

    Removes any pre-existing `mael session record …` entries first so re-runs
    are idempotent and stale event/matcher combinations get cleaned up.
    """
    path = Path.home() / ".claude" / "settings.json"
    data = _read_json(path)

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return [f"Cannot install: {path} has non-object hooks"]

    # First pass: strip any prior mael entries across every hook event.
    any_removed = False
    for event_name, existing in list(hooks.items()):
        if not isinstance(existing, list):
            continue
        cleaned, removed = _strip_mael_hooks(existing)
        if removed:
            any_removed = True
            hooks[event_name] = cleaned

    # Second pass: install each (event, matcher, record-arg) entry.
    for event_name, matcher, record_arg in _SESSION_HOOKS:
        entry = {"type": "command", "command": f"mael session record {record_arg}"}
        block = {"matcher": matcher, "hooks": [entry]}
        existing = hooks.get(event_name)
        if isinstance(existing, list):
            existing.append(block)
        else:
            hooks[event_name] = [block]

    _write_json(path, data)
    action = "Reinstalled" if any_removed else "Installed"
    return [f"{action} mael session hooks in {path}"]


def install_session_channel_deps() -> list[str]:
    """Run `bun install` inside tools/mael-session-channel/."""
    channel_dir = get_channel_dir()
    if not channel_dir.exists():
        return [f"Channel dir not found at {channel_dir}; skipping bun install"]

    node_modules = channel_dir / "node_modules"
    if node_modules.exists():
        return [f"Channel deps already installed in {channel_dir}"]

    try:
        subprocess.run(
            ["bun", "install"],
            cwd=channel_dir,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return [
            "WARNING: bun not found on PATH; install from https://bun.sh.",
            "The session-tracking channel will not start until bun is installed.",
        ]
    except subprocess.CalledProcessError as e:
        return [f"WARNING: bun install failed: {e.stderr or e.stdout or e}"]

    return [f"Installed channel deps in {channel_dir}"]


def install_claude_integration(*, monitor: bool = True) -> list[str]:
    """Install skills, hooks, commands, and (optionally) the session monitor."""
    shared = get_shared_dir()
    claude_dir = Path.home() / ".claude"

    messages = []

    # Symlink skills
    skills_source = shared / "skills"
    if skills_source.exists():
        messages.extend(_symlink_items(skills_source, claude_dir / "skills"))

    # Symlink hooks
    hooks_source = shared / "hooks"
    if hooks_source.exists():
        messages.extend(_symlink_items(hooks_source, claude_dir / "hooks"))

    # Symlink commands
    commands_source = shared / "commands"
    if commands_source.exists():
        messages.extend(_symlink_items(commands_source, claude_dir / "commands"))

    if monitor:
        messages.extend(install_session_channel())
        messages.extend(install_session_hooks())
        messages.extend(install_session_channel_deps())

    # Keep an opted-in scheduled-task agent in sync (self-heals its `mael` path
    # after a self-update). A no-op when the opt-in marker is absent or off-mac.
    from .schedule_launchd import ensure_schedule_agent

    messages.extend(ensure_schedule_agent())

    return messages or ["Nothing to install"]
