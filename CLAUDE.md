# CLAUDE.md

@.claude/CLAUDE.local.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running Commands

Use `uv run` to execute commands in the project's virtual environment:

```bash
uv run pytest -m 'not slow'        # Skip slow e2e tests (recommended for dev)
uv run pytest                      # Run all tests including slow ones
uv run pytest tests/test_ports.py  # Run a single test file
uv run pytest -k "test_name"       # Run tests matching a pattern
uv run pytest --cov=maelstrom      # Run with coverage
uv run python -m maelstrom         # Run the module
bin/lint                           # Run ruff lint, ruff format check, pyright (gate before commit)
```

## Developer Documentation

See `docs/dev/` for architecture and design docs:

- `docs/dev/architecture-patterns.md` — canonical layered-architecture conventions for the
  Python core (storage / model / CLI layers), using the task subsystem as the worked reference.
- `docs/dev/tasks.md` — the task domain model: `parent` vs `follows`, dotted ids, session discovery.
- `docs/dev/stacking.md` — stacked branches: what a base is, why the base tip is stored, the
  stack tip, and why only `gh stack link` is used.
- `docs/dev/scheduled-tasks.md` — launchd firing mechanics for template tasks.
- `docs/dev/cmux.md` — how the `cmux/` package drives cmux: the three layers, the idempotent
  `ensure_*` verbs, and the pane 0/1/2 convention.
- `docs/dev/agent-daemon.md` — driving agents over a stream-json pipe: the flags, the event
  vocabulary, the reply shapes, and teleport.
- `docs/dev/orchestrator-ui.md` — the `web/` canvas app: its four layers, the event and
  command protocol, how the fake backend and a real one relate, and how to run it.
- `docs/dev/orchestrator-server.md` — the server behind that app: its layers, how it keeps the
  world fresh, launch, and the UI ↔ server wire protocol.

`CONTEXT.md` at the repo root is the domain glossary. Read it before you write prose or name
anything, and reuse its terms verbatim, including each term's `_Avoid_` list. Add new domain
terms there rather than defining them inline.

## User Documentation

User-facing documentation lives in `docs/guide/` and `docs/reference/`. Read it before you
change behaviour a user can see, and update it in the same change:

- `docs/guide/concepts.md` — what maelstrom is and how the components fit together.
- `docs/guide/multi-agent-workflow.md` — the core loop, end to end.
- `docs/reference/cli.md` — every command and flag.
- `docs/reference/configuration.md` — every config key.
- `docs/reference/environment.md` — every environment variable.

## Architecture

Maelstrom is an orchestration layer for multi-agent development. It uses cmux to manage
workspaces, git worktrees to isolate code, and Claude Code as its agent. It has its own
task notebook and its own dev environment manager. Worktrees use NATO phonetic alphabet
names (alpha, bravo, charlie, …) in a bare-like repository structure.

The CLI is built with **Click**. `src/maelstrom/cli.py` is the entry point; each subsystem
adds its own command group (`task_cli.py`, `env_cli.py`, `git_cli.py`, `github_cli.py`,
`session_cli.py`, `status_cli.py`, `admin_cli.py`, and the `integrations/` package).

For the module-by-module picture, read `docs/dev/architecture-patterns.md` — it documents the
storage / model / CLI layering the modules follow, rather than restating a file list that
goes stale.

### Key Concepts

- **Projects** live in `~/Projects/<name>/` (configurable via `~/.maelstrom/config.yaml`).
- **Worktrees** are subdirectories named alpha, bravo, etc. (not branch names).
- **PORT_BASE** is a 3-digit number (300-999); each service port = `PORT_BASE * 10 + index`.
- When creating worktrees, an existing `.env` from the project root is merged with generated
  port vars, with `$VAR` substitution.
