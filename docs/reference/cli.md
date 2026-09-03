# CLI reference

Every `mael` command and flag. For the reasoning behind them, read the
[guides](../guide/concepts.md).

Run `mael --help` or `mael <group> --help` to see the same information in the terminal.

## Global options

| Option | Description |
|---|---|
| `--version` | Print the version and exit. |
| `--json` | Print machine-readable JSON instead of a table. Honoured by `mael list-all`, `mael project list` and `mael git status` only; every other command ignores it. |
| `--help` | Print help and exit. |

```bash
mael --version
mael --json list-all         # one of the three commands that honour --json
mael task add --help         # per-command flags, straight from the source
```

## Targets

Most commands take an optional target in the form `project.worktree`:

```bash
mael list myproject           # every worktree in myproject
mael list myproject.b         # bravo (shortcode)
mael close                    # the worktree you are in
```

A single letter is a shortcode for the NATO name: `a` → alpha, `b` → bravo, `c` → charlie.
Inside a worktree, maelstrom detects the project and worktree from the current directory,
so the target is optional.

`mael env start`, `mael env stop`, `mael env restart` and `mael env logs` are the exception:
their argument names a **service**, and the worktree comes from `--worktree` or the current
directory.

---

## Worktrees

| Command | Description |
|---|---|
| `mael add [BRANCH]` | Add a worktree for `BRANCH`, and rebase `BRANCH` onto its base before the session starts. A new branch stacks on the project's stack tip. Recycles a closed worktree when one exists. With no `BRANCH`, creates a fresh worktree detached at `origin/main`: there is no branch to rebase, and no worktree is recycled. |
| `mael add-project GIT_URL` | Clone a repository and set it up for maelstrom. |
| `mael create-project NAME` | Create a GitHub repository with the maelstrom stub files, check it out, and open a worktree on `feat/start-project`. |
| `mael mv-project OLD NEW` | Rename a project and everything derived from its name. |
| `mael list [PROJECT]` | List open worktrees with branch, dirty files, local commits, PR, app URL and session. Closed worktrees are named on one line under the table. See [listing.md](../guide/listing.md) for what each column means. |
| `mael list-all` | List worktrees across every project. Adds a `PROJECT` column, and names worktrees by folder rather than by NATO name. Supports `mael --json list-all`. |
| `mael project list` | List every project under the projects directory, with its path and worktree count. Reads no git status, no ports and no sessions, so it stays fast where `mael list-all` does not. Supports `mael --json project list`. |
| `mael close [TARGETS]...` | Sync, check the worktree is clean, then check out main. Keeps the folder, name and ports. |
| `mael remove TARGETS...` | Delete one or more worktrees. |
| `mael rm TARGETS...` | Alias for `mael remove`. |
| `mael sync [TARGET]` | Rebase the worktree against its base (`origin/main` unless the branch is stacked). |
| `mael sync-all [PROJECT]` | Sync every worktree in the project, parents before their children. |
| `mael tidy-branches [PROJECT]` | Rebase feature branches, delete merged ones, force-push unmerged ones. Skips any branch another branch is stacked on. |
| `mael base [TARGET]` | Show the branch this worktree's work is stacked on. |
| `mael stack-tip [BRANCH]` | Show or move the branch new worktrees stack on. `main` resets it. |
| `mael promote [TARGET]` | Move this branch to the bottom of its stack so it can merge first. |
| `mael eject [TARGET]` | Pull this branch out of its stack onto `main`, leaving the rest alone. |

```bash
mael add-project https://github.com/org/repo.git  # clone into maelstrom's layout
mael add feature/avatar-upload                 # worktree for a branch, ports allocated
mael project list
mael close                                        # done: reset, keep name and ports
```

**`mael add`**

On checkout, `mael add` (re)generates the worktree's `.claude/CLAUDE.local.md` and `AGENTS.md`
(same content, with `@` imports inlined so opencode can read it — opencode reads only `AGENTS.md`
and does not resolve `@` imports). Both are gitignored per worktree.

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. Default: detect from the current directory. |
| `--open` | Open the configured editor instead of a Claude session. |
| `--no-recycle` | Always create a new worktree, even when closed ones exist. |
| `--base TEXT` | Stack the new branch on this branch. Default: the project's stack tip. Use `main` to start unstacked. |

