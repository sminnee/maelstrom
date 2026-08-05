# CLI reference

Every `mael` command and flag. For the reasoning behind them, read the
[guides](../guide/concepts.md).

Run `mael --help` or `mael <group> --help` to see the same information in the terminal.

## Global options

| Option | Description |
|---|---|
| `--version` | Print the version and exit. |
| `--json` | Print machine-readable JSON instead of a table. |
| `--help` | Print help and exit. |

## Targets

Most commands take an optional target in the form `project.worktree`:

```bash
mael list myproject           # every worktree in myproject
mael env start myproject.b    # bravo (shortcode)
mael close                    # the worktree you are in
```

A single letter is a shortcode for the NATO name: `a` → alpha, `b` → bravo, `c` → charlie.
Inside a worktree, maelstrom detects the project and worktree from the current directory,
so the target is optional.

---

## Worktrees

| Command | Description |
|---|---|
| `mael add [BRANCH]` | Add a worktree for `BRANCH`. Recycles a closed worktree when one exists. With no `BRANCH`, creates a fresh worktree on main and does not recycle. |
| `mael add-project GIT_URL` | Clone a repository and set it up for maelstrom. |
| `mael list [PROJECT]` | List worktrees with branch, dirty files, local commits, PR, app URL and session. |
| `mael list-all` | List worktrees across every project. |
| `mael close [TARGETS]...` | Sync, check the worktree is clean, then check out main. Keeps the folder, name and ports. |
| `mael remove TARGETS...` | Delete one or more worktrees. |
| `mael rm TARGETS...` | Alias for `mael remove`. |
| `mael sync [TARGET]` | Rebase the worktree against `origin/main`. |
| `mael sync-all [PROJECT]` | Sync every worktree in the project. |
| `mael tidy-branches [PROJECT]` | Rebase feature branches, delete merged ones, force-push unmerged ones. |

**`mael add`**

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. Default: detect from the current directory. |
| `--open` | Open the configured editor instead of a Claude session. |
| `--no-recycle` | Always create a new worktree, even when closed ones exist. |

**`mael add-project`**

| Option | Description |
|---|---|
| `--projects-dir TEXT` | Base directory for projects. Default: from `~/.maelstrom/config.yaml`, else `~/Projects`. |

**`mael close`**

| Option | Description |
|---|---|
| `--wait` | Wait for the PR to merge before closing. |
| `--timeout INTEGER` | Maximum seconds to wait for the merge. Default: 3600. |
| `--interval INTEGER` | Poll interval in seconds. Default: 30. |
| `--force` | Close incomplete work too. Aborts an in-progress sync, commits uncommitted changes as `wip: uncommitted changes`, keeps the branch and PR, and creates a "Reopen" task. |

**`mael remove` / `mael rm`**

| Option | Description |
|---|---|
| `-f`, `--force` | Skip the confirmation prompt for modified or untracked files. |

**`mael sync`**

| Option | Description |
|---|---|
| `--squash` | Autosquash `fixup!` commits while rebasing onto `origin/main`. |
| `--abort` | On conflict, abort the rebase and restore the worktree. |
| `--close` | If the branch is empty after the rebase, delete it (local and remote) and close the worktree. |

---

## Sessions and workspaces

| Command | Description |
|---|---|
| `mael open [TARGET]` | Start a Claude Code session in a worktree. |
| `mael claude [TARGET]` | Same as `mael open`. |
| `mael ide [TARGET]` | Open a worktree in the configured editor. |
| `mael session list` | List active Claude Code sessions. |
| `mael session record EVENT` | Update session state from a Claude Code hook event. Reads the payload as JSON on stdin. Not meant for humans. |
| `mael cmux status` | Report whether maelstrom can place a session into cmux. Starts cmux if it is down. Exits non-zero when cmux cannot be reached. |
| `mael status set TEXT` | Set the workspace status text shown in the cmux status bar. |
| `mael status clear` | Clear the workspace status. |

None of these take options beyond `--help`.

---

## Tasks

The task notebook. See [tasks.md](../guide/tasks.md).

| Command | Description |
|---|---|
| `mael task add [TITLE]` | Create a task and print its id. |
| `mael task load-many FILE` | Create a chain of tasks from a marked plan file. `-` reads stdin. |
| `mael task next` | Print the id of the next actionable task. |
| `mael task run ID` | Launch a task as a Claude session. Creates its worktree first. |
| `mael task list` | List actionable tasks. |
| `mael task show ID` | Show a summary of a task. |
| `mael task read ID` | Print the raw task file. |
| `mael task edit ID` | Open the task file in `$EDITOR` (default `vi`). Commits if it changed. |
| `mael task update ID [TITLE]` | Update a task's fields. |
| `mael task rm ID` | Delete a task and strip it from any dependents' `follows` lists. |
| `mael task log ID MSG` | Append a line to a task's log. |
| `mael task status <state> [ID]` | Move a task between lifecycle states. |
| `mael task prompt ID` | Print the initial Claude prompt for a task. |
| `mael task reconcile` | Reconcile in-progress tasks against live Claude sessions. |
| `mael task reindex` | Rebuild the metadata index from the notebook across all projects. |
| `mael task add-scheduled` | Fire every due template: duplicate it into a dated run and advance its watermark. |

