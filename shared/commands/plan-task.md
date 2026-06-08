# Plan Task Command

⚠️ **PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

This skill runs **inside a session that `mael` launched** — it is not a command you type in a shell
you opened yourself. `mael linear plan <issue>` (or `mael task add … --command plan-task`) creates a
planning task and launches a plan-mode session holding the brief; this skill is the prompt that runs
there.

The brief is **already in your initial prompt** (the planning task's content). Do **not** re-fetch it
from Linear, do not validate Linear status, and do not write any plan back to Linear. Your job is to
research, plan interactively, write a plan file, then **emit the notebook chain** that carries the
work forward.

## What This Command Does

1. **Plan mode check** (mandatory — fail immediately if not in plan mode).
2. **Research the codebase** with Explore subagents.
3. **Classify** single-session vs multi-session.
4. **Refine** the plan interactively with the user.
5. **Write** the plan to the plan file.
6. **After ExitPlanMode approval**: emit the chain (execute task, plus a `plan-next-step` task for
   multi-session work) and mark this planning task done. Do **not** implement.

## Command Logic

1. **MANDATORY Plan Mode Check**: MUST fail immediately if not in plan mode — do not proceed with any
   other logic. (Detect via `Plan mode is active` in system-reminder tags.)

2. **Read the brief from the initial prompt**: The Linear brief (`# <ID>: <title>` + description) is
   already in your prompt. Treat it as the source of truth for *what* to build. Do not call
   `mael linear read-task` — the brief is in front of you.

3. **Set status**:
   ```bash
   mael status set "Planning <ID>"
   ```

4. **Codebase Research**: Use the Task tool with Explore subagent(s):
   - Launch 1-3 Explore agents in parallel for efficient research.
   - Examine relevant files and subsystems mentioned in the brief.
   - Review existing patterns, dependencies, and integration points.
   - Understand current implementation state.

5. **Classify Session Type**:
   - **Single-session**: less than ~500 lines of new code; completable in one session.
   - **Multi-session**: larger scope, or a mechanical transformation whose mechanical piece should
     land first.
   - Use AskUserQuestion to confirm the classification with the user.

6. **Interactive Planning**: Use AskUserQuestion to discuss the plan:
   - Present your understanding based on research.
   - Discuss approach and trade-offs.
   - Iterate until the user is satisfied.

7. **Write Plan to File**: Write the implementation plan to the plan file (path provided in system
   context). Include:
   - `**Session type: single**` or `**Session type: multi**` after `# <ID>: <Title>`.
   - Context: why this change is being made.
   - Research findings (relevant files, patterns, dependencies).
   - For **single-session**: step-by-step implementation with specific file changes.
   - For **multi-session**: overall goal, architecture & design for the whole task, a bullet-point
     first-iteration scope, and remaining-work notes (see templates below).
   - Testing strategy.

8. **Present Plan**: Call ExitPlanMode with allowedPrompts:
   - `{"tool": "Bash", "prompt": "emit notebook chain tasks"}`

9. **After Plan Approval — Emit the chain and finish**: This replaces the old "write plan to Linear"
   handoff. You self-reference via `$MAEL_TASK_ID` (this planning task's id, exported into the
   session) and target the chain by the parent (`linear.<ID>`, where `<ID>` is the Linear identifier
   from the brief).

   **Single-session (spec A)** — emit one execute task, then finish:
   ```bash
   mael task add "Execute <ID>" --follow-end "linear.<ID>" --content-file <plan-file>
   mael task done "$MAEL_TASK_ID"
   ```
   The execute task has an empty `command`, so it is a plain execute in normal mode: its prompt is
   just `<title>` + the plan as content, with no skill invoked. The session then implements and the
   project's always-on "Finishing a task" rule (commit → `/code-review` → fixups → stop) closes it
   out.

   **Multi-session (spec B)** — emit a concrete first-iteration execute task **and** a fuzzy-tail
   `plan-next-step` task, then finish:
   ```bash
   mael task add "Execute: <iter-1 desc>" --follow-end "linear.<ID>" --content-file <iter1-file>
   mael task add "Plan next step" --command plan-next-step --follow-end "linear.<ID>" --content-file <tail-file>
   mael task done "$MAEL_TASK_ID"
   ```
   Both files are derived from the plan you just wrote:
   - `<iter1-file>`: the concrete iteration-1 scope (what the first execute session implements).
   - `<tail-file>`: the **fuzzy tail** — it MUST carry content, not be an empty placeholder:
     - a **bullet-point list of remaining work** (everything beyond iteration 1), and
     - a **summary of what should already have been done** by the time it runs (iteration-1 scope
       plus the overall goal / architecture context the next planner needs).

   **IMPORTANT: After emitting the chain and marking this task done, your work is DONE.** Do NOT
   begin implementing. Do NOT write code, edit source files, or create branches. Confirm the tasks
   were created and stop. Implementation happens in a later session via `mael task next --run`.

## Knowing your own task id

The session exports `MAEL_TASK_ID` (this planning task) and `MAEL_TASK_PARENT` (the
`linear.<ID>` parent). Use `$MAEL_TASK_ID` to `mael task done` yourself, and key `--follow-end` off
the parent (`linear.<ID>`); the Linear `<ID>` is also in the brief in your prompt.

## Error Cases

- Not in plan mode: "Plan-task command requires plan mode. Please enter plan mode first before using
  this command."

## Plan Structure

### Single-Session Plan

For tasks completable in one session (~500 lines or less):

```markdown
# <ID>: <Title>

**Session type: single**

## Context
Brief description of the problem and why this change is needed.

## Implementation Steps

### Step 1: <Description>
- Files to modify: ...
- Changes: ...

### Step 2: <Description>
...

## Files to Modify
| File | Change |
|------|--------|
| ... | ... |

## Verification
- How to test the changes
- Expected outcomes
```

This whole plan file becomes the **execute task's content** (`--content-file <plan-file>`). The
execute session reads it and implements directly.

### Multi-Session Plan

For larger tasks. Provides architectural design for the whole task; iteration 1 is a concrete scope,
and the tail is carried forward by the `plan-next-step` chain (not by a Linear description):

```markdown
# <ID>: <Title>

**Session type: multi**

## Context
Why this change is needed.

## Overall Goal
The full end state we're working toward.

## Architecture & Design
Detailed architectural changes across the whole task:
- Key design decisions and trade-offs
- New components/modules and their responsibilities
- Changes to existing interfaces or data flow
- Integration points and dependencies

## Files to Modify
| File | Change |
|------|--------|

## Iteration 1: <Description>
Concrete scope for the first execute session — this becomes `<iter1-file>`.
- ...

## Remaining Work
The fuzzy tail — everything beyond iteration 1. This (plus the Overall Goal / Architecture
context above) becomes `<tail-file>`, the content of the `plan-next-step` task.
- ...

## Verification
How to test the overall feature end-to-end.
```

The chain replaces the old rolling `## Next Iteration` / `## Completed Iteration` machinery that used
to live in the Linear description. Each iteration should:
- Be independently testable and pass CI when merged.
- Not break existing functionality.
- Not necessarily deliver end-user functionality (a back-end API before the front-end, or an enabling
  refactor, are valid iterations).
- Be ordered by dependency (later iterations can depend on earlier ones).
- For mechanical transformations: describe the mechanism clearly and note where test/type coverage
  gives confidence.

## Implementation Notes

- **Plan mode required**: fail immediately if not in plan mode.
- **Research before planning**: codebase research happens before the plan.
- **Interactive refinement**: discuss with the user before finalising.
- **Chain emitted after ExitPlanMode acceptance**: tasks are created only after the user approves the
  ExitPlanMode prompt.
- **No Linear writes**: Linear is a product-level mirror only; this skill never writes the plan back
  to a Linear description.
- **Progress tracking**: use TodoWrite to track planning progress.

## Integration with the notebook chain

- The execute task carries the plan as content; a plain execute runs **no skill** and finishes via
  the project's always-on "Finishing a task" rule.
- For multi-session work, `plan-next-step` (re)plans each subsequent increment, consuming the
  remaining-work tail this skill seeded and handing a refreshed tail to the next one.
- Advance the chain with `mael task next --run`.
