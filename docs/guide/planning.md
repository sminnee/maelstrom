# Planning

How a brief becomes a chain of tasks that agents execute.

## The shape

```
Linear issue (or a bare idea)
   │  mael linear plan PROJ-123
   ▼
Planning session — plan mode, in a cmux workspace
   │  research → discuss → write a plan file
   ▼
mael task status done  →  mael task load-many <plan> --run
   ▼
Execute session(s) — auto mode, own worktree
```

The plan file is not a document you then act on by hand. **Its blocks *are* the chain.**

## Starting a plan

From a Linear issue:

```bash
mael linear plan PROJ-123           # creates the task and launches it
mael linear plan PROJ-123 --no-run  # create only
```

`mael linear plan` is a thin wrapper over `mael task add`. It fetches the issue brief, makes
a `plan-task` task holding the brief as content, parents it under `linear.PROJ-123`, and
launches a plan-mode session. The planning defaults — `plan-task`, plan mode, the `opus`
model, the `linear.<ID>` parent, `linear.planned` post-action — are all overridable with the
matching flag.

Without a Linear issue, the same thing directly:

```bash
mael task add "Rework the port allocator" --run
```

New tasks default to plan mode, so a bare `--run` opens a planning session. Its chain
parents under the planning task's own id.

## Inside the planning session

The `plan-task` skill runs there. It:

1. Confirms it is in plan mode.
2. Researches the codebase with Explore sub-agents.
3. Classifies the work as single-session or multi-session.
4. Refines the plan with you interactively.
5. Writes the plan file and presents it with ExitPlanMode.

The brief is already in the session's opening prompt. You do not paste it.

Approving the plan runs the hand-off commands. The planning session **does not implement** —
a separate session owns the work.

## Single-session vs multi-session

**Single-session** — up to about 1500 lines of new code, landing as several ~500-line
commits. One execute block:

```markdown
---CREATE TASK iter---
title: "Execute: PROJ-123 — add avatar upload"
mode: auto
pre-action: linear.in-progress
follow-end: "*"
---
# PROJ-123: Avatar upload

## Context
...
## Implementation steps
...
## Verification
...
```

**Multi-session** — larger, or a mechanical transformation that should land first. One
concrete execute block plus a `plan-next-step` tail carrying the remaining work:

```markdown
---CREATE TASK iter1---
title: "Execute: avatar upload — iteration 1"
mode: auto
pre-action: linear.in-progress
follow-end: "*"
---
# PROJ-123: Avatar upload — Iteration 1

## Overall goal
## Architecture & design
## Iteration 1 scope
## Verification

---CREATE TASK tail---
title: Plan next step
command: plan-next-step
mode: plan
model: opus
follow: iter1
---
## Remaining work
- ...
## What should already be done
- ...
```

The `tail` block must carry a real remaining-work picture in its body. An empty placeholder
gives the next planner nothing to work from.

## The rules that make it work

### Leave `branch:` unset

Tasks inherit their parent's branch. That default keeps every iteration on **one branch**,
so `create-pr` appends to the pull request already open on it instead of opening another.
The task then merges **as a whole, once every iteration is complete**.

Setting `branch:` opts a task out into its own branch, worktree and PR. That is for
genuinely unrelated work — not for splitting one task's iterations, which forks a second
worktree and PR mid-task and gives up the accumulating PR.

### Set `mode:` on every block

New tasks default to plan mode. An execute block that omits `mode: auto` would **re-plan**
instead of running its plan.

- `mode: auto` on every execute block.
- `mode: plan` on a `plan-next-step` tail block.

### Close the planning task before `load-many --run`

```bash
mael linear set-status PROJ-123 planned   # mirror to Linear
mael task status done                     # close this planning task FIRST
mael task load-many <plan-file> --run     # create the chain, launch the head
```

The head block's `follow-end: "*"` makes it follow the planning task. While that task is
`in-progress` the head is blocked and `--run` launches **nothing** — silently, exiting 0.

The SessionEnd hook closes the planning task too, but it fires *after* `load-many` has run.
It is not a backstop for this ordering.

### Chain with `follow-end` and `follow`

- `follow-end: "*"` on the **head** block — append after the current leaf of the parent's
  child-chain, so the plan queues behind work already chained under the issue. Always quote
  the `*`.
- `follow: <block-name>` — intra-file ordering. A block runs only after the named block in
  the same file.

Blocks omit `parent:`; it defaults to `$MAEL_TASK_PARENT`.

### Set `model:` on the tail, not on execute blocks

