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
| **Storage** | `*_store.py`, `*_table.py` | An abstract base class plus an in-memory and a persistent backend. Hides *where* data lives. | [`task_table.py`](../../lib/domain/src/mael_domain/task_table.py) |
| **Model** | `*.py` | Pure domain logic. The store is injected; no I/O, no printing. Raises typed domain errors. | [`task.py`](../../lib/domain/src/mael_domain/task.py) |
| **CLI** | `*_cli.py` | Thin adapter: parse args → call one model function → render. The *only* layer that prints or converts errors to exit codes. | [`task_cli.py`](../../cli/src/mael_cli/task_cli.py) |

Dependencies point one way: CLI → model → store. The model never imports the CLI;
the store never imports the model. The storage and model layers live in
`mael_domain`, and the CLI layer in `mael_cli`. The import-linter contracts
below enforce the first half: no domain module reaches click or `mael_cli`,
even through a lazy import.

## The workspace

The repository is a uv workspace. Each workspace member is one package, and
each package follows the three layers inside it:

| Package | Member | Holds |
|---------|-----------|-------|
| `mael_common` | `lib/common/` | Leaves with no domain knowledge: `shell`, `util`, `table`, `cli_async`, `process_table`, `claude_paths` and `image`. |
| `mael_agent` | `lib/agent/` | The agent wire contract, the daemon transport and client, and the harness model. |
| `mael_domain` | `lib/domain/` | The domain: the storage and model layers for tasks, worktrees, environments, GitHub, the integrations, cmux and the state database, plus the orchestrator's wire protocol and normaliser. |
| `mael_daemon` | `agent-daemon/` | The agent daemon, which drives Claude Code agents, and its `mael-agent-daemon` CLI. |
| `mael_orchestrator` | `orchestrator-api/` | The orchestrator server with its Codex harness, and its `mael-orchestrator` CLI. |
| `mael_cli` | `cli/` | The `mael` CLI. |

`mael_agent` imports `mael_common`. `mael_domain` imports both. `mael_daemon`
imports `mael_agent` and `mael_common`. `mael_orchestrator` and `mael_cli`
import the three libraries. Nothing imports a service, `mael_daemon` or
`mael_orchestrator`: each is reached over its socket. Only
`mael_common.cli_async` in the libraries imports click. Import-linter
contracts in `pyproject.toml` enforce all of this, and `bin/lint` runs them as
`lint-imports`. Tests are outside the contracts, so a CLI test may still build
state through `mael_daemon.agent_model`.

The root `pyproject.toml` is not a member. It builds no package. It depends on
every member, so `uv sync --extra dev` installs all six editable into one
`.venv`, and it holds the one `dev` extra and the tool config for every gate.

Only `cli/` publishes. Its wheel, `sminnee-maelstrom`, bundles the three
libraries and `shared/`, because PyPI has no `mael-*` distribution to depend
on. `cli/hatch_build.py` adds them to the published wheel alone. The editable
install reaches the libraries in the checkout through `dev-mode-dirs` instead.
The daemon and the orchestrator server do not publish: they run from a
checkout.

A member's `tests/` has no `__init__.py`. Fixtures that every suite needs are
in the repo-root `conftest.py`, which imports no package. Keep each test file's
basename unique across the members, because pytest imports member tests by
basename.

A new model or store module goes in `mael_domain`. A module that only the CLI
calls stays in `mael_cli`, and one that only the orchestrator server calls
stays in `mael_orchestrator`. The domain suites'
fixtures are in `lib/domain/tests/domain_fixtures.py`, and its docstring says
how `cli/tests/` imports them too.

## The seven conventions

### 1. Three layers per feature

Storage / pure model / thin CLI, as above. The task subsystem is the worked
example:

- [`task_table.py`](../../lib/domain/src/mael_domain/task_table.py) — storage. Defines the
  `TaskTable` abstract base class
  (`load` / `list` / `save` / `delete` / `find_by_session_id` / `transact` /
  `changed_since` / `revision`), with `InMemoryTaskTable` and `SqliteTaskTable`
  backends.
- [`task.py`](../../lib/domain/src/mael_domain/task.py) — the pure model.
- [`task_cli.py`](../../cli/src/mael_cli/task_cli.py) — the thin CLI.

The service integrations follow the same split. Each of
[`integrations/`](../../lib/domain/src/mael_domain/integrations/)`linear.py`, `sentry.py`,
`slack.py` and `uptimerobot.py` holds the API client, the reusable operations
and the pure formatters. Its `*_cli.py` twin in `cli/src/mael_cli/integrations/`
holds the click group and the commands, and reaches the model as
`from mael_domain.integrations import linear`. Some commands still
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
[`task_actions.py`](../../lib/domain/src/mael_domain/task_actions.py), `announce` in the
worktree steps. The CLI passes a stderr echo. The orchestrator passes a logger.