**`mael add-project`**

| Option | Description |
|---|---|
| `--projects-dir TEXT` | Base directory for projects. Default: from `~/.maelstrom/config.yaml`, else `~/Projects`. |

**`mael create-project`**

`NAME` is the repository name. Use `owner/name` to create the repository in an
organization. The seed commit holds `.gitignore` (which ignores the per-worktree `.env` and
`.claude/CLAUDE.local.md` and `AGENTS.md`), a commented `.maelstrom.yaml`, `README.md` and `CLAUDE.md`.

The project is cloned over HTTPS, whatever `gh config get git_protocol` reports. Agents push
with a token over HTTPS, so an SSH remote breaks an unattended session. `mael add-project`
takes the URL you give it and does not rewrite the protocol.

| Option | Description |
|---|---|
| `--public` | Create a public repository. Default: private. |
| `--description TEXT` | Repository description. |
| `--projects-dir TEXT` | Base directory for projects. Default: from `~/.maelstrom/config.yaml`, else `~/Projects`. With a directory other than the configured one, no worktree is opened: `mael add` finds projects only in the configured directory. |

**`mael mv-project`**

A project name is load-bearing. The name is not stored as a field — it *is* the
directory name. The worktree folders, task and env directories, port allocations
and Claude Code state all follow from it. Do not rename a project with `mv`. Use this
command, which moves the directory and updates each of those.

Run it with `--dry-run` first to see the full plan.

| Option | Description |
|---|---|
| `--dry-run` | Show the plan without changing anything. |
| `-f`, `--force` | Stop running environments and Claude sessions instead of refusing. |
| `--git-url URL` | Also point `origin` at `URL`. |

The command refuses to run while the project has a running environment or a live
Claude session. Dirty worktrees are allowed.

Out of scope — these are not changed:

- **Claude session ids.** They derive from the project name, so a rename orphans
  every existing session. `mael task run` starts a fresh session instead of
  resuming. The plan reports how many tasks this affects.
- **`remote.origin.url`**, unless you pass `--git-url`.
- **Committed files.** A project name in `README.md` or `CLAUDE.md` stays as it is.

Run `mael doctor NEW` afterwards.

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
| `--squash` | Autosquash `fixup!` commits while rebasing onto the base. A `fixup!` aimed at a commit in the *parent* branch does not squash — put it in the parent's worktree. |
| `--base TEXT` | Stack this branch on `TEXT` before rebasing. `main` unstacks it. Rejects a self-base or a cycle with exit code 1. |
| `--abort` | On conflict, abort the rebase and restore the worktree. |
| `--close` | If the branch is empty after the rebase, delete it (local and remote) and close the worktree. |
| `--autorepair` | On conflict, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue the rebase. Announces the repair, then streams the session's output to the console. Supersedes `--abort`: a failure aborts and restores the worktree, except where the session finished the rebase on another branch and there is nothing to abort. |

```bash
mael sync --autorepair             # let a headless session resolve the conflict
mael sync --base feat/parent       # stack this branch on feat/parent, then rebase
mael sync --base main              # unstack it again
```

**`mael sync-all`**

| Option | Description |
|---|---|
| `--autorepair` | On conflict, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue. One session runs per conflicting worktree, in turn. |

```bash
mael sync-all --autorepair         # repair each conflicting worktree in the sweep
```

**`mael base`**

Prints the branch this worktree's work is stacked on. Change it with `mael sync --base`.

```bash
mael base                          # "feat/child is based on feat/parent."
```

**`mael stack-tip`**

The stack tip is one pointer per project: the branch new worktrees stack on. It advances to
each new branch, so stacks form a chain. When its branch is deleted the tip falls back to
`main`, so new work can never stack on a merged or abandoned branch. When its branch has had
no commits for 30 days, `mael add` warns and proceeds.

```bash
mael stack-tip                     # show where new work will stack
mael stack-tip feat/parent         # move it
mael stack-tip main                # reset — start unrelated work
```

**`mael promote` / `mael eject`**

