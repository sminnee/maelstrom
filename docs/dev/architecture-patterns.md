# Architecture Patterns

Conventions for the maelstrom Python core. New features should follow these; the
existing code is being converged onto them iteratively. The **task subsystem** is
the worked reference — when in doubt, copy how it is built.

This document covers *structure* (the layering view). For the task *domain model*
— what `parent`, `follows`, and dotted ids mean — see [`tasks.md`](tasks.md). For
Python style and Click rules (imports at top, `pathlib` over `os.path`,
`click.ClickException` for user errors, docstrings, type hints), see
[`.claude/review-guides/python.md`](../../.claude/review-guides/python.md).

## The three layers

Each feature is split into three files with one responsibility each:

| Layer | File | Responsibility | Reference |
|-------|------|----------------|-----------|
| **Storage** | `*_store.py`, `*_table.py` | An abstract base class plus an in-memory and a persistent backend. Hides *where* data lives. | [`task_table.py`](../../src/maelstrom/task_table.py) |
| **Model** | `*.py` | Pure domain logic. The store is injected; no I/O, no printing. Raises typed domain errors. | [`task.py`](../../src/maelstrom/task.py) |
| **CLI** | `*_cli.py` | Thin adapter: parse args → call one model function → render. The *only* layer that prints or converts errors to exit codes. | [`task_cli.py`](../../src/maelstrom/task_cli.py) |

Dependencies point one way: CLI → model → store. The model never imports the CLI;
the store never imports the model. `tests/test_service_boundary.py` enforces the
first half: no domain module reaches click or a `*_cli` module, even through a
lazy import.

## The seven conventions

### 1. Three layers per feature

Storage / pure model / thin CLI, as above. The task subsystem is the worked
example:

- [`task_table.py`](../../src/maelstrom/task_table.py) — storage. Defines the
  `TaskTable` abstract base class
  (`load` / `list` / `save` / `delete` / `find_by_session_id` / `transact` /
  `changed_since` / `revision`), with `InMemoryTaskTable` and `SqliteTaskTable`
  backends.
- [`task.py`](../../src/maelstrom/task.py) — the pure model.
- [`task_cli.py`](../../src/maelstrom/task_cli.py) — the thin CLI.

The service integrations follow the same split. Each of
[`integrations/`](../../src/maelstrom/integrations/)`linear.py`, `sentry.py`,
`slack.py` and `uptimerobot.py` holds the API client, the reusable operations
and the pure formatters. Its `*_cli.py` twin holds the click group and the
commands, and reaches the model as `from . import linear`. Some commands still
build their own queries in the CLI module, for example `mael linear release`
and `mael sentry list-issues`.

### 2. No I/O or printing in model code

`subprocess` and `click.echo` live only in the CLI/adapter layer. Model functions
take their inputs as arguments (including the injected store) and return data or
raise — they don't read the environment, shell out, or print. Because the model
only touches the injected store, it can be exercised against an `InMemoryStore`
with no git and no filesystem (see the task unit tests).

