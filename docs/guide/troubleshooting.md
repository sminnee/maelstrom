# Troubleshooting

## Start here

```bash
mael doctor            # project health, with automatic fixes
mael cmux status       # can maelstrom reach cmux?
mael list              # worktrees, branches, PRs, sessions
mael session list      # live Claude sessions
mael task reconcile    # do tasks and sessions agree?
```

## `mael doctor`

Doctor runs ten checks in order and fixes what it safely can. Each reports **OK**,
**FIXED**, **WARNING** or **ERROR**.

| Check | Fixes |
|---|---|
| `.mael` marker | No — its absence means this is not a maelstrom project, and doctor stops. |
| `core.bare = true` | Yes |
| Standard fetch refspec | Yes |
| `origin` remote configured | No |
| `origin/main` exists | No — try `git fetch origin`. |
| Local main against origin | Yes |
| Stale worktree registrations | Yes |
| Port allocations against worktrees | Yes |
| `.env` section markers | Reports |
| Permissions on files holding secrets | Yes |

Run it against a specific project:

```bash
mael doctor myproject
```

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

Reconcile distinguishes an `in-progress` task that ran before (a transcript persists →
`done`) from one that never ran (no transcript → `todo`), and a live session whose task is
not `in-progress` (→ `in-progress`).

---

## Tasks

### `load-many --run` launched nothing, and exited 0

The head block follows the planning task. While that task is `in-progress`, the head is
blocked, and `--run` launches nothing — silently.

Close the planning task **first**:

```bash
mael task status done
mael task load-many plan.md --run
```

### An execute task re-planned instead of implementing

Its block omitted `mode: auto`. New tasks default to plan mode.

```bash
mael task update <id> --mode auto
```

### Iterations opened separate pull requests

A block set `branch:`. Tasks must inherit their parent's branch to accumulate into one PR.
Leave `branch:` unset on plan blocks.

### `mael task next` returns nothing

```bash
mael task list --all-todo     # includes blocked-but-waiting tasks
```

A task is actionable only once everything it `follows` is done. If everything is blocked, an
upstream task has not closed — see "stuck in `in-progress/`" above.

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

It reports the marker, the plist, whether launchd loaded the job, the `pmset` wake line, and
the tail of `~/.maelstrom/schedule.log`.

Common causes:

- **The agent was never installed.** It is opt-in per machine: `mael schedule install`.
- **The Mac was asleep.** Without `--wake-at` it does not wake. There is **no backfill** —
  you get one catch-up run per template, not one per missed boundary.
- **Nothing was due.** Every run writes a dated header to the log first, so a header with no
  runs means the agent fired and found nothing due.

---

## See also

- [CLI reference](../reference/cli.md)
- [Configuration reference](../reference/configuration.md)