Every task command takes `--project TEXT` (default: from the current directory), except
`reindex`, which spans all projects.

**`mael task add`**

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. |
| `-c`, `--command TEXT` | Skill the launched session runs. |
| `-m`, `--mode TEXT` | Session mode. Default `plan`. Use `auto` for an unattended execute session, `normal` for a non-planning session that prompts. |
| `-b`, `--branch TEXT` | Branch for the task. Default `task/<id>`. |
| `-P`, `--parent TEXT` | Parent task id. Creates a child id. Defaults to `$MAEL_TASK_PARENT`. |
| `--pre-action TEXT` | Lifecycle action fired when the task starts, e.g. `linear.in-progress`. |
| `--post-action TEXT` | Lifecycle action fired when the task finishes, e.g. `linear.done`. |
| `--priority [critical\|high\|medium\|low]` | Task priority. Default `medium`. Affects list ordering and `task next`. |
| `--model TEXT` | LLM model for the session, e.g. `opus` or a full id. Default: your Claude Code default. |
| `--follow TEXT` | Id this task follows. Repeatable. |
| `--follow-end TEXT` | Follow the end leaves of the given id's follows-chain. Repeatable. Quote `"*"`. |
| `--content-file TEXT` | File whose contents become the task's Content section. `-` reads stdin. |
| `--from TEXT` | Seed the new task by duplicating this task's recipe. Other flags override. |
| `--template` | Park the new task in `template` status: a reusable, non-actionable recipe. |
| `--schedule TEXT` | Cron expression. Acted on only for template tasks, e.g. `'0 9 * * 1-5'`. |
| `-e`, `--edit` | Open the new task in `$EDITOR` after creating it. |
| `-r`, `--run` | Launch the task as a session immediately. |
| `--here` | With `--run`, launch in the current shell. No worktree, no new workspace. |

**`mael task update`**

Takes `--id` plus the same field flags as `add`: `--command`, `--mode`, `--branch`,
`--pre-action`, `--post-action`, `--priority`, `--model`, `--schedule`, `--content-file`.

| Option | Description |
|---|---|
| `--id TEXT` | Re-key the task, rewriting `follows` and `parent` references that point at it. |

Pass `''` to `--pre-action`, `--post-action`, `--model` or `--schedule` to clear the field.

**`mael task list`**

| Option | Description |
|---|---|
| `--status TEXT` | Filter by status (folder). |
| `--parent TEXT` | Filter by parent id. |
| `--all-todo` | Also show blocked-but-todo tasks. Still hides done and cancelled. |
| `--all` | Show everything, including done and cancelled. Takes precedence over `--all-todo`. |

**`mael task next`**

| Option | Description |
|---|---|
| `--parent TEXT` | Restrict to children of this id. |
| `--run` | Launch the next actionable task as a session. |
| `-b`, `--branch TEXT` | Restrict strictly to this branch. No fallback to other branches. |
| `--here` | With `--run`, launch in the current shell. |

By default `next` prefers a task on the current git branch, then falls back to the global
next task.

**`mael task run`**

| Option | Description |
|---|---|
| `--here` | Launch in the current shell. No worktree, no new workspace. |

**`mael task load-many`**

| Option | Description |
|---|---|
| `--run` | Launch every unblocked task created. Blocked tasks wait for `mael task next --run`. |
| `--here` | With `--run`, launch only the head task in the current shell. |

**`mael task status`**

| Command | Moves the task to |
|---|---|
| `mael task status todo [ID]` | `todo/` |
| `mael task status start [ID]` | `in-progress/` |
| `mael task status done [ID]` | `done/` |
| `mael task status block [ID]` | `blocked/` |
| `mael task status cancel [ID]` | `cancelled/` |
| `mael task status template [ID]` | `template/` |

`ID` defaults to `$MAEL_TASK_ID`, so a session can close its own task with
`mael task status done`.

**`mael task reconcile`**

| Option | Description |
|---|---|
| `--fix` | Apply the suggested corrections. Without it, prints a dry-run table only. |

**`mael task add-scheduled`**

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. |
| `--all-projects` | Scan every maelstrom project. This is the launchd entry point. |
| `--run` | Launch each due run into a session. |
| `--here` | With `--run`, launch in the current shell. |

