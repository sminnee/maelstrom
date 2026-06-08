# Plan Next Step Command

⚠️ **PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

This skill runs **inside a session that `mael` launched** — it is the fuzzy-tail planner of a
multi-session notebook chain (spec B). `mael task next --run` reached a `plan-next-step` task and
launched a plan-mode session holding that task's content. This skill plans **one** concrete next
step, emits an execute task for it, and — if work remains — re-queues another `plan-next-step` task
with a refreshed picture. It does **not** implement, and it never writes to Linear.

## What you already hold

Your initial prompt **is** this task's content: the running plan-of-record, which is
- a **bullet-point list of remaining work** (the tail beyond what's already been done), and
- a **summary of what should already have been done** by now (prior iterations' scope plus the
  overall goal / architecture context).

You open already holding this — you do not reconstruct it from scratch. You confirm it against
reality, plan the top item, and hand the next planner an updated tail.

## Command Logic

1. **MANDATORY Plan Mode Check**: MUST fail immediately if not in plan mode. (Detect via
   `Plan mode is active` in system-reminder tags.)

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

3. **Set status**:
   ```bash
   mael status set "Planning next step for <ID>"
   ```

4. **Plan one concrete step**: Take the **top** item from the remaining-work list and plan it in
   detail — a single, mergeable, independently-testable increment.
   - **Strong bias toward finishing**: if the remaining work is small enough to complete in one
     execute session (~500 lines or less), plan to finish ALL of it. Each step must leave less work
     than it found.
   - Use AskUserQuestion to confirm scope if the boundary is unclear.
   - Write this step's plan to a plan file (e.g. `next.md`).

5. **Present Plan**: Call ExitPlanMode with allowedPrompts:
   - `{"tool": "Bash", "prompt": "emit notebook chain tasks"}`

6. **After Plan Approval — emit and finish**:
   ```bash
   mael task add "Execute: <next>" --follow-end "linear.<ID>" --content-file <next.md>
   # only if more work remains after this step:
   mael task add "Plan next step" --command plan-next-step --follow-end "linear.<ID>" --content-file <tail.md>
   mael task status done
   ```
   - The execute task carries `<next.md>` (this step's plan) as content; it runs **no skill** and
     finishes via the project's always-on "Finishing a task" rule.
   - **Re-queue `plan-next-step` iff work remains.** `<tail.md>` is the **updated** plan-of-record:
     - the remaining-work list with **this step removed** (and course-corrected from what you
       learned), and
     - an **updated prior-work summary** that now includes this step's scope.
   - If this step finishes the task (nothing remains), do **not** re-queue `plan-next-step` — emit
     only the execute task and finish.

   **IMPORTANT: After emitting the tasks and marking this task done, your work is DONE.** Do NOT
   implement. Do NOT write code, edit source files, or create branches. Confirm and stop. The next
   increment runs via `mael task next --run`.

## How the rolling state travels

Each `plan-next-step` task hands the next one a refreshed `<tail.md>` — "what's left" shrinks and
"what's done" grows as the chain advances. This replaces the old `## Remaining Work` /
`## Completed Iteration` headings that used to live in the Linear description. Linear stays a
product-level mirror only.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the `linear.<ID>`
parent). Use `$MAEL_TASK_ID` to `mael task status done` yourself, and key `--follow-end` off the parent.

## Error Cases

- Not in plan mode: "Plan-next-step command requires plan mode. Please enter plan mode first."

## Implementation Notes

- **Plan mode required**: fail immediately if not in plan mode. (`--command plan-next-step` defaults
  the launched session to plan mode, so this should already hold.)
- **One step per session**: plan exactly one increment; let the chain carry the rest.
- **No Linear writes**: never write the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.
