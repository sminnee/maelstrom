---
name: cmux
description: "Open browsers and terminals in cmux panes. Use when the user asks to show a URL, open a browser, or create a new terminal."
---

# cmux

Use cmux only when `CMUX_SOCKET_PATH` is set. Pass `--socket "$CMUX_SOCKET_PATH"` to every command. Commands return `OK` or `OK <ref>`, not JSON.

Commands are non-blocking. Do not attempt cmux when the socket is absent or pass `--json`.

The standard worktree layout is: pane 0 Claude, pane 1 shell, pane 2 browsers. A workspace is a window, a pane is a split, and a surface is a tab.

```bash
# Browser or terminal; add --workspace <workspace_ref> when needed.
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type browser --url <url>
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type terminal --direction right

# Use the returned surface ref.
cmux --socket "$CMUX_SOCKET_PATH" send --surface <surface_ref> --text "<command>\n"
cmux --socket "$CMUX_SOCKET_PATH" close-surface --surface <surface_ref>

# Task status; icons include hammer and sparkle.
cmux --socket "$CMUX_SOCKET_PATH" set-status task "<label>" --icon <icon>
```

Terminal directions are `right` and `down`. For maelstrom's Python integration, read `docs/dev/cmux.md`.
