---
name: plan-task
description: Turn a task brief into an agreed chain of draft task files, then promote them and launch the head. Runs inside a `mael`-launched planning session. Invoked as the `/plan-task` slash command.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
---

# Plan Task Command

**Load the `planning` skill first** — it carries the draft-file mechanics this command relies
on: create drafts early, `richview` each one, sculpt, then promote on approval.

This skill runs **inside a session that `mael` launched** — it is not a command you type in a
shell you opened yourself. `mael linear plan <issue>` (or `mael task add … --command plan-task`)
creates a planning task and launches this session holding the brief.

The brief is **already in your initial prompt** (the planning task's content). Your job is to
research, then build the notebook chain as **draft task files** sculpted with the user. On
approval you promote the drafts into the notebook and launch the head — you must **not**
implement the work yourself.

## What This Command Does

1. **Read the brief** from the initial prompt.
2. **Research the codebase** with Explore subagents.
3. **Classify** single-session vs multi-session.
4. **Draft early, sculpt interactively** — draft task files, previewed with `richview`,
   refined with the user.
5. **Promote on approval** — create the chain and launch its head in a separate session.

## Command Logic

1. **Read the brief from the initial prompt**: The Linear brief (`# <ID>: <title>` +
   description) is already in your prompt. Treat it as the source of truth for *what* to build.

2. **Codebase Research**: Use the Agent tool with Explore subagent(s):
   - Launch 1-3 Explore agents in parallel for efficient research.
   - Examine relevant files and subsystems mentioned in the brief.
   - Review existing patterns, dependencies, and integration points.
   - Understand current implementation state.

3. **Classify Session Type**:
   - **Single-session**: up to ~1500 lines of new code — a session's worth, landing as several
     ~500-line commits; completable in one session.
   - **Multi-session**: larger scope, or a mechanical transformation whose mechanical piece
     should land first.
   - Use AskUserQuestion to confirm the classification with the user.

4. **Create the drafts early**: as soon as the chain's shape emerges, create one draft per
   future task in the worktree cwd — don't hold the plan in conversation:

   ```bash
   # The head execute task (always):
   mael task draft draft-iter1.md "Execute: <ID> — <short desc>" --mode auto --pre-action linear.in-progress

   # Multi-session only — the fuzzy-tail planner:
   mael task draft draft-tail.md "Plan next step" --command plan-next-step --mode normal --model opus
   ```

   Run `richview <file>` the moment each draft exists — it live-updates, so every later edit
   shows up by itself.

5. **Sculpt with the user**: edit the draft bodies directly as the plan firms up — see
   **Draft bodies** below for what each Content section carries. Discuss approach and
   trade-offs (AskUserQuestion where a decision is genuinely the user's); iterate until the
   user is satisfied. The drafts are inert while you sculpt — nothing runs until promotion.

6. **Promote on approval**: once the user approves the drafts, run — in this order:

   ```bash
   mael linear set-status <ID> planned              # mirror the plan to Linear (no plan body written)
   mael task promote draft-iter1.md --follow-end '*'    # creates the head; echoes its id
   mael task promote draft-tail.md --follow <id1>       # multi-session only; <id1> = the echoed head id
   mael task status done                            # close this planning task ($MAEL_TASK_ID)
   mael task next --run --parent "$MAEL_TASK_PARENT"    # head now actionable — launches it
   mael session end                                 # stop this planning session
   ```

   `--follow-end '*'` appends the head after the leaves of the parent's existing child-chain —
   which includes **this planning task**, still in-progress. So `mael task status done` must
   run before `mael task next --run`: closing this task is what makes the head actionable.
   `--parent` scopes the launch to this chain, so an unrelated actionable task elsewhere in the
   project cannot be picked instead. `next --run` prints the id it launches — confirm it is the
   head you promoted. When nothing in the chain is actionable it exits non-zero with "No
   actionable task." — a follow is still unmet, so inspect with `mael task list`.

   The launched session owns the implementation — you must **not** write code yourself.
   Promotion consumes each draft file, so nothing is left to clean up. If the user rejects the
   plan instead, delete the draft files.

   `mael session end` runs **last, and without asking** — see **End the session when the plan is
   launched** in the `planning` skill.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the
`linear.<ID>` parent — or, for an ad-hoc plan with no Linear issue, this planning task's own
id). `mael task status done` with no id closes **this** task — it falls back to
`$MAEL_TASK_ID` — so you never need to pass your own id. `promote` likewise defaults `--parent`
to `$MAEL_TASK_PARENT`, so the drafts nest under the Linear issue automatically and
`--follow-end '*'` / `--follow <id>` chain identically whether the parent is a Linear id or
this planning task's own id; the Linear `<ID>` is also in the brief in your prompt if you need
it.

## Draft recipes

Each draft's frontmatter is the task's recipe. The `draft` command seeds it from flags; edit
the file to adjust it.

**Leave `branch:` unset on every draft.** Tasks inherit their parent's branch, and that
default is what keeps all iterations of the plan on **one branch** — so the whole task
accumulates into a **single PR** rather than N separate ones. Each iteration's `create-pr`
appends to the PR already open on that branch instead of opening another. The task can then be
merged **as a whole, once every iteration is complete**, rather than iteration-by-iteration as
each lands.

Setting `branch:` opts a task out: it gets its own branch, worktree, and PR. That is for
genuinely unrelated work — **not** for splitting the iterations of one task, which forks a
second worktree and PR mid-task and gives up the single accumulating PR.

**Lifecycle actions** (`pre-action` / `post-action`) fire a Linear/Sentry status change when
the task starts / finishes, against the `linear.<ID>` parent. Use them so the chain mirrors
itself to Linear automatically — no manual `set-status`:
- `pre-action: linear.in-progress` — fired when the task is launched.

**Do not set `post-action: linear.done` on execute drafts.** The finishing sequence closes the
task at PR push, before `/watch-pr`, so a `post-action` would flip the Linear issue to
Unreleased while CI is still running — overwriting the "In Review" that `create-pr` just set.
Leave the issue in In Review and move it on deliberately with
`mael linear set-status <ID> done` once the work has actually landed.

The planning task already carries `post-action: linear.planned` (seeded by
`mael linear plan`), so finishing planning flips Linear to Planned and launching each execute
step flips it to In Progress. The issue then stays In Review (set by `create-pr`) until
someone moves it on explicitly.

**Modes.**
- `--mode auto` on every **execute** draft — it runs the plan as-is unattended (Claude's
  classifier-vetted auto permission mode), no skill. An execute task has an empty `command`:
  it runs **no skill** (not a re-plan) and finishes via the project's always-on "Finishing a
  task" rule (commit → `/code-review` → fixups → `create-pr --squash` → `task status done` →
  `/watch-pr`).
