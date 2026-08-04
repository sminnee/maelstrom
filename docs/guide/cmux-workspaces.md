# cmux workspaces

cmux manages the workspaces agent sessions run in. This is where you watch them.

## Why a workspace manager

You cannot supervise what you cannot find. Run three agents in three terminal tabs and
within an hour you have lost track of which tab holds which branch, which app is on which
port, and which agent is waiting on you.

Maelstrom gives every worktree **one workspace, always in the same place**, named after the
worktree. So `myproject-bravo` is where bravo's agent runs, its app is shown, and its shell
waits — every time.

## Sessions run in cmux workspaces

Maelstrom starts a Claude session by driving the cmux socket. If cmux is down, maelstrom
starts it. If cmux cannot be reached, maelstrom **fails** rather than running the agent
locally where you would not find it.

This is deliberate. A silent fallback to a local shell would leave agents running in places
the supervision commands cannot see, which defeats the purpose.

Check that the path works:

```bash
mael cmux status
```

It runs the same probe the launcher uses, starts cmux if it is down, and exits non-zero when
cmux cannot be reached. That makes it a health check for scheduled runs too.

## `--here` — the escape hatch

`--here` runs the agent in your current shell: no worktree, no new workspace.

```bash
mael task add "Quick fix" --run --here
mael task next --run --here
mael task run <id> --here
mael task load-many plan.md --run --here    # head task only
```

Use it when you are already in the right directory and want the session in front of you —
debugging, a one-off, or a machine with no cmux.

`--here` is a **choice you make**, never a fallback maelstrom takes for you. It bypasses the
launcher completely.

## The three-pane layout

Each workspace is named `<project>-<worktree>` and has three panes:

| Pane | Holds |
|---|---|
| 0 | The Claude session. |
| 1 | A shell. On creation it runs the project's `install_cmd`. |
| 2 | Browsers: the running app, and the pull request. |

Two cases, handled differently:

- **The workspace already exists** — maelstrom adds a fresh Claude tab to pane 0 and leaves
  every other pane alone. It does not install again.
- **No workspace** — maelstrom creates it with Claude as pane 0's first terminal, then
  splits a shell pane running `install_cmd`.

The Claude tab is what must succeed. The install/shell pane is best-effort: a workspace with
Claude but no shell pane is degraded, not a failure.

### The browser pane

`mael env start` puts the running app into pane 2, and `mael env open` reopens it:

```bash
mael env open        # browser pane for this worktree's app
```

Pull request URLs recycle a single `github.com` tab, so opening a second PR replaces the
first rather than piling up tabs.

## The status bar

Set text against the workspace so you can see at a glance what an agent is doing:

```bash
mael status set "Working on PROJ-123"
mael status clear
```

## Outside cmux

Every cmux call degrades silently when there is no cmux workspace to act on. So maelstrom
commands you run in a plain terminal — `mael list`, `mael env status`, `mael task list` —
work normally. Only *launching a session* requires cmux, and only because that session must
land somewhere you can find it.

## The socket

Maelstrom talks to cmux over a Unix socket, `/tmp/cmux.sock` by default. Override it:

```bash
export CMUX_SOCKET_PATH=/custom/path/cmux.sock
```

## See also

- [Concepts](concepts.md) — why cmux is in the stack.
- [Troubleshooting](troubleshooting.md) — when a session will not launch.
