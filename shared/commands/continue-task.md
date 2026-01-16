# Continue Task Command

⚠️ **PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

This command continues work on the next sub-task from in-progress Linear issues in the current
cycle, planning in detail how to complete it. Review any applicable code and do any necessary
research before confirming your plan.

## Usage

```
/continue-task [issue-id-or-description]
```

Examples:

- `/continue-task` - Automatically finds next task from current cycle
- `/continue-task NORT-489` - Continue specific issue
- `/continue-task mixpanel instrumentation` - Find and continue matching issue

## Command Logic

1. **MANDATORY Plan Mode Check**: MUST fail immediately if not in plan mode - do not proceed with
   any other logic
2. **Find the Task**: Use the Linear CLI to locate the task:

   ```bash
   # If argument provided and looks like issue ID:
   mael linear read-task NORT-489

   # If argument provided but not an ID, or no argument:
   mael linear list-tasks --status "In Progress"
   ```

3. **Identify Next Subtask**: If the issue has subtasks, find the first incomplete one (status not
   "Done" or "Canceled"). If all subtasks are complete, work on the parent issue itself.
4. **Start Task in Linear**: Mark the task as started immediately after identifying it:
   ```bash
   mael linear start-task <issue-id>
   ```
5. **Task Planning**: Create a plan to complete the identified task/sub-task
   - Use Task tool with Explore subagent(s) for codebase research
   - Plan the task **thoroughly** doing necessary code review & research before presenting the plan
   - **ALWAYS include these steps in the plan**:
     - **Implementation steps**: The actual work to complete the task
     - **Final step**: Create PR (`mael gh create-pr`) and submit to Linear
       (`mael linear submit-pr`)
6. **Write Plan to File**: Write the detailed plan to the plan file (path provided in system
   context)
7. **Present Plan**: Call ExitPlanMode with allowedPrompts:
   - `{"tool": "Bash", "prompt": "run tests"}`
   - `{"tool": "Bash", "prompt": "create PR and submit to Linear"}`

## Examples

```bash
# Continue work on next sub-task in current cycle
/continue-task

# Continue specific issue
/continue-task NORT-489

# Find and continue by description
/continue-task mixpanel instrumentation
```

## Error Cases

- Not in plan mode: "Continue-task command requires plan mode. Please enter plan mode first before
  using this command."
- No active cycle found
- No matching issue found
- No in-progress tasks in current cycle

## Status Transitions

When starting a task (`start-task`) - happens during planning:

- Sets status to "In Progress"
- Adds workspace label (astronort3/4/6) based on current directory
- Also updates parent task if working on a subtask

When PR is submitted (`submit-pr`) - happens as final execution step:

- Attaches PR URL to Linear task (auto-detected from current branch)
- Sets status to "In Review"
- If all sibling subtasks are "In Review", parent is also set to "In Review"

When PR is merged (manual or via `complete-task`):

- Subtasks: Set to "Done"
- Parent/standalone tasks: Set to "Unreleased"

## Implementation Notes

- **Start task during planning**: Linear status and label updates happen during planning mode, after
  identifying the task but before presenting the plan
- **Split operations for planning and execution**:
  - Planning phase: Locate task, start task in Linear, research codebase, present plan
  - Execution phase: Implement, create PR, submit PR to Linear (final step)
- **CLI tool handles**: Team ID, workspace labels, parent task updates, PR detection, and all Linear
  API usage
- **Plan mode detection**: Check for `Plan mode is active` in system-reminder tags. If not present,
  output error message and stop immediately.
- **Progress tracking**: Use TodoWrite to track implementation progress during execution

## Integration with Plan Task

- Works with issues that have sub-tasks created by `/plan-task`
- Automatically picks up next phase from Linear task breakdown
- Workspace labels enable filtering by codebase in Linear
- Status tracking happens in Linear, visible to entire team
