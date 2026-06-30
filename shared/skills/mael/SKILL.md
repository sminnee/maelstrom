---
name: mael
description: "Git workflow, commits, PRs, branches. Also Linear tasks, Sentry debugging, UptimeRobot monitor checks, and dev environment management. Invoke /mael before any git operations."
---

# Maelstrom CLI Skill

**All `mael` and `git` commands require `dangerouslyDisableSandbox: true`** — they need network access and git write access.

**Prefer `mael` commands over raw `git`/`gh`** — they handle worktree context, Linear integration, and status transitions automatically. Use `mael git status` not `git status`, `mael sync` not `git pull --rebase`, `mael gh create-pr` not `gh pr create`, `mael gh read-pr` not `gh pr view`, etc.

## Planning & Doing Work — the task notebook

The primary workflow is the **git-backed task notebook** (`mael task …`). You no longer type
`/plan-task` or `/continue-task` in a shell you open yourself — `mael` launches sessions, and each
task's `command` field decides which skill (if any) runs inside. The everyday loop is:

```bash
mael linear plan PROJ-XXX          # create + launch a plan-mode session for a Linear issue
mael task next --run               # launch the next ready task in the chain (repeat to advance)
```

`mael linear plan` is a thin wrapper over `mael task add` that seeds a `plan-task` task with the
Linear brief as content (parented under `linear.PROJ-XXX`). It **runs by default** — the planning
session launches immediately; pass `--no-run` to create the task without launching.

**How a task flows:**
- `mael linear plan PROJ-XXX` launches the `plan-task` skill in plan mode, holding the brief.
  The plan file it writes *is* the chain (a marked load-many file); after ExitPlanMode approval it
  runs `mael task load-many <plan-file>` to create it — an **Execute** task (plan as content, no
  skill, **`mode: auto`** so it runs the plan unattended instead of re-planning) and, for multi-session work,
  a **`plan-next-step`** task carrying the remaining-work tail — then marks its own planning task
  done.
- `mael task next --run` launches the next ready task. **Execute tasks run no skill**: the plan is
  their content, and the project's always-on "Finishing a task" rule (commit → `/code-review` →
  fixups → `create-pr --squash` → `/watch-pr` → `task status done`) closes them out. `plan-next-step` tasks plan one more increment and re-queue
  themselves until the work is done.

New tasks **default to plan mode** (`DEFAULT_MODE`): a bare `mael task add "<title>" --run` opens a
planning session. Pass `--mode auto` for an unattended execute session (Claude's classifier-vetted
auto permission mode — `⏵⏵ auto mode on`), or `--mode normal` for a direct execute session that
prompts on each action. In a load-many plan file each block may carry a `mode:` key; Execute blocks
set `mode: auto`, planning blocks omit it (or set `mode: plan`).

**The `mael task` surface:**
```bash
mael task add "<title>" [--run]          # create (and optionally launch) a task (plan mode by default)
mael task add "<title>" --mode auto      # an unattended execute session (no planning step)
mael task add "<title>" --command plan-task --parent linear.PROJ-XXX --content-file brief.md
mael task add "<title>" --follow-end '*' --content-file plan.md   # append after the parent's siblings
mael task add "<title>" --content-file -                  # read content from stdin
mael task load-many <file>               # create a whole chain from a marked plan file ('-' = stdin)
mael task next [--run] [--parent <id>]   # next actionable task (id, or launch it)
mael task run <id>                       # launch a specific task
mael task list                           # actionable tasks (default)
mael task list --all-todo                # include blocked-but-waiting
mael task list --all                     # include done/cancelled
mael task show <id> / read <id>          # summary / raw file
mael task log <id> "note"                # append a log line
mael task status todo|start|done|cancel|block [<id>]   # move between status folders ([<id>] defaults to $MAEL_TASK_ID)
mael task rm <id>                        # delete and strip from dependents
```