A stack registered on GitHub merges bottom-up, so an urgent PR stuck mid-stack needs a way
out. `promote` re-points this branch onto `main` **and** re-points anything based on it onto
this branch's old base, closing the stack up. `eject` skips that second half and leaves the
rest of the stack alone. Run `mael sync` afterwards, here and in any re-pointed worktree.

```bash
mael promote                       # jump the merge queue
mael eject                         # just leave the stack
```

See [stacking.md](../dev/stacking.md) for the full model.

`--autorepair` is available on every command that rebases: `mael sync`, `mael sync-all`,
`mael git squash`, and `mael gh create-pr`. Each one is off by default. The flag starts an
unattended agent, so it is for commands you run yourself. An agent already in a session
resolves its own conflicts instead.

---

## Sessions and workspaces

| Command | Description |
|---|---|
| `mael open [TARGET]` | Start a Claude Code session in a worktree. `--harness claude\|opencode` / `--opencode` picks the agent CLI. |
| `mael claude [TARGET]` | Same as `mael open`. |
| `mael add [BRANCH]` | Add a worktree for a branch and start a session in it. Takes `--harness` / `--opencode` too. |
| `mael ide [TARGET]` | Open a worktree in the configured editor. |
| `mael session list` | List active Claude Code sessions. |
| `mael session info [ID]` | Show the fields of one session. Defaults to the session you run it in. |
| `mael session end [ID]` | Stop one session and leave its worktree in place. Defaults to the session you run it in. |
| `mael session record EVENT` | Update session state from a Claude Code hook event. Reads the payload as JSON on stdin. Not meant for humans. |
| `mael cmux status` | Report whether maelstrom can place a session into cmux. Starts cmux if it is down. Exits non-zero when cmux cannot be reached. |
| `mael status set TEXT` | Set the workspace status text shown in the cmux status bar. |
| `mael status clear` | Clear the workspace status. |

```bash
mael open                          # Claude session in the current worktree
mael open myproject.b              # ...in bravo
mael session list                  # what is running, and in what state
mael session info                  # the session you are in
mael session info 97894d02         # ...named by an id prefix from the ID column
mael session info 4242             # ...named by a pid from the PID column
mael --json session info 97894d02  # the same fields as JSON
mael session end 97894d02          # stop that session
```

**Harness choice.** `mael add`, `mael open`, `mael claude`, `mael task run` and `mael task next --run`
take `--harness claude|opencode` (default `claude`) or the `--opencode` shorthand. With
`opencode` the session runs `opencode2` instead of `claude`. OpenCode assigns its own
session ids, so maelstrom does not pin, resume, or duplicate-guard those sessions — every
opencode launch starts a fresh session, and the task prompt reaches it via
`--prompt`.

When no harness flag is given, maelstrom detects the harness the command runs in: a shell
inside Claude Code (`CLAUDECODE=1`) defaults to `claude`, a shell inside OpenCode
(`OPENCODE_TERMINAL=1`) defaults to `opencode`. An explicit flag always wins — pass
`--harness claude` to launch Claude from inside an OpenCode session. A plain terminal with
neither variable set defaults to `claude`.

**`mael session info` and `mael session end`**

`ID` is a session id, a unique prefix of one (4 characters or more), or a pid. `mael session list`
prints both: the `ID` column holds the first 8 characters of the session id, and the `PID` column
holds the pid. A session maelstrom did not launch has a blank `ID`, so name that session by its
pid.

Without `ID`, both commands act on the session you run them in. Both exit non-zero when no session
matches, or when an id prefix matches more than one session.

A pid resolves even when `mael session list` does not show that session. The list comes from a
process sweep, and the sweep misses some sessions — frequently the one you are in. A pid is read
from the process itself, so it always resolves, and such a session shows only the fields the
process supplies. A pid that is not a running Claude session does not resolve.

`mael session end` stops the session and leaves the worktree in place — use `mael close` to tear
down the worktree as well. The stop is graceful: it sends SIGINT so a busy session can wind down,
waits 5 seconds, then sends SIGTERM to a survivor and waits 10 more. A session that ends on the
first signal returns at once, so the 15 seconds is the worst case. `mael session end` never sends
SIGKILL, which would risk a half-written transcript.

