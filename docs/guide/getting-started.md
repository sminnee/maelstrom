# Getting started

From nothing to a running agent session.

## Prerequisites

Maelstrom runs on macOS and on Ubuntu Server 24.04. The installer needs only `curl` and `tar`.
On a first install, `mael self check-host` offers every other tool.

| Tool | Why | Required |
|---|---|---|
| git | Worktrees. | Yes |
| [GitHub CLI](https://cli.github.com/) (`gh`) | Pull requests and CI. | Yes |
| [uv](https://docs.astral.sh/uv/) | Installs and runs maelstrom. | Yes |
| [Claude Code](https://claude.com/claude-code) (`claude`) | The agent maelstrom launches. | Yes |
| `lsof` | Finds the worktree each process runs in. | Yes |
| [Codex](https://github.com/openai/codex) (`codex`) | A second agent. It installs through npm, so it needs Node. | No |
| [Tailscale](https://tailscale.com/) | Reaches the orchestrator and the dev servers from other devices, over HTTPS. | No |
| [tangier](https://pypi.org/project/tangier/) | Runs a project's gates. | No |
| [cmux](https://github.com/sminnee/cmux) | Opens a worktree's terminal from the orchestrator UI. macOS only; install it from its README. | No |

A release install needs no Node and no pnpm: the bundle carries the built web UI.

## 1. Install maelstrom

Run the installer:

```bash
curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh
```

The installer does these steps:

1. It installs uv when uv is missing. uv fetches the Python the release uses.
2. It asks where your projects live, when `projects_dir:` is unset, and writes the key.
3. It writes the latest release bundle to `~/.maelstrom/install/`.
4. On a first install, it runs `mael self check-host`.
5. It runs `mael self setup`.

`check-host` checks each tool in the table above. For a missing tool, it prints the `brew` or
`apt-get` command, and runs it after a yes. Then it checks the sign-ins to `gh`, `claude` and
`codex`, and offers each login. When Tailscale is installed, it offers to connect the machine,
to set `dev_host:` to the tailnet name, and to issue a certificate for `dev_https:`. Run
`mael self check-host` again at any time. A met item asks nothing.

To answer the question from a script, pass the directory:

```bash
curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh -s -- --projects-dir ~/Projects
```

Setup writes `~/.local/bin/mael`, installs the Claude Code skills and hooks, and starts the host services and
the scheduler. See [host services](../dev/host-services.md). The last line names the URL of the
orchestrator. Put `~/.local/bin` on your PATH if it is not there already: `check-host` prints
the line to add.

Run the same line again to upgrade, or run `mael self update`. An upgrade does not run
`check-host`. The installer asks for `projects_dir:` only when the key is unset.

### On Ubuntu Server

The `check-host` commands use `apt-get` and `sudo`. Node comes from the NodeSource 22 repository,
because Ubuntu's own `nodejs` is v18. Tailscale runs `sudo tailscale up`, and makes you its
operator, so that `tailscale cert` runs without sudo.

cmux is not available on Linux. Agents run under the agent daemon, and the orchestrator UI shows
them. `mael agent attach` reaches each one. The orchestrator's `cmux` link refuses with "cmux
could not make the terminal".

### Install from main

To install maelstrom from its main branch, with no release, run this line in place of the
installer:

```bash
curl -LsSf https://raw.githubusercontent.com/sminnee/maelstrom/main/bin/install-main.sh | sh
```

The script asks where your projects live, and clones maelstrom into `<projects_dir>/maelstrom/`,
with `main` checked out in `_main`. It writes `update_channel: main`. Then it runs `_main`'s own
`bin/install.sh --source`. A source install needs Node and pnpm, so on a first install that
script runs `check-host --dev` before the build.

Run the same line again, or run `mael self update`, to pull `_main` and install it again. The
script does not make `maelstrom-alpha` or `_main`'s `.env`: `mael worktree new` and
`mael env reset` make them.

Check it worked:

```bash
mael --version
mael self status      # the release and the host services
```

## 2. Check your projects directory

Maelstrom keeps every project under one directory. The installer asked for it, and wrote it to
`~/.maelstrom/config.yaml`. The default is `~/Projects`. To change it, edit the file:

```yaml
projects_dir: ~/Code
open_command: cursor      # optional; default is "code"
```

Change it **before** you add a project. `mael project add --projects-dir` only affects the
clone; every other command reads `projects_dir` from this file.

## 3. Add a project

### A new project

```bash
mael project create repo
```

This creates `github.com/<you>/repo`, checks it out, and opens a worktree on
`feat/start-project` with a Claude session in it. The repository is private; pass
`--public` for a public one, and use `owner/name` to create it in an organization.

The first commit holds the files a maelstrom project needs:

| File | Purpose |
|---|---|
| `.gitignore` | Ignores `.env`, `.claude/CLAUDE.local.md` and `AGENTS.md`, which maelstrom generates per worktree. |
| `.maelstrom.yaml` | Commented stub. Fill it in at step 4. |
| `README.md` | Project title. |
| `CLAUDE.md` | Imports `.claude/CLAUDE.local.md`, which the first `mael add` writes. `AGENTS.md` (gitignored, generated per worktree) carries the same content with `@` imports inlined, for opencode, which reads only `AGENTS.md` and does not resolve `@` imports. |

### An existing repository

```bash
mael project add https://github.com/org/repo.git
```

Use the HTTPS URL. Agents push with a token over HTTPS, so an SSH remote breaks an
unattended session. `mael project add` clones the URL you give it and does not rewrite
the protocol.

The orchestrator UI does the same. On a fresh install with no project, the UI opens the
**Welcome dialog**. It shows where projects go, which skills `mael self setup` linked, where the
config is, the version and whether the agent host answers. Press **Add your first project**.
Later, press **Add project** in the **Projects** tab, or click the logo to see the Welcome dialog
again.

Give the git URL. The clone runs as an operation. A failed clone shows git's reason in the
dialog. When the clone ends, the **Project dialog** of the new project opens.

**Set up after adding** is on by default. When the clone ends, the dialog creates and
launches a task that runs `/setup-project`. That skill makes the dev env start, then audits
the project. See [Setting up a project](planning.md#setting-up-a-project).

Click a project's row to open its Project dialog. It has two tabs:

- **Overview** shows where `_main` is and where the worktrees go, for example
  `~/Projects/askastro/askastro-alpha..zulu`. It also shows the stack tip and the Linear link.
- **Worktrees** lists the project's worktrees, with their sync, environment and close controls.

Its **Set up** button launches the same task. **Create your first task** opens the new-work form
on the project. The set-up task does not count: the button reads **Create task** once the
project has any other task.

Both paths clone the repository into a bare-like layout, check main out into `_main`, and
create the first worktree, **alpha**:

```
~/Projects/repo/
├── .git/            # shared bare git directory
├── .mael            # marker: this is a maelstrom project
├── _main/           # the worktree that holds the main branch
└── repo-alpha/      # first feature worktree
```

Main stays in `_main` so every NATO (North Atlantic Treaty Organization) phonetic-alphabet
worktree is free for feature work. See
[worktrees.md](worktrees.md).

Add `.env`, `.claude/CLAUDE.local.md` and `AGENTS.md` to the repository's `.gitignore`. Maelstrom
generates all three per worktree. `mael project create` does this for you.

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

Maelstrom creates the branch, allocates ports, and writes a `.env`. On a new project
**alpha** is still free, so it is recycled rather than a new worktree being made:

```
Worktree recycled at: ~/Projects/repo/repo-alpha
Regenerated .env for repo/alpha.
App: http://localhost:3000
```

```bash
$ cat ~/Projects/repo/repo-alpha/.env
# Maelstrom port allocations
FRONTEND_PORT=3000
PORT_BASE=300
WORKTREE=alpha
WORKTREE_NUM=0
# End Maelstrom port allocations
```

Once alpha is busy, the next `mael add` creates **bravo**, and so on.

By default `mael add` also runs a Claude session in your terminal, in the new worktree. Pass
`--daemon` to start it on the agent daemon instead, or `--open` to open your editor.

## 6. Start the services

```bash
cd ~/Projects/repo/repo-alpha
mael env start
```

This runs `install_cmd`, then starts every service and reports where the app is:

```
APP RUNNING AT: *3000 • UPTIME: 6s

SERVICE  PID    STATUS   LOG
web      5442   running  ~/.maelstrom/logs/repo/alpha/web.log
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

This creates a task and starts a Claude session on the agent daemon. The orchestrator UI shows
it, and `mael agent attach <id>` opens it in your terminal. New tasks default to **plan mode**,
so the session plans with you before it writes code.

For an unattended session that implements straight away:

```bash
mael task add "Bump the pinned pyright" --mode auto --run
```

To run in your current shell instead of on the agent daemon:

```bash
mael task add "Quick fix" --run --cli
```

Watch what is running:

```bash
mael list             # what every worktree is doing — see listing.md
mael task list        # tasks you can start now
mael session list     # live Claude sessions
```

`mael list` and `mael task list` read the orchestrator server, which the `orchestrator` host
service runs. If it is down, run `mael self restart orchestrator`.

## 8. Close the worktree

When the work has merged:

```bash
mael close
```

Close syncs against the branch's base, checks the worktree is clean, and resets it to main. It
**keeps** the folder, the NATO name and the port allocation, so the next `mael add`
recycles the slot.

Close deliberately refuses to run when work would be lost:

```
Error: Worktree 'bravo' has uncommitted changes.
Error: Worktree 'bravo' has commits not merged to main.
```

Commit and merge first. To free the slot with work still in flight, use
`mael close --force`. That flag discards nothing: uncommitted changes are committed as
`wip: uncommitted changes`, the branch and its PR survive, and maelstrom creates a
"Reopen" task so the work is not forgotten.

`mael close --discard` removes tracked, staged, and non-ignored untracked files. It keeps
branch commits and ignored files such as `.env`.

To delete a worktree outright, use `mael remove`. See [worktrees.md](worktrees.md) for the
difference.

## Next

- [Concepts](concepts.md) — what the pieces are and why.
- [The multi-agent workflow](multi-agent-workflow.md) — running several agents at once.
- [Integrations](integrations.md) — connecting Linear, Sentry, GitHub and Slack.
- [Troubleshooting](troubleshooting.md) — when something does not work.
