---
name: mael
description: Manage Linear tasks and Sentry issues using the mael CLI. Use for task planning, debugging production errors, and tracking work across worktrees.
---

# Maelstrom CLI Skill

This skill provides CLI commands for managing Linear tasks and querying Sentry issues directly from Claude Code.

## Prerequisites

### Environment Variables

Set these in your project's `.env` file:

- `LINEAR_API_KEY` - Linear API key (required for Linear commands)
- `SENTRY_API_KEY` - Sentry auth token with `event:read` scope (required for Sentry commands)

### Configuration

Add integration settings to your project's `.maelstrom.yaml`:

```yaml
# Linear integration
linear_team_id: "your-team-uuid-here"
linear_workspace_labels:  # Optional: custom labels for worktrees
  - alpha
  - bravo
  - charlie

# Sentry integration
sentry_org: "your-org-slug"
sentry_project: "your-project-id"
```

## Linear Commands

### mael linear list-tasks

List tasks in the current cycle.

```bash
mael linear list-tasks [--status STATUS]
```

**Options:**
- `--status`: Filter by status name (partial match, case-insensitive)

**Examples:**
```bash
# List all tasks in current cycle
mael linear list-tasks

# List only in-progress tasks
mael linear list-tasks --status "In Progress"
```

### mael linear read-task

Read task details as markdown, including subtasks and Sentry issues.

```bash
mael linear read-task <issue-id>
```

**Arguments:**
- `issue-id`: Linear issue identifier (e.g., PROJ-123)

**Output includes:**
- Title, status, parent (if subtask), cycle, labels
- Full description
- List of subtasks with completion status
- Comments
- Attachments (Sentry links automatically fetch issue details)

### mael linear start-task

Start working on a task. Sets status to "In Progress" and adds worktree label.

```bash
mael linear start-task <issue-id>
```

**Behavior:**
- Sets task status to "In Progress"
- Detects worktree from current directory (alpha, bravo, etc.)
- Adds worktree name as label, removes other worktree labels
- If task is a subtask, also updates parent task with same status/label

### mael linear complete-task

Mark a task as complete.

```bash
mael linear complete-task <issue-id>
```

**Behavior:**
- Subtasks: Set status to "Done"
- Parent/standalone tasks: Set status to "Unreleased"
- If completing a subtask and all siblings are complete, parent is set to "Unreleased"

### mael linear create-subtask

Create a subtask on a parent issue.

```bash
mael linear create-subtask <parent-id> <title> [description]
```

**Arguments:**
- `parent-id`: Parent issue identifier (e.g., PROJ-123)
- `title`: Subtask title
- `description`: Optional subtask description

**Behavior:**
- Creates subtask linked to parent
- Inherits cycle from parent issue

### mael linear add-plan

Add an implementation plan section to a task's description.

```bash
mael linear add-plan <issue-id> <plan-content>
```

**Arguments:**
- `issue-id`: Linear issue identifier
- `plan-content`: Markdown content for the implementation plan

**Behavior:**
- Appends `## Implementation Plan` section to existing description
- Does not overwrite existing content

## Sentry Commands

### mael sentry list-issues

List unresolved issues for the project.

```bash
mael sentry list-issues [--env ENV]
```

**Options:**
- `--env`: Environment filter (default: `prod`)

**Output includes:**
- Short ID (e.g., PROJ-ABC)
- Title
- Last seen (relative time)
- Count: Total events (all time)
- Trend: Change in events over last 12h vs previous 12h

### mael sentry get-issue

Get issue details as markdown.

```bash
mael sentry get-issue <issue-id>
```

**Arguments:**
- `issue-id`: Sentry issue ID (numeric)

**Output includes:**
- Exception type and message
- Event metadata (ID, project, date)
- Tags
- Full stacktrace with code context and variable values

## Status Transitions

### Subtasks (issues with a parent)
- **Starting work**: Set to "In Progress"
- **Completing work**: Set to "Done"

### Parent Tasks (issues with subtasks)
- **Starting work**: Set to "In Progress" (when first subtask starts)
- **Completing work**: Set to "Unreleased" only when ALL subtasks are Done/Canceled

### Standalone Tasks (no parent, no subtasks)
- **Starting work**: Set to "In Progress"
- **Completing work**: Set to "Unreleased"

## Workflow: Planning a Task

1. **Get issue details**:
   ```bash
   mael linear read-task PROJ-123
   ```

2. **Research codebase**: Explore relevant code to understand context

3. **Create subtasks** for each planned phase:
   ```bash
   mael linear create-subtask PROJ-123 "Phase 1: Core functionality" "Description here"
   ```

4. **Add implementation plan** (optional):
   ```bash
   mael linear add-plan PROJ-123 "Overall plan summary..."
   ```

## Workflow: Working on a Task

1. **Find next task**:
   ```bash
   mael linear list-tasks --status "In Progress"
   # or
   mael linear read-task PROJ-123
   ```

2. **Start the task** (marks In Progress, adds worktree label):
   ```bash
   mael linear start-task PROJ-124
   ```

3. **Do the implementation work**

4. **Complete the task**:
   ```bash
   mael linear complete-task PROJ-124
   ```

## Workflow: Debugging Production Errors

1. **List unresolved issues**:
   ```bash
   mael sentry list-issues
   ```

2. **Prioritize by**: escalating trend > recency > frequency

3. **Get issue details**:
   ```bash
   mael sentry get-issue 12345678
   ```

4. **Investigate the stacktrace** and fix the issue

## Error Handling

Commands exit with code 1 and display error messages for:
- Missing environment variables (`LINEAR_API_KEY`, `SENTRY_API_KEY`)
- Missing configuration (`linear_team_id`, `sentry_org`, `sentry_project`)
- Issue not found
- API errors
- Missing workflow states ("In Progress", "Done", "Unreleased")