`mael session end` with no `ID` inside a session stops that session. The `mael` process is a
child of the session, so it signals its parent and exits once the parent is gone.

An ended session stays on disk. Its transcript is complete, and `claude --resume` opens it again.

`mael session end` does not close the task the session was launched for. The Claude `session-end`
hook still fires as the session shuts down, and that hook closes the task.

None of the other commands here take options beyond `--help`.

**`mael session record`**

`EVENT` is one of a fixed set. Each value maps to the session state maelstrom records:

| `EVENT` | Session state |
|---|---|
| `user-prompt-submit`, `ask-user-post` | `processing` |
| `stop`, `stop-failure`, `idle-prompt` | `idle` |
| `permission-prompt`, `elicitation-prompt` | `awaiting-permission` |
| `ask-user-pre` | `awaiting-user-input` |
| `session-end` | Closes the launching task and deletes the session file. Sets no state. |
| `heartbeat` | Bumps `updated_at` and leaves the state alone. |

`mael install` wires each hook to the right value, so you never pass `EVENT` yourself. An
unknown `EVENT` exits 2.

---

## Tasks

The task notebook. See [tasks.md](../guide/tasks.md).

| Command | Description |
|---|---|
| `mael task add [TITLE]` | Create a task and print its id. |
| `mael task draft FILE [TITLE]` | Write a draft task file — a task recipe outside the notebook. |
| `mael task promote FILE` | Create a task from a draft file, print its id, delete the file. |
| `mael task load-many FILE` | Create a chain of tasks from a marked plan file. `-` reads stdin. |
| `mael task next` | Print the id of the next actionable task. |
| `mael task run ID` | Launch a task as a Claude session. Creates its worktree first, and rebases the branch onto its base. A failed rebase blocks the launch and leaves the task TODO. |
| `mael task list` | List actionable tasks. |
| `mael task show ID` | Show a summary of a task. |
| `mael task get-status [ID]` | Print a task's status alone. Defaults to `$MAEL_TASK_ID`. |
| `mael task current` | Print the session's task as `ID:STATUS`, for a shell prompt. Prints an empty line outside a task session. |
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

```bash
mael task add "Add avatar upload" --run       # plan-mode session, launched now
mael task add "Wire the API" --mode auto \
  --follow-end '*'                            # append after the chain's current leaf
mael task next --run                          # launch the next actionable task
mael task status done                         # close $MAEL_TASK_ID
```

Quote `'*'` — an unquoted `*` is expanded by the shell before `mael` sees it.

`'*'` resolves against the task's parent, which defaults to `$MAEL_TASK_PARENT`. It therefore
appends to the chain only inside a launched session. Run from a plain shell with no parent set,
it silently resolves to nothing and the task follows nothing at all.

Every task command takes `--project TEXT` (default: from the current directory), except
`reindex`, which spans all projects. Only `task add` and `task add-scheduled` accept the
short form `-p`; every other task command takes `--project` in full.

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
| `--base TEXT` | Branch to stack this task's branch on. Default: the project's stack tip. Not the same as `--parent`: `--parent` shares one branch and one PR, `--base` stacks a different branch as its own PR. |
| `--follow TEXT` | Id this task follows. Repeatable. |
| `--follow-end TEXT` | Follow the end leaves of the given id's follows-chain. Repeatable. Quote `"*"`. |
| `--content-file TEXT` | File whose contents become the task's Content section. `-` reads stdin. |
| `--from TEXT` | Seed the new task by duplicating this task's recipe. Other flags override. |
| `--template` | Park the new task in `template` status: a reusable, non-actionable recipe. |
| `--schedule TEXT` | Cron expression. Acted on only for template tasks, e.g. `'0 9 * * 1-5'`. |
| `-e`, `--edit` | Open the new task in `$EDITOR` after creating it. |
| `-r`, `--run` | Launch the task as a session immediately. |
| `--here` | With `--run`, launch in the current shell. No worktree, no new workspace. |

**`mael task draft`**

