---
name: cmux
description: "Open browsers and terminals in cmux panes. Use when the user asks to show a URL, open a browser, or create a new terminal."
---

# cmux Integration Skill

This skill lets you interact with cmux to open browser panes and terminal panes for the user.

## Detection

cmux is available when the `CMUX_SOCKET_PATH` environment variable is set. Always check this before attempting cmux commands:

```bash
if [ -n "$CMUX_SOCKET_PATH" ]; then
  # cmux is available
fi
```

## Output Format

All cmux commands return plain text in `OK <ref>` format (not JSON). For example:
- `OK 6BA6371B-...` — success with a ref
- `OK` — success without a ref

## Opening a Browser Pane

To show the user a URL in a browser pane:

```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type browser --url <url>
```

To open in a specific workspace:
```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type browser --url <url> --workspace <workspace_ref>
```

## Opening a Terminal Pane

To open a new terminal pane alongside the current one:

```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type terminal --direction right
```

The `--direction` flag accepts `right` or `down`.

To open in a specific workspace:
```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type terminal --direction right --workspace <workspace_ref>
```

## Sending Commands to a Terminal

After creating a terminal pane, you can send commands to it using the surface ref from the `new-pane` response:

```bash
cmux --socket "$CMUX_SOCKET_PATH" send --surface <surface_ref> --text "cd /path/to/project\n"
```

## Closing a Surface

To close a browser or terminal pane:

```bash
cmux --socket "$CMUX_SOCKET_PATH" close-surface --surface <surface_ref>
```

## Setting Status

To show the current task in the cmux status bar:

```bash
cmux --socket "$CMUX_SOCKET_PATH" set-status task <issue-id> --icon <icon>
```

Known icon names: `hammer`, `sparkle`.

Examples:
```bash
cmux --socket "$CMUX_SOCKET_PATH" set-status task "NORT-123" --icon hammer
cmux --socket "$CMUX_SOCKET_PATH" set-status task "Planning NORT-123" --icon sparkle
```

## Common Patterns

**Show the running app to the user:**
```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type browser --url http://localhost:3010
```

**Open a second terminal for running tests:**
```bash
cmux --socket "$CMUX_SOCKET_PATH" new-pane --type terminal --direction right
```

**Set status when starting a task:**
```bash
cmux --socket "$CMUX_SOCKET_PATH" set-status task "NORT-123" --icon hammer
```

## Important Notes

- All cmux commands require `--socket "$CMUX_SOCKET_PATH"` to connect to the running cmux instance.
- Commands return plain text `OK <ref>` format, not JSON. Do NOT use `--json`.
- If `CMUX_SOCKET_PATH` is not set, cmux is not available — do not attempt to use it.
- All commands are non-blocking and return immediately.
