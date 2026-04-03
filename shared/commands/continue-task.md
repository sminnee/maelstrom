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
   **IMPORTANT**: This step is MANDATORY — do not skip or defer it.

4. **Set status**:
   ```bash
   mael status set "<issue-id>"
   ```

5. **Read Implementation Plan**: Fetch the plan from Linear:
   ```bash
   mael linear read-plan <issue-id>
   ```
   - If the task is a subtask and has no plan, also try reading the plan from the parent task
   - If no plan is found on either, fall back to researching the codebase using Task tool with
     Explore subagent(s) and planning inline (for tasks that were not planned via `/plan-task`)

6. **Determine Session Flow**: Check the plan for session type and existing progress:

   **If single-session plan (or no session type marker) AND no progress report comments**: execute
   as current behavior (step 7a).

   **If multi-session plan OR progress report comments exist**: follow the multi-session flow
   (step 7b).

### 7a. Single-Session Flow

Execute the plan directly:
- Use TodoWrite to track progress through the plan steps
- Follow the implementation steps in order
- Run tests as appropriate
- **Commit changes** with the issue ID in the message (see "Commit Messages" below):
  ```bash
  git add <files>
  printf 'feat: <description> [<issue-id>]\n' | git commit -F -
  ```
- **Create PR** and submit to Linear:
  ```bash
  mael gh create-pr <issue-id>
  ```

### 7b. Multi-Session Flow

a. **Determine current iteration**: Look at the plan to understand where we are:
   - `## First Iteration:` or `## Next Iteration:` heading = the current work to do
   - `## Completed Iteration:` sections = what's been done in previous sessions
   - If neither heading exists but `## Remaining Work` has content, this is a finishing session
   - **Backward compat**: If the plan uses the old format (no `## First Iteration:` or
     `## Next Iteration:` heading), also check progress report comments and fall back to
     comment-based flow (step 7b-legacy below).

b. **Enter plan mode**: Call **EnterPlanMode** to switch into plan mode for session planning.

c. **Research current state**: Examine the codebase to understand what's changed:
   - `git log --all --grep='<issue-id>' --oneline` to find previous commits for this ticket
   - `git diff origin/main` to see changes already made
   - Inspect relevant files to understand current implementation state

d. **Decide strategy**: Choose between a **progress step** or **finishing step**:
   - **Finishing step** if remaining work can fit in this session (~500 lines or less)
   - **Progress step** otherwise
   - **Strong bias toward finishing**: if it's close, attempt to finish rather than creating
     another increment. Each progress step must leave less work than it found.

e. **Write session plan** to the plan file:
   - For **progress step**: plan substantial, testable, mergeable progress toward overall goal.
     Must pass CI and not break anything. Doesn't have to deliver end-user functionality --
     e.g., back-end API before front-end, or refactoring to enable the feature.
   - For **finishing step**: plan to complete ALL remaining work.

f. **Exit plan mode**: Call **ExitPlanMode** with allowed prompts for implementation.

g. **Implement**: After approval, execute the session plan.

h. **Commit changes** with the issue ID in the message (see "Commit Messages" below):
   ```bash
   git add <files>
   printf 'feat: <description> [<issue-id>]\n' | git commit -F -
   ```

i. **Create/update PR**: Always create or update the PR:
   - For **progress step**: `mael gh create-pr --wait --progress <issue-id>` (uses "Progresses" in title, keeps status as "In Progress")
   - For **finishing step**: `mael gh create-pr --wait <issue-id>` (uses "Fixes" in title, sets status to "In Review")

   Each increment should be mergeable and pass CI, even if it doesn't deliver the whole feature.
   Run this using the Bash tool with `run_in_background: true` so you can continue other work while waiting for CI.

