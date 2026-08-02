# Maelstrom

Parallel development environment manager using git worktrees. Run multiple isolated development environments simultaneously, each with its own branch, port allocations, and running services. Integrates with GitHub, Linear, and Sentry.

## Installation

```bash
# Install from PyPI (recommended)
uv tool install sminnee-maelstrom

# Or run without installing
uvx sminnee-maelstrom <command>

# Or install locally for development
git clone https://github.com/sminnee/maelstrom.git
cd maelstrom
uv sync
uv tool install --editable .
```

## Quick Start

```bash
# Clone a project and initialize it for maelstrom
mael add-project git@github.com:org/repo.git

# Create a new worktree for a feature branch
mael add myproject feature/avatar-upload

# Or from within any worktree directory, the project is auto-detected
mael add feature/avatar-upload

# List all worktrees in a project
mael list myproject

# Start services
mael env start

# Open a worktree in your editor
mael open myproject.bravo

# Close a worktree when done (syncs, resets to main, ready for reuse)
mael close
```

### Targeting Worktrees

Most commands accept an optional target argument in the form `project.worktree`:

```bash
mael list myproject           # List worktrees in myproject
mael open myproject.bravo     # Open the bravo worktree
mael env start myproject.c    # Start services in charlie (shortcode)
```

Shortcodes map single letters to NATO names: `a` → alpha, `b` → bravo, `c` → charlie, etc. If you're inside a worktree directory, the project and worktree are auto-detected.

## Worktree Management

| Command | Description |
|---------|-------------|
| `mael add [PROJECT] BRANCH` | Create a new worktree (or recycle a closed one). Options: `--no-open`, `--no-recycle` |
| `mael remove TARGET` | Remove one or more worktrees. `-f` to skip dirty-file confirmation |
| `mael list [PROJECT]` | List worktrees with branch, dirty files, local commits, PR info, app URL |
| `mael list-all` | List worktrees across all projects. `--json` for machine-readable output |
| `mael open [TARGET]` | Open a worktree in the configured editor |
| `mael close [TARGET]` | Sync, verify clean, reset to main. Preserves folder and ports for recycling |
| `mael sync [TARGET]` | Rebase worktree against origin/main and push |
| `mael sync-all [PROJECT]` | Sync all worktrees in a project |
| `mael tidy-branches [PROJECT]` | Rebase feature branches, delete merged ones, force-push unmerged |
| `mael add-project GIT_URL` | Clone a repo and initialize for maelstrom. `--projects-dir` to override location |

### Worktree Naming

Worktrees use NATO phonetic alphabet names: alpha, bravo, charlie, delta, echo, foxtrot, golf, hotel, india, juliet, kilo, lima, mike, november, oscar, papa, quebec, romeo, sierra, tango, uniform, victor, whiskey, xray, yankee, zulu.

When a worktree is closed with `mael close`, it is reset to `origin/main` but the folder, NATO name, and port allocation are preserved. The next `mael add` will recycle a closed worktree rather than creating a new one.

### Repository Structure

```
~/Projects/myproject/
├── .git/                          # Shared bare git directory
├── myproject-alpha/               # Worktree (main branch)
│   ├── .maelstrom.yaml            # Project config (checked into repo)
│   ├── .env                       # Generated port assignments (gitignored)
│   ├── Procfile                   # Service definitions (checked into repo)
│   └── ...
├── myproject-bravo/               # Feature worktree
│   ├── .env                       # Different PORT_BASE
│   └── ...
└── myproject-charlie/             # Another feature worktree
    └── ...
```

## Configuration

### Project Configuration (`.maelstrom.yaml`)

Create this file in your repository root:

```yaml
# Port names — each gets a _PORT environment variable
port_names:
  - FRONTEND
  - SERVER
  - DB
  - REDIS

# Shared port names — allocated once per project, shared across worktrees
shared_port_names:
  - SHARED_REDIS

# Command to install dependencies (run on worktree creation and env start)
install_cmd: "uv sync"

# Fallback start command if no `services:` block or Procfile is present
start_cmd: "npm run dev"

# Preferred: structured service definitions (see "Environment Management" below).
# When present, `services:` supersedes `port_names` / `shared_port_names` / the
# Procfile / start_cmd.
# services:
#   frontend: { ports: [FRONTEND], command: "npm run dev" }
#   db-shared: { shared: true, engine: docker, image: postgres:16, ports: [DB] }

# Linear integration
linear:
  team_id: "your-team-uuid"
  workspace_labels: [alpha, bravo, charlie]
  product_label: "YourProduct"  # Auto-assigned to tasks; used by `mael linear release`

# Sentry integration
sentry_org: "your-org"
sentry_project: "your-project-slug"
```

### Global Configuration (`~/.maelstrom/config.yaml`)

```yaml
projects_dir: ~/Projects       # Base directory for projects
open_command: "cursor"         # Editor command (default: "code")

linear:
  api_key: "lin_api_xxx"       # Linear API key
```

## Port Allocation

Each worktree is assigned a unique `PORT_BASE` in the range 300–999. Service ports are calculated as `PORT_BASE * 10 + index`.

For example, with `PORT_BASE=300` and port names `[FRONTEND, SERVER, DB]`:

```bash
PORT_BASE=300
FRONTEND_PORT=3000
SERVER_PORT=3001
DB_PORT=3002
```

Port allocations are persisted in `~/.maelstrom/port_allocations.json` and checked for socket availability when assigned. The first port (`PORT_BASE * 10`) is used as the app URL.

> **Ceiling:** because ports are `PORT_BASE * 10 + index`, there are only **10 port slots per base** (index 0–9). A worktree needing more than 10 named ports in one scope would collide across bases. Structured `services:` allocate the union of their non-shared named ports off the local base and shared ports off a separate shared base.

## Environment Management

Maelstrom manages service processes for each worktree.

### Services (`services:` in `.maelstrom.yaml`)

The preferred way to define services is a structured `services:` map in
`.maelstrom.yaml`. Each service either runs a shell `command` or, when it
declares an `engine`, is a container maelstrom owns end-to-end (it synthesises
the `rm -f … ; run …` boilerplate for you). Each service lists the **named
ports** it owns; maelstrom allocates them and exposes `${NAME_PORT}` for use in
commands and container port-mappings.

```yaml
services:
  frontend:
    ports: [FRONTEND, FRONTEND_HMR]      # -> ${FRONTEND_PORT}, ${FRONTEND_HMR_PORT}
    dir: frontend
    command: env PORT=${FRONTEND_PORT} node server-dev.ts

  server:
    ports: [SERVER]
    command: env PORT=${SERVER_PORT} uv run serve-dev

  worker:
    command: uv run serve-dev worker       # no ports, no dir

  db-shared:
    shared: true                           # shared across worktrees in this project
    engine: apple-container                # or "docker"
    image: pgvector/pgvector:pg16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]           # host:container mappings
    volume: /var/lib/postgresql/data       # named volume derived from container name
    host_var: DB_HOST                       # apple-container: receives the polled VM IP
    env:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}

  redis-shared:
    shared: true
    engine: apple-container
    image: redis:8.4-alpine
    ports: [REDIS]
    publish: ["${REDIS_PORT}:6379"]
    volume: /data
```

**Service type** is inferred: an `engine:` (docker / apple-container) makes it a
container; otherwise it is a shell `command` service.

