# CLI reference

Every `mael` command and flag. For the reasoning behind them, read the
[guides](../guide/concepts.md).

Run `mael --help` or `mael <group> --help` to see the same information in the terminal.

## Global options

| Option | Description |
|---|---|
| `--version` | Print the version and exit. |
| `--json` | Print machine-readable JSON instead of a table. Honoured by `mael list-all`, `mael project list`, `mael git status` and `mael session info` only; every other command ignores it. |
| `--help` | Print help and exit. |

```bash
mael --version
mael --json list-all         # one of the four commands that honour --json
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

`_main` is a target like any other: `mael ide myproject._main`. It resolves to the
project's `_main` folder.

`mael env` is the exception: a verb that acts on one worktree takes it with `-w`, or from the
current directory. The positional argument of `start`, `stop`, `restart` and `logs` names a
**service**.

---

## Projects

The `mael project` group adds, creates, renames and lists projects.

| Command | Description |
|---|---|
| `mael project add GIT_URL` | Clone a repository and set it up for maelstrom. |
| `mael project create NAME` | Create a GitHub repository with the maelstrom stub files, check it out, and open a worktree on `feat/start-project`. |
| `mael project mv OLD NEW` | Rename a project and everything derived from its name. |
| `mael project list` | List every project under the projects directory, with its path and worktree count. Reads no git status, no ports and no sessions, so it stays fast where `mael list-all` does not. Supports `mael --json project list`. |
| `mael project check [PROJECT]` | Check one project's git layout, remotes, worktrees, ports and `.env` files, and fix the safe issues. For the host, run `mael self check-host`. |

```bash
mael project add https://github.com/org/repo.git  # clone into maelstrom's layout
mael project list
mael project check myproject        # check project health, and fix what it can
```

**`mael project add`**

| Option | Description |
|---|---|
| `--projects-dir TEXT` | Base directory for projects. Default: from `~/.maelstrom/config.yaml`, else `~/Projects`. |

**`mael project create`**

`NAME` is the repository name. Use `owner/name` to create the repository in an
organization. The seed commit holds `.gitignore` (which ignores the per-worktree `.env` and
`.claude/CLAUDE.local.md` and `AGENTS.md`), a commented `.maelstrom.yaml`, `README.md` and `CLAUDE.md`.

The project is cloned over HTTPS, whatever `gh config get git_protocol` reports. Agents push
with a token over HTTPS, so an SSH remote breaks an unattended session. `mael project add`
takes the URL you give it and does not rewrite the protocol.

| Option | Description |
|---|---|
| `--public` | Create a public repository. Default: private. |
| `--description TEXT` | Repository description. |
| `--projects-dir TEXT` | Base directory for projects. Default: from `~/.maelstrom/config.yaml`, else `~/Projects`. With a directory other than the configured one, no worktree is opened: `mael add` finds projects only in the configured directory. |

**`mael project mv`**

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

Run `mael project check NEW` afterwards.

---

## Worktrees

| Command | Description |
|---|---|
| `mael add [BRANCH]` | Add a worktree for `BRANCH`, and rebase `BRANCH` onto its base before the session starts. A new branch bases on `main`, unless the project's stack tip has been moved. Recycles a closed worktree when one exists. An existing worktree for `BRANCH` is reused and rebased too, but that rebase does not push. A recycled or reused worktree gets its `.env` rebuilt; running services restart only when the `.env` changed. A failed rebuild stops a recycle, and only warns on a reuse. With no `BRANCH`, creates a fresh worktree detached at `origin/main`: there is no branch to rebase, and no worktree is recycled. |
| `mael list [PROJECT]` | List open worktrees with branch, dirty files, local commits, PR, app URL and session. Closed worktrees are named on one line under the table. Reads the orchestrator server, and fails without one or for a project it does not know. A PR cell ending in `(stale)` is the last known state. See [listing.md](../guide/listing.md) for what each column means. |
| `mael list-all` | List worktrees across every project. Adds a `PROJECT` column, and names worktrees by folder rather than by NATO name. Reads the orchestrator server, and fails without one. `mael --json list-all` prints the server's `Worktree` entities, grouped by project. |
| `mael close [TARGETS]...` | Sync, check the worktree is clean, then check out main. Keeps the folder, name and ports. |
| `mael remove TARGETS...` | Delete one or more worktrees. |
| `mael rm TARGETS...` | Alias for `mael remove`. |
| `mael sync [TARGET]` | Rebase the worktree against its base (`origin/main` unless the branch is stacked). |
| `mael sync-all [PROJECT]` | Sync every worktree in the project, parents before their children. |
| `mael tidy-branches [PROJECT]` | Rebase feature branches, delete merged ones, force-push unmerged ones. Skips any branch another branch is stacked on. Deletes each deleted branch's working history. |
| `mael base [TARGET]` | Show the branch this worktree's work is stacked on. |
| `mael stack-tip [BRANCH]` | Show or move the branch new worktrees stack on. `main` is the default tip; passing it resets. |
| `mael promote [TARGET]` | Move this branch to the bottom of its stack so it can merge first. |
| `mael eject [TARGET]` | Pull this branch out of its stack onto `main`, leaving the rest alone. |

```bash
mael add feature/avatar-upload  # worktree for a branch, ports allocated
mael close                      # done: reset, keep name and ports
```

**`mael add`**

On checkout, `mael add` (re)generates the worktree's `.claude/CLAUDE.local.md` and `AGENTS.md`
(same content, with `@` imports inlined so opencode can read it — opencode reads only `AGENTS.md`
and does not resolve `@` imports). Both are gitignored per worktree.

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. Default: detect from the current directory. |
| `--model TEXT` | Model reference. A bare value selects Claude. A qualified value selects its CLI harness. Default: `claude:opus`. |
| `--execute-model TEXT` | Model the session switches to when its plan is approved. Default: none, which keeps the session on `--model` throughout. Claude models only. |
| `--open` | Open the configured editor instead of a Claude session. |
| `--no-recycle` | Always create a new worktree, even when closed ones exist. |
| `--base TEXT` | Stack the new branch on this branch. Default: the project's stack tip. Use `main` to start unstacked. |

**`mael close`**

| Option | Description |
|---|---|
| `--wait` | Wait for the PR to merge before closing. |
| `--timeout INTEGER` | Maximum seconds to wait for the merge. Default: 3600. |
| `--interval INTEGER` | Poll interval in seconds. Default: 30. |
| `--force` | Close incomplete work too. Aborts an in-progress sync, commits uncommitted changes as `wip: uncommitted changes`, keeps the branch and PR, and creates a "Reopen" task. |
| `--discard` | Discard tracked, staged, and non-ignored untracked files, then close without syncing. Branch commits and ignored files remain. Cannot combine with `--force`, `--trash` or `--wait`. |
| `--trash` | Set unmerged work aside. Moves the branch to `trash/<branch>` locally and on origin, and closes its open PR. Cannot combine with `--force`, `--discard` or `--wait`. |

`mael close` stops the worktree's sessions before it tears the worktree down. It asks the agent
daemon to stop the agents running there, then signals any remaining `claude` process. The daemon
records those as stopped, not crashed, so a closed worktree leaves nothing in `mael agent list
--all`.

`mael close --trash` runs that teardown, then commits uncommitted changes as
`wip: uncommitted changes`. It does not sync. The PR closes with the comment `Trashed: branch moved
to trash/<branch>`.

The push sends everything on the branch to origin, the wip commit included. That holds for a
branch that was never pushed too.

`--trash` refuses, and changes nothing, when:

- the target is `_main`, or the branch is `main`;
- the worktree is not on a branch;
- the branch is already under `trash/`;
- `trash/<branch>` already exists locally, or exists on origin at a different commit;
- another branch is stacked on the branch.

It stops, with the worktree still on its branch, when:

- the PR lookup or the PR close fails;
- `origin/<branch>` has commits that the local branch lacks;
- the push fails.

Run the same command again after the cause is fixed. The branch's stored base is cleared, and its
working history moves to the new name.

**`mael remove` / `mael rm`**

| Option | Description |
|---|---|
| `-f`, `--force` | Skip the confirmation prompt for modified or untracked files. |

`mael remove` runs the same teardown as `mael close` before it deletes the checkout: it stops the
environment, asks the agent daemon to stop the agents running there, then signals any remaining
`claude` process. A removed worktree therefore leaves nothing in `mael agent list --all` either.

**`mael sync`**

| Option | Description |
|---|---|
| `--squash` | Autosquash `fixup!` commits while rebasing onto the base. A `fixup!` aimed at a commit in the *parent* branch does not squash — put it in the parent's worktree. |
| `--base TEXT` | Stack this branch on `TEXT` before rebasing. `main` unstacks it. Rejects a self-base or a cycle with exit code 1. |
| `--abort` | On conflict, abort the rebase and restore the worktree. |
| `--close` | If the branch is empty after the rebase, delete it (local and remote) and close the worktree. Rejected with `--no-push`. |
| `--no-push` | Rebase only, and leave the remote branch alone. Use it when something else may be working in the worktree. |
| `--autorepair` | On conflict, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue the rebase. Announces the repair, then streams the session's output to the console. Supersedes `--abort`: a failure aborts and restores the worktree, except where the session finished the rebase on another branch and there is nothing to abort. |
| `--skip-pre-push` | Push without running the project's `pre_push_cmd`. For an emergency only. |

Before it pushes, `mael sync` runs the project's [`pre_push_cmd`](configuration.md#pre_push_cmd)
on the rebased branch. A failed check leaves the branch rebased and unpushed.

`mael sync` pushes only a branch that already has an `origin` branch. Otherwise it prints
`No origin/<branch>; not pushed`. When GitHub refuses the push, the branch stays rebased,
`mael sync` prints the refusal to stderr, and it exits 1.

```bash
mael sync --autorepair             # let a headless session resolve the conflict
mael sync --squash --no-push       # tidy Land's fixup! commits, publish nothing
mael sync --base feat/parent       # stack this branch on feat/parent, then rebase
mael sync --base main              # unstack it again
```

**`mael sync-all`**

| Option | Description |
|---|---|
| `--autorepair` | On conflict, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue. One session runs per conflicting worktree, in turn. |

`mael sync-all` does not run the project's [`pre_push_cmd`](configuration.md#pre_push_cmd).
When GitHub refuses a push, `mael sync-all` prints the refusal to stderr and goes on to the
next worktree. At the end it names the refused worktrees and exits 1.

```bash
mael sync-all --autorepair         # repair each conflicting worktree in the sweep
```

**`mael base`**

Prints the branch this worktree's work is stacked on. Change it with `mael sync --base`.

```bash
mael base                          # "feat/child is based on feat/parent."
```

**`mael stack-tip`**

The stack tip is one pointer per project: the branch new worktrees stack on. It is `main`
until you move it, so new work bases on `main`. When its branch is deleted the tip falls back to
`main`, so new work can never stack on a merged or abandoned branch. When its branch has had
no commits for 30 days, `mael add` warns and proceeds.

```bash
mael stack-tip                     # show where new work will stack
mael stack-tip feat/parent         # stack the next worktrees on feat/parent
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
and `mael gh create-pr`. Each one is off by default. The flag starts an
unattended agent, so it is for commands you run yourself. An agent already in a session
resolves its own conflicts instead.

