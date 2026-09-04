# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Anything a user would notice goes under `Unreleased` as it lands. `bin/publish` refuses to
release while that section is empty, and retitles it to the version it is releasing as it tags.

## [Unreleased]

### Changed

- **The orchestrator UI reads the world over REST.** The server serves every table under `/api`,
  sends change notices on one stream, and streams each open agent's transcript on a socket of its
  own with a resume cursor, so a reload fetches slim task rows instead of one snapshot holding
  every task's text, and a dropped connection shows a banner over the last known state instead
  of an empty canvas. Every control that sends a command shows its own pending and failed state.
  The in-browser fake backend and its FAKE chip are gone; the app runs against a server only.
  `VITE_ORCHESTRATOR_URL` is no longer read: the web dev server proxies `/api` to
  `ORCHESTRATOR_URL` instead.

- **The agent daemon's attach stream is gapless.** Every event it records carries a `mael_seq`,
  the backlog marker names the agent's `epoch` and the seq it reached, and `attach` takes `from`
  and `epoch` to replay only what a client missed. Events a client cannot see any more — a ring
  that rolled past its cursor, a queue that overflowed — arrive as a `mael_truncated` marker with
  the count, which the orchestrator shows as a gap in the transcript and `mael agent tail` prints
  as a line. A daemon restart is a new epoch, so a cursor from before it is ignored.

### Added

- **A task's status is settable from its card.** The expanded node's state strip carries the same
  status picker the task list has, so a decision taken on the canvas does not need a trip to the
  list. With the picker open, Esc closes the picker and leaves the card open. A free agent has no
  task, so its card has no status control.

- **Driven agents survive a crash.** A driven agent writes a normal Claude session transcript, so
  `mael agent resume ID` starts an exited agent again under its own id, with the conversation it
  had. `--text TEXT` replaces the default first turn. A daemon start resumes every agent that was
  running when the last daemon died, so restarting the daemon to pick up new code costs nothing.
  `mael agent stop ID` keeps the agent's spawn record but stops it being brought back, so a
  deliberately stopped agent stays resumable. The daemon keeps one spawn record per agent under
  `~/.maelstrom/agents/`, overridable with `MAEL_AGENT_SPEC_DIR`.

- **`mael agent list --stopped` names every session you can resume.** `mael agent resume ID` needs
  an id, and nothing printed the id of a session that had stopped. The listing reads Claude's own
  session transcripts for what each session was doing, and each spawn record for how to start it
  again. `--all` shows running and stopped together, and `-w PROJECT.WORKTREE` or `--project NAME`
  narrows the listing to one place.

- **The desk.** The orchestrator canvas draws the work on your desk, not every task in the world.
  A new task list view lists every task, with filters for status, project, branch and text, and
  each row adds its task to the desk or takes it off. Launching a task from the UI puts it on
  the desk. The desk is kept at `~/.maelstrom/desk.json`, so it survives a restart.

- **Every running agent is on the canvas.** Anything running is drawn whether or not you put it
  there, and joins the desk by itself. An agent you started by hand in a worktree, with no task
  behind it, draws as its own node named after the worktree it runs in. The desk entry outlives
  the agent, so stopped work stays on the canvas until you dismiss it from its card. The task
  list still lists tasks only.

- **`mael agent show ID`.** Prints one agent in full: what it last said, every option of a
  question with its description, the plan text of a plan review, and the command that answers
  the wait. `--json` emits the detail as JSON. Works on an exited agent.

- **`mael agent tail ID`.** Prints an agent's events and stops, without driving it. `-f` keeps
  streaming. The read-only half of `mael agent attach`.

- **The agent daemon starts on demand.** The first `mael agent` command that needs a daemon
  starts one, in its own process group, logging to `~/.maelstrom/agent-daemon.log`.
  `MAEL_AGENT_NO_AUTOSTART=1` turns that off; `MAEL_AGENT_LOG` moves the log.

- **`last_message` column on `mael agent list`.** What each agent last said, cut to one line.