Writes a task file to `FILE` instead of the store. The file is inert — invisible to `list`,
`next` and follow-end resolution — until `mael task promote` loads it. Identity fields
(`id`, `project`, `created`, `follows`) stay empty; they are allocated at promote time.

`TITLE` is required. The recipe flags are the same as `add`'s (`-c`/`--command`,
`-m`/`--mode`, `-b`/`--branch`, `-P`/`--parent`, `--pre-action`, `--post-action`,
`--priority`, `--model`, `--content-file`), plus:

| Option | Description |
|---|---|
| `--force` | Overwrite `FILE` if it already exists. Without it an existing file is an error. |

`draft` has no `--follow`, `--follow-end`, `--run` or `--template` — chain wiring and
launching happen at promote time. Prints the path it wrote.

```bash
mael task draft d1.md "Execute: add avatar upload" --mode auto \
    --pre-action linear.in-progress
```

**`mael task promote`**

Parses a draft file, creates the task in `todo`, prints the new id, and deletes the file.
Recipe fields come from the file; any flag given here overrides the file's value (the same
semantics as `add --from`). On an error — missing file, bad frontmatter, no title — the file
is left untouched and no task is created.

Takes `--project` and the recipe flags (`-c/--command`, `-m/--mode`, `-b/--branch`,
`-P/--parent`, `--pre-action`, `--post-action`, `--priority`, `--model`). There is no
`--content-file` — the draft's body already carries the content. Plus:

| Option | Description |
|---|---|
| `--follow TEXT` | Id the new task follows. Repeatable. |
| `--follow-end TEXT` | Follow the end leaves of the given id's follows-chain. Repeatable. Quote `"*"`. |

```bash
id=$(mael task promote d1.md --follow-end '*')   # capture the id to chain the next promote
mael task promote d2.md --follow "$id"
```

**`mael task update`**

`ID` is a required positional argument — the task to update. An optional `TITLE` follows it.
The field flags below are a **subset** of `add`'s, in long form only. `task update` has no
short flags, and it cannot set `--parent`, `--follow`, `--follow-end`, `--from`, `--template`,
`--edit`, `--run` or `--here`. A task's parent and its chain position are set at creation.

| Option | Description |
|---|---|
| `--id TEXT` | Re-key the task to a new id, rewriting `follows` and `parent` references that point at it. Applied first; the other flags then apply to the new id. |
| `--project TEXT` | Project name. Default: from the current directory. |
| `--command TEXT` | Skill the launched session runs. |
| `--mode TEXT` | Session mode, e.g. `plan`, `auto`, `normal`. |
| `--branch TEXT` | Branch for the task. |
| `--pre-action TEXT` | Lifecycle action fired when the task starts. |
| `--post-action TEXT` | Lifecycle action fired when the task finishes. |
| `--priority [critical\|high\|medium\|low]` | Task priority. |
| `--model TEXT` | LLM model for the session. |
| `--base TEXT` | Branch this task's branch stacks on. |
| `--schedule TEXT` | Cron expression. Acted on only for template tasks. |
| `--content-file TEXT` | File whose contents replace the Content section. `-` reads stdin. |

Pass `''` to `--pre-action`, `--post-action`, `--model`, `--base` or `--schedule` to clear
the field.

**`mael task list`**

| Option | Description |
|---|---|
| `--status TEXT` | Filter by status (folder). |
| `--parent TEXT` | Filter by parent id. |
| `--all-todo` | Also show waiting tasks (incomplete deps) and tasks parked in `blocked/`. Still hides done and cancelled. |
| `--all` | Show everything, including done and cancelled. Takes precedence over `--all-todo`. |

**`mael task next`**

| Option | Description |
|---|---|
| `--parent TEXT` | Restrict to children of this id. |
| `--run` | Launch the next actionable task as a session. |
| `-b`, `--branch TEXT` | Restrict strictly to this branch. No fallback to other branches. |
| `--here` | With `--run`, launch in the current shell. |
| `--harness NAME` | Agent harness to launch: `claude` (default) or `opencode`. Defaults to the harness the command runs in when neither flag is given. |
| `--opencode` | Shorthand for `--harness opencode`. |

By default `next` prefers a task on the current git branch, then falls back to the global
next task.

