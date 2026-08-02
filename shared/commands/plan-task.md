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
blocks *are* the notebook chain — after approval, run two commands **in this order**:
`mael task status done` (close this planning task) **then** `mael task load-many … --run` (create the
chain **and** auto-launch its head execute task in a separate session). The order matters — see
**Command Logic** step 6. The head execute work runs in that new session — you must **not** implement
it yourself.

## What This Command Does

1. **Ensure plan mode** — switch via `EnterPlanMode` if the session isn't already in it.
2. **Research the codebase** with Explore subagents.
3. **Classify** single-session vs multi-session.
4. **Refine** the plan interactively with the user.
5. **Write** the load-many plan file (preamble + `---CREATE TASK ...---` blocks), then present it
   with ExitPlanMode. Approving it marks this task done and then runs
   `mael task load-many <plan-file> --run` (which also auto-launches the head execute task in a
   separate session). Do **not** implement — the new session owns the work.

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
   - **Single-session**: up to ~1500 lines of new code — a session's worth, landing as several
     ~500-line commits; completable in one session.
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
   The plan file you wrote *is* the chain: approving it runs the three post-approval commands, **in
   this order** —
   ```bash
   mael linear set-status <ID> planned      # mirror the plan to Linear (no plan body written)
   mael task status done                    # close this planning task ($MAEL_TASK_ID) FIRST
   mael task load-many <plan-file> --run    # create every block's task, then launch the head
   ```
   **Order matters.** `--run` only launches tasks that are *actionable*, and the head block's
   `follow-end: "*"` makes it follow this planning task. While this task is `in-progress` the head is
   blocked and `--run` launches nothing — silently, exiting 0. Closing this task first puts it in
   `done/`, satisfying that dependency so the head launches. The SessionEnd hook also closes this
   task when the session ends, but that is **too late** to unblock the head — it fires after
   `load-many` has already run. Run `mael task status done` explicitly, before `load-many`; the hook
   is not an adequate backstop for this ordering.

   `--run` then auto-launches the head execute task (the first created block) in a **separate** claude
   session as soon as the chain is created. That session owns the implementation — you must **not**
   write code yourself. `load-many --run` prints a line naming the launched task; the head runs
   independently from there.
   `<plan-file>` is a placeholder — substitute the **actual path you wrote the plan file to** (the
   path from system context). There is no plan-file env var; the only source of the path is the file
   you just created. Run `mael task load-many <that-literal-path>`, not `mael task load-many <plan-file>`.
   Each execute block's task has an empty `command` and `mode: auto`, so it's a plain unattended
   execute that runs **no skill** (not a re-plan) and finishes via the project's always-on "Finishing a task" rule
   (commit → `/code-review` → fixups → `create-pr --squash` → `task status done` → `/watch-pr`).
   **Do NOT implement** — do not write code, edit source files, or create branches;
   the head execute session is launched automatically by `--run`, and subsequent increments still
   advance via `mael task next --run`.

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
`model`, `priority`, `parent`, `branch`, `pre-action`, `post-action`, `follow`, `follow-end`. A block
ends at the next open marker or EOF — so back-to-back blocks need no explicit terminator.

`model` pins the session's LLM (`claude --model`) — an alias like `opus` or a full id. Leave it unset
on **execute** blocks so they inherit the user's default; set it on a `plan-next-step` tail block (see
below).

**Leave `branch:` unset on every block.** Tasks inherit their parent's branch, and that default is
what keeps all iterations of the plan on **one branch** — so the whole task accumulates into a
**single PR** rather than N separate ones. Each iteration's `create-pr` appends to the PR already
open on that branch instead of opening another. The task can then be merged **as a whole, once
every iteration is complete**, rather than iteration-by-iteration as each lands.

Setting `branch:` opts a task out: it gets its own branch, worktree, and PR. That is for genuinely
unrelated work — **not** for splitting the iterations of one task, which forks a second worktree and
PR mid-task and gives up the single accumulating PR.

**Lifecycle actions** (`pre-action` / `post-action`) fire a Linear/Sentry status change when the
task starts / finishes, against the `linear.<ID>` parent. Use them so the chain mirrors itself to
Linear automatically — no manual `set-status`:
- `pre-action: linear.in-progress` — fired when the task is launched.

**Do not set `post-action: linear.done` on execute steps.** The finishing sequence now closes the
task at PR push, before `/watch-pr`, so a `post-action` would flip the Linear issue to Unreleased
while CI is still running — overwriting the "In Review" that `create-pr` just set. Leave the issue
in In Review and move it on deliberately with `mael linear set-status <ID> done` once the work has
actually landed.