---

## Sessions

| Command | Description |
|---|---|
| `mael add [BRANCH]` | Add or reuse a worktree. It prepares the worktree, starts installation, then selects an agent or shell surface. `--cli`, `--daemon`, and `--no-agent` select that surface. |
| `mael ide [TARGET]` | Open a worktree in the configured editor. |
| `mael session list` | List running Claude Code sessions. |
| `mael session info [ID]` | Show the fields of one session. Defaults to the session you run it in. |

```bash
mael add feature/avatar-upload     # create or reuse a branch worktree
mael session list                  # what is running
mael session info                  # the session you are in
mael session info 97894d02         # ...named by an id prefix from the ID column
mael session info 4242             # ...named by a pid from the PID column
mael --json session info 97894d02  # the same fields as JSON
```

**Harness transport.** `mael add`, `mael task run`, `mael task next --run`, `mael task add`,
`mael linear plan`, `mael task load-many` and `mael task add-scheduled` take `--cli` or `--daemon`. `mael add --model` accepts a model reference.
A bare value selects Claude. A qualified value selects its CLI harness.
The default is `claude:opus`. `--model` conflicts with `--open` and `--no-agent`.
`--execute-model` names the model the session switches to when its plan is
approved. It must be a Claude model: `/model` cannot change which binary is
running. On `mael add` it conflicts with `--open`, `--no-agent` and `--cli` —
none of those reaches the approval that would perform the switch.
`mael add --no-agent` prepares a shell without an agent. It conflicts with
`--open`, `--cli`, and `--daemon`.

`mael add` starts the installer in the background in every case.

| Harness | What runs | Who owns the agent |
|---|---|---|
| `cli` (default) | `claude`, `codex`, or `opencode`, in this shell, in the worktree | This shell |
| `daemon` | The agent daemon's `claude` child. The launch prints `mael agent attach <id>`. | The agent daemon |

`mael task run --cli` prepares the worktree first, as a daemon launch does: it rebases the
branch, refreshes the `.env`, and moves the task to in-progress. Then it replaces the `mael`
process with the CLI, in the worktree. `mael add --no-agent` starts a temporary child shell in
the worktree.

`--cli` needs a terminal. A driven agent has none. Nor has an agent's tool call outside the
daemon, because its stdin and stdout are pipes. From a driven agent, `mael add`,
`mael task run` and `task next --run` start a child driven agent by default. With no terminal,
each launch refuses `--cli`, and `mael add --no-agent` prepares the worktree and starts nothing. `task add --run` and `linear plan` start
the agent on the daemon unless `--cli` is given. `task load-many --run` and
`task add-scheduled --run` can launch more than one session, so they refuse `--cli`.

Daemon transport supports Claude model references only. Use `--cli` with a
`codex:*` or `opencode:*` model reference.
The session appears in `mael agent list` and in the orchestrator UI. `mael agent attach <id>`
opens it in a terminal. Ctrl-C detaches the client and leaves the agent running. The child is
still `claude` with the session's environment, so skills, hooks and `CLAUDE.md` behave as they
always did.

`claude:sonnet`, `claude:opus`, `claude:fable`, `codex:luna`, `codex:terra`,
`codex:sol`, `codex:astra`, `opencode:kimi`, `opencode:glm`,
`opencode:glm-flash`, `opencode:qwen`, `opencode:qwen-flash`, and
`opencode:deepseek` select a CLI. Claude and OpenCode pass their alias. The
listed Codex aliases resolve as follows:

| Model reference | Codex model | Reasoning effort |
|---|---|---|
| `codex:astra` | `gpt-6-astra` | Codex default |
| `codex:sol` | `gpt-5.6-sol` | `low` |
| `codex:terra` | `gpt-5.6-terra` | `medium` |
| `codex:luna` | `gpt-5.6-luna` | Codex default |

Unknown Codex aliases pass through unchanged. Blank models resolve to
`claude:opus`. Bare models remain Claude-compatible. `--daemon` refuses a
non-Claude model until that harness has a daemon. `--harness`, `--claude`, and
`--codex` were removed; use a transport flag. `--here` was removed: `--cli` runs in this shell.

A `--cli` run has no daemon to switch the model when the plan is approved, so a task's
`execute-model` is reported and ignored there.

```bash
mael add feature/avatar-upload --cli
mael task run NORT-7 --cli   # selected CLI in this shell
mael task run NORT-7 --daemon
```

**`mael session info`**

`ID` is a session id, a unique prefix of one (4 characters or more), or a pid. `mael session list`
prints both: the `ID` column holds the first 8 characters of the session id, and the `PID` column
holds the pid. A session maelstrom did not launch has a blank `ID`, so name that session by its
pid.