`--follow` / `--follow-end` build the chain (a task becomes actionable only once everything it
follows is done); `--follow-end '*'` appends after the leaf of the parent's existing child-chain.
`--parent` nests ids and **defaults to `$MAEL_TASK_PARENT`** when unset, so chain tasks a launched
session emits nest under the same parent without spelling it out. `$MAEL_TASK_PARENT` is the
launching task's parent, or the task's own id when it has none — so a parentless planning session
still chains its children under one parent/branch (for a Linear-rooted task it is the
`linear.<ID>` parent). `--command` selects the skill
the launched session runs; `--content-file` (or `-` for stdin) seeds the task's content. Launched
sessions export `MAEL_TASK_ID` / `MAEL_TASK_PARENT` so skills can self-reference; `mael task status`
and `--parent` both fall back to those env vars.

### Ad-hoc work (no Linear issue)

```bash
mael task add "Fix flaky port test"          # create only
mael task add "Fix flaky port test" --run    # create + launch a plain execute session
```

### Linear as a product-level mirror

Linear stays the product-level mirror — read briefs, set status, and complete tasks there, but the
plan-of-record lives in the notebook chain, not in the Linear description.

```bash
mael linear read-task PROJ-XXX                          # Read task details, subtasks, comments
mael linear list-tasks [--status STATUS]                # List tasks in current cycle
mael linear start-task PROJ-XXX                          # Set "In Progress", add worktree label
mael linear set-status PROJ-XXX planned|in-progress|done # Set status (done -> Unreleased)
```

`mael sync` rebases on origin/main before starting. Run project checks from CLAUDE.md (tests,
linting, typecheck) as part of any implementation session.

