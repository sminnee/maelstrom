# Troubleshooting

What to run when something is wrong, and what the common failures mean. After the first two
sections, this page is organised by subsystem — find the heading that matches what broke.

## Start here

```bash
mael doctor            # project health, with automatic fixes
mael cmux status       # can maelstrom reach cmux?
mael list              # what every worktree is doing — see listing.md
mael session list      # live Claude sessions
mael task reconcile    # do tasks and sessions agree?
```

## `mael doctor`

Doctor runs thirteen checks in order and fixes what it safely can. Each reports **OK**,
**FIXED**, **WARNING** or **ERROR**.

| Check | Fixes |
|---|---|
| `.mael` marker | No — its absence means this is not a maelstrom project, and doctor stops. |
| `core.bare = true` | Yes |
| Standard fetch refspec | Yes |
| `notes.rewriteRef` | Yes — without it a rebase drops the notes `/code-review` writes. |
| `origin` remote configured | No |
| Remote default branch exists | No — try `git fetch origin`. |
| Default branch tracks its remote | Yes — a bare clone sets no upstream, so `git pull` in `_main` fails. |
| Local main against origin | Yes |
| Default branch is checked out in `_main` | No — moving it moves a checkout you may be working in. Doctor prints the commands. |
| Stale worktree registrations | Yes |
| Port allocations against worktrees | Yes |
| `.env` section markers | Reports |
| Permissions on files holding secrets | Yes |

Run it against a specific project:

```bash
mael doctor myproject
```

Doctor reads the default branch from `refs/remotes/origin/HEAD`, so a project on `develop`,
`master` or any other branch is checked against that branch. The rest of maelstrom still
assumes `main`.

---

## Sessions

### A session will not launch

Maelstrom places sessions in cmux and **fails rather than falling back** to a local shell.
So a launch failure usually means cmux is unreachable.

```bash
mael cmux status
```

This starts cmux if it is down and exits non-zero when it cannot be reached. Then check the
socket:

```bash
echo $CMUX_SOCKET_PATH      # default is /tmp/cmux.sock
```

To work without cmux for one session, use the escape hatch:

```bash
mael task run <id> --here
```

### "A session is already live for this task"

`mael task run` refuses to launch when a live session already holds the task, and names the
pid and worktree. Either attach to that session, or if it is a leftover:

```bash
mael task reconcile --fix
```

A *finished* task is deliberately not blocked — it stays re-runnable, reattaching with
`claude --resume`.

### `mael list` shows no session for a running agent

Liveness comes from live `claude` processes and their working directories, not from a
registry. A `claude` you started yourself, outside `mael`, appears with no task attached —
it carries no `--session-id`.

### A task is stuck in `in-progress/`

This is the failure that matters most, because an `in-progress` task **blocks every task
that follows it**.

```bash
mael task reconcile          # show the mismatches
mael task reconcile --fix    # apply the corrections
```

Reconcile corrects three mismatches:

| Observed state | Evidence | Corrected to |
|---|---|---|
| `in-progress`, no live session | A transcript persists — the task ran | `done` |
| `in-progress`, no live session | No transcript — the task never ran | `todo` |
| Not `in-progress` | A live session is working on it | `in-progress` |

---

## Tasks

### An execute task re-planned instead of implementing

Its draft omitted `--mode auto`. New tasks default to plan mode.

```bash
mael task update <id> --mode auto
```

### Iterations opened separate pull requests

A task set `branch:`. Tasks must inherit their parent's branch to accumulate into one PR.
Leave `branch:` unset on drafts.

### `mael task next` returns nothing

```bash
mael task list --all-todo     # includes waiting and parked tasks
```

A task is actionable only once everything it `follows` is done, and only if it is not parked
in `blocked/` or `template/`. If nothing is actionable, either an upstream task has not closed
— see "stuck in `in-progress/`" above — or the task you expect was parked by hand.

By default `next` prefers the current git branch, then falls back globally. `--branch`
removes the fallback, so it can legitimately return nothing.

### Task listings look wrong after a manual edit

The metadata index is a rebuildable cache:

```bash
mael task reindex
```

---

## Worktrees

### `close` refuses

