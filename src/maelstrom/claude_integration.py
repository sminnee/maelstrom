"""Integration with Claude Code for skills and hooks."""

import json
import os
import shutil
from pathlib import Path


def get_shared_dir() -> Path:
    """Get path to maelstrom's shared/ directory."""
    module_dir = Path(__file__).parent
    dev_path = module_dir.parent.parent / "shared"
    if dev_path.exists():
        return dev_path
    raise FileNotFoundError("Could not locate maelstrom shared directory")


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


def read_json(path: Path) -> dict:
    """Read a JSON object, returning ``{}`` if it is missing or unreadable.

    Public because other modules read the same third-party files (notably
    ``~/.claude.json``); a second private reader would drift from this one.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


#: The `mael` command groups that must run outside Claude Code's sandbox.
#:
#: `mael task` is the launch path: it connects to the agent daemon's Unix
#: domain socket, writes the port allocations under `~/.maelstrom`, and creates
#: a sibling worktree. A sandbox denies all three, and the denial surfaces as
#: "No agent daemon", so a session cannot start the next task in its own chain.
SANDBOX_EXCLUSIONS = ("mael task:*",)


def install_sandbox_exclusions(path: Path | None = None) -> list[str]:
    """Add :data:`SANDBOX_EXCLUSIONS` to `sandbox.excludedCommands`.

    Merges into whatever the file holds: the entries already there keep their
    order, and a second run adds nothing. `path` defaults to the real
    `~/.claude/settings.json` and exists for the tests.
    """
    path = path if path is not None else Path.home() / ".claude" / "settings.json"
    data = read_json(path)

    sandbox = data.setdefault("sandbox", {})
    if not isinstance(sandbox, dict):
        return [f"Cannot install: {path} has non-object sandbox"]
    excluded = sandbox.setdefault("excludedCommands", [])
    if not isinstance(excluded, list):
        return [f"Cannot install: {path} has non-list sandbox.excludedCommands"]

    added = [rule for rule in SANDBOX_EXCLUSIONS if rule not in excluded]
    if not added:
        return [f"Sandbox exclusions already installed in {path}"]
    excluded.extend(added)
    _write_json(path, data)
    return [f"Installed sandbox exclusions in {path}: {', '.join(added)}"]


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
            h
            for h in block_hooks
            if not (
                isinstance(h, dict)
                and isinstance(h.get("command"), str)
                and h["command"].startswith("mael session record")
            )
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


def remove_session_hooks(path: Path | None = None) -> list[str]:
    """Clear `mael session record …` hooks an older `mael install` wrote.

    Those hooks fed the session registry, which no longer exists, so each one
    now runs a command that is gone. `path` defaults to the real
    `~/.claude/settings.json` and exists for the tests.

    An event whose blocks all belonged to `mael` is dropped, because an event
    key with no hooks under it says nothing. The `hooks` object itself is left
    even when empty: it is the user's, and this file belongs to Claude Code.
    """
    path = path if path is not None else Path.home() / ".claude" / "settings.json"
    data = read_json(path)

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []

    removed_any = False
    for event_name, existing in list(hooks.items()):
        if not isinstance(existing, list):
            continue
        cleaned, removed = _strip_mael_hooks(existing)
        if removed:
            removed_any = True
            if cleaned:
                hooks[event_name] = cleaned
            else:
                del hooks[event_name]

    if not removed_any:
        return []
    _write_json(path, data)
    return [f"Removed stale mael session hooks from {path}"]


def remove_session_channel(path: Path | None = None) -> list[str]:
    """Clear the `mael-session` MCP entry an older `mael install` wrote.

    It pointed at `mael session-channel`, which no longer exists, so Claude
    Code would try to spawn a missing command on every session.

    Only an entry that still runs `mael` is removed. The key is the user's file,
    so one they repurposed for a server of their own is left alone rather than
    deleted on a name match.
    """
    path = path if path is not None else Path.home() / ".claude.json"
    data = read_json(path)

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    entry = servers.get("mael-session")
    if not isinstance(entry, dict) or entry.get("command") != "mael":
        return []

    del servers["mael-session"]
    _write_json(path, data)
    return [f"Removed stale mael-session MCP entry from {path}"]


def install_claude_integration() -> list[str]:
    """Install skills and hooks, and clear what older versions installed."""
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

    messages.extend(install_sandbox_exclusions())

    messages.extend(remove_session_hooks())
    messages.extend(remove_session_channel())

    # Keep an opted-in scheduled-task agent in sync (self-heals its `mael` path
    # after a self-update). A no-op when the opt-in marker is absent or off-mac.
    from .schedule_launchd import ensure_schedule_agent

    messages.extend(ensure_schedule_agent())

    return messages or ["Nothing to install"]
