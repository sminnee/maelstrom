# Environment variables

What maelstrom reads, and what it sets.

---

## Variables maelstrom sets

### In each worktree's `.env`

`mael add` writes a `.env` file in the worktree. `mael env reset` regenerates it. The file
merges the project root's `.env` (as a template, with `$VAR` substitution) with the
generated variables below.

| Variable | Example | Meaning |
|---|---|---|
| `WORKTREE` | `bravo` | The worktree's NATO name. |
| `WORKTREE_NUM` | `1` | The name's index: alpha = 0, bravo = 1, charlie = 2, … |
| `PORT_BASE` | `300` | A 3-digit port base (300-999). Written whenever the project configures any port. See the caveat below. |
| `<NAME>_PORT` | `FRONTEND_PORT=3010` | One per named port. A local port is `<local base> * 10 + index`; a shared port is `SHARED_PORT_BASE * 10 + index`. |
| `SHARED_PORT_BASE` | `300` | The project's shared port base. Written only when shared ports are configured. |

A project with no ports at all gets neither base.

> **`PORT_BASE` is unreliable when a project has both local and shared ports.** Maelstrom
> writes the local ports first and the shared ports second, and each pass writes `PORT_BASE`.
> The shared pass therefore overwrites it, so `PORT_BASE` holds the *shared* base while the
> worktree's own `<NAME>_PORT` values still derive from the local one. Read the individual
> `<NAME>_PORT` variables rather than recomputing ports from `PORT_BASE`. With only shared
> ports configured, `PORT_BASE` and `SHARED_PORT_BASE` hold the same value.

Port names come from the `services:` block, or from the legacy `port_names` list. See
[dev-environments.md](../guide/dev-environments.md).

A generated `.env` for bravo, in a project with a `FRONTEND` and `SERVER` port and a shared
`DB` port:

```bash
# Maelstrom port allocations
DB_PORT=3000
FRONTEND_PORT=3010
PORT_BASE=300
SERVER_PORT=3011
SHARED_PORT_BASE=300
WORKTREE=bravo
WORKTREE_NUM=1
# End Maelstrom port allocations
API_URL=http://localhost:3010
```

Maelstrom owns the marked block and sorts it alphabetically. Content from the project root's
`.env` follows it, with `$VAR` substituted. Anything you add outside the markers survives
`mael env reset`.

The local base here is 301 — `FRONTEND_PORT` is `301 * 10 + 0` — but `PORT_BASE` reads 300
because the shared pass wrote last. That is the clobber described above.

### Into service processes

`mael env start` builds each service's environment in three layers. A later layer wins:

1. The environment of the `mael` process itself.
2. The worktree's `.env` file.
3. The service's own `env:` block in `.maelstrom.yaml`, with `$VAR` substituted from layers 1
   and 2. An unknown `$VAR` is left as written, matching shell behaviour.

Layer 3 applies to one service only. Two services in one worktree can therefore hold different
values for the same name.

| Variable | Set by | Meaning |
|---|---|---|
| `host_var` (the name you choose) | `mael env start` | The polled VM IP of a shared `apple-container` service, e.g. `DB_HOST`. It lands in the spawn environment of sibling services only, never in `.env`. |

### Into launched Claude sessions

A **task-backed** launch — `mael task run` or `mael task next --run` — exports these into the
`claude` process:

| Variable | Meaning |
|---|---|
| `MAEL_TASK_ID` | The launched task's id. `mael task status done` and `mael task log` fall back to it, so a session can close its own task without naming it. |
| `MAEL_TASK_PARENT` | The launching task's `parent`, or its own id when it has none. New tasks default their `--parent` to it, so a session's follow-ups continue the same chain and land in the same PR. |
| `MAEL_SESSION_ID` | The deterministic Claude session id. No Python reads it — the only consumer is the session-channel, which records it in the `~/.maelstrom` registry because Claude Code does not export `CLAUDE_SESSION_ID` to subprocesses. |

`MAEL_SESSION_ID` rides on the `claude` command line rather than in the environment dict
beside `MAEL_TASK_ID`, but a task-backed launch is the only path that sets any of the three.

**`mael open` and `mael claude` set none of them.** Those commands launch a plain session with
no task attached. `mael task status done` in such a session fails with "No task id given and
MAEL_TASK_ID is not set". Name the task explicitly there.

`MAEL_TASK_PARENT` is what keeps a chain together without anyone naming the parent. A session
launched for task `linear.ME-41.1` sees:

```bash
echo $MAEL_TASK_ID       # linear.ME-41.1
echo $MAEL_TASK_PARENT   # linear.ME-41

mael task add "Add the migration" --mode auto     # parent defaults to linear.ME-41
mael task status done                             # closes linear.ME-41.1
```

The new task lands under `linear.ME-41` beside its sibling, so both share one branch and one
pull request. A task with no parent exports its own id instead, so a parentless session still
roots one chain.

---

## Variables maelstrom reads

### Integration keys

Each key resolves in this order: **environment variable → a `.env` file walked upward from
the current directory → `~/.maelstrom/config.yaml`**. The walk continues past `.env` files
that lack the key.

| Variable | Config fallback | Used by |
|---|---|---|
| `LINEAR_API_KEY` | `linear.api_key` | `mael linear …` |
| `SENTRY_API_KEY` | `sentry.api_key` | `mael sentry …` |
| `UPTIMEROBOT_API_KEY` | `uptimerobot.api_key` | `mael uptimerobot …` |

### Other

| Variable | Default | Meaning |
|---|---|---|
| `CMUX_SOCKET_PATH` | `/tmp/cmux.sock` | Socket maelstrom uses to drive cmux. Set it when cmux listens elsewhere. |
| `EDITOR` | `vi` | Editor for `mael task edit` and `mael task add --edit`. |
| `TMPDIR` | system temp | Scratch directory for artifact downloads. |

`GITHUB_TOKEN` is not read directly — `mael gh …` shells out to the `gh` CLI, which uses
its own authentication.

---

## See also

- [Configuration](configuration.md) — both config files.
- [Dev environments](../guide/dev-environments.md) — how ports are allocated.
- [Tasks](../guide/tasks.md) — how `MAEL_TASK_PARENT` builds chains.
