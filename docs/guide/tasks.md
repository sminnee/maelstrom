# Tasks

The task notebook is how maelstrom knows what each agent is doing, and in what order.

## What a task is

A task is a markdown file with YAML frontmatter, stored in a git-backed notebook at
`~/.maelstrom/tasks`. **Its status is the folder it sits in**:

```
<project>/
├── todo/           actionable, or waiting on a dependency
├── in-progress/    a session is running it
├── done/
├── blocked/
├── cancelled/
└── template/       a reusable recipe; never actionable itself
```

Moving a task between statuses moves its file. The id is stable across the move.

```bash
mael task add "Add a hello endpoint"
mael task read <id>
```

```yaml
---
id: PROJ-123.1
title: "Execute: PROJ-123 — add the hello endpoint"
project: myproject
command: ""
mode: auto
branch: ""
parent: linear.PROJ-123
follows: [PROJ-123]
priority: medium
---
# The task's content — an execute task's content is its plan.
```

## The three relationships

`parent`, `follows` and dotted ids do three different jobs. They are separable, and
understanding that is the single most important idea in the system.

### `parent` groups a chain — one PR per parent

A task's `parent` groups it into a **linear chain of siblings that share one branch and one
pull request**. This is not an arbitrary tree: siblings under a parent run in `follows`
order and merge as a single PR.

A task with no `parent` **roots its own chain** — it self-parents.

The parent is often a *virtual* root rather than a real task:

- **Linear-rooted work** parents under `linear.<ID>`. The issue is the chain's root, and
  every task planned for it lands in one PR.
- **Ad-hoc work** parents under the planning task's own id.

### `follows` orders execution

A task becomes actionable only once **everything it follows is done**.

```bash
mael task add "Second step" --follow <first-id>
mael task add "Append after my siblings" --follow-end '*'
```

`--follow-end '*'` means "append after the current leaf of my parent's child-chain". Always
quote the `*`: unquoted it is a YAML alias, and `"\*"` is a bad escape. Both fail to parse.

### Dotted ids express lineage

Dots in an **id** capture nesting, independently of grouping:

- `PROJ-12.3` — a numeric child of `PROJ-12`.
- `maintenance.2026-07-02` — a scheduled run of the `maintenance` template.

### Why separable matters

A scheduled run named `maintenance.2026-07-02` has an **empty `parent`**. Its id says it
descends from `maintenance`; its empty parent lets it root its own chain. So every firing
is isolated with its own branch and PR, instead of piling onto the template's chain.

That only works because id-lineage and PR-grouping are different things.

## Chaining without spelling it out

A session launched by `mael task run` exports `MAEL_TASK_ID` and `MAEL_TASK_PARENT` — the
launching task's `parent`, or its own id when it has none.

New tasks default `--parent` to `$MAEL_TASK_PARENT`. So a skill running inside a session can
emit follow-ups without naming the parent. Those follow-ups continue the same chain, and land
in the same PR. An explicit `--parent` always wins.

Likewise `mael task status done` with no id closes **the current task**, falling back to
`$MAEL_TASK_ID`.

## Creating tasks

```bash
mael task add "Fix the flaky port test"                    # create only
mael task add "Fix the flaky port test" --run              # create and launch (plan mode)
mael task add "Bump pyright" --mode auto --run             # unattended execute session
mael task add "Risky migration" --mode normal --run        # prompts on each action
mael task add "..." --content-file brief.md                # seed the content
mael task add "..." --content-file -                       # ...from stdin
mael task add "..." --command plan-task --parent linear.PROJ-123
mael task add "..." --from <other-id>                      # duplicate a recipe
```

### Modes

New tasks default to **plan** mode, so a bare `--run` opens a planning session.

| Mode | Behaviour |
|---|---|
| `plan` | Plans and asks before acting. The default. |
| `auto` | Unattended execute session. Runs its plan without prompting. |
| `normal` | Execute session that prompts on each action. |

An **execute task runs no skill**: its content *is* its plan, and it implements it directly.
Set `--command` to run a skill instead, such as `plan-task` or `plan-next-step`.

### Other fields

