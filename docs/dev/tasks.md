# The task domain model

The mental model behind `mael task …`. This is the *conceptual* overview; the
authoritative mechanics live in the code docstrings (`task.py`, `task_cli.py`)
that assume it. For the layering view of the same subsystem see
[`architecture-patterns.md`](architecture-patterns.md); for launchd scheduling
mechanics see [`scheduled-tasks.md`](scheduled-tasks.md).

## Tasks & status folders

A task is a markdown file at `<project>/<status>/<id>.md` — YAML frontmatter
(`id`, `title`, `parent`, `follows`, `branch`, …) over a free-text body. **Status
is the folder**: `todo/`, `in-progress/`, `done/`, `template/`, etc. Moving a task
between statuses moves its file; the id is stable across the move.

## `parent` groups a linear chain = one PR

A task's `parent` groups it into a **linear chain of sibling tasks that share one
branch and one pull request** ("one PR per parent"). It is *not* an arbitrary
tree — siblings under a parent execute in `follows` order and merge as a single
PR. A task with no `parent` **roots its own chain** (it self-parents:
`MAEL_TASK_PARENT = task.id`).

The parent is often a *virtual* root rather than another real task:

- **Linear-rooted work** parents under `linear.<ID>` — the issue is the chain's
  root, and every task planned for it lands in one PR.
- **Ad-hoc work** parents under the planning task's own id — a bare
  `mael task add … --run` session self-parents and its emitted chain hangs off
  that.

## Dotted ids express the fuller hierarchy

Dots in an **id** capture *lineage / nesting*, independently of chain-grouping:

- `<parent>.<n>` — a numeric child (e.g. `PROJ-12.3`).
- `<template>.<date>` — a scheduled run (e.g. `maintenance.2026-07-02`).

The id is where nesting is expressed; `parent` is where PR-grouping is expressed —
and they are separable. A run named `maintenance.2026-07-02` can have an **empty
`parent`** yet still read as descended from `maintenance` via its id. That exact
separation is what keeps scheduled runs clean: the dot-id names and dedups the
run under its template, while the empty `parent` lets the run root its own chain.

## `follows` vs `parent`

They are orthogonal:

- **`follows`** controls *execution order* — a task is actionable only once
  everything it follows is done. `follow-end:"*"` means "append after my
  parent-chain's current leaf."
- **`parent`** controls *PR grouping / branch* — which chain (and therefore which
  branch and PR) the task belongs to.

A chain is typically a `follows` line-up of siblings that all share one `parent`.

## `MAEL_TASK_PARENT` and chaining

A session launched by `mael task run` exports `MAEL_TASK_ID` and
`MAEL_TASK_PARENT` (the launching task's `parent`, or its own id when it has
none). New tasks default their `parent` to `$MAEL_TASK_PARENT`, so a skill running
inside a session can emit follow-ups that continue the same chain without spelling
out the parent. An explicit `--parent` always wins.

## Scheduled runs

A scheduled *run* is a dot-id child *name* of its template
(`<template>.<date>`) but a **parentless chain root**. Its `parent` is empty, so
the launcher exports `MAEL_TASK_PARENT = run.id`. Each firing's
follow-ups therefore nest under **the run**, not the template — every firing is isolated
rather than piling onto the template's chain. The trade-offs (a generated branch
and PR per firing; the run is not listed under `list --parent <template>`) are
deliberate. See [`scheduled-tasks.md`](scheduled-tasks.md) for the launchd
firing mechanics.

## Session discovery — one live session per task

Each task maps to a **deterministic Claude session id**: `session_id_for(project,
task_id)` (a `uuid5` over `project` and `task_id`). `mael task run` passes it as
`claude --session-id <id>`, so the same task always resolves to the same session.

Claude Code's own uniqueness rule for that id is **file-based**: it stores the
session transcript at `~/.claude/projects/<sanitised-cwd>/<session-id>.jsonl` and
**refuses to start** `claude --session-id <id>` when that file already exists for
the cwd. So a task whose session has run before relaunches with
`claude --resume <id>` instead, which reattaches the existing conversation.

Because the id derives from the **project name**, renaming a project changes every
id. This orphans the existing sessions by design: `mael mv-project` warns about it
rather than migrating transcripts, and `mael task run` then starts a fresh session
instead of resuming.

`session_discovery.py` answers "is there a **live** session?" from the running
`claude` processes themselves, not from any file. A live session's **cwd is the
worktree it was launched in**, so one sweep gives every live session's real
worktree:

1. **pids** — `pgrep -x claude`. `-x` matches the exact command name, so `bun`
   MCP-channel helpers and `Code Helper` are excluded — only the CLI itself.
2. **cwd** — one batched `lsof -a -d cwd -p <pids> -F pn` resolves every pid's
   working directory in a single call.
3. **session id** — one batched `ps -o command=` recovers the `--session-id`
   `mael` launched each process with, the durable link back to the task.

The whole sweep costs ~0.03s. Callers work through `LiveSessionSet`, which sweeps
once on first use and then answers per-worktree questions off that shared list,
so a pass over many worktrees still shells out only once.

**Rejected alternatives.** Neither transcripts nor the registry can decide
liveness:

- **Transcript + `lsof`.** A running `claude` CLI appends to its transcript and
  closes it, rather than holding the file descriptor open. `lsof` on transcripts
  therefore reports nothing for live sessions, and false-positives on editor
  tabs. It is also slow: a system-wide `lsof` sweep per worktree made `mael list`
  take ~49s.
- **The `~/.maelstrom` session registry.** It misses the current session and its
  `state` goes stale, so it cannot be the authority. It survives only as
  *optional enrichment* for `mael session list`.

`mael task run` consults the live sweep before launching and **refuses only when
the session is live** (naming the pid and worktree, hinting `mael task
reconcile`). A *finished* task is deliberately **not** blocked — it must stay
re-runnable. `mael list`, `mael session list` and `mael task reconcile` read the
same source, so all four always agree.

The registry's primary key is the same deterministic id. The Claude harness does
**not** export `CLAUDE_SESSION_ID` to channel subprocesses, so `mael task run`
exports it as `MAEL_SESSION_ID` on the `claude` command. The session-channel then
records that as the registry `session_id`. Discovery does not depend on this — it
globs by id — but it keeps the registry-hint fast-path and `reconcile`'s
primary-key match trustworthy.