- `--mode normal` on the **`plan-next-step`** tail draft — the next increment is planned
  afresh in a normal-permission session that writes its own drafts.

**Set `--model` on the tail draft** to the model *this* session is running — read it from your
system prompt ("You are powered by the model named …") and write that literal alias (e.g.
`opus`), not an env var. Planning is where the leverage is, so every planner in the chain
stays on one model. Leave `--model` unset on execute drafts: they inherit the user's default.

## Draft bodies

The draft's `## Content` section becomes the **execute task's content** — the plan the execute
session reads and implements directly. Edit it in place; `richview` shows every change.

### Single-session — one execute draft

```markdown
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

## Seams under test
The public boundaries this slice is tested at, and what each one verifies. Agreeing these
here is what lets the execute session write tests without stopping to ask.

## Verification
- How to test the changes
- Expected outcomes
```

### Multi-session — execute draft + tail draft

The `draft-iter1.md` body carries the first iteration in detail plus the overall picture:

```markdown
# <ID>: <Title> — Iteration 1

## Overall Goal
The full end state we're working toward.

## Architecture & Design
Detailed architectural changes across the whole task:
- Key design decisions and trade-offs
- New components/modules and their responsibilities
- Changes to existing interfaces or data flow

## Iteration 1 scope
Concrete scope for this first execute session — a thin **vertical** slice cutting through
every layer it touches, shipping its own tests and delivering user-visible behaviour, sized at
up to ~1500 lines across several ~500-line commits. Not a layer ("the back end", "the tests").
- ...

## Seams under test
The public boundaries this iteration is tested at, and what each one verifies. Agreeing these
here is what lets the execute session write tests without stopping to ask.

## Verification
How to test this iteration.
```

