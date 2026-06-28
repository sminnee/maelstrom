# Plan Task Command

⚠️ **PLAN MODE REQUIRED**: This command only works in plan mode. The launched session normally
starts there already; if it didn't, call `EnterPlanMode` to switch (the user approves the switch)
before doing anything else — don't hard-fail.

This skill runs **inside a session that `mael` launched** — it is not a command you type in a shell
you opened yourself. `mael linear plan <issue>` (or `mael task add … --command plan-task`) creates a
planning task and launches a plan-mode session holding the brief; this skill is the prompt that runs
there.

The brief is **already in your initial prompt** (the planning task's content). Your job is to
research, plan interactively, then write a **load-many plan file** whose `---CREATE TASK ...---`
blocks *are* the notebook chain — after approval, run two commands: `mael task load-many` (create the
chain) **then** `mael task status done` (close this planning task).

## What This Command Does

1. **Ensure plan mode** — switch via `EnterPlanMode` if the session isn't already in it.
2. **Research the codebase** with Explore subagents.
3. **Classify** single-session vs multi-session.
4. **Refine** the plan interactively with the user.
5. **Write** the load-many plan file (preamble + `---CREATE TASK ...---` blocks), then present it
   with ExitPlanMode. Approving it runs `mael task load-many <plan-file>` and marks this task done.
   Do **not** implement.

## Command Logic

1. **Ensure plan mode**: detect via `Plan mode is active` in system-reminder tags. If it's already
   active, proceed. If not, call `EnterPlanMode` to switch (the user approves the switch) — only if
   that's declined should you stop.

2. **Read the brief from the initial prompt**: The Linear brief (`# <ID>: <title>` + description) is
   already in your prompt. Treat it as the source of truth for *what* to build.

3. **Codebase Research**: Use the Task tool with Explore subagent(s):
   - Launch 1-3 Explore agents in parallel for efficient research.
   - Examine relevant files and subsystems mentioned in the brief.
   - Review existing patterns, dependencies, and integration points.
   - Understand current implementation state.

4. **Classify Session Type**:
   - **Single-session**: less than ~500 lines of new code; completable in one session.
   - **Multi-session**: larger scope, or a mechanical transformation whose mechanical piece should
     land first.
   - Use AskUserQuestion to confirm the classification with the user.

5. **Interactive Planning**: Use AskUserQuestion to discuss the plan:
   - Present your understanding based on research.
   - Discuss approach and trade-offs.
   - Iterate until the user is satisfied.

6. **Write the load-many plan file** (path provided in system context) in the marker format — see
   **Plan Structure** below for the templates: single-session = one `iter` execute block;
   multi-session = an `iter1` execute block plus a `tail` `plan-next-step` block.

   Then present the plan with ExitPlanMode as usual, with
   `allowedPrompts: [{"tool": "Bash", "prompt": "mael task load-many"}, {"tool": "Bash", "prompt": "mael task status done"}]`.
   The plan file you wrote *is* the chain: approving it runs the three post-approval commands —
   ```bash
   mael linear set-status <ID> planned      # mirror the plan to Linear (no plan body written)
   mael task load-many <plan-file>          # create every block's task in one atomic commit
   mael task status done                    # close this planning task ($MAEL_TASK_ID)
   ```
   `<plan-file>` is a placeholder — substitute the **actual path you wrote the plan file to** (the
   path from system context). There is no plan-file env var; the only source of the path is the file
   you just created. Run `mael task load-many <that-literal-path>`, not `mael task load-many <plan-file>`.
   (Ending the planning session also auto-closes the task via the SessionEnd hook, so this
   `mael task status done` is a no-op if the session ends first — but run it anyway so the task
   closes before any chained session continues.)
   Each execute block's task has an empty `command` and `mode: auto`, so it's a plain unattended
   execute that runs **no skill** (not a re-plan) and finishes via the project's always-on "Finishing a task" rule
   (commit → `/code-review` → fixups → `create-pr --squash` → `/watch-pr` → `task status done`). **Do NOT implement** — do not write code, edit source files, or create branches;
   implementation happens in a later session via `mael task next --run`.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the
`linear.<ID>` parent — or, for an ad-hoc plan with no Linear issue, this planning task's own id).
`mael task status done` with no id closes **this** task — it falls back to
`$MAEL_TASK_ID` — so you never need to pass your own id. Block `parent` likewise defaults to
`$MAEL_TASK_PARENT`, so blocks omit it and chain with `follow-end: "*"` (append after siblings) /
`follow: <block>` identically whether the parent is a Linear id or this planning task's own id;
the Linear `<ID>` is also in the brief in your prompt if you need it.

## Plan Structure

The plan file is a load-many file: a short preamble (ignored by `load-many`, for the human reviewer)
followed by `---CREATE TASK <name>---` blocks. Each block is `frontmatter` + `markdown body`; the
body becomes the created task's Content. Frontmatter keys: `title` (required), `command`, `mode`,
`parent`, `pre-action`, `post-action`, `follow`, `follow-end`. A block ends at the next open marker
or EOF — so back-to-back blocks need no explicit terminator.

**Lifecycle actions** (`pre-action` / `post-action`) fire a Linear/Sentry status change when the
task starts / finishes, against the `linear.<ID>` parent. Use them so the chain mirrors itself to
Linear automatically — no manual `set-status`:
- `pre-action: linear.in-progress` — fired when the task is launched.
- `post-action: linear.done` — fired when the session ends (task → done; Linear → Unreleased).

**`post-action: linear.done` belongs only on the LAST execute step** — it moves the Linear issue to
Unreleased, which is wrong while work remains:
- **Single-session plan** (one `iter` block, no `tail`): it *is* the last step, so set both
  `pre-action: linear.in-progress` **and** `post-action: linear.done`.
- **Multi-session plan** (an `iter1` block followed by a `plan-next-step` `tail`): `iter1` is **not**
  the last step, so set **only** `pre-action: linear.in-progress` — no `post-action`. The
  `plan-next-step` chain seeds `post-action: linear.done` on whichever step it decides is final.

The planning task already carries `post-action: linear.planned` (seeded by `mael linear plan`), so
finishing planning flips Linear to Planned and launching each execute step flips it to In Progress;
only the final step flips it to Unreleased.

**Mode markers are required on every block.** New tasks default to *plan* mode, so an Execute block
that omits `mode:` would wrongly re-plan instead of running its plan. Always set:
- `mode: auto` on every **execute** block (`iter` / `iter1`) — it runs the plan as-is unattended
  (Claude's classifier-vetted auto permission mode), no skill.
- `mode: plan` on the **`plan-next-step`** tail block — the next increment is planned afresh. Add an optional `---END TASK <name>---` only when prose for the human reviewer
follows a block (it stops that prose leaking into the block's body).

**Parent + chaining.** `load-many` defaults each block's `parent` to `$MAEL_TASK_PARENT`
(`linear.<ID>`), so blocks omit `parent:` and nest under the Linear issue automatically. Chain with:
- `follow-end: "*"` — "append me after the end of my parent's existing child-chain" (the current leaf
  of the siblings under `linear.<ID>`). Use this on the **head** block so the plan queues behind any
  work already chained under the issue — always quote it: `follow-end: "*"`. Unquoted `*` (YAML
  alias) and escaped `"\*"` (bad escape) both fail to parse.
- `follow: <block-name>` — intra-file ordering: a block runs only after the named block in this same
  file.

### Single-Session Plan

For tasks completable in one session (~500 lines or less) — one execute block whose body is the full
implementation plan:

```markdown
This plan creates the notebook chain for <ID>. After approval, run:
    mael task load-many <this file>   # create the chain
    mael task status done             # close this planning task

---CREATE TASK iter---
title: "Execute: <ID> — <short desc>"
mode: auto
pre-action: linear.in-progress
post-action: linear.done
follow-end: "*"
---
# <ID>: <Title>

## Context
Brief description of the problem and why this change is needed.

## Implementation Steps

### Step 1: <Description>
- Files to modify: ...
- Changes: ...

## Files to Modify
| File | Change |
|------|--------|
| ... | ... |

## Verification
- How to test the changes
- Expected outcomes
```

The block body becomes the **execute task's content**. The execute session (no skill) reads it and
implements directly.

### Multi-Session Plan

For larger tasks — a concrete `iter1` execute block plus a fuzzy-tail `plan-next-step` block. The
`iter1` block uses `follow-end: "*"` (append after existing siblings); the `tail` block uses
`follow: iter1` (run after iter1 in this file). The tail block carries the remaining-work picture in
its **body** — it must not be an empty placeholder:

```markdown
This plan creates the notebook chain for <ID>. After approval, run:
    mael task load-many <this file>   # create the chain
    mael task status done             # close this planning task

---CREATE TASK iter1---
title: "Execute: <iteration-1 desc>"
mode: auto
pre-action: linear.in-progress
follow-end: "*"
---
# <ID>: <Title> — Iteration 1

## Overall Goal
The full end state we're working toward.

## Architecture & Design
Detailed architectural changes across the whole task:
- Key design decisions and trade-offs
- New components/modules and their responsibilities
- Changes to existing interfaces or data flow

## Iteration 1 scope
Concrete scope for this first execute session.
- ...

## Verification
How to test this iteration.

---CREATE TASK tail---
title: Plan next step
command: plan-next-step
mode: plan
follow: iter1
---
## Remaining work
The fuzzy tail — everything beyond iteration 1 (bullet list).
- ...

## What should already be done
A summary of iteration-1 scope plus the overall goal / architecture context the next planner needs.
```

The chain replaces the old rolling `## Next Iteration` / `## Completed Iteration` machinery that
used to live in the Linear
description. Each iteration should:
- Be independently testable and pass CI when merged.
- Not break existing functionality.
- Not necessarily deliver end-user functionality (a back-end API before the front-end, or an enabling
  refactor, are valid iterations).
- Be ordered by dependency (later iterations can depend on earlier ones).
- For mechanical transformations: describe the mechanism clearly and note where test/type coverage
  gives confidence.

## Implementation Notes

- **Plan mode required**: switch via `EnterPlanMode` if the session isn't already in plan mode.
- **Research before planning**: codebase research happens before the plan.
- **Interactive refinement**: discuss with the user before finalising.
- **Chain loaded after ExitPlanMode acceptance**: `mael task load-many <plan-file>` runs only after
  the user approves the ExitPlanMode prompt.
- **No Linear plan body**: Linear is a product-level mirror only; the skill mirrors *status*
  (`set-status … planned`) but never writes the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.

## Integration with the notebook chain

- The execute task carries the plan as content; a plain execute runs **no skill** and finishes via
  the project's always-on "Finishing a task" rule.
- For multi-session work, `plan-next-step` (re)plans each subsequent increment, consuming the
  remaining-work tail this skill seeded and handing a refreshed tail to the next one.
- Advance the chain with `mael task next --run`.
