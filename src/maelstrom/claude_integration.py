"""Integration with Claude Code for skills and hooks."""

import json
import os
import shutil
import subprocess
from pathlib import Path


WRAPPER_MARKER_BEGIN = "# >>> mael session wrapper >>>"
WRAPPER_MARKER_END = "# <<< mael session wrapper <<<"

WRAPPER_BODY = """\
# Wraps `claude` to always load the mael session-tracking channel,
# which is required while custom channels are in research-preview.
claude() {
  command claude --dangerously-load-development-channels server:mael-session "$@"
}
"""


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


def install_session_hooks() -> list[str]:
    """Install UserPromptSubmit / Stop / Notification hooks in ~/.claude/settings.json."""
    path = Path.home() / ".claude" / "settings.json"
    data = _read_json(path)

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return [f"Cannot install: {path} has non-object hooks"]

    event_map = {
        "UserPromptSubmit": "user-prompt-submit",
        "Stop": "stop",
        "Notification": "notification",
    }

    messages = []
    for event_name, record_arg in event_map.items():
        command = f"mael session record {record_arg}"
        new_entry = {"type": "command", "command": command}
        matcher_block = {"matcher": "", "hooks": [new_entry]}

        existing = hooks.get(event_name)
        if not isinstance(existing, list):
            hooks[event_name] = [matcher_block]
            messages.append(f"Installed {event_name} hook in {path}")
            continue

        # Find any matcher block already containing a mael-session hook and replace it.
        replaced = False
        kept_blocks = []
        for block in existing:
            if not isinstance(block, dict):
                kept_blocks.append(block)
                continue
            block_hooks = block.get("hooks", [])
            if not isinstance(block_hooks, list):
                kept_blocks.append(block)
                continue
            non_mael = [
                h for h in block_hooks
                if not (isinstance(h, dict)
                        and isinstance(h.get("command"), str)
                        and h["command"].startswith("mael session record"))
            ]
            if len(non_mael) == len(block_hooks):
                kept_blocks.append(block)
                continue
            replaced = True
            if non_mael:
                # Keep the block with its other hooks intact.
                kept = dict(block)
                kept["hooks"] = non_mael
                kept_blocks.append(kept)
            # Drop the mael hooks; we'll re-add ours as its own block below.

        kept_blocks.append(matcher_block)
        hooks[event_name] = kept_blocks
        messages.append(
            f"{'Updated' if replaced else 'Installed'} {event_name} hook in {path}"
        )

    _write_json(path, data)
    return messages


def install_claude_wrapper() -> list[str]:
    """Append a `claude()` shell function to ~/.zshrc that loads the dev channel."""
    rc = Path.home() / ".zshrc"
    block = f"{WRAPPER_MARKER_BEGIN}\n{WRAPPER_BODY}{WRAPPER_MARKER_END}\n"

    if not rc.exists():
        rc.write_text(block)
        return [
            f"Created {rc} with claude() wrapper.",
            f"Re-source your shell: source {rc}",
        ]

    text = rc.read_text()

    if WRAPPER_MARKER_BEGIN in text and WRAPPER_MARKER_END in text:
        start = text.index(WRAPPER_MARKER_BEGIN)
        end = text.index(WRAPPER_MARKER_END) + len(WRAPPER_MARKER_END)
        current = text[start:end]
        if current.strip() == block.strip():
            return [f"claude() wrapper already present in {rc}"]
        return [
            f"WARNING: {rc} has an existing mael wrapper block with different contents.",
            "Resolve manually (remove the old block to let `mael install` rewrite it).",
        ]

    sep = "" if text.endswith("\n") else "\n"
    rc.write_text(text + sep + "\n" + block)
    return [
        f"Appended claude() wrapper to {rc}.",
        f"Re-source your shell: source {rc}",
    ]


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
        messages.extend(install_claude_wrapper())
        messages.extend(install_session_channel_deps())

    return messages or ["Nothing to install"]