Without `ID`, the command shows the session you run it in. It exits non-zero when no session
matches, or when an id prefix matches more than one session.

A pid resolves even when `mael session list` does not show that session. The list comes from a
process sweep, and the sweep misses some sessions — frequently the one you are in. A pid is read
from the process itself, so it always resolves, and such a session shows only the fields the
process supplies. A pid that is not a running Claude session does not resolve.

A driven agent ends its own session with the `<end-session>` marker. See `CONTEXT.md`,
"End-session request".

None of the other commands here take options beyond `--help`.

---

## Tasks

The task notebook. See [tasks.md](../guide/tasks.md).

| Command | Description |
|---|---|
| `mael task add [TITLE]` | Create a task and print its id. |
| `mael task draft FILE [TITLE]` | Write a draft task file — a task recipe outside the notebook. |
| `mael task promote FILES...` | Create one task per draft file, chained in order. Print the ids and delete the files. |
| `mael task load-many FILE` | Create a chain of tasks from a marked plan file. `-` reads stdin. |
| `mael task next` | Print the id of the next actionable task. |
| `mael task run ID` | Launch a task as a Claude session. Creates its worktree first, and rebases the branch onto its base. A reused worktree is rebased without pushing. A failed rebase blocks the launch and leaves the task TODO. A recycled or reused worktree gets its `.env` rebuilt, as `mael add` does; a failed rebuild warns and does not block the launch. |
| `mael task list` | List actionable tasks. |
| `mael task show ID` | Show a summary of a task. |
| `mael task get-status [ID]` | Print a task's status alone. Defaults to `$MAEL_TASK_ID`. |
| `mael task current` | Print the session's task as `ID:STATUS`, for a shell prompt. Prints an empty line outside a task session. |
| `mael task read ID` | Print the task as markdown, rendered from its row. |
| `mael task edit ID` | Open the task in `$EDITOR` (default `vi`). Writes it back if it changed. |
| `mael task update ID [TITLE]` | Update a task's fields. |
| `mael task rm ID` | Delete a task and strip it from any dependents' `follows` lists. |
| `mael task log ID MSG` | Append a line to a task's log. |
| `mael task status <state> [ID]` | Move a task between lifecycle states. |
| `mael task prompt ID` | Print the initial Claude prompt for a task. |
| `mael task reconcile` | Reconcile in-progress tasks against live Claude sessions. |
| `mael task add-scheduled` | Fire every due template: duplicate it into a dated run and advance its watermark. A template with a `trigger` reads its workflow's completed runs first. |

```bash
mael task add "Add avatar upload" --run       # plan-mode session, launched now
mael task add "Wire the API" --mode auto \
  --follow-end '*'                            # append after the chain's current leaf
mael task next --run                          # launch the next actionable task
mael task status done                         # close $MAEL_TASK_ID
```