- **`args:` on a container service.** A container service in `.maelstrom.yaml` can pass
  arguments to its image, e.g. `args: ["-c", "max_locks_per_transaction=1024"]` to raise a
  Postgres lock limit.

- **Optional services.** A service in `.maelstrom.yaml` can set `optional: true`.
  - `mael env start` skips it; `mael env start ladle` starts that one service alone.
  - `mael env stop ladle` and `mael env restart ladle` take a service name too.
  - Optional services still own their declared ports, so marking one optional never
    renumbers the services after it.
  - A service cannot be both `optional` and `shared`. Named services need a `services:`
    block; a Procfile project reports an error.

- **`/code-review` reviews prose with its own sub-agent.** Alongside the per-commit code
  reviewers, one further sub-agent reads the whole branch's comments, docstrings and documents.
  It sweeps the repo for the same explanation written in several places, and names which copy to
  keep. Prose no longer competes with architecture findings for a commit reviewer's attention,
  and duplication that spans files is now visible. The agent is skipped on a branch that changes
  no prose. A cut in a file the branch never touched is raised with you, never applied silently.

- **opencode compatibility.** Shared skills now set `opencode/slash: true` in their frontmatter,
  so they appear in opencode's interactive `/` command catalog (opencode registers skills from
  `~/.claude/skills` but only lists them when the flag is present). `mael add` and worktree
  reuse also generate a gitignored `AGENTS.md` per worktree — the same content as `CLAUDE.md`
  with `@` imports inlined — because opencode reads only `AGENTS.md` and does not resolve
  `@` imports.

- **Read a task's status.** `mael task get-status [ID]` prints the status word by itself, and
  falls back to `$MAEL_TASK_ID` like the other task commands. `mael task current` prints the
  session's task as `ID:STATUS`, for a shell prompt or status line: it prints an empty line and
  exits 0 when there is no task, so a prompt keeps rendering wherever it runs.

### Changed

- **`mael env start`, `stop`, `restart` and `logs` take a service, not a worktree.** Their
  argument named a worktree before. It now names a service, and the worktree moves to
  `-w`/`--worktree`, defaulting to the current directory as it always did. One argument cannot
  mean both: maelstrom would have had to read `.maelstrom.yaml` to tell `ladle` the service from
  `ladle` the worktree, and reject any name that was both.
  - `mael env start myproject.b` becomes `mael env start -w myproject.b`.
  - `mael env logs myproject.b web` becomes `mael env logs web -w myproject.b`.
  - `mael env status`, `reset`, `open` and `list` still take a worktree target.

- **The five slash commands are now skills.** `/plan-task`, `/plan-next-step`, `/reopen-branch`,
  `/resolve-rebase-conflicts` and `/watch-pr` moved from `shared/commands/` to `shared/skills/`,
  and `mael install` now links them into `~/.claude/skills/` instead of `~/.claude/commands/`.
  You type them exactly as before. This makes them visible to opencode, which reads global skills
  from `~/.claude/skills/` but reads commands only from its own directory. After upgrading, delete
  the five stale links left under `~/.claude/commands/` — `mael install` no longer visits that
  directory, so it cannot clear them for you.

- **`mael list` is roughly twice as fast.** On a project with 6 open and 12 closed worktrees it
  went from 7.5s to about 3s. Two lookups that ran once per worktree now run once per project:
  the pull request lookup is a single GraphQL query for every open pull request, and the
  closed-worktree check is a single `rev-list`. The table is unchanged. When the batched pull
  request lookup fails, each branch falls back to its own lookup, so a failure costs one blank
  row rather than a blank column.

## [0.1.2] - 2026-08-11

### Added

- **Published to PyPI as `sminnee-maelstrom`.** Install with `uv tool install sminnee-maelstrom`,
  or run it without installing via `uvx sminnee-maelstrom <command>`, instead of installing from
  the git URL.