---

## Dev environments

See [dev-environments.md](../guide/dev-environments.md).

| Command | Description |
|---|---|
| `mael env start [TARGET]` | Run the install command, then start every service. |
| `mael env stop [TARGET]` | Stop every service. SIGTERM, then SIGKILL after 10s. |
| `mael env restart [TARGET]` | Restart services. |
| `mael env status [TARGET]` | Show service PIDs, status and log paths. |
| `mael env logs [TARGET] [SERVICE]` | Show service logs. |
| `mael env list [PROJECT]` | List running environments for a project. |
| `mael env list-all` | List running environments across every project. |
| `mael env stop-all` | Stop every running environment. |
| `mael env reset [TARGET]` | Regenerate the `.env` file, e.g. after changing ports in `.maelstrom.yaml`. |
| `mael env open [TARGET]` | Open the browser pane for a running environment. |

| Command | Option | Description |
|---|---|---|
| `env start` | `--skip-install` | Skip the install step before starting. |
| `env restart` | `--install` | Run the install step before starting. |
| `env logs` | `-n INTEGER` | Number of lines to show. |
| `env logs` | `-f`, `--follow` | Follow log output. |

---

## GitHub

| Command | Description |
|---|---|
| `mael gh create-pr [ISSUE_ID]` | Create a PR for the current worktree, or push if one exists. |
| `mael gh read-pr [TARGET]` | Read PR status, comments and check results. |
| `mael gh show-code [TARGET]` | Show commits and uncommitted changes for a worktree. |
| `mael gh check-log RUN_ID` | Show full log output for a GitHub Actions run. |
| `mael gh download-artifact RUN_ID ARTIFACT_NAME` | Download an artifact from a workflow run. |
| `mael gh wait-for-pr [TARGET]` | Wait for CI checks to finish on the current PR. |

**`mael gh create-pr`**

With `ISSUE_ID` (e.g. `ME-41`), appends `(Fixes ISSUE_ID)` to the PR title for Linear
auto-linking and sets the Linear issue to "In Review".

| Option | Description |
|---|---|
| `--draft` | Create as a draft PR. |
| `--progress` | Use `(Progresses ISSUE_ID)` instead of `Fixes`, and do not set "In Review". For multi-session tasks with work remaining. |
| `--wait` | Wait for CI checks to finish after creating the PR. |
| `--wait-for-review` | Wait until a reviewer leaves feedback. Exits 0 on the first review, 2 on timeout. |
| `--squash` | Autosquash `fixup!` commits before pushing. |
| `--target TEXT` | Project/worktree target for directory resolution. |

**`mael gh read-pr`**

| Option | Description |
|---|---|
| `--wait` | Wait for CI checks to finish. Exit 0 = pass, 1 = fail, 2 = timeout. |
| `--wait-for-review` | Wait until a reviewer leaves feedback. Exits 0 on the first review, 2 on timeout. |
| `--all-comments` | Include comments made before the last pushed commit. |

**`mael gh show-code`**

| Option | Description |
|---|---|
| `--committed` | Show only committed changes. |
| `--uncommitted` | Show only uncommitted changes. |

**`mael gh check-log`**

| Option | Description |
|---|---|
| `--failed-only` | Show only failed step logs. |

**`mael gh wait-for-pr`**

| Option | Description |
|---|---|
| `--timeout INTEGER` | Timeout in seconds. Default: 1800. |
| `--interval INTEGER` | Poll interval in seconds. Default: 30. |

Exit codes: 0 = passed, 1 = failed, 2 = timeout.

---

## Git helpers

| Command | Description |
|---|---|
| `mael git status [TARGET]` | Show a compact git status summary. |
| `mael git squash [TARGET]` | Rebase onto `origin/main`, autosquashing `fixup!` commits. Does not push. |
| `mael git merge [TARGET]` | Rebase the current branch onto main, fast-forward main to it, and push. |

**`mael git merge`**

| Option | Description |
|---|---|
| `--close` | After merging, close the worktree and delete the feature branch. |
| `--no-squash` | Skip autosquashing `fixup!` commits during the rebase. |

---

## Linear

See [integrations.md](../guide/integrations.md).