`mael task list`, `show`, `read`, `status`, `add`, `promote` and `load-many` call the
orchestrator server that
[`orchestrator_url:`](configuration.md#global-configuration--maelstromconfigyaml) names. They fail
when that key is unset or no server answers. The server runs the status actions, and `status`
prints each action's line on stderr. The server writes each task that `add`, `promote` and
`load-many` create, and puts it on the desk, so it shows on the canvas at once.

Quote `'*'` — an unquoted `*` is expanded by the shell before `mael` sees it.

`'*'` resolves against the task's parent, which defaults to `$MAEL_TASK_PARENT`. It therefore
appends to the chain only inside a launched session. Run from a plain shell with no parent set,
it silently resolves to nothing and the task follows nothing at all.

Every task command takes `--project TEXT` (default: from the current directory). Only
`task add` and `task add-scheduled` accept the short form `-p`; every other task command takes
`--project` in full.

**`mael task add`**

A task with no parent gets a random 4-character id, such as `k3f9`. A child gets `<parent>.<n>`.

| Option | Description |
|---|---|
| `-p`, `--project TEXT` | Project name. |
| `-c`, `--command TEXT` | Skill the launched session runs. May carry arguments, e.g. `"impeccable shape"`. |
| `-m`, `--mode TEXT` | Session mode. Default `plan`. Use `auto` for an unattended execute session, `normal` for a non-planning session that prompts. |
| `-b`, `--branch TEXT` | Branch for the task. Default `task/<id>`. |
| `-P`, `--parent TEXT` | Parent task id. Creates a child id. Defaults to `$MAEL_TASK_PARENT`. |
| `--pre-action TEXT` | Lifecycle action fired when the task starts, e.g. `linear.in-progress`. |
| `--post-action TEXT` | Lifecycle action fired when the task finishes, e.g. `linear.done`. |
| `--priority [critical\|high\|medium\|low]` | Task priority. Default `medium`. Affects list ordering and `task next`. |
| `--model TEXT` | LLM model for the session, e.g. `opus` or a full id. Default: `opus`, applied when the task launches. |
| `--execute-model TEXT` | Model the session switches to when its plan is approved. Default: none, which keeps the session on `--model` throughout. Claude models only. |
| `--base TEXT` | Branch to stack this task's branch on. Default: the project's stack tip. Not the same as `--parent`: `--parent` shares one branch and one PR, `--base` stacks a different branch as its own PR. |
| `--follow TEXT` | Id this task follows. Repeatable. |
| `--follow-end TEXT` | Follow the end leaves of the given id's follows-chain. Repeatable. Quote `"*"`. |
| `--content-file TEXT` | File whose contents become the task's Content section. `-` reads stdin. |
| `--from TEXT` | Seed the new task by duplicating this task's recipe. Other flags override. |
| `--template` | Park the new task in `template` status: a reusable, non-actionable recipe. |
| `--schedule TEXT` | Cron expression. Acted on only for template tasks, e.g. `'0 9 * * 1-5'`. |
| `--trigger TEXT` | Fire on a finished GitHub Actions run: `gh-action/<workflow>[@<branch>] [<conclusion>,...]`. Acted on only for template tasks. A value that does not parse is refused. See [Fire on a finished build](../guide/scheduled-work.md#fire-on-a-finished-build). |
| `--comm TEXT` | A **Comm** this task feeds, such as `c3`. Repeatable. Default: the parent task's comms. An id that names no comm is refused. |
| `-e`, `--edit` | Open the new task in `$EDITOR` after creating it. |
| `-r`, `--run` | Launch the task immediately: the server starts it on the agent daemon. Prints the operation id. |
| `--cli` | With `--run`, run the model CLI in this shell, in the task's worktree, with no server launch. |
| `--daemon` | With `--run`, launch on the agent daemon through the server. This is the default. |

**`mael task draft`**

Writes a task file to `FILE` instead of the store. The file is inert — invisible to `list`,
`next` and follow-end resolution — until `mael task promote` loads it. Identity fields
(`id`, `project`, `created`, `follows`) stay empty; they are allocated at promote time.

`TITLE` is required. The recipe flags are the same as `add`'s (`-c`/`--command`,
`-m`/`--mode`, `-b`/`--branch`, `-P`/`--parent`, `--pre-action`, `--post-action`,
`--priority`, `--model`, `--execute-model`, `--content-file`), plus:

| Option | Description |
|---|---|
| `--force` | Overwrite `FILE` if it already exists. Without it an existing file is an error. |

`draft` has no `--follow`, `--follow-end`, `--run` or `--template` — chain wiring and
launching happen at promote time. Prints the path it wrote. It creates `FILE`'s parent
directory. Drafts belong in `.drafts/`, which `mael` gitignores.

```bash
mael task draft .drafts/d1.md "Add avatar upload" --mode plan \
    --pre-action linear.in-progress
```

**`mael task promote`**

Parses each draft file, creates one task per file in `todo`, prints the new ids one per line,
and deletes the files. Each task follows the one before it. Recipe fields come from each file;
any flag given here overrides every file's value (the same semantics as `add --from`). The set
is one notebook write. On an error — missing file, bad frontmatter, no title — every file is
left untouched and no task is created.

With no `--follow` or `--follow-end`, the set **splices** in after `$MAEL_TASK_ID`. The first
task follows that task, and every task that followed it now follows the last new task. With
`MAEL_TASK_ID` unset, or naming no task in the project, the first task follows nothing.

Takes `--project` and the recipe flags (`-c/--command`, `-m/--mode`, `-b/--branch`,
`-P/--parent`, `--pre-action`, `--post-action`, `--priority`, `--model`,
`--execute-model`). There is no
`--content-file` — the draft's body already carries the content. Plus:

| Option | Description |
|---|---|
| `--follow TEXT` | Id the first new task follows, instead of the splice. Repeatable. |
| `--follow-end TEXT` | The first new task follows the end leaves of the given id's follows-chain, instead of the splice. Repeatable. Quote `"*"`. |

```bash
mael task promote .drafts/d1.md .drafts/d2.md            # splice after $MAEL_TASK_ID
mael task promote .drafts/d1.md .drafts/d2.md --follow-end '*'  # append to the parent's chain
```

**`mael task update`**

`ID` is a required positional argument — the task to update. An optional `TITLE` follows it.
The field flags below are a **subset** of `add`'s, in long form only. `task update` has no
short flags, and it cannot set `--parent`, `--follow`, `--follow-end`, `--from`, `--template`,
`--edit`, `--run`, `--cli` or `--daemon`. A task's parent and its chain position are set at creation.

| Option | Description |
|---|---|
| `--id TEXT` | Re-key the task to a new id, rewriting `follows` and `parent` references that point at it. Applied first; the other flags then apply to the new id. |
| `--project TEXT` | Project name. Default: from the current directory. |
| `--command TEXT` | Skill the launched session runs. May carry arguments, e.g. `"impeccable shape"`. |
| `--mode TEXT` | Session mode, e.g. `plan`, `auto`, `normal`. |
| `--branch TEXT` | Branch for the task. |
| `--pre-action TEXT` | Lifecycle action fired when the task starts. |
| `--post-action TEXT` | Lifecycle action fired when the task finishes. |
| `--priority [critical\|high\|medium\|low]` | Task priority. |
| `--model TEXT` | LLM model for the session. |
| `--execute-model TEXT` | Model the session switches to when its plan is approved. |
| `--base TEXT` | Branch this task's branch stacks on. |
| `--schedule TEXT` | Cron expression. Acted on only for template tasks. |
| `--trigger TEXT` | Build trigger, `gh-action/<workflow>[@<branch>] [<conclusion>,...]`. Acted on only for template tasks. |
| `--comm TEXT` | Replace the comms this task feeds. Repeatable. An id that names no comm is refused. |
| `--content-file TEXT` | File whose contents replace the Content section. `-` reads stdin. |

Pass `''` to `--pre-action`, `--post-action`, `--model`, `--execute-model`, `--base`,
`--schedule`, `--trigger` or `--comm` to clear the field.

**`mael task list`**

Rows are in priority order. Within one priority, the oldest task is first.

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
| `--cli`, `--daemon` | Mutually exclusive harness transports. Default: `--cli`, which runs in this shell, in the task's worktree. |

`next` picks by priority. Within one priority, it picks the oldest task. By default `next`
prefers a task on the current git branch, then falls back to the global next task.

**`mael task run`**

| Option | Description |
|---|---|
| `--cli`, `--daemon` | Mutually exclusive harness transports. Default: `--cli`, which runs in this shell, in the task's worktree. |

**`mael task load-many`**

| Option | Description |
|---|---|
| `--run` | Launch every actionable task created: the server starts each on the agent daemon. A task that still follows an unfinished id waits for `mael task next --run`. |
| `--cli`, `--daemon` | `--run` always launches on the daemon, so it refuses `--cli`. |

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
stays in `todo`; advance it later with `mael task next --run`. A launch that fails prints a
warning on stderr. The task stays in `todo`, and the command still exits 0.

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
| `--run` | Launch each due run as a driven agent on the agent daemon. `MAEL_HARNESS_TYPE` does not change it. |
| `--cli`, `--daemon` | `--run` always launches on the daemon, so it refuses `--cli`. |

---

## Wiki

The cross-project development-pattern wiki. Each page is one row in the state database,
`~/.maelstrom/state/state.db`. See
[concepts.md](../guide/concepts.md).

| Command | Description |
|---|---|
| `mael wiki list` | Print every page with its one-line description. |
| `mael wiki read PAGE` | Print the raw page content. |
| `mael wiki update PAGE` | Create or replace a page. |

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
in. This path is how `mael add --daemon` and `mael task run --daemon` launch a session: the daemon
runs the agent, and the launch prints `mael agent attach <id>`. `mael agent start` makes an agent
directly. See [agent-daemon.md](../dev/agent-daemon.md) for the protocol.

| Command | Description |
|---|---|
| `mael agent start [CWD]` | Start an agent in CWD (default `.`). Takes `--prompt`, `--mode`, `--model`, `--execute-model`, `--session-id`. |
| `mael agent list` | Show every agent, what each waiting one waits on, and what each last said. A subagent follows its parent under a dotted id (`ID.1`), with `parent` and `description` columns. `--stopped` shows sessions that have stopped and can be resumed; `--all` shows both. `-w PROJECT.WORKTREE` and `--project NAME` narrow the stopped half of the listing, and imply `--stopped` on their own. `--json` emits rows as JSON. |
| `mael agent show ID` | Show one agent in full: the last thing it said, every question option, the plan, and the command that answers the wait. On a parent it ends with a `Subagents:` table; on a dotted id it shows that subagent. `--json` emits the detail as JSON. |
| `mael agent tail ID` | Print an agent's events and stop, without driving it. `-f` keeps streaming. `--raw` prints each event as JSON, one per line, which is how a fixture is recorded. A dotted id tails one subagent's stream; a parent's tail shows none of its subagents. The read-only half of `attach`. |
| `mael agent say ID TEXT` | Send TEXT to an agent as a user message. |
| `mael agent run ID COMMAND` | Run COMMAND in the agent's directory and give it the output. Killed after 30s; output is capped. |
| `mael agent answer ID CHOICE` | Answer an agent's pending question. CHOICE answers every question the agent asked. `--request ID` names which wait, needed only when several are open. |
| `mael agent approve ID` | Approve an agent's pending plan or tool call. Approving a plan also clears the agent's context and moves it to `auto`. A plan with no plan file is denied instead. `--request ID` names which wait, needed only when several are open. `--remember` also tells the agent not to ask again, with the lasting allow the agent offered; it fails when the agent offered none. |
| `mael agent deny ID` | Deny it, or decline a question. `--reason TEXT` reaches the agent as the tool result. `--request ID` names which wait, needed only when several are open. |
| `mael agent attach ID` | Teleport into an agent as a terminal UI. Esc interrupts the turn; Shift-Tab changes the mode; Ctrl-C detaches. Needs a terminal. |
| `mael agent interrupt ID` | Abandon the turn an agent is running, leaving the agent alive. Denies a pending wait first. |
| `mael agent set-mode ID MODE` | Change a running agent's permission mode: `plan`, `normal` or `auto`. Takes effect on the running turn, and the spawn record keeps it. |
| `mael agent recover ID` | Clear a poisoned context and send the agent its work again, for an agent that answers but does no work. Keeps the child and its session. |
| `mael agent stop ID` | Stop an agent. No daemon start brings a stopped agent back, but its spawn record is kept, so `mael agent resume ID` still works. |
| `mael agent resume ID` | Start an exited agent again, keeping its id and its conversation. `--text TEXT` replaces the default first turn. |
| `mael agent cost [ID]` | Show what each agent spent and which stage of its work spent it, from the milestone ledger. Reads the state database, not the daemon, so a stopped agent still reports. An exited agent carries a **closing row** named `<final>` for what it spent after its last marker. Per stage: the tokens it consumed, split own and subagent, and the running total. `$` covers the agent's own requests alone — a subagent's spend is reported in tokens, because there is no price table. With no ID, every agent. `--json` emits the report as JSON. |
| `mael agent register ID` | Adopt an agent already live on a daemon with no Agent record: read its row from the daemon's own `list` and write a record by hand. The orchestrator's `list` adopts such an agent on its own, so this is for when it has not. `--task-id ID` names the Task it belongs to. |

```bash
mael agent start . --prompt "run the tests"     # starts the daemon too; prints the agent id
mael agent start /tmp --mode auto               # unattended
mael agent list                                 # who is waiting, on what, and what each said
mael agent list --stopped                       # what has stopped, and can be resumed
mael agent list --stopped -w maelstrom.alpha    # ...in one worktree only
mael agent list --all --json                    # running and stopped, as JSON
mael agent show 1761dcf6                        # every option, with descriptions; Subagents: at the end
mael agent show 1761dcf6.1                      # one subagent
mael agent tail 1761dcf6.1                      # one subagent's stream
mael agent answer 1761dcf6 "Green"              # answer a question
mael agent approve 0b2f5f5b                     # approve a plan or a tool call
mael agent deny 0b2f5f5b --reason "not now"
mael agent say 1761dcf6 "also update the README"
mael agent run 1761dcf6 "git log --oneline -3"
mael agent tail 1761dcf6                        # print the history, then stop
mael agent tail -f 1761dcf6                     # ...and keep streaming
mael agent tail --raw 1761dcf6 > events.jsonl   # record it as JSON
mael agent attach 1761dcf6                      # teleport: the terminal UI
mael agent interrupt 1761dcf6                   # abandon the turn, keep the agent
mael agent set-mode 1761dcf6 auto               # done planning: let it work
mael agent recover 1761dcf6                     # it replies but does nothing: clear and re-send its work
mael agent stop 1761dcf6
mael agent resume 1761dcf6                      # after a crash: same id, same conversation
mael agent resume 1761dcf6 --text "rerun the failing test"
mael agent cost                                 # every agent: where its tokens went
mael agent cost 1761dcf6                        # one agent, stage by stage
mael agent register 1761dcf6 --task-id NORT-42  # adopt a live agent with no record
```

Two things start a daemon, and nothing else does: the OS supervisor runs the production daemon,
and `mael env start` runs a worktree's:

```bash
mael self restart agent-daemon                  # the production daemon, on ~/.maelstrom/state/daemon
mael env start                                  # this worktree's daemon
mael env restart agent-daemon                   # pick up code changed since it started
```

Both run [`mael-agent-daemon serve`](#the-agent-daemon). The production daemon is the
`agent-daemon` host service, which the OS supervisor runs from the current release. A worktree's
daemon is a service of its environment, so its lifetime is the environment's. No `mael agent`
command, session launch or orchestrator poll starts one. A command that finds no daemon names the
root it looked for and both ways to start one.

Everything a daemon owns lives under its root: its socket, lock, pid file, log and `agents/`
spawn records. `MAEL_AGENT_ROOT` names that root, and the environment manager writes it into each
worktree's `.env`. There is no fallback and no `--root` flag, so a daemon cannot be started on a
root its environment does not own.

Which daemon your shell reaches depends on the directory you stand in — see
[dev-environments.md](../guide/dev-environments.md#an-agent-daemon-per-environment).

A daemon holds the code it started with, so `mael-agent-daemon status` names the tree serving
you. See [agent-daemon.md](../dev/agent-daemon.md#a-daemon-per-environment).

A crashed child shows as `exited(N)` in `mael agent list`, and `mael agent resume` brings it back
with the conversation it had. A daemon start resumes every agent that was running, under the same
ids. Claude keeps the conversation in its own session transcript; the daemon keeps one spawn
record per agent under `<root>/agents/`.

`mael agent list --stopped` names every session `mael agent resume` can bring back. It reads
Claude's transcripts for what each session was doing, and each session's spawn record for how to
start it again. Both are needed, so the listing names only sessions the daemon started. A session
you started by hand in a terminal has no record, and `claude --resume` brings that one back.

The listing subtracts the sessions that are still running, because a resume of one is refused.

---

## The agent daemon

`mael-agent-daemon` runs the agent daemon and reads its records. It is a separate command, not a
`mael` group: the published `mael` package does not include the daemon. A release bundle
installs it, and runs it as the `agent-daemon` host service. In a maelstrom checkout, run it with
`uv run`.

| Command | Description |
|---|---|
| `mael-agent-daemon serve` | Run the agent daemon in the foreground, on the root `MAEL_AGENT_ROOT` names. Exits 2 when that variable is unset. The `agent-daemon` host service and `mael env start` run it; you rarely run it by hand. |
| `mael-agent-daemon status` | Print which daemon serves this environment's root: its root, socket, pid, version, spawn-record directory, start time, agent count, and the worktree its code came from. |
| `mael-agent-daemon list` | Every spawn record with its pid, whether that pid is alive, whether the daemon holds it, what it was doing at the last shutdown, and a `mismatch` column naming a stray, a crash, a retired record or a duplicate. A driven `claude` no record names is a row of its own. `--all-roots`, `--json`. |
| `mael-agent-daemon reconcile` | Say what `gc` would do, doing nothing: one verdict per record and per unplaced process. Asks the daemon when one answers, else reads the records and the process table itself. `--all-roots`, `--json`. |
| `mael-agent-daemon gc` | Kill the strays and duplicates, retire the older of two running records on one session, and write off the crashed. Never resumes: a stray's record stays `running` for the next daemon start. Under `--all-roots` a process no root claims is killed too. `--all-roots`, `--json`. |

```bash
uv run mael-agent-daemon status                 # which daemon is serving, running whose code
uv run mael-agent-daemon list                   # every record: pid, alive, held, mismatch
uv run mael-agent-daemon reconcile              # what gc would do
uv run mael-agent-daemon gc                     # after a daemon died: kill its strays
```

---

## The orchestrator server

`mael-orchestrator` serves the world to the orchestrator UI: tasks from the notebook, worktrees
from `list-all`, and agents from the agent host, over HTTP. It is a separate command, not a `mael`
group: the published `mael` package does not include the server. A release bundle installs it, and
runs it as the `orchestrator` host service. In a maelstrom checkout, run it with `uv run`. See
[orchestrator-server.md](../dev/orchestrator-server.md) for the routes.

| Command | Description |
|---|---|
| `mael-orchestrator serve` | Run the orchestrator server in the foreground. `--host` (default `127.0.0.1`), `--port` (default `8765`), `--log-level` (`debug`, `info`, `warning`, `error`; default `info`), `--tls-cert` and `--tls-key` (serve HTTPS; default `DEV_TLS_CERT` and `DEV_TLS_KEY`, so a worktree under `dev_https:` serves HTTPS with no flag), `--web-root` (the built UI to serve beside the API; default `MAEL_WEB_ROOT`, else the installed release's `web` component). The agent host is the daemon `MAEL_AGENT_ROOT` names, so a worktree's orchestrator talks to that worktree's daemon. |

```bash
uv run mael-orchestrator serve                     # http://127.0.0.1:8765
uv run mael-orchestrator serve --port 3072         # what mael env start runs, on the worktree's port
uv run mael-orchestrator serve --log-level warning # quieter: drop the per-command trace
```

The server never starts the agent host. Under maelstrom, `mael env start` runs the server and the
web app together.

## Dev environments

See [dev-environments.md](../guide/dev-environments.md).

| Command | Description |
|---|---|
| `mael env start [SERVICE]` | Run the install command, then start every non-optional service, or one named service. Services already running are left alone, so a start repairs a half-started environment rather than refusing. Under `dev_https:` it renews the dev certificate first, and stops if Tailscale refuses. |
| `mael env stop [SERVICE]` | Stop the environment's services, or one named service. SIGTERM, then SIGKILL after 10s. Refuses a name that is neither declared nor running. |
| `mael env restart [SERVICE]` | Restart services, or one named service. |
| `mael env restart --changed` | Restart only the running services whose [service version](../../CONTEXT.md) moved since they started. Prints `<service>: <old> → <new>` for each, or `Nothing changed.` Needs a `pipeline.toml` — see [Service versions](../guide/dev-environments.md#service-versions). |
| `mael env status` | Show service PIDs, status and log paths. A declared service that never started shows as `stopped`; `dead` means it started and then died. |
| `mael env logs [SERVICE]` | Show service logs, or one service's log. |
| `mael env list [PROJECT]` | List running environments for a project. |
| `mael env list-all` | List running environments across every project. |
| `mael env stop-all` | Stop every running environment. |
| `mael env reset` | Regenerate the `.env` file, e.g. after changing ports in `.maelstrom.yaml`. Always restarts running services. |

```bash
mael env start                     # install, then start every non-optional service
mael env start ladle               # start the service 'ladle', alone
mael env stop ladle                # stop it again; the rest keep running
mael env start ladle -w askastro.b # ...in askastro bravo
mael env logs -f                   # follow every service's log
mael env logs frontend -f          # ...or one named service
mael env stop                      # before heavy multi-file editing
mael env reset                     # regenerate .env after changing ports
mael env restart --changed         # restart what the last pull changed
```

| Command | Option | Description |
|---|---|---|
| `env start` | `--skip-install` | Skip the install step before starting. |
| `env restart` | `--install` | Run the install step before starting. |
| `env restart` | `--changed` | Restart only the running services whose service version moved. Takes no `SERVICE` and no `--install`. |
| `env start`, `env stop`, `env restart`, `env logs`, `env status`, `env reset` | `-w`, `--worktree TARGET` | The worktree, as `project.worktree` — e.g. `-w askastro.b`. Default: the current directory. A positional `TARGET` on `status` and `reset` is a deprecated alias for `-w`. |
| `env logs` | `-n INTEGER` | Number of lines to show. Default: 100. |
| `env logs` | `-f`, `--follow` | Follow log output. |

### The fixed environment

A project that sets `main_port_base:` in `.maelstrom.yaml` makes `_main` an env target on ports
that never change. See [the fixed environment](../guide/worktrees.md#the-fixed-environment).

```bash
mael env reset -w myproject._main    # write _main/.env off the reserved base
mael env start -w myproject._main    # start it
mael env status -w myproject._main
```

---

## GitHub

| Command | Description |
|---|---|
| `mael gh create-pr` | Create a PR for the current worktree, or push if one exists. |
| `mael gh link-pr REF` | Register an existing PR on a task. |
| `mael gh withdraw-pr` | Withdraw a task's Registered PR while more work is done. |
| `mael gh read-pr [TARGET]` | Read PR status, comments and check results. |
| `mael gh show-code [TARGET]` | Show commits and uncommitted changes for a worktree. |
| `mael gh check-log RUN_ID` | Show full log output for a GitHub Actions run. |
| `mael gh download-artifact RUN_ID ARTIFACT_NAME` | Download an artifact from a workflow run. |
| `mael gh has-pr [TARGET]` | Report whether this branch has a PR, as an exit code. |
| `mael gh wait-for-pr [TARGET]` | Wait for CI checks to finish on the current PR. |

```bash
mael gh show-code --uncommitted      # review before committing
mael gh create-pr --squash           # autosquash fixups and push
mael gh read-pr                      # status, comments, unresolved threads
mael gh check-log 12345678 --failed-only
```

**`mael gh create-pr`**

When `MAEL_TASK_ID` is set, appends `[<task-id>]` to a new PR title, and registers the PR on
that task as its Registered PR. The task card then shows the PR.

The PR body comes from the PR draft — see [the pull requests guide](../guide/pull-requests.md).

A failed body write warns and still returns the PR URL — the branch is pushed either way.

The command runs the project's [pre-push check](../guide/pull-requests.md#the-pre-push-check)
after its last rebase, then pushes once. A failed check pushes nothing and opens no PR.

| Option | Description |
|---|---|
| `--draft` | Create as a draft PR. |
| `--wait` | Wait for CI checks to finish after creating the PR. |
| `--wait-for-review` | Wait until a reviewer leaves feedback. Exits 0 on the first review, 2 on timeout. |
| `--squash` | Autosquash `fixup!` commits before pushing. |
| `--autorepair` | On a conflict in the rebase before the push, run a headless Claude session (`/resolve-rebase-conflicts`) to resolve it and continue. |
| `--skip-pre-push` | Push without running the project's `pre_push_cmd`. For an emergency only. |
| `--target TEXT` | Project/worktree target for directory resolution. |

**`mael gh link-pr`**

Registers a PR on a task, so the task card shows it. Use it for a PR that `create-pr` did not
make. `REF` is a PR number, `#number`, or a GitHub PR URL. A bare number takes its URL from the
repository's `origin` remote. A later registration replaces the earlier one.

```bash
mael gh link-pr 118
mael gh link-pr https://github.com/acme/northwind/pull/118 --task NORT-7
```

| Option | Description |
|---|---|
| `--task TEXT` | Task to register the PR on, in the current project. Default: `$MAEL_TASK_ID`. With neither, the command fails. |

**`mael gh withdraw-pr`**

Marks a task's Registered PR as a Withdrawn PR while the agent does more work after the push, so
the user is not asked to review it. The task keeps the PR. The next registration makes the PR
ready again: a `create-pr` push, a `link-pr`, or a PR link tag. A task with no Registered PR
fails.

```bash
mael gh withdraw-pr
mael gh withdraw-pr --task NORT-7
```

| Option | Description |
|---|---|
| `--task TEXT` | Task whose PR to withdraw, in the current project. Default: `$MAEL_TASK_ID`. With neither, the command fails. |

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

**`mael gh has-pr`**

Reports whether this branch has a pull request. The answer is the exit code, for a skill to
branch on:

| Exit code | Meaning |
|---|---|
| 0 | The branch has a PR. |
| 1 | The branch has none. |
| 2 | The check could not run. |

| Option | Description |
|---|---|
| `--open` | Count only a PR still waiting to merge; treat merged or closed as none. |

The 2 is what keeps the answer honest. An unauthenticated or missing `gh` cannot tell whether a
PR exists, and reading that as "no PR" would open a duplicate. `/code-review` calls this to pick
a fresh pass over an additive one.

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
| `mael git merge [TARGET]` | Rebase the current branch onto main, fast-forward main to it, and push. |
| `mael git squash-branch [TARGET]` | Collapse the branch's commits into one, keeping a working history. |
| `mael git uncommit-branch [TARGET]` | Return the branch to unstaged changes at its base tip, keeping a working history. |

```bash
mael git status              # compact summary; also honours --json
mael git merge --close       # merge, then close the worktree
mael git squash-branch       # collapse the commits into one, still committed
mael git squash-branch --local   # collapse only the commits never pushed
mael git uncommit-branch     # collapse the commits back into the working tree
```

To autosquash `fixup!` commits without pushing — during Land, where fixups are made — use
`mael sync --squash --no-push`.

**`mael git merge`**

| Option | Description |
|---|---|
| `--close` | After merging, close the worktree and delete the feature branch. |
| `--no-squash` | Skip autosquashing `fixup!` commits during the rebase. |

**`mael git squash-branch`**

Collapses the branch's commits into one commit, and leaves that commit on the branch. See
[the pull requests guide](../guide/pull-requests.md) for the workflow.

`/code-review` runs it to put the branch's final state in one commit the reviewers read whole.
The work stays committed, so a stray `git reset --hard` cannot destroy it and a parallel agent
reading the tree sees an ordinary branch. Because it rebases, it also does the work of
`mael sync --no-push`.

The squashed commit's subject is always `wip: squashed for review`. `/present` re-cuts it away.
Its body lists each commit it collapsed, oldest first: the short SHA, then the message. The
reasons of the build commits stay on the branch, and the working history keeps each SHA
reachable, so `git show <sha>` still works.

It prints the base, the working-history ref, the commit count and the diff stat.

**`mael git uncommit-branch`**

Returns the branch to unstaged changes at its base tip, keeping a working history.

This is `squash-branch` followed by `git reset --mixed HEAD^`. `/present` runs it to re-cut the
reviewed tree into story commits. Run it yourself only to re-cut a branch's commits by hand.

It prints the base, the working-history ref, the commit count, the short SHA and message of each
commit it uncommitted, and the diff stat. After `squash-branch`, the squashed commit's message holds the
build commits' messages, so `/present` reads them all here.

**Scope, on both commands**

| Option | Description |
|---|---|
| `--remote` | Collapse every commit ahead of the base fork point. The default. |
| `--local` | Collapse only the commits that were never pushed. |

`--local` collapses `origin/<branch>..HEAD`, so already-pushed commits keep their own subjects.
Use it to re-review a branch whose PR is already open: the reviewers read the new work alone,
rather than re-reading what already merged. A branch that was never pushed has nothing pushed to
keep, so `--local` takes the whole branch.

`--local` rewrites the pushed commits during the rebase, so their SHAs change and the next push
must force. `mael gh create-pr` already pushes with `--force-with-lease`.

**Refusals, on both commands**

Each exits 1 and changes nothing when:

- the working tree has uncommitted changes (`.env` excluded);
- a rebase or merge is in progress;
- the branch has no commits ahead of its base;
- the rebase conflicts. The rebase aborts and the tree is restored. Run `mael sync` first;
- `--local` only: every commit is already pushed, so there is nothing new to collapse;
- `--local` only: the unpushed commits hold a `fixup!` aimed at an already-pushed commit, which
  the collapse would silently discard. Run `mael sync --squash --no-push` first, or use
  `--remote`.

The rebase is not optional: a base tip computed against a stale base would collapse someone
else's work into the tree.

`mael tidy-branches`, `mael sync --close` and `mael git merge --close` delete a branch's working
history with the branch.

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
`--execute-model`, `--follow`, `--follow-end`, `--cli`, `--daemon` — plus:

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

## Comms

A **Comm** is something to tell people outside the team when work lands. These commands let an
agent change comms. A person uses the Comms view of the orchestrator UI.

| Command | Description |
|---|---|
| `mael comms new TITLE` | Create a comm and print its id, such as `c1`. |
| `mael comms list` | Print the open comms: id, title, category, project, recipients and the number of linked tasks. |
| `mael comms close COMM` | Close a comm. A closed comm leaves the open list. |
| `mael comms link COMM TASK...` | Link tasks to a comm. All or nothing: an unknown task links none. |
| `mael comms edit COMM` | Change a comm's title, content, recipients, category or project. |
| `mael comms landings` | Print each tracked task's **Landing**: its PR, its status, and each environment's state. |

```bash
mael comms new "Invoice export" --to "#cs" --to jo@acme.test --category release   # prints c1
mael task add "Export invoices as CSV" --comm c1               # its children inherit c1
mael comms link c1 2026-10-08.3 2026-10-08.4                   # link two more tasks
mael comms edit c1                                             # edit the content in $EDITOR
mael comms close c1
mael comms list --all
```

A link is the task's own `comms` field, so `mael task update --comm` changes it too.

**`mael comms new`**

| Option | Description |
|---|---|
| `--content TEXT` | The request, as it came in. |
| `--to TEXT` | A recipient: a Slack channel, an email address or a note. Repeatable. |
| `--category TEXT` | The comm's category: free text, such as `release`. |
| `--project TEXT` | The project a task made from the comm goes to. Default: the **Default project** of the category, else the project of the current directory, else none. `''` sets none. |

**`mael comms list`**

| Option | Description |
|---|---|
| `--all` | Show closed comms too. |
| `--category TEXT` | Show only the comms in this category. |

**`mael comms link`**

| Option | Description |
|---|---|
| `--unlink` | Remove the link instead. |
| `--project TEXT` | Project of the tasks. Default: from the current directory. |

**`mael comms edit`**

With no option, `edit` opens the content in `$EDITOR`. Closing the editor without saving changes
nothing.

| Option | Description |
|---|---|
| `--title TEXT` | Set the title. |
| `--content TEXT` | Set the content. |
| `--to TEXT` | Set the recipients. Repeatable. Replaces the list. |
| `--clear-to` | Remove every recipient. |
| `--category TEXT` | Set the category. `''` clears it. |
| `--project TEXT` | Set the project. `''` clears it. |

An unknown project is refused by `new` and `edit`.

**`mael comms landings`**

```bash
mael comms landings                                # one row per tracked task
mael comms landings --since 2026-10-07T00:00:00Z   # the steps recorded after this time
```

A tracked task is a task with a registered PR. The orchestrator server writes the rows on its
worktree poll, so the command shows what the last poll saw. An environment the server could
not read shows `unknown`.

| Option | Description |
|---|---|
| `--since TEXT` | List the landing steps recorded after this ISO time, one row per step, instead of one row per task. |

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

## The installer (`install.sh`)

`install.sh` installs or upgrades the release bundle in `~/.maelstrom/install/`, then runs the new
release's `mael self setup`. It installs uv first when uv is missing. Before it downloads or
builds, it asks where the projects live when `projects_dir:` is unset, and writes the key. On a
first install, it runs `mael self check-host` before setup. With `--source`, it runs
`check-host --dev` from the checkout before the build, because the build needs Node and pnpm. See [host services](../dev/host-services.md#the-installer).

```bash
curl -LsSf https://github.com/sminnee/maelstrom/releases/latest/download/install.sh | sh
```

| Option | Description |
|---|---|
| (none) | Download and install the latest GitHub Release. |
| `--tag TAG` | Install the GitHub Release `TAG`, such as an older one to roll back to. |
| `--bundle FILE` | Install a local `maelstrom.tar.gz`. |
| `--source DIR` | Pull the git checkout `DIR`, build its bundle, tagged `main-<sha12>`, and install that. |
| `--root DIR` | The install root. Default: `~/.maelstrom/install`. |
| `--projects-dir DIR` | The directory that holds the projects. Written as `projects_dir:`. Default: the stored key, else a question, else `~/Projects`. A relative path is refused. |
| `--no-setup` | Run neither `mael self check-host` nor `mael self setup`. The projects directory is still resolved and written. |

Give at most one of `--tag`, `--bundle` and `--source`.

### Install from main (`install-main.sh`)

`install-main.sh` installs maelstrom from its main branch, on a host with no release. It makes
maelstrom a project at `<projects_dir>/maelstrom/`: a bare clone, with `main` in `_main`. It writes
`update_channel: main`, then runs `_main`'s own `bin/install.sh --source`. When the checkout
exists, it skips the clone, so the same line upgrades. A `maelstrom` directory that is not
a maelstrom checkout is refused.

```bash
curl -LsSf https://raw.githubusercontent.com/sminnee/maelstrom/main/bin/install-main.sh | sh
```

| Option | Description |
|---|---|
| `--projects-dir DIR` | As for `install.sh`. Passed on to it. |
| `--no-setup` | Passed on to `install.sh`. |

`MAEL_REPO_URL` changes the repo it clones. See [environment.md](environment.md).

## Host control (`mael self`)

The `mael self` group installs, upgrades and looks after maelstrom on this host. A host runs
two [host services](../dev/host-services.md), `orchestrator` and `agent-daemon`, and the hourly
scheduler. The OS supervisor runs all three: launchd on macOS, systemd user units on Linux.

| Command | Description |
|---|---|
| `mael self setup` | Bring this host to the state the current [release bundle](../../CONTEXT.md) wants. `install.sh` runs it after each install. Run it yourself to repair a host. Every step is safe to repeat. See below. |
| `mael self check-host` | Check this host's tools, sign-ins, Tailscale and secret file permissions, and offer to fix each. `install.sh` runs it on a first install. Every step is safe to repeat. See below. |
| `mael self update` | Install the latest release, then run its setup. See below. |
| `mael self status` | Report the release and its component versions, each host service and the scheduler (loaded, pid, last exit, log), and the `mael` shim. |
| `mael self uninstall` | Remove the host services, the scheduler and the `mael` shim. Keeps `~/.maelstrom/install` and the notebook. On macOS, it also clears a repeating `pmset` wake an older scheduler set, and asks for sudo only if one is set. |
| `mael self restart [orchestrator\|agent-daemon]...` | Restart host services through the supervisor. Default: both. A restart of the agent daemon stops its agents; they resume after it. |
| `mael self export [PATH]` | Write this host's [State archive](../../CONTEXT.md): one zip of the state database and the attachments. It runs while the host services run. See below. |
| `mael self import PATH [--replace]` | Load a State archive into this host. It stops the host services, then starts them again. See below. |

```bash
mael self status                     # run this first when a host service or a scheduled task fails
mael self check-host                 # check the tools and sign-ins, and offer to fix each
mael self update                     # install the latest release on the stored channel
mael self update --channel main      # build and install _main
mael self update --tag v0.2.0rc1     # install one GitHub Release
mael self restart agent-daemon       # the production daemon picks up new code
mael self setup                      # repair the host
mael self export                     # write maelstrom-<host>-<date>.zip here
mael self import maelstrom-old-2026-11-06.zip   # load it on the new host
```

**`mael self setup`**

| Option | Description |
|---|---|
| `--changed TEXT` | The components that changed, comma-separated. Their host services restart. Default: none, so nothing restarts. |
| `--source DIRECTORY` | The checkout the release was built from. Links its jig plugin (see [the jig](../guide/jig.md#use-a-local-copy-of-the-plugin)). |

The installer writes `projects_dir:`. Setup asks for it only when the key is unset and a terminal
can answer. Without a terminal, it writes `~/Projects`. It moves an older install's state into `~/.maelstrom/state` once,
migrates `state.db`, writes `orchestrator_url:` when it is unset (see
[configuration.md](configuration.md)), installs the Claude integration, the plugins (see below) and the `mael` shim, then
loads the host services and the scheduler. Its last line names the orchestrator URL. It
restarts only the host services whose components or unit files changed. See
[host services](../dev/host-services.md#setup) for each step and its order.

A migration is forward-only and has no undo. See
[data-architecture.md](../dev/data-architecture.md), "Schema versions".

Setup registers maelstrom's plugin marketplace, `maelstrom`, with Claude Code and Codex. It has
two plugins:

- `maelstrom` holds the skills maelstrom needs, such as `mael` and `plan-task`. Each session
  maelstrom launches loads it, so you install nothing for it.
- `minnstack` holds optional development practices, such as `design-build-test` and
  `code-review`.

Install either plugin globally, so a plain `claude` session has its skills too. Remove it with
`uninstall`:

```bash
claude plugin install minnstack@maelstrom --scope user
claude plugin install maelstrom@maelstrom --scope user
claude plugin uninstall minnstack@maelstrom
codex plugin add minnstack@maelstrom
```

Setup installs `maelstrom` into Codex disabled. To use its skills in plain `codex`, set
`enabled = true` under `[plugins."maelstrom@maelstrom"]` in `~/.codex/config.toml`; setup keeps it.

**`mael self check-host`**

| Option | Description |
|---|---|
| `--dev` | Also check `node` and `pnpm`, which only a source install (`install.sh --source`) uses. |

The check runs these steps in order:

1. **Package manager.** `brew` on macOS, `apt-get` on Linux. Without Homebrew, it prints the
   Homebrew install line and skips every `brew` command.
2. **Tools.** Required: `git`, `gh`, `uv`, `claude`, `lsof`. Optional: `codex`, `tailscale`,
   `tangier`. For a missing tool, it prints the install command, and runs it after a yes. Codex
   installs through npm, so a yes to `codex` without Node installs Node first.
3. **Secret files.** It tightens `~/.maelstrom` to 0700, and `config.yaml`, the port allocations
   and each spawn record to 0600.
4. **Sign-ins.** `gh`, `claude` and, when installed, `codex`. For a signed-out tool, it offers the
   login.
5. **Plugins**, when `claude` is installed. It offers to install `minnstack`, the optional
   development-practice skills, into Claude Code, and into Codex when it is there.
6. **Tailscale**, when installed. It offers `tailscale up`, and on Linux makes you the tailscale
   operator. It offers to set `dev_host:` to the tailnet name, and to issue a certificate. It
   writes `dev_https: true` only when the certificate issues.
7. **PATH.** It warns when `~/.local/bin` is not on your PATH.

Without a terminal, the check asks nothing. It runs only the secret-file fix, which only narrows a
mode. It looks up tools on the host services' PATH.

| Exit code | Meaning |
|---|---|
| 0 | Every required tool is installed, and `gh` and `claude` are signed in. |
| 1 | A required item is unmet. The last line names each one. |

**`mael self update`**

| Option | Description |
|---|---|
| `--channel [stable\|main]` | Where to update from: `stable`, the latest GitHub Release, or `main`, a bundle built from the maelstrom project's `_main`. Stored as `update_channel` in `~/.maelstrom/config.yaml` for later updates. Default: the stored channel, else `stable`. |
| `--tag TEXT` | Install this GitHub Release, such as a pre-release or an older version to roll back to. Stable channel only. |

The `stable` channel downloads that release's `install.sh` and runs it. The `main` channel runs
`_main`'s own `bin/install.sh --source`, which pulls `_main`, runs its `bin/install`, builds a
bundle tagged `main-<commit>` and installs it. A tag that runs already is not built again. `install-main.sh` makes `_main` (see
[above](#install-from-main-install-mainsh)). Either way the installer then runs the new release's
`mael self setup`.

Run it from a shell, not from an agent of the production daemon. The daemon restart stops
that agent.

**`mael self export`**

| Argument | Description |
|---|---|
| `PATH` | Where to write the zip. It must not exist. Default: `maelstrom-<host>-<date>.zip` in the current directory. |

The zip holds `manifest.json`, `state.db` and each regular file under `attachments/`. The export
copies the database through SQLite's backup API, so it sees every committed write, also one that
is only in the WAL (write-ahead log) file. It skips a symlink. A failed export leaves no zip.

The zip does not carry these:

- the host config, `~/.maelstrom/config.yaml`
- the agent transcripts in `~/.claude`
- the daemon root, `~/.maelstrom/state/daemon/`

**`mael self import`**

| Option | Description |
|---|---|
| `--replace` | Replace state that is already here. The old `state.db`, its WAL files and `attachments/` move to `~/.maelstrom/state/replaced-<YYYYMMDD-HHMMSS>/`. The import prints the path. Delete it when you are done. |

The import reads the whole zip before it changes the disk. It refuses these:

- a zip from a newer build: its format or a schema version is above this build's
- an entry with an absolute path, a `..` part or a symlink, or an entry outside the three names
  above
- a file that is not a zip, or a zip with no `manifest.json` or no `state.db`
- a `state.db` that is not a SQLite database
- a host that holds state, without `--replace`

A host holds state when its database has a row in a table that is not a cache, such as a task,
a comm, a wiki page or an agent, or when its attachments directory is not empty. The empty database that `mael self setup` creates does not
count, so a new host needs no `--replace`.

Then it stops the host services and the scheduler, puts the state in place, migrates it, and
starts them again. They start again also when a step fails. Run it from a shell, not from an
agent session: the agent daemon stops.

To move to a new host:

1. On the old host, run `mael self export`.
2. On the new host, install maelstrom. `install.sh` runs `mael self setup`, which creates an
   empty `state.db`.
3. On the new host, run `mael self import <zip>`.

Task rows keep their absolute worktree paths. A host with another home directory or another
`projects_dir:` does not find those worktrees.

A worktree's own database is not production's. `bin/install` creates and upgrades it as the
project's `install_cmd`: on each worktree open, on `mael env start` and on
`mael env restart --install`.
