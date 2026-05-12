---
name: mael
description: "Git workflow, commits, PRs, branches. Also Linear tasks, Sentry debugging, UptimeRobot monitor checks, and dev environment management. Invoke /mael before any git operations."
---

# Maelstrom CLI Skill

**All `mael` and `git` commands require `dangerouslyDisableSandbox: true`** — they need network access and git write access.

**Prefer `mael` commands over raw `git`/`gh`** — they handle worktree context, Linear integration, and status transitions automatically. Use `mael git status` not `git status`, `mael sync` not `git pull --rebase`, `mael gh create-pr` not `gh pr create`, `mael gh read-pr` not `gh pr view`, etc.

## Planning Work

Research the task, write a plan, and break it down if needed.

```bash
mael linear read-task PROJ-XXX                          # Read task details, subtasks, comments
mael linear list-tasks [--status STATUS]                # List tasks in current cycle
```

**Write a plan** to the task description (replaces any existing plan, updates status to "Planned"):
```bash
mael linear write-plan PROJ-XXX plan.md
mael linear read-plan PROJ-XXX                          # Read it back later
mael linear edit-plan PROJ-XXX old.md new.md             # Search/replace within plan (file-based)
mael linear edit-plan PROJ-XXX -s "old" "new"            # Search/replace within plan (string mode)
```

**Break down large tasks** into subtasks (inherit parent's cycle):
```bash
mael linear create-subtask PROJ-XXX "Phase 1: title" "description"
```

**Create standalone tasks** (not linked to a parent):
```bash
mael linear create-task "title" "description"
```

Slash commands for assisted planning:
- `/plan-task PROJ-XXX` — research and create implementation plan (plan mode required)
- `/create-subtasks PROJ-XXX` — research and break into subtasks (plan mode required)

## Doing Work

```bash
mael sync                                # Rebase on origin/main before starting
mael linear start-task PROJ-XXX          # Set "In Progress", add worktree label
```

Then implement. Run project checks from CLAUDE.md (tests, linting, typecheck).

For multi-session tasks, use `/continue-task PROJ-XXX` — it reads the plan/progress from Linear, executes one iteration, writes a progress report, and creates/updates the PR.

## Testing Work

**Stop environments during heavy editing** — file watchers trigger constant rebuilds. Restart when ready to test.

```bash
mael env stop                            # Stop before multi-file edits
# ... make changes ...
mael env start                           # Start services (runs install_cmd first)
mael env start --skip-install            # Skip install_cmd
mael env status                          # Check service status
mael env list                            # Running environments for this project
mael env list-all                        # All running environments
```

Run the project's test suite and linting as defined in CLAUDE.md.

## Finalising Work

```bash
mael linear complete-task PROJ-XXX       # Subtask -> "Done", standalone -> "Unreleased"
mael linear add-comment PROJ-XXX file.md # Add progress/completion notes
mael linear release                      # Promote all "Unreleased" with product label to "Done"
```

When completing a subtask, if all siblings are Done/Canceled, the parent auto-transitions to "Unreleased".

## Committing & Creating PRs

**Commit format** — use `printf` piped to `git commit -F -` (heredocs fail in sandbox):
```bash
mael gh show-code --uncommitted          # Review changes before committing
git add file1.py file2.py
printf 'feat: add new feature [PROJ-XXX]\n\nDetailed description.\n' | git commit -F -
```

Prefixes: `feat:` (new behaviour), `fix:` (bug fix), `refactor:` (no behaviour change), `chore:` (everything else).
Append Linear issue ID in brackets when applicable.

**Check status before pushing:**
```bash
mael git status                          # Branch info, diff stats, recent commits
mael gh show-code --committed            # All changes since branching from main
```

**Create or update PR:**
```bash
mael gh create-pr PROJ-XXX --wait        # Link Linear task, wait for CI
```
- Run with `run_in_background: true` so you can continue working while CI runs.
- Force-pushes branch with `--force-with-lease`.
- New PR: uses first commit as title. Existing PR: just pushes.
- With `ISSUE_ID`: appends `(Fixes ISSUE_ID)` to title, sets task to "In Review".
- `--progress`: uses `(Progresses ISSUE_ID)` instead, skips "In Review". Use for multi-session tasks with remaining work.
- `--draft`: create as draft PR.
- `--wait`: blocks until CI completes (exit 0=pass, 1=fail, 2=timeout).

**Code review before PR** (optional):
1. `/review-branch` (plan mode required) — produces review findings
2. Fix issues: `git add <files> && git commit --fixup=<original-sha>`
3. `mael review squash` — autosquash fixups into targets (aborts on conflicts)
4. `mael review status` — check for unsquashed fixups
5. `mael gh create-pr`

## Working with PR Failures

```bash
mael gh read-pr                          # Merge status, comments, CI results
mael gh read-pr --all-comments           # Include comments older than the last push
mael gh read-pr --wait                   # Wait for CI to finish (use run_in_background)
mael gh check-log <run_id>               # Full GitHub Actions logs
mael gh check-log <run_id> --failed-only # Just failed steps
mael gh download-artifact <run_id> <name>            # Test results, screenshots, etc.
```

`read-pr` shows top-level PR comments, review summaries, and unresolved inline review threads. Comments older than the most recent push are collapsed into a count line by default; pass `--all-comments` to expand them.

Fix issues, commit, then `mael gh create-pr --wait` again to push and re-check CI.

## Working with Sentry

```bash
mael sentry list-issues [--env ENV]      # Unresolved issues (default: prod)
mael sentry get-issue <issue-id>         # Full details with stacktrace and variables
```

Prioritize by: escalating trend > recency > frequency. Investigate the stacktrace and fix.

## Working with UptimeRobot

```bash
mael uptimerobot status                  # Current status of configured monitors with 24h/7d/30d uptime
mael uptimerobot outages [--since 24h] [--limit 20]   # Recent down events, newest first
mael uptimerobot monitors                # All monitors on the account with IDs (for initial setup)
```

Use `status` for "is anything down right now?" and `outages` to investigate
recent incidents. Run `monitors` once to discover IDs, then list them under
`uptimerobot.monitors` in `.maelstrom.yaml`. With no monitors configured,
commands fall back to all monitors on the account.

`--since` accepts `30m`, `24h`, `7d`, etc.

## Status Transitions

```
Todo -> Planned        (write-plan or create-subtask)
Planned/Todo -> In Progress  (start-task)
In Progress -> In Review     (create-pr ISSUE-ID)
In Review -> Done/Unreleased (complete-task)
Unreleased -> Done           (release)
```

**Subtasks:** complete to "Done". **Parent tasks:** complete to "Unreleased" only when ALL subtasks are Done/Canceled. Parent is promoted to "In Progress" when subtask starts or PR is raised, but NOT set to "In Review" when subtask is.

## Workspace Status

```bash
mael status set "Working on PROJ-XXX"    # Shown in cmux status bar
mael status clear
```

## Prerequisites

- **GitHub CLI:** `brew install gh && gh auth login`
- **Env vars** in `.env`: `LINEAR_API_KEY`, `SENTRY_API_KEY`, `UPTIMEROBOT_API_KEY` (or set under `uptimerobot.api_key` in `~/.maelstrom/config.yaml`)
- **Config** in `.maelstrom.yaml`: `linear.team_id`, `sentry.org`, `sentry.project_id`, `uptimerobot.monitors`
