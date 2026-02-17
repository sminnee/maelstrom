# Plan Task Command

⚠️ **PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

Creates a detailed implementation plan for a Linear task and writes it to the task's description.
The task should be in "Todo" status with no existing plan.

## Usage

```
/plan-task <issue-id-or-description>
```

Examples:

- `/plan-task ME-32`
- `/plan-task manage agent plans`

## What This Command Does

1. **Fetches the issue** from Linear using the CLI tool
2. **Validates** the task is in "Todo" status and has no existing plan
3. **Researches the codebase** to understand the issue context
4. **Creates a detailed implementation plan** through interactive discussion
5. **Exits plan mode** with allowed prompt to write the plan to Linear
6. **After approval**: writes the plan to Linear via `mael linear write-plan`

## Command Logic

1. **MANDATORY Plan Mode Check**: MUST fail immediately if not in plan mode - do not proceed with
   any other logic

2. **Fetch Issue Details**: Use the Linear CLI to get issue information:

   ```bash
   mael linear read-task <issue-id>
   ```

   - If argument looks like an issue ID (e.g., `ME-32`), use it directly
   - Otherwise, use `list-tasks` to find matching issues by title

3. **Validate Task State**:
   - Task should be in "Todo" status (warn if not, but continue)
   - Check for existing "# Implementation Plan" in description (warn if found - plan will replace
     it)

4. **Codebase Research**: Use Task tool with Explore subagent(s) to research the codebase:
   - Launch 1-3 Explore agents in parallel for efficient research
   - Examine relevant code files and subsystems mentioned in the issue
   - Review existing patterns and architecture in affected areas
   - Identify dependencies and integration points
   - Understand current implementation state

5. **Interactive Planning**: Use AskUserQuestion tool to discuss the plan with the user:
   - Present your understanding of the task based on research
   - Discuss approach and any trade-offs
   - Iterate on the plan until the user is satisfied

6. **Write Plan to File**: Write the detailed implementation plan to the plan file (path provided
   in system context). The plan should include:
   - Context section: why this change is being made
   - Research findings (relevant files, patterns, dependencies)
   - Step-by-step implementation approach with specific file changes
   - Testing strategy
   - A final step: "Write plan to Linear and create PR"
     (`mael linear write-plan <issue-id> <plan-file>`, then later `mael gh create-pr` and
     `mael linear submit-pr`)

7. **Present Plan**: Call ExitPlanMode with allowedPrompts:
   - `{"tool": "Bash", "prompt": "write plan to Linear"}`

8. **After Plan Approval - Write to Linear**: Execute:
   ```bash
   mael linear write-plan <issue-id> <plan-file-path>
   ```
   where `<plan-file-path>` is the plan file path from system context.

## Error Cases

- Not in plan mode: "Plan-task command requires plan mode. Please enter plan mode first before using
  this command."
- No matching issue found
- Task not in Todo status (warning, not blocking)

## Plan Structure

The implementation plan should follow this structure:

```markdown
# <ISSUE-ID>: <Title>

## Context
Brief description of the problem and why this change is needed.

## Implementation Steps

### Step 1: <Description>
- Files to modify: ...
- Changes: ...

### Step 2: <Description>
...

### Final Step: Create PR and submit to Linear
- `mael gh create-pr`
- `mael linear submit-pr <issue-id>`

## Files to Modify
| File | Change |
|------|--------|
| ... | ... |

## Verification
- How to test the changes
- Expected outcomes
```

## Implementation Notes

- **Plan mode required**: Command must fail immediately if not in plan mode
- **Research before planning**: Codebase research happens before creating the plan
- **Interactive refinement**: Discuss the plan with the user before finalizing
- **Plan written after ExitPlanMode acceptance**: The plan is only uploaded to Linear after
  the user approves the ExitPlanMode prompt
- **Plan mode detection**: Check for `Plan mode is active` in system-reminder tags. If not present,
  output error message and stop immediately.
- **Progress tracking**: Use TodoWrite to track planning progress

## Integration with Continue Task

- After a plan is written to Linear, `/continue-task` reads it and begins implementation
- The plan provides the detailed context so `/continue-task` does not need plan mode
- Plans can be re-written by running `/plan-task` again (replaces the existing plan section)
