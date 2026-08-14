# Plan Next Step Command

**Load the `planning` skill first** — it carries the draft-file mechanics this command relies
on: create drafts early, `richview` each one, sculpt, then promote on approval.

This skill runs **inside a session that `mael` launched** — it is the fuzzy-tail planner of a
multi-session notebook chain. `mael task next --run` reached a `plan-next-step` task and
launched this session holding that task's content. This skill plans **one** concrete next step
and builds it as **draft task files**: an execute draft for this step and — if work remains — a
tail draft that re-queues `plan-next-step` with a refreshed picture. On approval you promote
the drafts and launch the head. It does **not** implement — the launched session owns the step
— and it never writes to Linear.

## What you already hold

Your initial prompt **is** this task's content: the running plan-of-record, which is
- a **bullet-point list of remaining work** (the tail beyond what's already been done), and
- a **summary of what should already have been done** by now (prior iterations' scope plus the
  overall goal / architecture context).

You open already holding this — you do not reconstruct it from scratch. You confirm it against
reality, plan the top item, and hand the next planner an updated tail.

## Command Logic

1. **Reconcile intended vs actual**: Read the remaining-work list and prior-work summary from
   your prompt, then research the current state to confirm what has actually landed:
   ```bash
   git log --all --grep='<ID>' --oneline   # previous commits for this chain
   mael git status
   git diff origin/main                     # changes already made
   ```
   Inspect the relevant files. Reconcile what the summary *says* should be done against what
   the repo *shows* is done. The `<ID>` is the Linear identifier — it's in `$MAEL_TASK_PARENT`
   (`linear.<ID>`) and in your prompt's prior-work summary.

2. **Plan one concrete step**: Take the **top** item from the remaining-work list and plan it
   in detail — a single, mergeable, independently-testable increment.
   - **Strong bias toward finishing**: if the remaining work is small enough to complete in
     one execute session (up to ~1500 lines of new code — a session's worth, landing as
     several ~500-line commits), plan to finish ALL of it. Each step must leave less work than
     it found.
   - **Re-cut a layer-shaped tail.** The remaining-work list you inherited may itself be
     sliced by layer ("the front end", "the e2e tests") — an older plan, or a planner that
     sliced wrong. Do not faithfully reproduce that. Re-cut the remaining work into thin
     **vertical** slices (each an end-to-end cut through every layer it touches, shipping its
     own tests) and plan the top one, handing the re-cut list to the next planner in the tail
     draft's body.
   - **Name the seams the step is tested at**, under a `## Seams under test` heading in the
     execute draft's body. The execute session builds test-first (`/tdd`), which bars testing
     at a seam nobody agreed; it runs `mode: auto` with no one to ask, so agree them here,
     with the user's approval of the draft. Name the public boundary each test observes
     behaviour through, not the internals behind it. Use `/codebase-design` for the vocabulary
     when the boundary itself is the open question.
   - Use AskUserQuestion to confirm scope if the boundary is unclear.
   - **Decide: is this the final step?** After scoping, judge whether this step exhausts the
     remaining-work list. That decision picks the draft set (final = no tail draft).

3. **Create the drafts early**: as soon as the step's shape emerges, create the draft files in
   the worktree cwd:

   ```bash
   # The execute step (always):
   mael task draft draft-step.md "Execute: <next step desc>" --mode auto --pre-action linear.in-progress

   # Only when work remains beyond this step — the refreshed tail:
   mael task draft draft-tail.md "Plan next step" --command plan-next-step --mode normal --model opus
   ```

   Run `richview <file>` the moment each draft exists — it live-updates, so every later edit
   shows up by itself. Sculpt the bodies with the user; see **Draft bodies** below.

4. **Promote on approval**: once the user approves the drafts, run — in this order:

   ```bash
   mael task promote draft-step.md --follow-end '*'    # creates the step; echoes its id
   mael task promote draft-tail.md --follow <id1>      # only if a tail exists; <id1> = the echoed step id
   mael task status done                           # close this planning task ($MAEL_TASK_ID)
   mael task next --run --parent "$MAEL_TASK_PARENT"   # step now actionable — launches it
   mael session end                                # stop this planning session
   ```

   `--follow-end '*'` appends the step after the leaves of the parent's existing child-chain —
   which includes **this planning task**, still in-progress. So `mael task status done` must
   run before `mael task next --run`: closing this task is what makes the step actionable.
   `--parent` scopes the launch to this chain, so an unrelated actionable task elsewhere in the
   project cannot be picked instead. `next --run` prints the id it launches — confirm it is the
   step you promoted. When nothing in the chain is actionable it exits non-zero with "No
   actionable task." — a follow is still unmet, so inspect with `mael task list`.

   The launched session owns the implementation — you must **not** write code, edit source
   files, or create branches. Promotion consumes each draft file. If the user rejects the plan
   instead, delete the draft files. No Linear step here — this skill never writes to Linear.

   `mael session end` runs **last, and without asking** — see **End the session when the plan is
   launched** in the `planning` skill.

## Draft recipes

- `--mode auto` on the **execute** (`step`) draft — it runs the plan unattended (Claude's
  classifier-vetted auto permission mode) instead of re-planning. Its `command` stays empty:
  it runs **no skill** and finishes via the project's always-on "Finishing a task" rule.
- `--mode normal` on the **tail** draft — the next `plan-next-step` session plans in a
  normal-permission session that writes its own drafts.
- **Set `--model` on the tail draft** to the model *this* session is running — read it from
  your system prompt ("You are powered by the model named …") and write that literal alias
  (e.g. `opus`), not an env var, so the task file stays self-describing. This keeps every
  planner in the chain on one model. Leave `--model` unset on the execute draft — it inherits
  the user's Claude Code default.
- **Leave `branch:` unset on both drafts.** Tasks inherit their parent's branch, so every step
  continues on the **same branch** as the steps before it and `create-pr` appends to the PR
  already open there rather than opening another. That keeps the whole task in one
  accumulating PR, which can be merged **as a whole once every step is complete**, rather than
  step-by-step as each lands. Setting `branch:` forks a new worktree and a separate PR
  mid-task and gives that up.
- Put lifecycle actions on the **execute** draft so each step mirrors itself to Linear. Set
  `--pre-action linear.in-progress` (fired on launch) on every step, whether or not a tail
  follows.
- **Do not set `post-action: linear.done`** — not even on the final step. The finishing
  sequence closes the task at PR push, before `/watch-pr`, so a `post-action` would flip the
  Linear issue to Unreleased while CI is still running, overwriting the "In Review" that
  `create-pr` just set. Leave the issue in In Review and move it on deliberately with
  `mael linear set-status <ID> done` once the work has actually landed.

## Draft bodies

The execute draft's `## Content` section is this step's detailed plan — the execute session
reads it and implements directly. Include the `## Seams under test` heading.

The tail draft's body is the **updated** plan-of-record: the remaining-work list with **this
step removed** (course-corrected from what you learned), plus a prior-work summary that now
includes this step's scope. It must not be an empty placeholder:

```markdown
## Remaining work
<remaining-work list with this step removed…>

## What should already be done
<updated prior-work summary including this step…>
```

When this step exhausts the remaining work, emit **just** the execute draft — no tail, so the
chain ends here. Once its execute session merges, the feature is done.

## How the rolling state travels

Each `plan-next-step` task hands the next one a refreshed tail body — "what's left" shrinks
and "what's done" grows as the chain advances. Linear stays a product-level mirror only.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the
`linear.<ID>` parent — or, for an ad-hoc chain with no Linear issue, the original planning
task's own id). `mael task status done` with no id closes **this** task — it falls back to
`$MAEL_TASK_ID` — so you never need to pass your own id. `promote` likewise defaults
`--parent` to `$MAEL_TASK_PARENT`, so the drafts nest under the same parent and
`--follow-end '*'` / `--follow <id>` chain identically whether the parent is a Linear id or a
planning task's own id.

## Implementation Notes

- **This session plans** — it writes draft task files and nothing else: no project source
  edits, no branches, no implementation (the `planning` skill's discipline).
- **One step per session**: plan exactly one increment; let the chain carry the rest. One
  step, not one *small* step — the increment should be a substantial vertical slice (sized up
  to ~1500 lines, several ~500-line commits), not a layer or a sliver. Where the remaining
  work is smaller than that, the bias toward finishing wins — don't pad a step to reach the
  number.
- **No Linear writes**: never write the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.
