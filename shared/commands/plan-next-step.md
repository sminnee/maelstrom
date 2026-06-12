# Plan Next Step Command

⚠️ **PLAN MODE REQUIRED**: This command only works in plan mode. The launched session normally
starts there already; if it didn't, call `EnterPlanMode` to switch (the user approves the switch)
before doing anything else — don't hard-fail.

This skill runs **inside a session that `mael` launched** — it is the fuzzy-tail planner of a
multi-session notebook chain (spec B). `mael task next --run` reached a `plan-next-step` task and
launched a plan-mode session holding that task's content. This skill plans **one** concrete next
step and writes a **load-many plan file** whose blocks *are* the next chain: an execute block for
this step and — if work remains — a `tail` `plan-next-step` block with a refreshed picture. The only
post-approval action is one `mael task load-many` call. It does **not** implement, and it never
writes to Linear.

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
     execute session (~500 lines or less), plan to finish ALL of it. Each step must leave less work
     than it found.
   - Use AskUserQuestion to confirm scope if the boundary is unclear.
   - **Decide: is this the final step?** After scoping, judge whether this step exhausts the
     remaining-work list. That decision picks the plan template (final = no `tail`).
   - Write a **load-many plan file** (e.g. `next.md`) using the matching template in
     **Plan templates** below.

   Then present the plan with ExitPlanMode as usual, with
   `allowedPrompts: [{"tool": "Bash", "prompt": "mael task load-many"}]`. The plan file *is* the
   chain: approving it runs `mael task load-many <next.md>` to create the tasks, then
   `mael task status done` closes this planning task. The execute block's task runs **no skill** and
   finishes via the project's always-on "Finishing a task" rule. **Do NOT implement** — do not write
   code, edit source files, or create branches; the next increment runs via `mael task next --run`.

## Plan templates

Pick by the final-step decision in step 3.

Both blocks nest under the parent automatically — `mael task load-many` defaults each block's
`parent` to `$MAEL_TASK_PARENT` (`linear.<ID>`), so you don't spell it out. Chaining is expressed by:
- `follow-end: "*"` on the **head** block — "append me after the end of my parent's existing
  child-chain" (the current leaf of the sibling chain under `linear.<ID>`) — always quote it:
  `follow-end: "*"`. Unquoted `*` (YAML alias) and escaped `"\*"` (bad escape) both fail to parse.
- `follow: <block-name>` on later blocks — intra-file ordering by block name.

Set `mode:` on every block: `mode: normal` on the **execute** (`step`) block so it runs the plan
instead of re-planning, and `mode: plan` on the **`tail`** block so the next `plan-next-step` session
opens in plan mode. New tasks default to plan mode, so the execute block's `mode: normal` is required.

### More work remains — execute block + `tail`

The `tail` block re-queues `plan-next-step` with the **updated** plan-of-record in its body: the
remaining-work list with **this step removed** (course-corrected from what you learned), plus a
prior-work summary that now includes this step's scope.

```markdown
This step's chain. The only action is:
    mael task load-many <this file>

---CREATE TASK step---
title: "Execute: <next step desc>"
mode: normal
follow-end: "*"
---
<this step's detailed plan…>

---CREATE TASK tail---
title: Plan next step
command: plan-next-step
mode: plan
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
This step's chain. The only action is:
    mael task load-many <this file>

---CREATE TASK step---
title: "Execute: <final step desc>"
mode: normal
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
parent). `mael task status done` with no id closes **this** task — it falls back to `$MAEL_TASK_ID`
— so you never need to pass your own id. Block `parent` likewise defaults to `$MAEL_TASK_PARENT`, so
blocks can omit it and chain with `follow-end: "*"` (append after siblings) / `follow: <block>`.

## Implementation Notes

- **Plan mode required**: the `tail` block sets `mode: plan`, so a `plan-next-step` session launches
  in plan mode already; if it didn't, switch via `EnterPlanMode` rather than failing.
- **One step per session**: plan exactly one increment; let the chain carry the rest.
- **No Linear writes**: never write the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.