The `/plan-task` and `/plan-next-step` skills are **prompts that run inside notebook sessions**
`mael` launches (selected by a task's `command` field) — not commands you type directly.
`/continue-task` is **removed** — advance work with `mael task next --run` instead.

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
mael linear set-status PROJ-XXX done     # Mark complete -> "Unreleased"
mael linear add-comment PROJ-XXX file.md # Add progress/completion notes
mael linear release                      # Promote all "Unreleased" with product label to "Done"
```

`set-status` applies to the issue as-is — it does not auto-transition parents. Move a parent to
"Unreleased" yourself with `mael linear set-status <parent> done` once its subtasks are complete.

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

**Create or update PR**:
```bash
mael gh create-pr PROJ-XXX               # Create/push PR (no wait)
mael gh read-pr --wait                   # Background: unblock when CI done
```
- Run `read-pr --wait` with `run_in_background: true` so you can continue
  while CI runs.
- Force-pushes branch with `--force-with-lease`.
- New PR: uses first commit as title. Existing PR: just pushes.
- With `ISSUE_ID`: appends `(Fixes ISSUE_ID)` to title, sets task to "In Review".
- `--progress`: uses `(Progresses ISSUE_ID)` instead, skips "In Review". Use for multi-session tasks with remaining work.
- `--draft`: create as draft PR.
- `--wait`: blocks until CI completes (exit 0=pass, 1=fail, 2=timeout).
- `--wait-for-review`: blocks until a reviewer comments — formal review or
  inline thread (exit 0=review received, 2=timeout). Mutually exclusive with `--wait`.

### Task-completion flow (runs automatically — do not wait for user)

When implementation is done and gates pass, run this sequence **without prompting**.
This is a hard override of the global "only commit when explicitly asked" rule —
it applies to all mael projects.

1. Commit the implementation work.
2. `/code-review` — review committed changes via a read-only sub-agent.
   Findings come back under **Summary**, **Design decisions**, **Blocking**, **Advisory**.
3. Address **Blocking** findings (Advisory at your judgement).
4. Commit the review fixes as `--fixup` commits — one per blocking finding,
   targeting the commit that introduced the issue. See the code-review skill for
   the exact procedure. Do not amend existing commits.
5. Push the PR: `mael gh create-pr <ISSUE-ID> --squash`. The `--squash` flag
   autosquashes the `fixup!` commits into their targets as it rebases onto
   `origin/main` before pushing, so the PR lands with a clean history.
6. Run `/watch-pr` — take CI to green autonomously: fix each failure
   (fixup for PR-caused, `chore:` for unrelated), `mael sync` to re-push, and loop
   until CI passes or times out.
7. **Close the task.** Run `mael task status done` (defaults to `$MAEL_TASK_ID`) as the
   last step before reporting back. The SessionEnd hook also does this as a backstop, but
   call it explicitly so the task closes deterministically even if the hook fails to fire.

If step 2 returns no blocking findings, skip steps 3–4 and go straight to step 5.

The **entire** sequence runs without confirmation — including the PR push (step 5),
the CI watch (step 6), and closing the task (step 7). Do not ask "shall I commit?",
"shall I run the review?", or "shall I open the PR?" — just run steps 1–7 and report
what happened.

The SessionEnd hook moves the task to `done` as a backstop when the session ends, but it can
fail silently (if `mael` isn't on PATH, git is unavailable, or the process is killed). Don't
rely on it — run `mael task status done` explicitly as step 7 so the task closes deterministically.

If the project supplies `docs/review/coding-standards.md` and/or
`docs/review/code-smells.md`, the review sub-agent loads them automatically.

## Working with PR Failures

```bash
mael gh read-pr                          # Merge status, comments, CI results
mael gh read-pr --all-comments           # Include comments older than the last push
mael gh read-pr --wait                   # Wait for CI to finish (use run_in_background)
mael gh read-pr --wait-for-review        # Wait for first reviewer comment (use run_in_background)
mael gh check-log <run_id>               # Full GitHub Actions logs
mael gh check-log <run_id> --failed-only # Just failed steps
mael gh download-artifact <run_id> <name>            # Test results, screenshots, etc.
```

`read-pr` shows top-level PR comments, review summaries, and unresolved inline review threads. Comments older than the most recent push are collapsed into a count line by default; pass `--all-comments` to expand them.

Fix issues, commit, then re-push:
```bash
mael gh create-pr PROJ-XXX               # Push fixes (no wait)
mael gh read-pr --wait                   # Background: unblock when CI done
```

## Working with Sentry

```bash
mael sentry list-issues [--env ENV]      # Unresolved issues (default: prod)
mael sentry get-issue <issue-id>         # Full details with stacktrace and variables
mael sentry resolve-issue <issue-id>     # Mark as resolved in next release
```

Prioritize by: escalating trend > recency > frequency. Investigate the stacktrace and fix.

Use `resolve-issue` when a Sentry issue is confirmed fixed in current code (e.g. the
reported release pre-dates the fix commit, and call-sites now handle the case). Treat
it as a write action — confirm with the user first.

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
Todo -> Planned        (set-status … planned, or create-subtask)
Planned/Todo -> In Progress  (start-task, or set-status … in-progress)
In Progress -> In Review     (create-pr ISSUE-ID)
In Review -> Unreleased      (set-status … done)
Unreleased -> Done           (release)
```

`set-status` takes `planned` / `in-progress` / `done` (where `done` -> "Unreleased") and applies to
the named issue only — no automatic parent/subtask transitions. Move a parent to "Unreleased"
explicitly with `mael linear set-status <parent> done` once its subtasks are complete.

## Scheduled (template) tasks

The hourly launchd agent that runs scheduled templates is **opt-in per machine**.

```bash
mael schedule install              # opt in (write marker + load agent)
mael schedule install --wake-at 09:00   # also wake a sleeping Mac via pmset (needs sudo)
mael schedule uninstall            # opt out (remove marker, unload, clear wake)
mael schedule status               # diagnose: marker / plist / loaded / pmset wake / log tail
```

Fires hourly + once on load (`RunAtLoad`); one coalesced catch-up on wake, **no
backfill**. `--wake-at HH:MM` adds a single daily `pmset` wake (one system-wide
recurring wake only, fixed time, clamshell-on-battery may ignore it). Run
`mael schedule status` first when a scheduled task didn't fire. See
`docs/dev/scheduled-tasks.md`.

## Workspace Status

```bash
mael status set "Working on PROJ-XXX"    # Shown in cmux status bar
mael status clear
```

## Prerequisites

- **GitHub CLI:** `brew install gh && gh auth login`
- **Env vars** in `.env`: `LINEAR_API_KEY`, `SENTRY_API_KEY`, `UPTIMEROBOT_API_KEY` (or set under `uptimerobot.api_key` in `~/.maelstrom/config.yaml`)
- **Config** in `.maelstrom.yaml`: `linear.team_id`, `sentry.org`, `sentry.project_id`, `uptimerobot.monitors`