**Engines.** `docker` and `apple-container` are both supported. Apple `container`
runs each container as its own VM on a private `192.168.64.x` network. For those,
set `host_var:` (e.g. `DB_HOST`) — maelstrom polls `container inspect` for the
VM's IP at start time and injects it into the process environment of sibling
services (so a command service's `env:` can reference `${DB_HOST}`). The IP flows
into the spawn environment only, never into `.env`. If the IP never resolves
(~10s poll), the start fails loudly rather than leaving services pointed at the
wrong host. `host_var` is only meaningful for shared apple-container services.

**Shared services.** `shared: true` marks a service as shared across worktrees in
the same project (started once, subscribed to by later worktrees). Late
subscribers reuse the already-discovered `host_var` IP rather than re-inspecting.

### Procfile (legacy fallback)

If no `services:` block is present, maelstrom falls back to a `Procfile` in your
repository root:

```
web: npm run dev
worker: python manage.py worker
redis: redis-server --port $REDIS_PORT
```

On the Procfile path, services with names ending in `-shared` are shared across
worktrees. If neither `services:` nor a Procfile is present, maelstrom falls back
to `start_cmd` from `.maelstrom.yaml`. Precedence is **`services:` → Procfile →
`start_cmd`**; migrate projects one at a time — there is no flag day.

### Commands

| Command | Description |
|---------|-------------|
| `mael env start [TARGET]` | Run install command, then start all services. `--skip-install` to skip |
| `mael env stop [TARGET]` | Stop all services (SIGTERM, then SIGKILL after 10s) |
| `mael env status [TARGET]` | Show service PIDs, status, and log file paths |
| `mael env logs [TARGET] [SERVICE]` | View service logs. `-f` to follow, `-n NUM` for line count |
| `mael env list [PROJECT]` | List running environments for a project |
| `mael env list-all` | List all running environments across all projects |
| `mael env stop-all` | Stop all environments globally |

## GitHub Integration

| Command | Description |
|---------|-------------|
| `mael gh create-pr [ISSUE_ID]` | Create or update a pull request. `--draft` for draft PRs, `--target` for worktree |
| `mael gh read-pr [TARGET]` | Show PR status, unresolved comments, and CI check results |
| `mael gh show-code [TARGET]` | Show commits and diffs. `--committed` or `--uncommitted` |
| `mael gh check-log RUN_ID` | View GitHub Actions logs. `--failed-only` for failures |
| `mael gh download-artifact RUN_ID NAME` | Download a workflow artifact. `-o DIR` for output |

## Linear Integration

| Command | Description |
|---------|-------------|
| `mael linear list-tasks` | List tasks in the current cycle. `--status STATUS` to filter |
| `mael linear read-task ISSUE_ID` | Show task details, subtasks, comments |
| `mael linear start-task ISSUE_ID` | Mark task as In Progress, add worktree label |
| `mael linear complete-task ISSUE_ID` | Mark task as Done (subtask) or Unreleased (parent) |
| `mael linear create-subtask PARENT TITLE [DESC]` | Create a subtask linked to a parent |
| `mael linear write-plan ISSUE_ID FILE` | Write a plan file to the task description |
| `mael linear read-plan ISSUE_ID` | Extract and display the plan from a task |
| `mael linear release` | Promote all "Unreleased" tasks with product label to "Done" |

## Sentry Integration

| Command | Description |
|---------|-------------|
| `mael sentry list-issues` | List unresolved issues. `--env ENV` (default: prod) |
| `mael sentry get-issue ISSUE_ID` | Show exception details, tags, and stacktraces |

## Code Review

| Command | Description |
|---------|-------------|
| `mael sync --squash` | Autosquash all `fixup!` commits while rebasing onto `origin/main` |

## Claude Code Integration

Maelstrom includes skills and commands for Claude Code:

```bash
# Install skills, hooks, and commands into ~/.claude/
mael install

# Update maelstrom from git
mael self-update
```

Once installed, these skills are available in Claude Code:

| Skill | Description |
|-------|-------------|
| `/mael` | Load git workflow, Linear, Sentry, and env management instructions |
| `mael linear plan ISSUE_ID [--run]` | Seed a notebook planning task from a Linear brief (launches `plan-task`) |
| `mael task next --run` | Launch the next ready task in the notebook chain |
| `/review-branch` | Review code changes before creating a PR (requires plan mode) |

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=maelstrom
```

## Notes for Contributors

### Publishing to PyPI

The package is published to PyPI as `sminnee-maelstrom`. To publish a new version:

```bash
# Set your PyPI token (or add UV_PUBLISH_TOKEN to .env)
export UV_PUBLISH_TOKEN=pypi-...

# Publish with a patch version bump (0.1.0 → 0.1.1)
./bin/publish

# Or bump minor/major
./bin/publish --minor
./bin/publish --major
```

This bumps the version, builds, publishes to PyPI, commits the version change, and tags it. After publishing, run `git push && git push --tags`.

## License

MIT
