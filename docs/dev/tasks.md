# The task domain model

The mental model behind `mael task …`. This is the *conceptual* overview; the
authoritative mechanics live in the code docstrings (`task.py`, `task_cli.py`)
that assume it. For the layering view of the same subsystem see
[`architecture-patterns.md`](architecture-patterns.md); for launchd scheduling
mechanics see [`scheduled-tasks.md`](scheduled-tasks.md).

## Tasks & status

A task is one row in the state database, keyed by `<project>/<id>`. It carries the
frontmatter fields (`id`, `title`, `parent`, `follows`, `branch`, …) and its prose
in the same row, so a write is one transaction over the whole task.

**Status is a column**: `todo`, `in-progress`, `done`, `template`, and the rest.
Changing one updates a column; the id is stable across the change. The markdown
export lays each task out at `<project>/<status>/<id>.md`, which is where the
folder-per-status layout survives — as a rendering, not as the authority.

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

## `parent` vs `base` — near-identical names, near-opposite meanings

A task's `base` frontmatter names **the branch this task's branch is stacked on**. It is
not a variant of `parent`, and the two are never derived from each other:

| | `parent` | `base` |
|---|---|---|
| what it groups | tasks | branches |
| branch | siblings share **one** branch | each is a **different** branch |
| pull request | siblings land in **one** PR | each gets its **own** PR |
| where it lives | task frontmatter | git config (`base:` seeds it) |

`parent` says "this task is more of the same work". `base` says "this branch builds on
that branch". A chain of five tasks under one parent is one PR; five tasks each with a
`base` are five PRs that merge bottom-up.

`base` is a declarative *input*: it seeds the branch's stored base the first time the
worktree is set up. The live value lives in git config, so a later `mael sync --base`
changes the branch without rewriting the task file. An empty `base` uses the project's
stack tip. See [`stacking.md`](stacking.md).

## Dotted ids express the fuller hierarchy

Dots in an **id** capture *lineage / nesting*, independently of chain-grouping:

- `<parent>.<n>` — a numeric child (e.g. `k3f9.1`, `PROJ-12.3`).
- `<template>.<date>` — a scheduled run (e.g. `maintenance.2026-07-02`).

The id is where nesting is expressed; `parent` is where PR-grouping is expressed —
and they are separable. A run named `maintenance.2026-07-02` can have an **empty
`parent`** yet still read as descended from `maintenance` via its id. That exact
separation is what keeps scheduled runs clean: the dot-id names and dedups the
run under its template, while the empty `parent` lets the run root its own chain.

A new top-level task gets a random id: 4 characters from `a-z0-9`, such as `k3f9`. The
allocator draws again when the project already has the id, or when YAML reads the id as a
non-string, such as `0123` or `true`: a task draft names ids in YAML. An older task can have a
`YYYY-MM-DD.<n>` id.

A random id holds no date, so the order of tasks comes from the `created` field, oldest first.
The id is the tie-break. `creation_order` in `task.py` is that key. `mael task next`, `mael task
list`, `TaskTable.list` and the task list in the orchestrator UI all use this order. A child
therefore sorts by the time it was created, not next to its parent.

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

A task's sessions are its **Agent records** (see `CONTEXT.md`). Every launch of a task
writes one. `AgentStore.for_task` reads a task's records, and `AgentStore.for_session`
reads the records of one session id.

| Launch path | Writer of the record | Record key |
|---|---|---|
| The orchestrator UI | `DaemonRouter`, from the `task` field of the `start` payload | The agent id |
| `mael task run` on the daemon | `start_agent_in_worktree`, after the daemon names the agent | The agent id |
| `mael task run --cli` and `--here` | The launcher, once the pane is placed or before the exec | `cli-<random>` |

A launch chooses its session with `choose_session`, after it opens the worktree:

1. It resumes the newest session of the task that has a transcript in that worktree, with
   `claude --resume <id>`.
2. With no such session, it mints a random id and starts with `claude --session-id <id>`.

The transcript test needs the worktree path, because Claude Code stores a transcript at
`~/.claude/projects/<sanitised-cwd>/<session-id>.jsonl`. Claude Code refuses
`--session-id <id>` when that file exists, which is why a new session never reuses an id.

A task with no record has nothing to resume. That is the case for a task that last ran
through a path that wrote no record: its next launch starts a new session, and the old
transcript stays on disk.

A task rename and `mael project mv` re-key the records with the task, so a renamed task
keeps its sessions. Both refuse while a session of the task is live, because the live
session holds the old id in `MAEL_TASK_ID`.

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

**Rejected alternatives.** Neither transcripts nor a registry can decide
liveness:

- **Transcript + `lsof`.** A running `claude` CLI appends to its transcript and
  closes it, rather than holding the file descriptor open. `lsof` on transcripts
  therefore reports nothing for live sessions, and false-positives on editor
  tabs. It is also slow: a system-wide `lsof` sweep per worktree made `mael list`
  take ~49s.
- **A `~/.maelstrom` session registry.** One existed, written per session by an
  MCP channel. It missed the current session and its `state` went stale, so it
  could never be the authority; it has been removed. What a driven agent is
  doing now comes from the agent daemon, which `mael agent list` reads.

`mael task run` consults the live sweep before launching and **refuses only when
the session is live** (naming the pid and worktree, hinting `mael task
reconcile`). A *finished* task is deliberately **not** blocked — it must stay
re-runnable. `mael list`, `mael session list` and `mael task reconcile` read the
same source, so all four always agree.

The harness exports a session id of its own, as `CLAUDE_CODE_SESSION_ID`, but
that id cannot key a task. `CLAUDE_CODE_SESSION_ID` names the conversation running
now, and a `/clear` starts a new conversation and moves it. The derived id never
moves, which is why the task table keys on it. `mael session info` and
`mael session end` are the commands that want the live id, and they read
`CLAUDE_CODE_SESSION_ID` for it.
