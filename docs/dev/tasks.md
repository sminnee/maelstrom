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
(`<template>.<date>`) but a **parentless chain root**. Because its `parent` is
empty, the launcher exports `MAEL_TASK_PARENT = run.id`, so each firing's
follow-ups nest under **the run**, not the template — every firing is isolated
rather than piling onto the template's chain. The trade-offs (a generated branch
and PR per firing; the run is not listed under `list --parent <template>`) are
deliberate. See [`scheduled-tasks.md`](scheduled-tasks.md) for the launchd
firing mechanics.