The planning task already carries `post-action: linear.planned` (seeded by `mael linear plan`), so
finishing planning flips Linear to Planned and launching each execute step flips it to In Progress.
The issue then stays In Review (set by `create-pr`) until someone moves it on explicitly.

**Mode markers are required on every block.** New tasks default to *plan* mode, so an Execute block
that omits `mode:` would wrongly re-plan instead of running its plan. Always set:
- `mode: auto` on every **execute** block (`iter` / `iter1`) — it runs the plan as-is unattended
  (Claude's classifier-vetted auto permission mode), no skill.
- `mode: plan` on the **`plan-next-step`** tail block — the next increment is planned afresh. Add an optional `---END TASK <name>---` only when prose for the human reviewer
follows a block (it stops that prose leaking into the block's body).

**Set `model:` on the tail block** to the model *this* session is running — read it from your system
prompt ("You are powered by the model named …") and write that literal alias (e.g. `model: opus`),
not an env var. Planning is where the leverage is, so every planner in the chain stays on one model.
Leave `model:` unset on execute blocks: they inherit the user's default.

**Parent + chaining.** `load-many` defaults each block's `parent` to `$MAEL_TASK_PARENT`
(`linear.<ID>`), so blocks omit `parent:` and nest under the Linear issue automatically. Chain with:
- `follow-end: "*"` — "append me after the end of my parent's existing child-chain" (the current leaf
  of the siblings under `linear.<ID>`). Use this on the **head** block so the plan queues behind any
  work already chained under the issue — always quote it: `follow-end: "*"`. Unquoted `*` (YAML
  alias) and escaped `"\*"` (bad escape) both fail to parse.
- `follow: <block-name>` — intra-file ordering: a block runs only after the named block in this same
  file.

### Single-Session Plan

For tasks completable in one session (up to ~1500 lines of new code — a session's worth, landing as
several ~500-line commits) — one execute block whose body is the full implementation plan:

```markdown
This plan creates the notebook chain for <ID>. To execute this plan, run these
commands instead of implementing anything below — then stop:
    mael task status done                   # close this planning task first
    mael task load-many <this file> --run   # create the chain, launch the head task

---CREATE TASK iter---
title: "Execute: <ID> — <short desc>"
mode: auto
pre-action: linear.in-progress
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
This plan creates the notebook chain for <ID>. To execute this plan, run these
commands instead of implementing anything below — then stop:
    mael task status done                   # close this planning task first
    mael task load-many <this file> --run   # create the chain, launch the head task

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
Concrete scope for this first execute session — a thin **vertical** slice cutting through every
layer it touches, shipping its own tests and delivering user-visible behaviour, sized at up to
~1500 lines across several ~500-line commits. Not a layer ("the back end", "the tests").
- ...

## Verification
How to test this iteration.

---CREATE TASK tail---
title: Plan next step
command: plan-next-step
mode: plan
model: opus
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
- Be a **thin vertical slice**: an end-to-end cut through every layer it touches (back end, front
  end, and its own tests), delivering user-visible behaviour. Layer-shaped iterations — "the back-end
  API", "the front end", "the e2e tests" — are an **antipattern**; a plan whose iterations are named
  after layers has been sliced the wrong way, so re-cut it.
- **Ship its tests with the slice they cover.** No test-only iterations: the tests for a slice belong
  in that slice's session.
- Be sized at up to ~1500 lines of new code — one execute session, landing as several ~500-line
  commits.
- Be independently testable and pass CI when merged.
- Not break existing functionality.
- Land on the plan's **shared branch** — leave `branch:` unset so every iteration continues on the
  same branch, appending to one accumulating PR that can be merged as a whole once every iteration
  is done.
- Be ordered by dependency (later iterations can depend on earlier ones).
- For mechanical transformations: describe the mechanism clearly and note where test/type coverage
  gives confidence.
  - The same applies to an enabling refactor that is *genuinely* mechanical and cannot be folded
    into the slice that needs it. This is a narrow exception to the vertical-slice rule above — it
    does not license splitting feature work by layer.

**Aim for ≤3 iterations per task.** If the breakdown seems to need more, that usually means the
slices are too thin or sliced by layer — re-cut them vertically first. If it genuinely still needs
more than three, **stop and ask the user** (AskUserQuestion) whether to split the issue into
separate tasks or approve a longer chain — rather than silently emitting one.

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
- The head execute session is launched automatically by `mael task load-many … --run`; advance the
  chain beyond it with `mael task next --run`.
