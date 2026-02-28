# Maelstrom

Parallel development environment manager using git worktrees. Run multiple isolated development environments simultaneously, each with its own branch, port allocations, and running services. Integrates with GitHub, Linear, and Sentry.

## Installation

```bash
# Using uv (recommended)
uv tool install git+https://github.com/sminnee/maelstrom.git

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

# Fallback start command if no Procfile is present
start_cmd: "npm run dev"

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

## Environment Management

Maelstrom manages service processes for each worktree.

### Procfile

Define services in a `Procfile` in your repository root:

```
web: npm run dev
worker: python manage.py worker
redis: redis-server --port $REDIS_PORT
```

Services with names ending in `-shared` are shared across worktrees in the same project. If no Procfile is present, maelstrom falls back to `start_cmd` from `.maelstrom.yaml`.

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
| `mael review squash` | Squash all `fixup!` commits via autosquash rebase |
| `mael review status` | Show pending fixup commits and their targets |

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
| `/plan-task ISSUE_ID` | Plan implementation for a Linear task (requires plan mode) |
| `/continue-task ISSUE_ID` | Read plan from Linear and begin implementation |
| `/create-subtasks ISSUE_ID` | Break a task into subtasks (requires plan mode) |
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

## Desktop UI & Agent-CLI (Incomplete)

A Tauri desktop app (`app/`) and Node.js agent-CLI bridge (`agent-cli/`) are in development. These will provide a graphical interface for managing sessions and interacting with Claude Code via a Unix domain socket protocol. They are not yet functional.

## License

MIT