**`mael task run`**

| Option | Description |
|---|---|
| `--here` | Launch in the current shell. No worktree, no new workspace. |
| `--harness NAME` | Agent harness to launch: `claude` (default) or `opencode`. Defaults to the harness the command runs in when neither flag is given. |
| `--opencode` | Shorthand for `--harness opencode`. |

**`mael task load-many`**

| Option | Description |
|---|---|
| `--run` | Launch every actionable task created. A task that still follows an unfinished id waits for `mael task next --run`. |
| `--here` | With `--run`, launch only the head task in the current shell. |

`FILE` is a preamble the human reads, then one `---CREATE TASK <name>---` block per task.
Each block is frontmatter, then a body that becomes the task's content. A block ends at the
next marker or at end of file:

```markdown
Any preamble here is ignored by load-many.

---CREATE TASK build-endpoint---
title: Build the upload endpoint
mode: auto
follow-end: "*"
---

Accept a multipart POST at /avatar. Store to S3. Return the URL.

---CREATE TASK wire-ui---
title: Wire the upload button
mode: auto
follow: build-endpoint
---

Call the endpoint from the profile page and show the result.
```

The `---` after the frontmatter is required. Without it the whole block reads as body text and
`load-many` fails with "Block '&lt;name&gt;' is missing a title".

Set `mode: auto` on every execute block — the default is plan mode, so a block that omits it
re-plans instead of running. Leave `branch:` unset so the chain shares one branch and one PR.

```bash
mael task load-many plan.md --run      # create the chain and launch every actionable task
mael task load-many - --run            # read the plan from stdin
```

`--run` only launches **actionable** tasks. A block that follows a task still `in-progress`
stays in `todo`; advance it later with `mael task next --run`.

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

## Wiki

The cross-project development-pattern wiki. Pages live beside the task notebook in
`~/.maelstrom/tasks`, so every change is committed. See
[concepts.md](../guide/concepts.md).

| Command | Description |
|---|---|
| `mael wiki list` | Print every page with its one-line description. |
| `mael wiki read PAGE` | Print the raw page content. |
| `mael wiki update PAGE` | Create or replace a page, and commit it. |

```bash
mael wiki list                                        # every page, with descriptions
mael wiki read dev-patterns/python/pypi-publication   # read one
printf '%s\n' "$body" | mael wiki update dev-patterns/python/pypi-publication \
  --content-file -                                    # replace it, and commit
```

`PAGE` is a relative path such as `dev-patterns/python/pypi-publication`. The `.md` suffix is
optional. The convention is `dev-patterns/<language-or-area>/<topic>`, but any safe relative
path is accepted.

**`mael wiki update`**

| Option | Description |
|---|---|
| `--content-file TEXT` | File holding the whole page body. `-` reads stdin. Required. |

The content replaces the whole page — there is no partial edit. To change one part, read the
page first, then write the full body back.

Give each page a one-line `description:` in YAML frontmatter. `mael wiki list` prints it.

---

## Agents

Drive Claude agents over a stream-json pipe, and answer them from outside the terminal they run
in. This path runs beside `mael open` and does not replace it: an agent the daemon drives has no
cmux pane and no TTY. See [agent-daemon.md](../dev/agent-daemon.md) for the protocol.

| Command | Description |
|---|---|
| `mael agent daemon` | Run the agent daemon in the foreground. `--socket PATH` overrides the socket. |
| `mael agent start [CWD]` | Start an agent in CWD (default `.`). Takes `--prompt`, `--mode`, `--model`, `--session-id`. |
| `mael agent list` | Show every agent, what each waiting one waits on, and what each last said. `--json` emits rows as JSON. |
| `mael agent show ID` | Show one agent in full: its messages, every question option, the plan, and the command that answers the wait. `--json` emits the detail as JSON. |
| `mael agent tail ID` | Print an agent's events and stop, without driving it. `-f` keeps streaming. The read-only half of `attach`. |
| `mael agent say ID TEXT` | Send TEXT to an agent as a user message. |
| `mael agent answer ID CHOICE` | Answer an agent's pending question. CHOICE answers every question the agent asked. |
| `mael agent approve ID` | Approve an agent's pending plan or tool call. |
| `mael agent deny ID` | Deny it. `--reason TEXT` reaches the agent as the tool result. |
| `mael agent attach ID` | Stream an agent's events, and forward each line you type. |
| `mael agent stop ID` | Stop an agent, and forget it. A stopped agent is not brought back. |
| `mael agent resume ID` | Start an exited agent again, keeping its id and its conversation. `--text TEXT` replaces the default first turn. |