j. **Update plan inline** (progress step only, NOT for finishing step):
   Use `mael linear edit-plan` to atomically update the rolling plan structure. Use file-based
   mode since this is multiline markdown:

   1. Write `old.md` containing the text from the current iteration heading through end of
      `## Remaining Work` section (i.e., from `## First Iteration: Foo` or `## Next Iteration: Foo`
      through the end of the Remaining Work content).
   2. Write `new.md` containing:
      - `## Completed Iteration: <Current Iteration Description>` — body is **rewritten** to
        describe what actually happened: what was built, key decisions, deviations from plan,
        notes for future iterations. Keep it concise but accurate.
      - `## Next Iteration: <New Description>` — promoted from Remaining Work, potentially
        refined based on learnings from this session.
      - `## Remaining Work` — updated with the promoted item removed, potentially
        course-corrected based on what was learned.
   3. Run:
      ```bash
      mael linear edit-plan <issue-id> old.md new.md
      ```

   This single edit atomically updates the rolling structure.

   For small course-corrections, string mode also works:
   ```bash
   mael linear edit-plan <issue-id> -s "old text" "new text"
   ```

k. **Add a one-liner comment** (progress step only, NOT for finishing step):
   After the plan edit, add a brief comment to keep the Linear activity feed readable:
   ```bash
   mael linear add-comment <issue-id> <comment-file>
   ```
   Comment body: `Completed iteration: <iteration description>`

### 7b-legacy. Legacy Multi-Session Flow (backward compat)

For plans using the old format (no `## First Iteration:` or `## Next Iteration:` heading),
fall back to comment-based progress tracking:

- Read progress report comments chronologically to understand previous session work
- After completing work, write a progress report comment (same format as before):
  ```markdown
  ## Progress Report

  **Session**: [n] ([date])
  **Strategy**: [Making progress / Finishing]

  ### Completed This Session
  - [bullets]

  ### Current State
  - [where things stand, decisions made]

  ### Remaining Work
  - [high-level bullets]
  ```

## Commit Messages

All commits made by continue-task **must include the issue ID** in square brackets at the end, using
conventional commit prefixes. This enables searching git history by ticket number to review progress
across sessions.

**Format**: `<prefix>: <description> [<issue-id>]`

**Examples**:
- `feat: add comment creation API and CLI command [ME-32]`
- `chore: update plan-task to support multi-session classification [ME-32]`
- `fix: prevent duplicate browser panes on env start [ME-45]`

For multi-session tasks, each session's commit message should describe what that session accomplished.
Multiple commits per session are fine — each should have the issue ID suffix.

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

When PR is created (`create-pr <issue-id>`) - happens as final step:

- **Without `--progress`**: PR title has `(Fixes ISSUE-ID)`, sets status to "In Review"
- **With `--progress`**: PR title has `(Progresses ISSUE-ID)`, keeps status as "In Progress"
- Promotes parent from early states (Todo/Planned/Backlog) to "In Progress"

When PR is merged (manual or via `complete-task`):

- Subtasks: Set to "Done"
- Parent/standalone tasks: Set to "Unreleased"

## Implementation Notes

- **No plan mode required for single-session**: Single-session plans are read from Linear and
  immediately implemented. Planning has already been completed via `/plan-task`.
- **Plan mode used for multi-session**: Multi-session tasks enter plan mode to create a session
  plan before implementation. This allows the user to review and approve the session scope.
- **Fallback for unplanned tasks**: If no plan is found on the task or its parent, the command
  falls back to researching the codebase and implementing based on the task description alone.
- **Split operations**:
  - Start task in Linear (status + label updates)
  - Read plan from Linear
  - Determine session flow (single vs multi)
  - For multi-session: enter plan mode, plan session, exit plan mode
  - Implement changes
  - Create PR with issue ID (handles both GitHub PR and Linear status update)
  - For multi-session progress steps: write progress report comment
- **CLI tool handles**: Team ID, workspace labels, parent task updates, PR detection, and all Linear
  API usage
- **Progress tracking**: Use TodoWrite to track implementation progress
- **Inline plan updates**: Multi-session progress is tracked inline in the plan via `edit-plan`,
  not via comments. Comments are only used for one-liner activity feed entries and legacy compat.

## Integration with Plan Task

- Works with tasks that have plans written by `/plan-task`
- Also works with issues that have sub-tasks created by `/create-subtasks`
- Automatically picks up next phase from Linear task breakdown
- Workspace labels enable filtering by codebase in Linear
- Status tracking happens in Linear, visible to entire team
- Multi-session plans track progress inline in the plan description via `edit-plan`
- Falls back to comment-based progress for plans using the old format (no iteration headings)
