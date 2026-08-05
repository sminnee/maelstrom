# Getting started

From nothing to a running agent session.

## Prerequisites

The install commands below assume macOS. Maelstrom itself needs Python 3.11 or later and is
not macOS-only, but `mael schedule` is: it drives launchd, and no-ops on other platforms.

| Tool | Why | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Installs and runs maelstrom. | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Claude Code](https://claude.com/claude-code) | The agent maelstrom launches. | `npm install -g @anthropic-ai/claude-code` |
| [cmux](https://github.com/sminnee/cmux) | Manages the workspaces sessions run in. | See the cmux README. |
| [GitHub CLI](https://cli.github.com/) | Pull requests and CI. | `brew install gh && gh auth login` |
| [bun](https://bun.sh/) | Runs the session-tracking channel. | `curl -fsSL https://bun.sh/install \| bash` |
| git | Worktrees. | Preinstalled on macOS. |

Only `uv` and git are needed to create worktrees. You need cmux and Claude Code to launch
agent sessions, `gh` for pull requests, and `bun` for session tracking. Skip the channel
with `mael install --no-monitor` if you do not want it.

## 1. Install maelstrom

```bash
uv tool install sminnee-maelstrom
```

Then install the Claude Code skills and hooks:

```bash
mael install
```

Check it worked:

```bash
mael --version
mael cmux status      # confirms maelstrom can reach cmux
```

## 2. Point maelstrom at your projects directory

Maelstrom keeps every project under one directory. The default is `~/Projects`. To use
another, create `~/.maelstrom/config.yaml`:

```yaml
projects_dir: ~/Code
open_command: cursor      # optional; default is "code"
```

Set this **before** you add a project. `mael add-project --projects-dir` only affects the
clone; every other command reads `projects_dir` from this file.

## 3. Add a project

### A new project

```bash
mael create-project repo
```

This creates `github.com/<you>/repo`, checks it out, and opens a worktree on
`feat/start-project` with a Claude session in it. The repository is private; pass
`--public` for a public one, and use `owner/name` to create it in an organization.

The first commit holds the files a maelstrom project needs:

| File | Purpose |
|---|---|
| `.gitignore` | Ignores `.env` and `.claude/CLAUDE.local.md`, which maelstrom generates per worktree. |
| `.maelstrom.yaml` | Commented stub. Fill it in at step 4. |
| `README.md` | Project title. |
| `CLAUDE.md` | Imports `.claude/CLAUDE.local.md`, which the first `mael add` writes. |

### An existing repository

```bash
mael add-project git@github.com:org/repo.git
```

This clones the repository into a bare-like layout and creates the first worktree,
**alpha**, on the main branch:

```
~/Projects/repo/
├── .git/            # shared bare git directory
├── .mael            # marker: this is a maelstrom project
└── repo-alpha/      # worktree on main
```

Add `.env` and `.claude/CLAUDE.local.md` to the repository's `.gitignore`. Maelstrom
generates both per worktree. `mael create-project` does this for you.

## 4. Describe the project's services

Create `.maelstrom.yaml` in the repository root and commit it:

```yaml
install_cmd: "npm install"

services:
  web:
    ports: [FRONTEND]
    command: npm run dev -- --port ${FRONTEND_PORT}
```

Each name in `ports:` becomes a `<NAME>_PORT` variable that maelstrom allocates per
worktree. See [dev-environments.md](dev-environments.md) for containers, shared services
and the Procfile fallback.

## 5. Add a worktree

```bash
cd ~/Projects/repo/repo-alpha
mael add feature/hello
```

Maelstrom creates the branch, adds the worktree as **bravo**, allocates ports, and writes
a `.env`:

```
Worktree created at: ~/Projects/repo/repo-bravo
  → repo/bravo (created)
App: http://localhost:3000
```

```bash
$ cat ~/Projects/repo/repo-bravo/.env
# Maelstrom port allocations
FRONTEND_PORT=3000
PORT_BASE=300
WORKTREE=bravo
WORKTREE_NUM=1
# End Maelstrom port allocations
```

By default `mael add` also launches a Claude session in a cmux workspace. Pass `--open` to
open your editor instead.

## 6. Start the services

```bash
cd ~/Projects/repo/repo-bravo
mael env start
```

This runs `install_cmd`, then starts every service and reports where the app is:

```
APP RUNNING AT: *3000 • UPTIME: 6s

SERVICE  PID    STATUS   LOG
web      5442   running  ~/.maelstrom/logs/repo/bravo/web.log
```

Useful follow-ups:

```bash
mael env status       # PIDs, status, log paths
mael env logs -f      # follow the logs
mael env stop         # stop the services
```

## 7. Launch an agent session

```bash
mael task add "Add a hello endpoint" --run
```

This creates a task and launches a Claude session in a cmux workspace with three panes:
Claude, a shell, and browsers. New tasks default to **plan mode**, so the session plans
with you before it writes code.

For an unattended session that implements straight away:

```bash
mael task add "Bump the pinned pyright" --mode auto --run
```

To run in your current shell instead of a cmux workspace:

```bash
mael task add "Quick fix" --run --here
```

Watch what is running:

```bash
mael list             # worktrees, branches, PRs, app URLs, sessions
mael task list        # tasks you can start now
mael session list     # live Claude sessions
```

## 8. Close the worktree

When the work has merged:

```bash
mael close
```

Close syncs against `origin/main`, checks the worktree is clean, and resets it to main. It
**keeps** the folder, the NATO name and the port allocation, so the next `mael add`
recycles the slot.

Close deliberately refuses to run when work would be lost:

```
Error: Worktree 'bravo' has uncommitted changes.
Error: Worktree 'bravo' has commits not merged to main.
```

Commit and merge first. If you must free the slot with work still in flight, use
`mael close --force` — it discards nothing: uncommitted changes are committed as
`wip: uncommitted changes`, the branch and its PR survive, and maelstrom creates a
"Reopen" task so the work is not forgotten.

To delete a worktree outright, use `mael remove`. See [worktrees.md](worktrees.md) for the
difference.

## Next

- [Concepts](concepts.md) — what the pieces are and why.
- [The multi-agent workflow](multi-agent-workflow.md) — running several agents at once.
- [Integrations](integrations.md) — connecting Linear, Sentry, GitHub and Slack.
- [Troubleshooting](troubleshooting.md) — when something does not work.
