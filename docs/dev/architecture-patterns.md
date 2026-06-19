# Architecture Patterns

Conventions for the maelstrom Python core. New features should follow these; the
existing code is being converged onto them iteratively. The **task subsystem** is
the worked reference — when in doubt, copy how it is built.

This document covers *structure*. For Python style and Click rules (imports at
top, `pathlib` over `os.path`, `click.ClickException` for user errors, docstrings,
type hints), see [`.claude/review-guides/python.md`](../../.claude/review-guides/python.md).

## The three layers

Each feature is split into three files with one responsibility each:

| Layer | File | Responsibility | Reference |
|-------|------|----------------|-----------|
| **Storage** | `*_store.py` | A `Protocol` plus an in-memory and a persistent backend. Hides *where* data lives. | [`task_store.py`](../../src/maelstrom/task_store.py) |
| **Model** | `*.py` | Pure domain logic. The store is injected; no I/O, no printing. Raises typed domain errors. | [`task.py`](../../src/maelstrom/task.py) |
| **CLI** | `*_cli.py` | Thin adapter: parse args → call one model function → render. The *only* layer that prints or converts errors to exit codes. | [`task_cli.py`](../../src/maelstrom/task_cli.py) |

Dependencies point one way: CLI → model → store. The model never imports the CLI;
the store never imports the model.

## The six conventions

### 1. Three layers per feature

Storage / pure model / thin CLI, as above. The task subsystem is the worked
example: [`task_store.py`](../../src/maelstrom/task_store.py) defines the
[`TaskStore` Protocol](../../src/maelstrom/task_store.py#L33)
(`list_dir` / `read` / `write` / `delete` / `exists` / `transaction`) with
`InMemoryStore` and `GitFileStore` backends; [`task.py`](../../src/maelstrom/task.py)
is the pure model; [`task_cli.py`](../../src/maelstrom/task_cli.py) is the thin CLI.

### 2. No I/O or printing in model code

`subprocess` and `click.echo` live only in the CLI/adapter layer. Model functions
take their inputs as arguments (including the injected store) and return data or
raise — they don't read the environment, shell out, or print. Because the model
only touches the injected store, it can be exercised against an `InMemoryStore`
with no git and no filesystem (see the task unit tests).

> Sanctioned exceptions are rare, obvious, and documented — they are not licence
> for general I/O in the model:
>
> - launching an interactive editor (e.g. `edit_in_editor`,
>   [`task.py`](../../src/maelstrom/task.py#L942)), which is inherently a side
>   effect on the user's terminal;
> - generating a descriptive branch name
>   ([`branch_name.py`](../../src/maelstrom/branch_name.py)), which shells out to
>   `claude -p` for a slug. Contained because every path falls back to a
>   deterministic offline slug and the subprocess is reached through an
>   injectable `runner`, so the model stays exercisable with no CLI.

### 3. One error contract

The model raises **typed domain errors** (`KeyError` for "task not found",
`ValueError` for invalid input, etc.). The **CLI layer is the only place** that
catches them and converts to `click.ClickException` / exit codes — see the
`except KeyError: raise click.ClickException(...)` pattern throughout
[`task_cli.py`](../../src/maelstrom/task_cli.py).

This is the convention to converge on. Today the codebase is inconsistent and
these are the things to fix as each module is refactored:

- integrations raise `click.ClickException` directly from non-CLI code,
- `worktree.py` / `env.py` raise bare `RuntimeError`,
- `cli.py` raises `SystemExit` / `click.UsageError` inline.

Model code should raise domain errors; only the `*_cli.py` layer should know about
Click or exit codes.

### 4. Empty `__init__.py`; import from concrete submodules

`__init__.py` carries nothing but the package docstring and `__version__`.
Import from the concrete module (`from .worktree import create_worktree`), never
re-export through the package, and **never** import another module's `_private`
helpers.

Anti-pattern (to be removed in a later iteration): `cli.py` imports
`_ensure_cmux_browser` and `_print_service_status` from `env_cli`. A leading
underscore means "private to this module" — reaching across for it couples the two
files. If two modules need a helper, promote it to a public function with a real
name.

### 5. All persistence goes through a store abstraction

Persisted state goes through a store like [`TaskStore`](../../src/maelstrom/task_store.py#L33),
not ad-hoc `json.dump`. A store gives you a swappable in-memory backend for tests,
a single place for atomicity and locking, and (for `GitFileStore`) versioning and
transactions for free.

Counter-example to migrate: [`env.py`'s `save_env_state`](../../src/maelstrom/env.py#L175)
writes JSON directly with `json.dump`, and is **not atomic** — a crash mid-write
can leave a truncated state file. A JSON store mirroring `TaskStore` (atomic
write-to-temp-then-rename, in-memory backend for tests) is the target.

### 6. Imports at the top of the file

No function-body imports. This repeats
[`.claude/review-guides/python.md`](../../.claude/review-guides/python.md) because
it has been violated (the now-removed `cmd_ui` / `cmd_self_update` build code used
`import subprocess` inside the function body). Module-level imports keep
dependencies visible and avoid per-call import cost. New and refactored code must
not reintroduce them.

## Applying this

When adding or refactoring a feature, ask:

1. Is there a `*_store.py` Protocol with an in-memory backend, so the model is
   testable without git/filesystem?
2. Is the model pure — no `click`, no `subprocess`, no `print`, store injected?
3. Does the model raise typed domain errors, and is the `*_cli.py` the only place
   that turns them into `ClickException` / exit codes?
4. Are all imports public, top-of-file, and from concrete submodules?

If yes to all four, it matches the task subsystem and this document.
