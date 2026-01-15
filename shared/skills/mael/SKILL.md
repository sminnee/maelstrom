---
name: mael
description: Manage development workflow using the mael CLI. Covers Linear task management, Sentry error debugging, and git/GitHub operations (syncing, commits, pull requests).
---

# Maelstrom CLI Skill

This skill provides CLI commands for managing Linear tasks, querying Sentry issues, and handling git/GitHub workflows directly from Claude Code.

## Prerequisites

### GitHub CLI

Install and authenticate the GitHub CLI (`gh`) for git/GitHub commands:
```bash
brew install gh
gh auth login
```

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

## Git & GitHub Commands

### mael sync

Rebase the current worktree against origin/main. Run this before starting work.

```bash
mael sync [target]
```

**Arguments:**
- `target` (optional): Project/worktree identifier (uses current directory if omitted)

**Behavior:**
- Fetches latest from remote (`git fetch origin`)
- Rebases current branch against `origin/main` using `--autostash`
- On conflicts: displays helpful instructions with commands to resolve

### mael gh create-pr

Create a new pull request or push updates to an existing one.

```bash
mael gh create-pr [target] [--draft]
```

**Arguments:**
- `target` (optional): Project/worktree identifier

**Options:**
- `--draft`: Create PR as a draft (only for new PRs)

**Behavior:**
- Pushes current branch to origin with `-u` flag
- If no PR exists: creates one using first commit message as title
- If PR exists: just pushes the latest changes
- Returns the PR URL

### mael gh read-pr

Check PR status, review comments, and CI results.

```bash
mael gh read-pr [target]
```

**Arguments:**
- `target` (optional): Project/worktree identifier

**Output includes:**
- PR number, title, URL, and merge status
- Unresolved review comments (file, line, author, preview)
- CI check status grouped by: Failed, Pending, Passing
- For failed checks: truncated logs and available artifacts

### mael gh check-log

View full GitHub Actions logs for a workflow run.

```bash
mael gh check-log <run_id> [--failed-only]
```

**Arguments:**
- `run_id`: GitHub Actions workflow run ID

**Options:**
- `--failed-only`: Show only failed step logs

### mael gh download-artifact

Download artifacts from a workflow run.

```bash
mael gh download-artifact <run_id> <artifact_name> [-o OUTPUT_DIR]
```

**Arguments:**
- `run_id`: GitHub Actions run ID
- `artifact_name`: Name of artifact to download

**Options:**
- `-o, --output`: Output directory (defaults to current directory)

### mael gh show-code

Show committed and/or uncommitted changes in the worktree.

```bash
mael gh show-code [target] [--committed] [--uncommitted]
```

**Arguments:**
- `target` (optional): Project/worktree identifier

**Options:**
- `--committed`: Show only commits since branching from main
- `--uncommitted`: Show only working directory changes
- Default (no flags): Show both

**Output includes:**
- Commits since diverging from `origin/main` with full diffs
- Working directory diff (`git diff HEAD`)

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

## Workflow: Git & Pull Requests

1. **Before starting work**, sync with main:
   ```bash
   mael sync
   ```

2. **During work**, commit changes regularly:
   ```bash
   git add .
   git commit -m "Description of changes"
   ```

3. **When ready for review**, create or update PR:
   ```bash
   mael gh create-pr
   ```

## Workflow: Checking PR Status

1. **Check if PR was merged** or has issues:
   ```bash
   mael gh read-pr
   ```
   This shows: merge status, unresolved comments, and CI check results.

2. **For failed CI checks**, view full logs:
   ```bash
   mael gh check-log <run_id>
   ```

3. **Download artifacts** (test results, screenshots, etc.):
   ```bash
   mael gh download-artifact <run_id> <artifact_name>
   ```

## Workflow: Code Review

1. **Review all changes** in the worktree:
   ```bash
   mael gh show-code
   ```

2. **Review only committed changes** (since branching from main):
   ```bash
   mael gh show-code --committed
   ```

3. **Review only uncommitted changes** (working directory):
   ```bash
   mael gh show-code --uncommitted
   ```

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
