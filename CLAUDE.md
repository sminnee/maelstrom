# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running Commands

Use `uv run` to execute commands in the project's virtual environment:

```bash
uv run pytest                      # Run all tests
uv run pytest tests/test_ports.py  # Run a single test file
uv run pytest -k "test_name"       # Run tests matching a pattern
uv run pytest --cov=maelstrom      # Run with coverage
uv run python -m maelstrom         # Run the module
```

# Maelstrom Workflow

**Always load the `/mael` skill before beginning any work.** It provides essential instructions for git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for `/plan-task`, `/continue-task`, and `/review-branch` commands.

(maelstrom instructions end)

## Architecture

Maelstrom manages parallel development environments using git worktrees. It uses a bare-like repository structure where worktrees are named using NATO phonetic alphabet (alpha, bravo, charlie, etc.).

### Module Structure

- **cli.py** - Argparse-based CLI entry point (`mael` command). Commands use `resolve_context()` to determine project/worktree from args or cwd.
- **context.py** - Resolves project/worktree context from CLI args or current directory. Handles `project.worktree` argument parsing and cwd detection.
- **config.py** - Loads `.maelstrom.yaml` project configuration (port_names, start_cmd, install_cmd).
- **worktree.py** - Core git worktree operations: create, remove, list. Handles bare repo setup, branch detection, and .env file generation.
- **ports.py** - Port allocation using PORT_BASE scheme. Checks socket availability and generates `*_PORT` environment variables.

### Key Concepts

- **Projects** live in `~/Projects/<name>/` (configurable via `~/.maelstrom.yaml`)
- **Worktrees** are subdirectories named alpha, bravo, etc. (not branch names)
- **PORT_BASE** is a 3-digit number (100-999); each service port = PORT_BASE * 10 + index
- When creating worktrees, existing `.env` from project root is merged with generated port vars, with `$VAR` substitution

# Maelstrom Workflow

**Always load the `/mael` skill before beginning any work.** It provides essential instructions for git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for `/plan-task`, `/continue-task`, and `/review-branch` commands.

(maelstrom instructions end)
