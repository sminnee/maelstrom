# Maelstrom: multi-agent development with Claude Code

One agent on one branch is easy. Several at once is not. Each agent needs its own working
directory, its own database and ports, and somewhere you can watch it. Something has to track
what each one is doing, and in what order.

Maelstrom automates that. It uses git worktrees to isolate code, Claude Code as its agent, and
an orchestrator UI to show every agent. Integrations with Linear, Sentry and GitHub keep the workflow
streamlined. Maelstrom adds two things of its own. A task notebook tracks what each agent is
doing and in what order. A dev environment manager gives each worktree isolated services and
ports. It is a highly opinionated Swiss-army knife.

## Installation

A release is a release bundle, `maelstrom.tar.gz`, on a
[GitHub Release](https://github.com/sminnee/maelstrom/releases). One installer installs and
upgrades it, on macOS and Ubuntu Server 24.04. It needs only curl and tar, and installs uv
itself:

```bash
curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh
```

It puts maelstrom in `~/.maelstrom/install/`, apart from any checkout. It asks where your
projects live, when that is not yet set. Each install runs `mael self check-host --quiet`, which offers the missing host tools and
the sign-ins. Then it runs `mael self setup`. Setup writes `~/.local/bin/mael`, installs the Claude
Code skills and hooks, and starts the orchestrator and the agent daemon as host services. `mael self update` installs
the next release. See [Getting started](docs/guide/getting-started.md).

To install maelstrom from its main branch, with no release, run:

```bash
curl -LsSf https://raw.githubusercontent.com/sminnee/maelstrom/main/bin/install-main.sh | sh
```

`mael self update` then stays on main. See
[Install from main](docs/guide/getting-started.md#install-from-main).

For local development:

```bash
git clone https://github.com/sminnee/maelstrom.git
cd maelstrom
uv sync --extra dev
uv tool install "tangier>=0.3.5"   # for the gates, the gate watch and service versions
uv run mael --help
```

### Prerequisites

`mael self check-host` checks each tool and sign-in, and offers to install or fix each one. The
installer runs it on each install; run it again at any time. See
[Getting started](docs/guide/getting-started.md#prerequisites) for what each tool is for.
[cmux](https://github.com/sminnee/cmux) is optional, and macOS only: install it from its README.

## Quick start

```bash
# Clone a repository into maelstrom's layout. main goes in "_main", and this
# creates the "alpha" worktree for work.
mael project add https://github.com/org/repo.git
cd ~/Projects/repo/repo-alpha

# Add a worktree for a branch. Maelstrom allocates ports and writes .env.
# On a new project alpha is still free, so it is recycled.
mael add feature/avatar-upload
#   Worktree recycled at: ~/Projects/repo/repo-alpha
#   App: http://localhost:3000

# Start the project's services in that worktree.
mael env start

# Launch an agent session. It runs on the agent daemon, shows in the
# orchestrator UI, starts in plan mode, and plans the work with you first.
mael task add "Add avatar upload" --run

# See what is running.
mael list
mael task list

# When the work has merged: reset the worktree, keeping its name and ports.
mael close
```

Full walkthrough: [Getting started](docs/guide/getting-started.md).

## The pieces

Each component earns its place by the role it plays in the loop.

| Component | Role |
|---|---|
| [Orchestrator UI](docs/guide/concepts.md#the-orchestrator-ui-and-server) | Shows every agent, and where you watch and answer them |
| [git worktrees](docs/guide/worktrees.md) | Isolate code — a branch and a working directory per unit of work |
| Claude Code | The agent that does the work |
| [Task notebook](docs/guide/tasks.md) | Detailed task management — what each agent is doing, in what order |
| [Wiki](docs/guide/concepts.md#the-wiki--cross-project-patterns) | Design patterns that apply to more than one project |
| [Dev environments](docs/guide/dev-environments.md) | Isolated services and ports per worktree |
| [Linear / Sentry / GitHub](docs/guide/integrations.md) | Streamline the workflow |

`mael task add --run` starts an agent on the agent daemon, and the orchestrator UI shows it.
`mael agent attach <id>` opens it in your terminal. `mael add` and `mael task run` run the model
CLI in your shell by default.

## Documentation

### Guides

| Page | What it covers |
|---|---|
| [Concepts](docs/guide/concepts.md) | What maelstrom is for, and how the pieces fit together |
| [Getting started](docs/guide/getting-started.md) | Install → first worktree → first agent session |
| [The multi-agent workflow](docs/guide/multi-agent-workflow.md) | The core loop: plan → chain → parallel sessions → PR |
| [Worktrees](docs/guide/worktrees.md) | Lifecycle, naming, recycling, close vs remove |
| [Reading `mael list`](docs/guide/listing.md) | What each column means, and where each fact comes from |
| [Dev environments](docs/guide/dev-environments.md) | `services:`, engines, ports, `.env` |
| [Tasks](docs/guide/tasks.md) | Tasks, `parent`/`follows`, `load-many`, chains |
| [Planning](docs/guide/planning.md) | Linear plan → plan-task → chain → PR |
| [Pull requests](docs/guide/pull-requests.md) | The finishing sequence, code review, watching CI |
| [Integrations](docs/guide/integrations.md) | Linear, Sentry, Slack, UptimeRobot, GitHub |
| [Scheduled work](docs/guide/scheduled-work.md) | Templates, cron, build triggers, the scheduler |
| [Troubleshooting](docs/guide/troubleshooting.md) | `project check`, `reconcile`, `session list`, common failures |

### Reference

| Page | What it covers |
|---|---|
| [CLI](docs/reference/cli.md) | Every command and flag |
| [Configuration](docs/reference/configuration.md) | Both config files, every key |
| [Environment variables](docs/reference/environment.md) | Everything read and set |

### For contributors

- [Domain glossary](CONTEXT.md) — the domain terms, and the words to avoid for each.
- [Architecture patterns](docs/dev/architecture-patterns.md) — the storage / model / CLI layering.
- [The task domain model](docs/dev/tasks.md) — `parent` vs `follows`, ids, session discovery.
- [Scheduled tasks](docs/dev/scheduled-tasks.md) — launchd and systemd firing mechanics.
- [cmux control](docs/dev/cmux.md) — how maelstrom drives the cmux socket.
- [CI gates](docs/dev/ci.md) — the tangier gates, and how CI reuses a local pass.

## Development

```bash
uv sync --all-extras           # install dev dependencies
uv run pytest -m 'not e2e'     # tests, skipping e2e
uv run pytest                  # everything
uv run pytest --cov=mael_cli --cov=mael_domain  # with coverage
bin/lint                       # ruff lint, ruff format check, pyright, import contracts, vulture
```

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup,
the gates to run before you commit, and the commit and changelog conventions.

## Release

A release is a [release bundle](CONTEXT.md) on a GitHub Release. See
[the ADR](docs/adr/0001-maelstrom-installs-as-a-release-bundle.md). To make one, run this from
a clean checkout of the branch you want to release:

```bash
./bin/publish --dry-run   # rehearse: gates and the bundle build, then revert the bump
./bin/publish             # patch bump (0.1.1 → 0.1.2)
./bin/publish --minor
./bin/publish --major
```

`bin/publish` runs these steps in order:

1. Rebase onto `origin/main`.
2. Run the three gates CI runs.
3. Write the new version to every `pyproject.toml`, each member's `__init__.py`, and `uv.lock`.
4. Retitle the changelog's `Unreleased` section to the new version, and commit.
5. Tag `vX.Y.Z`, and push the commit and the tag.

The rebase runs first, so the tagged commit is already in its final form. A tag written before a
rebase ends up on a commit the rebase then rewrites.

The tag push runs `.github/workflows/release.yml`, which builds and publishes the bundle. See
[The release workflow](docs/dev/ci.md#the-release-workflow).

`bin/publish` requires:

- A clean working tree. The release commit stages only the version files and the changelog.
- A non-empty `## [Unreleased]` section in `CHANGELOG.md`.
- `mael` on `PATH`, for the rebase.

If the push fails, the script prints the commands to finish by hand.

`--dry-run` runs the gates and builds the bundle into `dist/`, then reverts the bump. It skips
the rebase, the commit and the tag.

Afterwards, `mael linear release` promotes the Linear issues sitting in "Unreleased" to "Done".
It touches no versions or tags — it is only the Linear-side bookkeeping for a release that has
already shipped.

## License

[MIT](LICENSE)
