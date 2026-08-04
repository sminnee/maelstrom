# Maelstrom: multi-agent development with cmux and Claude Code

Maelstrom is an orchestration layer for multi-agent development. It uses cmux to manage
workspaces, git worktrees to isolate code, and Claude Code as its agent. Integrations with
Linear, Sentry and GitHub keep the workflow streamlined. It also has its own task management
system for detailed task management, and dev environment management to create suitably
isolated environments for each worktree. It is a highly opinionated Swiss-army knife.

One agent on one branch is easy. Several at once is not: each needs its own checkout, its
own database and ports, and somewhere you can watch it — and something has to track what
each one is doing, and in what order. Maelstrom automates that.

## Installation

```bash
# From PyPI (recommended)
uv tool install sminnee-maelstrom

# Or run without installing
uvx sminnee-maelstrom <command>

# Or for local development
git clone https://github.com/sminnee/maelstrom.git
cd maelstrom
uv sync
uv tool install --editable .
```

Then install the Claude Code skills and hooks:

```bash
mael install
```

### Prerequisites

[uv](https://docs.astral.sh/uv/), git, [Claude Code](https://claude.com/claude-code),
[cmux](https://github.com/sminnee/cmux), [GitHub CLI](https://cli.github.com/) and
[bun](https://bun.sh/). Only `uv` and git are needed to create worktrees; cmux and Claude
Code are needed to launch agent sessions. See
[Getting started](docs/guide/getting-started.md#prerequisites) for what each one is for and
how to install it.

## Quick start

```bash
# Clone a repository into maelstrom's layout. This creates the "alpha" worktree.
mael add-project git@github.com:org/repo.git
cd ~/Projects/repo/repo-alpha

# Add a worktree for a branch. Maelstrom allocates ports and writes .env.
mael add feature/avatar-upload
#   → repo/bravo (created)
#   App: http://localhost:3000

# Start the project's services in that worktree.
cd ~/Projects/repo/repo-bravo
mael env start

# Launch an agent session. It opens in its own cmux workspace,
# in plan mode, and plans the work with you first.
mael task add "Add avatar upload" --run

# See what is running.
mael list
mael task list

# When the work has merged: reset the worktree, keeping its name and ports.
mael close
```

Full walkthrough: [Getting started](docs/guide/getting-started.md).

## The pieces

Each component earns its place by the role it plays in the workflow.

| Component | Role |
|---|---|
| [cmux](docs/guide/cmux-workspaces.md) | Manages workspaces — where sessions run and where you watch them |
| [git worktrees](docs/guide/worktrees.md) | Isolate code — a branch and a checkout per unit of work |
| Claude Code | The agent that does the work |
| [Task notebook](docs/guide/tasks.md) | Detailed task management — what each agent is doing, in what order |
| [Dev environments](docs/guide/dev-environments.md) | Isolated services and ports per worktree |
| [Linear / Sentry / GitHub](docs/guide/integrations.md) | Streamline the workflow |

Sessions run in cmux workspaces. Maelstrom drives the cmux socket, starts cmux if it is
down, and fails rather than running an agent somewhere you cannot find it. `--here` is the
deliberate local-shell escape hatch.

## Documentation

### Guides

| Page | What it covers |
|---|---|
| [Concepts](docs/guide/concepts.md) | What maelstrom is for, and how the pieces fit together |
| [Getting started](docs/guide/getting-started.md) | Install → first worktree → first agent session |
| [The multi-agent workflow](docs/guide/multi-agent-workflow.md) | The core loop: plan → chain → parallel sessions → PR |
| [cmux workspaces](docs/guide/cmux-workspaces.md) | Workspaces, the three-pane layout, `--here` |
| [Worktrees](docs/guide/worktrees.md) | Lifecycle, naming, recycling, close vs remove |
| [Dev environments](docs/guide/dev-environments.md) | `services:`, engines, ports, `.env` |
| [Tasks](docs/guide/tasks.md) | Tasks, `parent`/`follows`, `load-many`, chains |
| [Planning](docs/guide/planning.md) | Linear plan → plan-task → chain → PR |
| [Pull requests](docs/guide/pull-requests.md) | The finishing sequence, code review, watching CI |
| [Integrations](docs/guide/integrations.md) | Linear, Sentry, Slack, UptimeRobot, GitHub |
| [Scheduled work](docs/guide/scheduled-work.md) | Templates, cron, launchd |
| [Troubleshooting](docs/guide/troubleshooting.md) | `doctor`, `reconcile`, `session list`, common failures |

### Reference

| Page | What it covers |
|---|---|
| [CLI](docs/reference/cli.md) | Every command and flag |
| [Configuration](docs/reference/configuration.md) | Both config files, every key |
| [Environment variables](docs/reference/environment.md) | Everything read and set |

### For contributors

- [Architecture patterns](docs/dev/architecture-patterns.md) — the storage / model / CLI layering.
- [The task domain model](docs/dev/tasks.md) — `parent` vs `follows`, ids, session discovery.
- [Scheduled tasks](docs/dev/scheduled-tasks.md) — launchd firing mechanics.

## Development

```bash
uv sync --all-extras           # install dev dependencies
uv run pytest -m 'not slow'    # tests, skipping slow e2e
uv run pytest                  # everything
uv run pytest --cov=maelstrom  # with coverage
bin/lint                       # pyright type checking
```

## Contributing

Issues and pull requests are welcome. Please run `bin/lint` and the test suite before
opening one, and keep documentation in step with behaviour changes.

### Publishing to PyPI

The package is published as `sminnee-maelstrom`:

```bash
export UV_PUBLISH_TOKEN=pypi-...   # or add it to .env
./bin/publish                      # patch bump (0.1.0 → 0.1.1)
./bin/publish --minor
./bin/publish --major
```

This bumps the version, builds, publishes, commits the version change and tags it.
Afterwards run `git push && git push --tags`.

## License

[MIT](LICENSE)