```bash
mael agent start . --prompt "run the tests"     # starts the daemon too; prints the agent id
mael agent start /tmp --mode auto               # unattended
mael agent list                                 # who is waiting, on what, and what each said
mael agent show 1761dcf6                        # every option, with descriptions
mael agent answer 1761dcf6 "Green"              # answer a question
mael agent approve 0b2f5f5b                     # approve a plan or a tool call
mael agent deny 0b2f5f5b --reason "not now"
mael agent say 1761dcf6 "also update the README"
mael agent tail 1761dcf6                        # print the history, then stop
mael agent tail -f 1761dcf6                     # ...and keep streaming
mael agent attach 1761dcf6                      # teleport: live stream, typed input
mael agent stop 1761dcf6
mael agent resume 1761dcf6                      # after a crash: same id, same conversation
mael agent resume 1761dcf6 --text "rerun the failing test"
mael agent daemon                               # run the daemon in the foreground instead
```

The first command that needs the daemon starts it, in its own process group, logging to
`~/.maelstrom/agent-daemon.log` — a foreground `mael agent daemon` ignores that log. Set
`MAEL_AGENT_NO_AUTOSTART=1` to turn auto-start off. Every agent is a normal `claude` process.

A crashed child shows as `exited(N)` in `mael agent list`, and `mael agent resume` brings it back
with the conversation it had. A daemon start resumes every agent that was running, under the same
ids. Claude keeps the conversation in its own session transcript; the daemon keeps one spawn
record per agent under `~/.maelstrom/agents/`.

## Orchestrator

Serve the world to the orchestrator UI: tasks from the notebook, worktrees from `list-all`, and
agents from the agent host, over one WebSocket. See
[orchestrator-server.md](../dev/orchestrator-server.md) for the protocol.

| Command | Description |
|---|---|
| `mael orchestrator serve` | Run the orchestrator server in the foreground. `--host` (default `127.0.0.1`), `--port` (default `8765`), `--socket PATH` for the agent host's socket. |

```bash
mael orchestrator serve                     # ws://127.0.0.1:8765
mael orchestrator serve --port 3072         # what mael env start runs, on the worktree's port
```

The first command that needs the agent host starts one, as `mael agent` does. Under maelstrom,
`mael env start` runs the server and the web app together.

## Dev environments

See [dev-environments.md](../guide/dev-environments.md).

| Command | Description |
|---|---|
| `mael env start [SERVICE]` | Run the install command, then start every non-optional service, or one named service. |
| `mael env stop [SERVICE]` | Stop the environment's services, or one named service. SIGTERM, then SIGKILL after 10s. |
| `mael env restart [SERVICE]` | Restart services, or one named service. |
| `mael env status [TARGET]` | Show service PIDs, status and log paths. A declared service that never started shows as `stopped`; `dead` means it started and then died. |
| `mael env logs [SERVICE]` | Show service logs, or one service's log. |
| `mael env list [PROJECT]` | List running environments for a project. |
| `mael env list-all` | List running environments across every project. |
| `mael env stop-all` | Stop every running environment. |
| `mael env reset [TARGET]` | Regenerate the `.env` file, e.g. after changing ports in `.maelstrom.yaml`. |
| `mael env open [TARGET]` | Open the browser pane for a running environment. |

```bash
mael env start                     # install, then start every non-optional service
mael env start ladle               # start the service 'ladle', alone
mael env stop ladle                # stop it again; the rest keep running
mael env start ladle -w askastro.b # ...in askastro bravo
mael env logs -f                   # follow every service's log
mael env logs frontend -f          # ...or one named service
mael env stop                      # before heavy multi-file editing
mael env reset                     # regenerate .env after changing ports
```

