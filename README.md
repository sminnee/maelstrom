# Maelstrom: multi-agent development with cmux and Claude Code

One agent on one branch is easy. Several at once is not. Each agent needs its own working
directory, its own database and ports, and somewhere you can watch it. Something has to track
what each one is doing, and in what order.

Maelstrom automates that. It uses cmux to manage workspaces, git worktrees to isolate code,
and Claude Code as its agent. Integrations with Linear, Sentry and GitHub keep the workflow
streamlined. Maelstrom adds two things of its own. A task notebook tracks what each agent is
doing and in what order. A dev environment manager gives each worktree isolated services and
ports. It is a highly opinionated Swiss-army knife.

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
# Clone a repository into maelstrom's layout. main goes in "_main", and this
# creates the "alpha" worktree for work.
mael add-project https://github.com/org/repo.git
cd ~/Projects/repo/repo-alpha

# Add a worktree for a branch. Maelstrom allocates ports and writes .env.
# On a new project alpha is still free, so it is recycled.
mael add feature/avatar-upload
#   Worktree recycled at: ~/Projects/repo/repo-alpha
#   App: http://localhost:3000

# Start the project's services in that worktree.
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

Each component earns its place by the role it plays in the loop.

| Component | Role |
|---|---|
| [cmux](docs/guide/cmux-workspaces.md) | Manages workspaces — where sessions run and where you watch them |
| [git worktrees](docs/guide/worktrees.md) | Isolate code — a branch and a working directory per unit of work |
| Claude Code | The agent that does the work |
| [Task notebook](docs/guide/tasks.md) | Detailed task management — what each agent is doing, in what order |
| [Wiki](docs/guide/concepts.md#the-wiki--cross-project-patterns) | Design patterns that apply to more than one project |
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
| [Reading `mael list`](docs/guide/listing.md) | What each column means, and where each fact comes from |
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

- [Domain glossary](CONTEXT.md) — the domain terms, and the words to avoid for each.
- [Architecture patterns](docs/dev/architecture-patterns.md) — the storage / model / CLI layering.
- [The task domain model](docs/dev/tasks.md) — `parent` vs `follows`, ids, session discovery.
- [Scheduled tasks](docs/dev/scheduled-tasks.md) — launchd firing mechanics.
- [cmux control](docs/dev/cmux.md) — how maelstrom drives the cmux socket.

## Development

```bash
uv sync --all-extras           # install dev dependencies
uv run pytest -m 'not slow'    # tests, skipping slow e2e
uv run pytest                  # everything
uv run pytest --cov=maelstrom  # with coverage
bin/lint                       # pyright type checking
```

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup,
the gates to run before you commit, and the commit and changelog conventions.

## Release

The package is published as `sminnee-maelstrom`. A release is one command, run by hand from a
clean checkout of the branch you want to release:

```bash
./bin/publish --dry-run   # rehearse: gates and build, then revert the bump
./bin/publish             # patch bump (0.1.1 → 0.1.2)
./bin/publish --minor
./bin/publish --major
```

`bin/publish` does the whole release, in this order: rebase onto `origin/main`, run the three
gates CI runs, write the new version to `pyproject.toml`, `src/maelstrom/__init__.py` and
`uv.lock`, build, retitle the changelog's `Unreleased` section to the new version, commit,
upload to PyPI, then tag `vX.Y.Z` and push the commit and the tag.

The order is deliberate. The rebase runs first so the commit that gets tagged is already in its
final form — a tag written before a rebase ends up on a commit the rebase then rewrites. The
commit is written before the upload so a published version always has a commit behind it; if the
upload fails, the commit is rolled back and the version is not spent. The tag comes last, and
records a release that has already happened rather than triggering one.

Past a successful upload there is no rollback, because PyPI will not accept a re-upload of a
version it has already seen. If the push fails after that point, the script says so and prints
the commands to finish by hand.

It requires:

- `UV_PUBLISH_TOKEN` in the environment or in `.env` (gitignored).
- A clean working tree — the release commit stages only the version files and the changelog, and
  the failed-upload rollback is a hard reset.
- A non-empty `## [Unreleased]` section in `CHANGELOG.md`.
- `mael` on `PATH`, for the rebase.

`--dry-run` runs the gates and the build, then reverts the bump. It skips the rebase, the token
check, the commit, the upload and the tag, so it needs no PyPI credentials.

Afterwards, `mael linear release` promotes the Linear issues sitting in "Unreleased" to "Done".
It touches no versions or tags — it is only the Linear-side bookkeeping for a release that has
already shipped.

## License

[MIT](LICENSE)