- **The task notebook.** `mael task` is a git-backed notebook of agent work. Each task is a
  markdown file holding a plan, and launches exactly one Claude session. Tasks chain with
  `--follow` and `--follow-end`, group under a `--parent` that gives the chain one branch and one
  pull request, and carry a priority, a model, a mode and a branch. `mael task next --run`
  advances the chain, taking the current branch into account; `mael task load-many` creates a
  whole chain from a plan file, and `--run` launches every task in the batch that is not blocked.
  A task parked in `blocked/` never launches and is hidden from the default `mael task list`,
  which `--all-todo` reveals. A task closes automatically when its agent session ends.
  Supporting commands: `list`, `add`, `rm`, `update`, `edit`, `prompt`, `status`, and `reconcile`.
- **Scheduled tasks.** A task parked in `template/` with a `schedule` becomes a recipe that fires
  on its own. `mael schedule install` sets up the hourly launchd agent — no sudo needed, because
  launchd runs a job missed during sleep on the next wake by itself. `mael schedule status`
  explains why a run did or did not happen, and each firing is timestamped in `schedule.log`.
  Scheduled runs inherit their template's branch and root their own chain. Schedule matching and
  the date in an inferred task id both follow the machine's local timezone, not UTC.
- **cmux workspace integration.** Sessions run in a named cmux workspace with a fixed three-pane
  layout — the Claude session, a shell, and browsers. Maelstrom creates the workspace on
  `mael add`, opens browsers on `mael env start`, closes them on `mael env stop`, and closes the
  workspace on `mael close`. Pull request URLs reuse an existing browser pane rather than opening
  another one or stealing focus. `mael add` on a branch that is already open joins its live
  workspace instead of recycling a worktree. `mael status set`/`clear` drives the cmux status
  line.
- **Session tracking.** `mael session list` shows live sessions, established from the running
  `claude` processes rather than from a state file. A task resolves to a deterministic session
  id, so maelstrom refuses to launch a task that is already running, can resume a stopped
  session, and can tell a task that never ran from one that finished.
- **Dev environment commands.** `mael env restart` and `mael env reset` join `start`/`stop`;
  `reset` regenerates `.env` files after a port change. Services are declared as structured
  `services:` entries in `.maelstrom.yaml`, with `engine:` support for docker and
  apple-container. Shared services start once per project rather than once per worktree.
- **Project management commands.** `mael create-project` starts a new project from scratch and
  `mael mv-project` renames one, alongside the existing `add-project`.
- **Git commands.** `mael git status` prints a compact summary, `mael git squash` runs an
  autosquash rebase without pushing, and `mael git merge` merges a branch back to main locally.
  `mael sync` gained `--abort`, `--close` and `--squash`.
- **`mael doctor`.** Checks the local setup and reports what is wrong with it.
- **`mael wiki`.** A cross-project wiki of development patterns, stored in the same git-backed
  store as the task notebook.
- **Linear commands.** `mael linear plan` turns an issue into a planning task and runs it,
  `create-task` files a standalone backlog issue, `edit-plan` updates a multi-session plan in
  place, and `set-status` replaces the old `complete-task` with the full
  `planned | in-progress | done` transition set. Task lifecycle actions sync status to Linear
  automatically, and images in a Linear description are copied into the task repo so they survive
  offline.
- **Sentry commands.** `mael sentry resolve-issue` closes an issue once it is confirmed fixed, and
  `list-issues` gained a `--since` window filter.
- **UptimeRobot commands.** `mael uptimerobot status` answers whether anything is down now, and
  `outages` investigates recent incidents.
- **`mael slack post`.** Posts a message through a webhook, rendering Markdown and splitting long
  posts across several blocks.
- **Pull request tooling.** `mael gh create-pr` gained `--progress` for multi-session work,
  `--wait` to block on CI, `--wait-for-review` to block on a reviewer, and `--squash` to
  autosquash fixup commits as it rebases. `mael gh read-pr` takes `--wait` and
  `--wait-for-review` too, and shows every comment, split by whether it predates the most recent
  push.
