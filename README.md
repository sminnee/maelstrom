# Maelstrom

Parallel development environment manager using git worktrees. Run multiple Claude Code agents, each in an isolated git worktree with its own port allocations.

## Installation

```bash
# Using uv (recommended)
uv tool install git+https://github.com/sminnee/maelstrom.git

# Or install locally for development
git checkout git+https://github.com/sminnee/maelstrom.git
cd maelstrom
uv sync
uv tool install --editable . 
```

## Quick Start

```bash
# Initialize a repository for maelstrom (migrates to bare structure)
mael init /path/to/project

# Create a new worktree for a feature branch
mael create-worktree /path/to/project feature/avatar-upload

# List all worktrees
mael list /path/to/project

# Remove a worktree when done
mael rm-worktree /path/to/project feature/avatar-upload
```

## Configuration

Create a `.maelstrom.yaml` file in your repository:

```yaml
# Port names - each gets a _PORT environment variable
port_names:
  - FRONTEND
  - SERVER
  - DB
  - REDIS
  - FETCHER_MCP

# Command to start services
start_cmd: ult
```

Each worktree gets a `.env` file with port assignments:

```bash
PORT_BASE=100
FRONTEND_PORT=1000
SERVER_PORT=1001
DB_PORT=1002
REDIS_PORT=1003
FETCHER_MCP_PORT=1004
```

## Port Numbering

- PORT_BASE is a 3-digit number (100-999)
- Each port name gets PORT_BASE * 10 + index
- Example: PORT_BASE=100 → ports 1000-1009

## Repository Structure

Maelstrom uses a bare-like repository structure:

```
project/
├── .git/                     # Git directory
├── alpha/                    # Default worktree (main branch)
│   ├── .maelstrom.yaml       # Config (checked into repo)
│   ├── .env                  # Port assignments (gitignored)
│   └── ...
├── feature-avatar-upload/    # Feature worktree
│   ├── .env                  # Different PORT_BASE
│   └── ...
└── fix-login-bug/            # Another worktree
    └── ...
```

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=maelstrom
```

## License

MIT
