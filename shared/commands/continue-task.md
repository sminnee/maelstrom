# Continue Task Command

This command continues work on a Linear task by reading its implementation plan and executing it.
It can also pick up the next sub-task from in-progress issues in the current cycle.

## Usage

```
/continue-task [issue-id-or-description]
```

Examples:

- `/continue-task` - Automatically finds next task from current cycle
- `/continue-task ME-32` - Continue specific issue
- `/continue-task mixpanel instrumentation` - Find and continue matching issue

## Command Logic

1. **Find the Task**: Use the Linear CLI to locate the task:

   ```bash
   # If argument provided and looks like issue ID:
   mael linear read-task ME-32

   # If argument provided but not an ID, or no argument:
   mael linear list-tasks --status "In Progress"
   ```

2. **Identify Next Subtask**: If the issue has subtasks, find the first incomplete one (status not
   "Done" or "Canceled"). If all subtasks are complete, work on the parent issue itself.

3. **Start Task in Linear**: Mark the task as started immediately after identifying it:
   ```bash
   mael linear start-task <issue-id>
   ```

4. **Read Implementation Plan**: Fetch the plan from Linear:
   ```bash
   mael linear read-plan <issue-id>
   ```
   - If the task is a subtask and has no plan, also try reading the plan from the parent task
   - If no plan is found on either, fall back to researching the codebase using Task tool with
     Explore subagent(s) and planning inline (for tasks that were not planned via `/plan-task`)

5. **Execute the Plan**: Implement the changes described in the plan:
   - Use TodoWrite to track progress through the plan steps
   - Follow the implementation steps in order
   - Run tests as appropriate

6. **Final Steps**: Create PR and submit to Linear:
   ```bash
   mael gh create-pr
   mael linear submit-pr <issue-id>
   ```

## Examples

```bash
# Continue work on next sub-task in current cycle
/continue-task

# Continue specific issue
/continue-task ME-32

# Find and continue by description
/continue-task mixpanel instrumentation
```

## Error Cases

- No active cycle found
- No matching issue found
- No in-progress tasks in current cycle

## Status Transitions

When starting a task (`start-task`):

- Sets status to "In Progress"
- Adds workspace label (e.g., golf) based on current directory
- Also updates parent task if working on a subtask

When PR is submitted (`submit-pr`) - happens as final step:

- Attaches PR URL to Linear task (auto-detected from current branch)
- Sets status to "In Review"
- If all sibling subtasks are "In Review", parent is also set to "In Review"

When PR is merged (manual or via `complete-task`):

- Subtasks: Set to "Done"
- Parent/standalone tasks: Set to "Unreleased"

## Implementation Notes

- **No plan mode required**: This command reads the plan from Linear and immediately begins
  implementation. Planning has already been completed via `/plan-task`.
- **Fallback for unplanned tasks**: If no plan is found on the task or its parent, the command
  falls back to researching the codebase and implementing based on the task description alone.
- **Split operations**:
  - Start task in Linear (status + label updates)
  - Read plan from Linear
  - Implement changes
  - Create PR and submit to Linear (final step)
- **CLI tool handles**: Team ID, workspace labels, parent task updates, PR detection, and all Linear
  API usage
- **Progress tracking**: Use TodoWrite to track implementation progress

## Integration with Plan Task

- Works with tasks that have plans written by `/plan-task`
- Also works with issues that have sub-tasks created by `/create-subtasks`
- Automatically picks up next phase from Linear task breakdown
- Workspace labels enable filtering by codebase in Linear
- Status tracking happens in Linear, visible to entire team