```bash
mael task add "..." --priority high      # critical | high | medium | low
mael task add "..." --model opus         # pin the session's model
mael task add "..." --branch feature/x   # opt out of the parent's branch
mael task add "..." --pre-action linear.in-progress
mael task add "..." --post-action linear.done
```

Lifecycle actions fire a Linear status change when a task starts or finishes, so the chain
mirrors itself without manual `set-status` calls.

## Chains from a plan file

`mael task load-many` creates a whole chain from a marked plan file. This is how planning
sessions hand work off — see [planning.md](planning.md).

```bash
mael task load-many plan.md            # create the chain
mael task load-many plan.md --run      # ...and launch every unblocked task
mael task load-many - --run            # read from stdin
```

The file is a preamble the human reads, then `---CREATE TASK <name>---` blocks. Each block
is frontmatter plus a body; the body becomes the task's content. A block ends at the next
marker or at EOF, so back-to-back blocks need no terminator. Add `---END TASK <name>---`
only when prose follows a block.

Block frontmatter keys: `title` (required), `command`, `mode`, `model`, `priority`,
`parent`, `branch`, `pre-action`, `post-action`, `follow`, `follow-end`.

### Two rules that decide whether it works

**Set `mode:` on every block.** New tasks default to plan mode, so an execute block that
omits `mode: auto` re-plans instead of running its plan.

**Leave `branch:` unset on every block.** Tasks inherit their parent's branch. That default
keeps every iteration on one branch, accumulating into a **single pull request** that merges
as a whole. Setting `branch:` opts a task out into its own worktree and PR — right for
genuinely unrelated work, wrong for splitting one task's iterations.

### Close the planning task before `--run`

```bash
mael task status done                  # FIRST
mael task load-many plan.md --run      # then
```

`--run` only launches **actionable** tasks. The head block carries `follow-end: "*"`, so it
follows the planning task. While the planning task is `in-progress`, the head is blocked and
`--run` launches nothing — silently, exiting 0.

The SessionEnd hook also closes the planning task, but it fires *after* `load-many` has run.
It is not a backstop for this ordering.

## Advancing a chain

```bash
mael task next                       # print the next actionable id
mael task next --run                 # launch it
mael task next --run --parent linear.PROJ-123
mael task next --run --branch feature/x     # strictly this branch
mael task run <id>                   # launch a specific task
```

By default `next` prefers a task on the current git branch, then falls back to the global
next task. `--branch` removes the fallback.

Ordering is by priority (critical → low), then by dependency.

## Watching

```bash
mael task list              # actionable now
mael task list --all-todo   # include blocked-but-waiting
mael task list --all        # include done and cancelled
mael task list --parent linear.PROJ-123
mael task show <id>         # summary
mael task read <id>         # the raw file
mael task log <id> "note"   # append to the task's log
```

## Editing and lifecycle

```bash
mael task edit <id>                       # open in $EDITOR, commit if changed
mael task update <id> "New title"
mael task update <id> --mode auto --priority high
mael task update <id> --id PROJ-9.2       # re-key, rewriting references
mael task update <id> --model ''          # clear a field
mael task rm <id>                         # delete, stripping it from dependents

mael task status start|done|todo|block|cancel|template [<id>]
```

`<id>` defaults to `$MAEL_TASK_ID`.

## When tasks and sessions disagree

A session can die without its task moving. Reconcile them:

```bash
mael task reconcile          # show the mismatches
mael task reconcile --fix    # apply the corrections
```

Liveness comes from live `claude` processes, the same source `mael task run`'s
duplicate-launch guard uses, so the two always agree. Reconcile distinguishes an
`in-progress` task that *ran* before (a transcript persists → `done`) from one that *never
ran* (no transcript → `todo`), and a live session whose task is not `in-progress`
(→ `in-progress`).

The metadata index is a rebuildable cache. If a manual edit diverges it:

```bash
mael task reindex
```

## See also

- [Planning](planning.md) — how plan files become chains.
- [Scheduled work](scheduled-work.md) — templates and cron.
- [The task domain model](../dev/tasks.md) — the developer-level view.