- **`mael close --wait` and `mael close --force`.** `--wait` closes the worktree once its pull
  request merges; `--force` closes with incomplete work, committing it as `wip: uncommitted
  changes` rather than discarding it.
- **Skills.** `/code-review` reviews a branch one commit at a time against a shared review guide,
  skipping commits it has already reviewed and capping each run at eight. `/watch-pr` takes CI to
  green on its own. `/review-project-hygiene` audits a project against a hygiene checklist. Five
  general-purpose skills and a `writing-for-humans` skill ship alongside them.
- **Secret handling.** Files holding secrets are written 0600 inside a locked transaction, and
  `mael doctor` reports any that are not.

### Changed

- **Sentry configuration keys are nested.** `sentry_org` and `sentry_project` in `.maelstrom.yaml`
  become `sentry.org` and `sentry.project_id`, matching the Linear block. An existing config needs
  updating by hand.
- **Sessions launch only through the cmux socket.** There is no implicit fallback to running
  Claude in the local shell — a session you cannot watch is worse than a failure. `--here` is the
  deliberate escape hatch for a local run.
- **`mael open` launches Claude**, not an editor. `mael ide` opens the editor and `mael env open`
  opens the app. `mael add` no longer opens VS Code by default.
- **New tasks default to plan mode.** A bare `mael task add "…" --run` opens a planning session.
  Use `--mode auto` for an unattended execute session, or `--mode normal` to be prompted per
  action.
- **Pull request titles** use the `(Fixes ISSUE-ID)` suffix instead of an `[ISSUE-ID]` prefix.
- **Branch names are generated from the task title** rather than from the worktree, and the first
  child of a parent inherits the parent's branch so a chain lands as one pull request.
- **Closing or removing a worktree stops its environment first**, so services are not left
  orphaned, and `mael close` also stops any Claude session running in it.
- **`mael add` prints the app URL** instead of dumping the whole generated `.env`.
- **Worktree `.env` files keep their source template.** `$VAR` references are resolved at write
  time and the original text is preserved in a `# source:` comment, so values can be re-resolved
  when ports change. New variables added in a worktree are copied back to the project `.env`.
- **Claude Code memory and settings are shared across worktrees** through symlinks, and each
  worktree gets a generated `.claude/CLAUDE.local.md` instead of an edited `CLAUDE.md`.
- **`mael list` drops the IDE column** and reconciles state from session discovery.
- **Project roots use `core.bare=true`**, which removes a phantom worktree entry, and main is
  checked out into `_main` rather than into the `alpha` worktree.
- **Documentation was overhauled.** `CONTEXT.md` is now the domain glossary, `docs/guide/` and
  `docs/reference/` cover the workflow and the full command and configuration surface, and
  `docs/dev/` covers architecture.

### Removed

- **`mael review-prepare`** and the `/review-branch` skill, both replaced by `/code-review`.
- **`mael linear complete-task`**, replaced by `mael linear set-status`.
- **The `claude()` shell wrapper.** Maelstrom no longer installs it.
- **`project-skeleton`** and `learn-project-structure`, replaced by `/review-project-hygiene`.
- **`mael gh download-artifact -o/--output`**, now that downloads always go to `$TMPDIR`.

### Fixed

- **`mael linear release` releases every unreleased issue.** It passed no page size to Linear, so
  it silently promoted only the first 50 and reported that count as the whole job — the failure
  only appearing at the scale where the command matters. It now paginates, gained `--dry-run`, and
  continues past a single failing issue while still exiting non-zero.
- **`mael self-update` re-installs dependencies after pulling.** It previously ran `git pull`
  without re-resolving them, so an update that added a dependency left the environment missing the
  package and every command importing it failed with `ModuleNotFoundError`.
- **`mael gh download-artifact` writes to `$TMPDIR`** rather than the current directory. The
  `-o/--output` option is gone, and the extracted files are listed after the download.
- **Full pull request review comments are shown** instead of being truncated to 100 characters.

## [0.1.1] - 2026-02-28

The first published release. It predates this changelog, so its contents are not itemised here.
