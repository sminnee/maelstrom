"""Integration with Claude Code: its settings, and what older versions installed.

The skills are plugins now: see :mod:`mael_cli.self.plugins`.
"""

import json
from pathlib import Path

from mael_common.util import read_json


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
    """Install the sandbox exclusions, and clear what older versions installed."""
    messages = install_sandbox_exclusions()
    messages.extend(remove_session_hooks())
    messages.extend(remove_session_channel())
    return messages