> Sanctioned exceptions are rare, obvious, and documented — they are not licence
> for general I/O in the model:
>
> - launching an interactive editor (e.g. `edit_in_editor`,
>   [`task.py`](../../lib/domain/src/mael_domain/task.py#L942)), which is inherently a side
>   effect on the user's terminal;
> - generating a descriptive branch name
>   ([`branch_name.py`](../../lib/domain/src/mael_domain/branch_name.py)), which shells out to
>   `claude -p` for a slug. Contained because every path falls back to a
>   deterministic offline slug and the subprocess is reached through an
>   injectable `runner`, so the model stays exercisable with no CLI.
> - discovering a container's VM IP
>   ([`services.py`](../../lib/domain/src/mael_domain/services.py) `discover_container_ip`),
>   which polls `container inspect`. Same containment: the subprocess is reached
>   through an injectable `runner`, so command synthesis and IP parsing stay pure
>   and testable with a fake runner. The `services:` schema lives in
>   [`config.py`](../../lib/domain/src/mael_domain/config.py); command/container builders and
>   the per-engine table live in `services.py`; the two-phase start (containers
>   first, VM IP injected into sibling command services' spawn env) lives in
>   [`env.py`](../../lib/domain/src/mael_domain/env.py).
> - resolving a rebase conflict
>   ([`rebase_repair.py`](../../lib/domain/src/mael_domain/rebase_repair.py)), which runs
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
[`task_cli.py`](../../cli/src/mael_cli/task_cli.py).

A domain the two builtins do not describe gets a named error instead — one that
is neither "not found" nor "bad input". Give a subsystem's errors one base, so a
CLI catches the family by name rather than listing every subclass.
[`worktree_model.py`](../../lib/domain/src/mael_domain/worktree_model.py) has `WorktreeError`
over `UnclosableWorktreeError`, `WorktreeNamesExhaustedError` and
`WorktreeSetupError`.
[`integrations/errors.py`](../../lib/domain/src/mael_domain/integrations/errors.py) has
`IntegrationError` over `IntegrationHTTPError`.

A family can have one conversion point instead of a catch in every command.
`IntegrationGroup` in
[`integrations/group_cli.py`](../../cli/src/mael_cli/integrations/group_cli.py) is
the group class of every integration. It turns an `IntegrationError` into
`click.ClickException` with the same message.

`str()` on a `KeyError` quotes its argument, so a CLI rendering a domain error
takes the message from [`util.error_text`](../../lib/common/src/mael_common/util.py) rather
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
[`ensure_cmux_browser`](../../cli/src/mael_cli/env_cli.py) and
[`print_service_status`](../../cli/src/mael_cli/env_cli.py) are public for exactly
this reason — `cli.py` needs them. A leading underscore means "private to this
module", and reaching across for it couples the two files.

### 5. All persistence goes through a store abstraction

Persisted state goes through a store like
[`TaskTable`](../../lib/domain/src/mael_domain/task_table.py), not ad-hoc `json.dump`. A store
gives you a swappable in-memory backend for tests, a single place for atomicity
and locking, and — on the state database — transactions and a revision counter
for free.

The env subsystem is the worked example of this convention beyond `task`.
[`env_store.py`](../../lib/domain/src/mael_domain/env_store.py) defines the
[`EnvStore` Protocol](../../lib/domain/src/mael_domain/env_store.py), with an `InMemoryEnvStore`
and a `JsonEnvStore` backend. `JsonEnvStore` writes to a temp file then renames it,
via [`util.atomic_write_json`](../../lib/common/src/mael_common/util.py). `env.py` writes only
through the store.

Counter-example still to migrate: [`ports.py`](../../lib/domain/src/mael_domain/ports.py)
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

**[`state_db/`](../../lib/domain/src/mael_domain/state_db/) is the exception, and the
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
grep -cE '\b(run_cmd|run_git)\w*\(' lib/domain/src/mael_domain/<module>.py
```

| Module | `run_cmd` / `run_git` call sites |
|--------|----------------------------------|
| [`worktree.py`](../../lib/domain/src/mael_domain/worktree.py) | 100 |
| [`github.py`](../../lib/domain/src/mael_domain/github.py) | 26 |
| [`task.py`](../../lib/domain/src/mael_domain/task.py) | 1 — the `$EDITOR` launch, a convention 2 exception |
| [`worktree_model.py`](../../lib/domain/src/mael_domain/worktree_model.py), [`github_model.py`](../../lib/domain/src/mael_domain/github_model.py), [`task_actions.py`](../../lib/domain/src/mael_domain/task_actions.py), [`task_launch.py`](../../lib/domain/src/mael_domain/task_launch.py), [`normalise.py`](../../lib/domain/src/mael_domain/normalise.py), [`world.py`](../../orchestrator-api/src/mael_orchestrator/world.py) | 0 |

The count includes the `run_cmd_async` sites, which are the already-converted
ones — it measures where the I/O is, not how much of it still blocks.

So a converted function is one that shells out, or awaits something that does.

**One loop per process.** [`cli_async.py`](../../lib/common/src/mael_common/cli_async.py)
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