| Command | Description |
|---|---|
| `mael linear plan ISSUE_ID` | Seed a notebook planning task from a Linear issue and launch it. |
| `mael linear list-tasks` | List tasks in the current cycle, or all active tasks if there is no cycle. |
| `mael linear read-task ISSUE_ID` | Read task details as markdown. |
| `mael linear create-task TITLE [DESCRIPTION]` | Create a task in the project backlog. |
| `mael linear create-subtask PARENT_ID TITLE [DESCRIPTION]` | Create a subtask on a parent issue. |
| `mael linear start-task ISSUE_ID` | Set to "In Progress" and add the workspace label. |
| `mael linear set-status ISSUE_ID {planned\|in-progress\|done}` | Set the issue status. `done` maps to "Unreleased". |
| `mael linear add-comment ISSUE_ID COMMENT_FILE` | Add a comment from a markdown file. |
| `mael linear write-plan ISSUE_ID PLAN_FILE` | Write a plan into the issue description. |
| `mael linear read-plan ISSUE_ID` | Read the plan from the issue description. |
| `mael linear edit-plan ISSUE_ID OLD_ARG NEW_ARG` | Search and replace within the plan section. |
| `mael linear release` | Promote every "Unreleased" issue with the product label to "Done". |

`set-status` applies to the named issue only. It does not transition parents or subtasks.

**`mael linear plan`**

Runs by default: the planning session launches immediately. It takes the same
block-settable options as `mael task add` — `--project`, `-c/--command`, `-m/--mode`,
`-b/--branch`, `-P/--parent`, `--pre-action`, `--post-action`, `--priority`, `--model`,
`--follow`, `--follow-end`, `--here` — plus:

| Option | Description |
|---|---|
| `--run` / `--no-run` | Launch the planning session immediately. Default: run. |

The planning defaults (`plan-task` command, `plan` mode, `opus` model, the
`linear.<ID>` parent, `linear.planned` post-action) are defaults the matching flag
overrides. Passing an explicit empty value, e.g. `--post-action ''`, clears the field.

**`mael linear list-tasks`**

| Option | Description |
|---|---|
| `--status TEXT` | Filter by status name. Partial match. |

**`mael linear edit-plan`**

| Option | Description |
|---|---|
| `-s`, `--string` | Treat the arguments as literal strings instead of file paths. |

---

## Sentry

| Command | Description |
|---|---|
| `mael sentry list-issues` | List unresolved issues for the project. |
| `mael sentry get-issue ISSUE_ID` | Get issue details as markdown, with stacktrace and variables. |
| `mael sentry resolve-issue ISSUE_ID` | Mark an issue as resolved in the next release. |

**`mael sentry list-issues`**

| Option | Description |
|---|---|
| `--env TEXT` | Environment filter. Default: `prod`. |
| `--since TEXT` | Only issues last seen in this window, e.g. `30m`, `24h`, `7d`. Default: all unresolved issues. |

---

## Slack

| Command | Description |
|---|---|
| `mael slack post [MESSAGE]` | Post a message to Slack. Reads stdin when `MESSAGE` is omitted. |

| Option | Description |
|---|---|
| `--channel TEXT` | Webhook name from `slack.webhooks`. Default: the first one defined. |

---

## UptimeRobot

| Command | Description |
|---|---|
| `mael uptimerobot status` | Show current status and uptime of the configured monitors. |
| `mael uptimerobot outages` | List recent outage log entries across the configured monitors. |
| `mael uptimerobot monitors` | List every monitor on the account, ignoring project config. Use this to discover ids. |

**`mael uptimerobot outages`**

| Option | Description |
|---|---|
| `--since TEXT` | Time window, e.g. `30m`, `24h`, `7d`. Default: `24h`. |
| `--limit INTEGER` | Maximum log entries per monitor. Default: 20. |

With no monitors configured, these commands fall back to every monitor on the account.

---

## Scheduled work

See [scheduled-work.md](../guide/scheduled-work.md). The agent is opt-in per machine.

| Command | Description |
|---|---|
| `mael schedule install` | Opt this machine in: write the marker and load the launchd agent. |
| `mael schedule uninstall` | Opt this machine out: remove the marker and tear the agent down. |
| `mael schedule status` | Report the marker, plist, loaded job, `pmset` wake and log tail. |

**`mael schedule install`**

| Option | Description |
|---|---|
| `--wake-at HH:MM` | Schedule a daily `pmset` wake so a sleeping Mac runs the job. Needs sudo. `HH:MM` is local time. One system-wide repeating wake only; it replaces any prior one and is set one minute before `HH:MM`. Clamshell-on-battery laptops may ignore it. |

---

## Maintenance

| Command | Description |
|---|---|
| `mael doctor [PROJECT]` | Check project health and fix issues automatically. |
| `mael install` | Install maelstrom's Claude Code skills and hooks into `~/.claude/`. |
| `mael self-update` | Update maelstrom to the latest version from git. |
| `mael session-channel` | Launch the Bun-based session-tracking MCP channel. Invoked by Claude Code, not by humans. |

**`mael install`**

| Option | Description |
|---|---|
| `--no-monitor` | Skip the session-tracking MCP channel, its hooks and its dependencies. |