Pin the tail block to the model the planning session is running, so every planner in the
chain stays on one model. Planning is where the leverage is. Leave `model:` unset on execute
blocks so they inherit your default.

### Lifecycle actions mirror to Linear

- `pre-action: linear.in-progress` on execute blocks — fires when the task launches.
- **Do not set `post-action: linear.done` on execute steps.** The finishing sequence closes
  the task at PR push, before the CI watch, so a post-action would flip Linear to Unreleased
  while CI is still running — overwriting the "In Review" that `create-pr` just set. Move
  the issue on deliberately with `mael linear set-status <ID> done` once the work has landed.

## Slice vertically

Each iteration must be a **thin vertical slice**: an end-to-end cut through every layer it
touches, shipping its own tests and delivering behaviour a user can see.

Layer-shaped iterations — "the back-end API", "then the front end", "then the e2e tests" —
are an **antipattern**. A plan whose iterations are named after layers has been sliced the
wrong way. Re-cut it.

Each iteration should:

- Ship its tests with the slice they cover. No test-only iterations.
- Be up to about 1500 lines of new code.
- Be independently testable and pass CI when merged.
- Not break existing behaviour.
- Land on the shared branch.
- Be ordered by dependency.

**Aim for three iterations or fewer.** More than that usually means the slices are too thin
or sliced by layer. If it genuinely needs more, the planner stops and asks whether to split
the issue instead.

The one narrow exception: an enabling refactor that is *genuinely* mechanical and cannot be
folded into the slice that needs it. That does not license splitting feature work by layer.

## Continuing a multi-session chain

```bash
mael task next --run
```

When this reaches a `plan-next-step` task, the `plan-next-step` skill runs. It holds the
running plan-of-record in its prompt — the remaining-work list plus a summary of what should
already be done. It:

1. Reconciles what the summary claims against what the repository shows.
2. Plans **one** concrete next step, biased toward finishing.
3. Re-cuts a layer-shaped tail vertically rather than reproducing it faithfully.
4. Decides whether this step is the last one. If so, its plan file has no `tail` block.
5. Writes a new plan file and hands the refreshed tail to the next planner.

Then the same two commands, in the same order: close, then load.

## Auditing an existing project

`/review-project-hygiene` is a second way into a chain. It does not start from a brief. It
reads a project that already exists — usually after its first MVP — and turns the gaps it
finds into a plan file, in the same load-many form as above.

```bash
mael task add "Hygiene audit: forecastel" --run
```

Then run `/review-project-hygiene` in the planning session.

The skill audits tooling, CI/CD, tests and specs, docs, agent config, security and
dependencies, dead code, and architecture fences. It looks
hardest for **gates that report success but cannot fail** — a CI step named "Lint" that runs
the write variant of a formatter, a deploy that never checks whether the build passed, a
dead-code config no job invokes. An absent gate is visible. A broken one is not.

Two things it deliberately does not do:

- **It never edits the project.** Every agreed fix becomes a task. The audit reads and
  reports; the chain it emits does the work.
- **It checks that code conventions are *documented*, not whether the code follows them.**
  Reviewing code against a standard is [`/code-review`](pull-requests.md)'s job.

### The table gate

The audit stops in the middle and shows you a table — one row per check, with its state, a
recommendation, and an S/M/L effort:

| Check | State | Recommendation | Effort |
|---|---|---|---|
| Lint gate | ✗ runs `npm run format` (writes, always exits 0) | switch to `format:check` | S |
| Dead code | ✗ none configured | add knip + CI gate | M |
| Arch fence | n/a — 2 crates, below threshold | none | — |

**The plan file is written after you confirm this table, from the confirmed rows only.** A row
you decline is dropped, and nothing that was not in the table reaches the plan.

The `n/a` rows are there on purpose. Showing that a check was considered and declined is what
makes the table reviewable, and it surfaces a wrong threshold before it becomes a task. If the
audit declines a check you wanted, or recommends one that makes no sense for the project, say
so — that is a checklist bug worth fixing.

There are two review points, not one: the table agrees the scope, and ExitPlanMode then
approves the chain that implements it.

The confirmed rows become one execute block per theme, chained with `follow`, with `branch:`
unset — so the whole audit lands as one pull request.

## Linear stays a mirror

The plan of record lives in the notebook chain, not in a Linear description. The planning
skill mirrors *status* with `set-status … planned`, but never writes the plan body back to
Linear.

`mael linear write-plan` / `read-plan` / `edit-plan` still exist for when you do want a plan
in the issue description.

## See also

- [Tasks](tasks.md) — parent, follows, load-many.
- [Pull requests](pull-requests.md) — how an execute session finishes.