The `draft-tail.md` body carries the remaining-work picture — it must not be an empty
placeholder:

```markdown
## Remaining work
The fuzzy tail — everything beyond iteration 1 (bullet list).
- ...

## What should already be done
A summary of iteration-1 scope plus the overall goal / architecture context the next planner
needs.
```

## Slicing rules

Each iteration should:
- Be a **thin vertical slice**: an end-to-end cut through every layer it touches (back end,
  front end, and its own tests), delivering user-visible behaviour. Layer-shaped iterations —
  "the back-end API", "the front end", "the e2e tests" — are an **antipattern**; a plan whose
  iterations are named after layers has been sliced the wrong way, so re-cut it.
- **Ship its tests with the slice they cover.** No test-only iterations: the tests for a slice
  belong in that slice's session.
- **Name the seams it is tested at.** The execute session builds test-first (`/tdd`), and that
  skill forbids testing at a seam nobody agreed. An unattended `mode: auto` session has no one
  to ask, so the seams must be settled here, with the user's approval of the draft. Name the
  public boundary each test observes behaviour through — not the internals behind it. Use
  `/codebase-design` for the vocabulary when the boundary itself is the open question.
- Be sized at up to ~1500 lines of new code — one execute session, landing as several
  ~500-line commits.
- Be independently testable and pass CI when merged.
- Not break existing functionality.
- Land on the plan's **shared branch** — leave `branch:` unset so every iteration continues on
  the same branch, appending to one accumulating PR that can be merged as a whole once every
  iteration is done.
- Be ordered by dependency (later iterations can depend on earlier ones).
- For mechanical transformations: describe the mechanism clearly and note where test/type
  coverage gives confidence.
  - The same applies to an enabling refactor that is *genuinely* mechanical and cannot be
    folded into the slice that needs it. This is a narrow exception to the vertical-slice rule
    above — it does not license splitting feature work by layer.

**Aim for ≤3 iterations per task.** If the breakdown seems to need more, that usually means
the slices are too thin or sliced by layer — re-cut them vertically first. If it genuinely
still needs more than three, **stop and ask the user** (AskUserQuestion) whether to split the
issue into separate tasks or approve a longer chain — rather than silently emitting one.

## Implementation Notes

- **This session plans** — it writes draft task files and nothing else: no project source
  edits, no branches, no implementation (the `planning` skill's discipline).
- **Research before planning**: codebase research happens before the drafts.
- **Interactive refinement**: sculpt the drafts with the user before promoting.
- **Promotion only after approval**: `mael task promote` runs only once the user approves the
  drafts.
- **No Linear plan body**: Linear is a product-level mirror only; the skill mirrors *status*
  (`set-status … planned`) but never writes the plan back to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.

## Integration with the notebook chain

- The execute task carries the plan as content; a plain execute runs **no skill** and finishes
  via the project's always-on "Finishing a task" rule.
- For multi-session work, `plan-next-step` (re)plans each subsequent increment, consuming the
  remaining-work tail this skill seeded and handing a refreshed tail to the next one.
- The head execute session is launched by `mael task next --run` at the end of the approval
  sequence; advance the chain beyond it the same way.