| Command | Option | Description |
|---|---|---|
| `env start` | `--skip-install` | Skip the install step before starting. |
| `env restart` | `--install` | Run the install step before starting. |
| `env start`, `env stop`, `env restart`, `env logs` | `-w`, `--worktree TARGET` | The worktree, as `project.worktree` — e.g. `-w askastro.b`. Default: the current directory. |
| `env logs` | `-n INTEGER` | Number of lines to show. Default: 100. |
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

```bash
mael gh show-code --uncommitted      # review before committing
mael gh create-pr ME-41 --squash     # autosquash fixups, push, set Linear "In Review"
mael gh read-pr                      # status, comments, unresolved threads
mael gh check-log 12345678 --failed-only
```

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
| `--autorepair` | On a conflict in the pre-push sync, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue. |
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
| `mael git squash [TARGET]` | Rebase onto the branch's base (`origin/main` unless the branch is stacked), autosquashing `fixup!` commits. Does not push. |
| `mael git merge [TARGET]` | Rebase the current branch onto main, fast-forward main to it, and push. |

```bash
mael git status              # compact summary; the only other --json consumer
mael git squash              # tidy fixups without pushing
mael git merge --close       # merge, then close the worktree
```

**`mael git squash`**

| Option | Description |
|---|---|
| `--autorepair` | On conflict, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue the rebase. The command still pushes nothing. |

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
| `mael linear release` | Promote every "Unreleased" issue with the product label to "Done". `--dry-run` lists what would move without changing anything. |

```bash
mael linear plan ME-41                          # plan an issue; launches the session
mael linear read-task ME-41                     # the brief, as markdown
mael linear edit-plan ME-41 old.md new.md       # arguments are file paths...
mael linear edit-plan ME-41 'old text' 'new text' --string   # ...unless -s
mael linear set-status ME-41 done               # "Unreleased"
```

`set-status` applies to the named issue only. It does not transition parents or subtasks.

**`mael linear plan`**

Runs by default: the planning session launches immediately. It takes the same
block-settable options as `mael task add` — `--project`, `-c/--command`, `-m/--mode`,
`-b/--branch`, `-P/--parent`, `--pre-action`, `--post-action`, `--priority`, `--model`,
`--follow`, `--follow-end`, `--here` — plus:

| Option | Description |
|---|---|
| `--run` / `--no-run` | Launch the planning session immediately. Default: run. |

The planning defaults (`plan-task` command, `normal` mode, `opus` model, the
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

```bash
mael sentry list-issues --since 24h    # what broke today
mael sentry get-issue 4521             # stacktrace and variables
```

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

```bash
mael slack post "Deploy finished"
printf 'Release notes\n' | mael slack post --channel releases
```

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

```bash
mael uptimerobot status               # is anything down right now?
mael uptimerobot monitors             # run once to discover ids for .maelstrom.yaml
mael uptimerobot outages --since 7d
```

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
| `mael schedule uninstall` | Opt this machine out: remove the marker and tear the agent down. Clears a repeating `pmset` wake left by the removed `--wake-at`, asking for sudo only if one is set. |
| `mael schedule status` | Report the marker, plist, loaded job and log tail. |

```bash
mael schedule install       # opt this machine in; no sudo, asks nothing
mael schedule status        # run this first when a task did not fire
```

A sleeping Mac runs the job on its next wake, as one coalesced catch-up. No command wakes it.

None of these take options beyond `--help`.

---

## Maintenance

| Command | Description |
|---|---|
| `mael doctor [PROJECT]` | Check project health and fix issues automatically. |
| `mael install` | Install maelstrom's Claude Code skills and hooks into `~/.claude/`. |
| `mael self-update` | Update maelstrom to the latest version from git. |
| `mael session-channel` | Launch the Bun-based session-tracking MCP channel. Invoked by Claude Code, not by humans. |

```bash
mael install                 # skills and hooks into ~/.claude/
mael doctor myproject        # check project health, and fix what it can
mael self-update
```

**`mael install`**

| Option | Description |
|---|---|
| `--no-monitor` | Skip the session-tracking MCP channel, its hooks and its dependencies. |