A model that must report while it runs takes a line callback instead of
printing: `warn: Callable[[str], None]` in
[`task_actions.py`](../../src/maelstrom/task_actions.py), `announce` in the
worktree steps. The CLI passes a stderr echo. The orchestrator passes a logger.

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
> - discovering a container's VM IP
>   ([`services.py`](../../src/maelstrom/services.py) `discover_container_ip`),
>   which polls `container inspect`. Same containment: the subprocess is reached
>   through an injectable `runner`, so command synthesis and IP parsing stay pure
>   and testable with a fake runner. The `services:` schema lives in
>   [`config.py`](../../src/maelstrom/config.py); command/container builders and
>   the per-engine table live in `services.py`; the two-phase start (containers
>   first, VM IP injected into sibling command services' spawn env) lives in
>   [`env.py`](../../src/maelstrom/env.py).
> - resolving a rebase conflict
>   ([`rebase_repair.py`](../../src/maelstrom/rebase_repair.py)), which runs
>   `claude -p /resolve-rebase-conflicts` in the conflicted worktree. Same
>   containment as `branch_name.py`: the subprocess is reached through an
>   injectable `repair_runner` on both autorepair entry points
>   (`sync_worktree_with_autorepair` and `squash_worktree_with_autorepair`), so
>   the state machine around it — abort on failure, verify the branch, re-sync to
>   push — is exercised against real git with a fake runner. The module is a
>   leaf: it imports only the standard library and `shell`, which is itself a
>   leaf, so `worktree.py` can call it without reaching the launcher layer.

### 3. One error contract

The model raises **typed domain errors** (`KeyError` for "task not found",
`ValueError` for invalid input, etc.). The **CLI layer is the only place** that
catches them and converts to `click.ClickException` / exit codes — see the
`except KeyError: raise click.ClickException(...)` pattern throughout
[`task_cli.py`](../../src/maelstrom/task_cli.py).

A domain the two builtins do not describe gets a named error instead — one that
is neither "not found" nor "bad input". Give a subsystem's errors one base, so a
CLI catches the family by name rather than listing every subclass.
[`worktree_model.py`](../../src/maelstrom/worktree_model.py) has `WorktreeError`
over `UnclosableWorktreeError`, `WorktreeNamesExhaustedError` and
`WorktreeSetupError`.
[`integrations/errors.py`](../../src/maelstrom/integrations/errors.py) has
`IntegrationError` over `IntegrationHTTPError`.

A family can have one conversion point instead of a catch in every command.
`IntegrationGroup` in
[`integrations/group_cli.py`](../../src/maelstrom/integrations/group_cli.py) is
the group class of every integration. It turns an `IntegrationError` into
`click.ClickException` with the same message.

`str()` on a `KeyError` quotes its argument, so a CLI rendering a domain error
takes the message from [`util.error_text`](../../src/maelstrom/util.py) rather
than from `str(exc)`.

This is the convention to converge on. Today the codebase is inconsistent and
these are the things to fix as each module is refactored:

- `env.py` and `github.py` raise bare `RuntimeError`,
- `cli.py` raises `SystemExit` / `click.UsageError` inline.

Model code should raise domain errors; only the `*_cli.py` layer should know about
Click or exit codes.

### 4. Empty `__init__.py`; import from concrete submodules

`__init__.py` carries nothing but the package docstring and `__version__`.
Import from the concrete module (`from .worktree import create_worktree`), never
re-export through the package, and **never** import another module's `_private`
helpers.

If two modules need a helper, promote it to a public function with a real name.
[`ensure_cmux_browser`](../../src/maelstrom/env_cli.py) and
[`print_service_status`](../../src/maelstrom/env_cli.py) are public for exactly
this reason — `cli.py` needs them. A leading underscore means "private to this
module", and reaching across for it couples the two files.

### 5. All persistence goes through a store abstraction

Persisted state goes through a store like
[`TaskTable`](../../src/maelstrom/task_table.py), not ad-hoc `json.dump`. A store
gives you a swappable in-memory backend for tests, a single place for atomicity
and locking, and — on the state database — transactions and a revision counter
for free.

The env subsystem is the worked example of this convention beyond `task`.
[`env_store.py`](../../src/maelstrom/env_store.py) defines the
[`EnvStore` Protocol](../../src/maelstrom/env_store.py), with an `InMemoryEnvStore`
and a `JsonEnvStore` backend. `JsonEnvStore` writes to a temp file then renames it,
via [`util.atomic_write_json`](../../src/maelstrom/util.py). `env.py` writes only
through the store.

Counter-example still to migrate: [`ports.py`](../../src/maelstrom/ports.py)
reads and writes `~/.maelstrom/port_allocations.json` directly with `json.load` /
`json.dump`, and the write is **not atomic** — a crash mid-write can leave a
truncated allocations file. A `PortStore` mirroring `EnvStore` (atomic write,
in-memory backend for tests) is the target.

### 6. Imports at the top of the file

No function-body imports. This repeats
[`.claude/review-guides/python.md`](../../.claude/review-guides/python.md) because
the rule is easy to break by accident. Module-level imports keep dependencies
visible and avoid per-call import cost. New and refactored code must not
reintroduce them, whatever the surrounding module already does.

### 7. Async where the I/O is, and nowhere else

The orchestrator server and the agent daemon run on an event loop. A call that
blocks that loop stops every socket it serves, so anything doing I/O on their
paths is a coroutine.

That rule stops at the I/O. A pure function has no await point to yield at, so
`async def` on one buys nothing and costs an `await` at every call site.
`GitFileStore` stays sync for a stronger reason: it holds a cross-process
`flock`, which is hostile to being made async, and it is not a bottleneck.

**[`state_db/`](../../src/maelstrom/state_db/) is the exception, and the
reason is reversibility rather than I/O.** Its public surface is `async def`
and its engine is sync `sqlite3` called inline, so an `await` there yields
nothing today. The surface is async because the tables it holds may later move
to a database reached over a network, and converting a store afterwards means
converting every caller in every subsystem that reads it. One private helper,
`_call`, is the whole seam: moving its body to `aiosqlite` changes no caller,
no store and no test of a caller. Write a store async from the start when its
backend may become a network database; keep it sync otherwise.

Where the I/O actually is. Re-derive the counts with:

```bash
grep -cE '\b(run_cmd|run_git)\w*\(' src/maelstrom/<module>.py
```

| Module | `run_cmd` / `run_git` call sites |
|--------|----------------------------------|
| [`worktree.py`](../../src/maelstrom/worktree.py) | 100 |
| [`github.py`](../../src/maelstrom/github.py) | 26 |
| [`task.py`](../../src/maelstrom/task.py) | 1 — the `$EDITOR` launch, a convention 2 exception |
| [`worktree_model.py`](../../src/maelstrom/worktree_model.py), [`github_model.py`](../../src/maelstrom/github_model.py), [`task_actions.py`](../../src/maelstrom/task_actions.py), [`task_launch.py`](../../src/maelstrom/task_launch.py), [`orchestrator/normalise.py`](../../src/maelstrom/orchestrator/normalise.py), [`orchestrator/world.py`](../../src/maelstrom/orchestrator/world.py) | 0 |

The count includes the `run_cmd_async` sites, which are the already-converted
ones — it measures where the I/O is, not how much of it still blocks.

So a converted function is one that shells out, or awaits something that does.

**One loop per process.** [`cli_async.py`](../../src/maelstrom/cli_async.py)
holds `AsyncGroup`/`AsyncCommand`; a `mael` group built with it may have
coroutine commands, and the loop is opened once around the invocation. A
command converts by adding `async` and nothing else.

This is what makes a sync twin unnecessary. `asyncio.run` cannot nest, so a
codebase that opens a loop per call site needs a blocking copy of everything a
command might reach — which is what `github.py`'s `*_async` pairs and the sync
`DaemonClient` were. Do not add a `foo_async` beside a `foo`: convert `foo`,
and let its callers await it.

One twin survives: `session_discovery._sweep_blocking`, reached from the
`LiveSessionSet.sessions` property. It goes when its remaining synchronous
callers — in `task_cli`, `worktree` and `agent_server` — move to
`await sweep()`, which is part of converting `worktree.py`.

## Applying this

When adding or refactoring a feature, ask:

1. Is there a `*_store.py` Protocol with an in-memory backend, so the model is
   testable without git/filesystem?
2. Is the model pure — no `click`, no `subprocess`, no `print`, store injected?
3. Does the model raise typed domain errors, and is the `*_cli.py` the only place
   that turns them into `ClickException` / exit codes?
4. Are all imports public, top-of-file, and from concrete submodules?
5. Is anything that shells out a coroutine, and is everything pure left sync?

If yes to all five, it matches the task subsystem and this document.
