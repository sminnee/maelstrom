# Plan Next Step Command

⚠️ **PLAN MODE REQUIRED**: This command only works in plan mode. The launched session normally
starts there already; if it didn't, call `EnterPlanMode` to switch (the user approves the switch)
before doing anything else — don't hard-fail.

This skill runs **inside a session that `mael` launched** — it is the fuzzy-tail planner of a
multi-session notebook chain (spec B). `mael task next --run` reached a `plan-next-step` task and
launched a plan-mode session holding that task's content. This skill plans **one** concrete next
step and writes a **load-many plan file** whose blocks *are* the next chain: an execute block for
this step and — if work remains — a `tail` `plan-next-step` block with a refreshed picture. After
approval, run two commands **in this order**: `mael task status done` (close this planning task)
**then** `mael task load-many … --run` (create the chain **and** auto-launch every unblocked block —
here just the head execute task, since the `tail` follows it — in a separate session). The order
matters — see **Command Logic** step 3.
It does **not** implement — the launched session owns the step — and it never writes to Linear.

## What you already hold

Your initial prompt **is** this task's content: the running plan-of-record, which is
- a **bullet-point list of remaining work** (the tail beyond what's already been done), and
- a **summary of what should already have been done** by now (prior iterations' scope plus the
  overall goal / architecture context).

You open already holding this — you do not reconstruct it from scratch. You confirm it against
reality, plan the top item, and hand the next planner an updated tail.

## Command Logic

1. **Ensure plan mode**: detect via `Plan mode is active` in system-reminder tags. If it's already
   active, proceed; if not, call `EnterPlanMode` to switch (the user approves the switch) — only if
   that's declined should you stop.

2. **Reconcile intended vs actual**: Read the remaining-work list and prior-work summary from your
   prompt, then research the current state to confirm what has actually landed:
   ```bash
   git log --all --grep='<ID>' --oneline   # previous commits for this chain
   mael git status
   git diff origin/main                     # changes already made
   ```
   Inspect the relevant files. Reconcile what the summary *says* should be done against what the repo
   *shows* is done. The `<ID>` is the Linear identifier — it's in `$MAEL_TASK_PARENT`
   (`linear.<ID>`) and in your prompt's prior-work summary.

3. **Plan one concrete step**: Take the **top** item from the remaining-work list and plan it in
   detail — a single, mergeable, independently-testable increment.
   - **Strong bias toward finishing**: if the remaining work is small enough to complete in one
     execute session (up to ~1500 lines of new code — a session's worth, landing as several
     ~500-line commits), plan to finish ALL of it. Each step must leave less work than it found.
   - **Re-cut a layer-shaped tail.** The remaining-work list you inherited may itself be sliced by
     layer ("the front end", "the e2e tests") — an older plan file, or a planner that sliced wrong.
     Do not faithfully reproduce that. Re-cut the remaining work into thin **vertical** slices (each
     an end-to-end cut through every layer it touches, shipping its own tests) and plan the top one,
     handing the re-cut list to the next planner in the `tail` body.
   - **Name the seams the step is tested at**, under a `## Seams under test` heading in the execute
     block. The execute session builds test-first (`/tdd`), which bars testing at a seam nobody
     agreed; it runs `mode: auto` with no one to ask, so agree them here under plan approval. Name
     the public boundary each test observes behaviour through, not the internals behind it. Use
     `/codebase-design` for the vocabulary when the boundary itself is the open question.
   - Use AskUserQuestion to confirm scope if the boundary is unclear.
   - **Decide: is this the final step?** After scoping, judge whether this step exhausts the
     remaining-work list. That decision picks the plan template (final = no `tail`).
   - Write a **load-many plan file** (e.g. `next.md`) using the matching template in
     **Plan templates** below.

   Then present the plan with ExitPlanMode as usual, with
   `allowedPrompts: [{"tool": "Bash", "prompt": "mael task load-many"}, {"tool": "Bash", "prompt": "mael task status done"}]`.
   The plan file *is* the chain: approving it first runs `mael task status done` to close this
   planning task, **then** `mael task load-many <plan-file> --run` to create the tasks **and**
   auto-launch every unblocked block — for this chain shape that is the head execute step alone,
   since the `tail` follows it — in a separate session.

   **Order matters.** `--run` only launches tasks that are *actionable*, and the head block's
   `follow-end: "*"` makes it follow this planning task. While this task is `in-progress` the head is
   blocked and `--run` launches nothing — silently, exiting 0. Closing this task first puts it in
   `done/`, satisfying that dependency so the head launches. The SessionEnd hook closes the task too,
   but only once the session ends — after `load-many` has already run — so it cannot substitute for
   running `mael task status done` explicitly, in order.

   `<plan-file>` is a placeholder — substitute the **actual path you wrote the
   plan file to** (e.g. `next.md`). There is no plan-file env var; the only source of the path is the
   file you just created, so run `mael task load-many <that-literal-path> --run`. The execute block's
   task runs **no skill** and finishes via the project's always-on "Finishing a task" rule. **Do NOT
   implement** — do not write code, edit source files, or create branches; the head step is
   auto-launched by `--run`, and `mael task next --run` remains the way to advance the chain further.

## Plan templates

Pick by the final-step decision in step 3.

Both blocks nest under the parent automatically — `mael task load-many` defaults each block's
`parent` to `$MAEL_TASK_PARENT` (`linear.<ID>`), so you don't spell it out. Chaining is expressed by:
- `follow-end: "*"` on the **head** block — "append me after the end of my parent's existing
  child-chain" (the current leaf of the sibling chain under `linear.<ID>`) — always quote it:
  `follow-end: "*"`. Unquoted `*` (YAML alias) and escaped `"\*"` (bad escape) both fail to parse.
- `follow: <block-name>` on later blocks — intra-file ordering by block name.

Set `mode:` on every block: `mode: auto` on the **execute** (`step`) block so it runs the plan
unattended (Claude's classifier-vetted auto permission mode) instead of re-planning, and `mode: plan`
on the **`tail`** block so the next `plan-next-step` session opens in plan mode. New tasks default to
plan mode, so the execute block's `mode: auto` is required.

Set `model:` on the **`tail`** block to the model *this* session is running — read it from your
system prompt ("You are powered by the model named …") and write that literal alias (e.g.
`model: opus`), not an env var, so the task file stays self-describing. This keeps every planner in
the chain on one model. Leave `model:` unset on the execute (`step`) block — it inherits the user's
Claude Code default.

**Leave `branch:` unset on the `step` block** (and on the `tail`). Tasks inherit their parent's
branch, so every step continues on the **same branch** as the steps before it and `create-pr`
appends to the PR already open there rather than opening another. That keeps the whole task in one
accumulating PR, which can be merged **as a whole once every step is complete**, rather than
step-by-step as each lands. Setting `branch:` forks a new worktree and a separate PR mid-task and
gives that up.

Put lifecycle actions on the **execute** (`step`) block so each step mirrors itself to Linear. Set
`pre-action: linear.in-progress` (fired on launch) on every step, whether or not a `tail` follows.

**Do not set `post-action: linear.done`** — not even on the final step. The finishing sequence now
closes the task at PR push, before `/watch-pr`, so a `post-action` would flip the Linear issue to
Unreleased while CI is still running, overwriting the "In Review" that `create-pr` just set. Leave
the issue in In Review and move it on deliberately with `mael linear set-status <ID> done` once the
work has actually landed.

### More work remains — execute block + `tail`

The `tail` block re-queues `plan-next-step` with the **updated** plan-of-record in its body: the
remaining-work list with **this step removed** (course-corrected from what you learned), plus a
prior-work summary that now includes this step's scope.

```markdown
This step's chain. To execute this plan, run these commands instead of
implementing anything below — then stop:
    mael task status done                   # close this planning task first
    mael task load-many <this file> --run   # create the chain, launch the head step

---CREATE TASK step---
title: "Execute: <next step desc>"
mode: auto
pre-action: linear.in-progress
follow-end: "*"
---
<this step's detailed plan…>

---CREATE TASK tail---
title: Plan next step
command: plan-next-step
mode: plan
model: opus
follow: step
---
## Remaining work
<remaining-work list with this step removed…>

## What should already be done
<updated prior-work summary including this step…>
```

### Final step — execute block only

When this step exhausts the remaining work, emit **just** the execute block — no `tail`, so the
chain ends here. Once its execute session merges, the feature is done.

```markdown
This step's chain. To execute this plan, run these commands instead of
implementing anything below — then stop:
    mael task status done                   # close this planning task first
    mael task load-many <this file> --run   # create the chain, launch the head step

---CREATE TASK step---
title: "Execute: <final step desc>"
mode: auto
pre-action: linear.in-progress
follow-end: "*"
---
<this final step's detailed plan…>
```

## How the rolling state travels

Each `plan-next-step` task hands the next one a refreshed `tail` block body — "what's left" shrinks
and "what's done" grows as the chain advances. This replaces the old `## Remaining Work` /
`## Completed Iteration` headings that used to live in the Linear description. Linear stays a
product-level mirror only.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the `linear.<ID>`
parent — or, for an ad-hoc chain with no Linear issue, the original planning task's own id).
`mael task status done` with no id closes **this** task — it falls back to `$MAEL_TASK_ID`
— so you never need to pass your own id. Block `parent` likewise defaults to `$MAEL_TASK_PARENT`, so
blocks can omit it and chain with `follow-end: "*"` (append after siblings) / `follow: <block>`
identically whether the parent is a Linear id or a planning task's own id.

## Implementation Notes

- **Plan mode required**: the `tail` block sets `mode: plan`, so a `plan-next-step` session launches
  in plan mode already; if it didn't, switch via `EnterPlanMode` rather than failing.
- **One step per session**: plan exactly one increment; let the chain carry the rest. One step, not
  one *small* step — the increment should be a substantial vertical slice (sized up to ~1500 lines,
  several ~500-line commits), not a layer or a sliver. Where the remaining work is smaller than
  that, the bias toward finishing wins — don't pad a step to reach the number.
- **No Linear writes**: never write the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.