```
Error: Worktree 'bravo' has uncommitted changes.
Error: Worktree 'bravo' has commits not merged to main.
```

Working as intended — close will not lose work. Commit and merge, or:

```bash
mael close --force
```

`--force` discards nothing: uncommitted changes are committed as `wip: uncommitted changes`,
the branch and its PR survive, and maelstrom creates a "Reopen" task.

### A rebase left the worktree mid-operation

```bash
mael sync --abort      # abort and restore instead of stopping mid-rebase
```

### `mael add` recycled a worktree I wanted fresh

```bash
mael add feature/x --no-recycle
```

### "Project not found"

Every command except `add-project --projects-dir` reads `projects_dir` from
`~/.maelstrom/config.yaml`:

```yaml
projects_dir: ~/Code
```

Set it before adding projects. Otherwise maelstrom looks in `~/Projects`.

### Renaming a project

Use `mael mv-project OLD NEW`. Do not rename the directory with `mv`.

A project name is load-bearing. The name is not stored as a field — it *is* the directory
name. The worktree folders, task and env directories, port allocations and Claude Code state
all follow from it. A plain `mv` breaks two of these silently:

- **Git worktree pointers break.** Each worktree records an absolute path to its
  administrative directory, and the repository records an absolute path back to
  each worktree. After a `mv` both point at directories that no longer exist, so
  git commands fail inside every worktree.
- **The next `mael doctor` deletes the port allocations.** Allocations are keyed
  by absolute project path. `doctor` prunes allocations whose worktree folders it
  cannot find, so it garbage-collects every port base for the project. Each
  worktree then gets new ports on its next start.

`mael mv-project` repairs the git pointers and moves the allocations across. Run
it with `--dry-run` first to see the full plan, and `mael doctor NEW` afterwards.

It does not migrate Claude sessions. Session ids derive from the project name, so
a rename orphans them: `mael task run` starts a fresh session rather than
resuming. The plan reports how many tasks this affects.

---

## Dev environments

### A service says `dead` right after starting

```bash
mael env logs <service>
```

The usual causes are a failed install step, a missing environment variable, or a port
already taken by something outside maelstrom.

### Ports are wrong after editing `.maelstrom.yaml`

```bash
mael env reset      # regenerate .env
```

Only the marked block in `.env` is regenerated; your own lines survive.

### A shared service will not stop

Shared services stay up while another worktree still subscribes. Stop the others, or:

```bash
mael env stop-all
```

### An apple-container service fails at start

If the VM IP never resolves within about 10 seconds, the start fails loudly rather than
leaving services pointed at the wrong host. Check the container is running, and that
`host_var:` is set on the **shared apple-container** service — it is not valid elsewhere.

### Constant rebuilds during editing

File watchers rebuild on every save. Stop the environment during heavy edits:

```bash
mael env stop
# ... make changes ...
mael env start
```

See [dev-environments.md](dev-environments.md#running) for why this is worth doing.

---

## Integrations

### A Sentry command says the integration is not configured

The keys nest under `sentry:`:

```yaml
sentry:
  org: "your-org"
  project_id: "your-project-slug"
```

Flat `sentry_org:` / `sentry_project:` keys are **not read**. This fails silently, so it is
worth checking first.

### An API key is not picked up

Resolution order is: environment variable → a `.env` file walked upward from the current
directory → `~/.maelstrom/config.yaml`. A `.env` closer to your current directory wins over
the global config.

### `gh` commands fail

```bash
gh auth status
gh auth login
```

---

## Scheduled work

### A scheduled task did not fire

```bash
mael schedule status
```

It reports the marker, the plist, whether launchd loaded the job, and the tail of
`~/.maelstrom/schedule.log`.

Common causes:

- **The agent was never installed.** It is opt-in per machine: `mael schedule install`.
- **The Mac was asleep.** Maelstrom does not wake it. The job runs on the next wake, and
  there is **no backfill** — you get one catch-up run per template, not one per missed
  boundary.
- **Nothing was due.** Every run writes a dated header to the log first, so a header with no
  runs means the agent fired and found nothing due.

---

## See also

- [CLI reference](../reference/cli.md)
- [Configuration reference](../reference/configuration.md)
