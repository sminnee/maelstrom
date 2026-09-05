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
| `WORKTREE` | `bravo` | The worktree's NATO name, or `_main`. |
| `WORKTREE_NUM` | `1` | The name's index modulo 16: alpha = 0, papa = 15, quebec = 0, `_main` = 0. See the caveat below. |
| `PORT_BASE` | `300` | The worktree's port base. A NATO worktree gets a 3-digit number from 300-999; `_main` gets the reserved `main_port_base`. Written whenever the project configures any port. See the caveat below. |
| `<NAME>_PORT` | `FRONTEND_PORT=3010` | One per named port. A local port is `<local base> * 10 + index`; a shared port is `SHARED_PORT_BASE * 10 + index`. |
| `SHARED_PORT_BASE` | `300` | The project's shared port base. Written only when shared ports are configured. |

A project with no ports at all gets neither base.

> **`WORKTREE_NUM` repeats after the 16th worktree.** The number is the NATO name's index
> modulo 16, because it names a Redis database and Redis numbers its databases 0-15. The
> wrap makes the number valid, but it is unique only for alpha to papa. quebec gets 0, the
> same number as alpha, so two live worktrees can share whatever you key on it. `_main` gets 0
> too, for the same reason: there is no free slot left. Use `WORKTREE` for a value that is
> unique across every worktree.

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
| `MAEL_TASK_ID` | The launched task's id. `mael task status done` and `mael task get-status` fall back to it, so a session can close its own task without naming it. `mael task current` reports it as `ID:STATUS`. |
| `MAEL_TASK_PARENT` | The launching task's `parent`, or its own id when it has none. New tasks default their `--parent` to it, so a session's follow-ups continue the same chain and land in the same PR. |
| `MAEL_TASK_SESSION_ID` | The task's derived Claude session id — a **task key, not a reference to the session running now**. No Python reads it. The only consumer is the session-channel, which uses it to key the task's file in the `~/.maelstrom` registry. |

`MAEL_TASK_SESSION_ID` rides on the `claude` command line rather than in the environment dict
beside `MAEL_TASK_ID`, but a task-backed launch is the only path that sets any of the three.

**Two session ids answer two different questions.** Use the one that matches your question:

| Question | Variable | Behaviour |
|---|---|---|
| Which task is this? | `MAEL_TASK_SESSION_ID` | Derived from the task. Set before the session starts, and never changes. |
| Which conversation is running now? | `CLAUDE_CODE_SESSION_ID` | Set by Claude Code. A `/clear` starts a new conversation and moves it. |

`CLAUDE_CODE_SESSION_ID` is Claude Code's own variable, not maelstrom's, and every session has it.
`mael session info` and `mael session end` read it to find the session you run them in. Those
commands fall back to `CLAUDE_PID` — also Claude Code's — because a `/clear` leaves the live id in
no command line, so the pid is the only handle that always resolves.

Do not use `MAEL_TASK_SESSION_ID` to name the session you are in. It holds the id the session
started with, which is right until a `/clear` and points at a finished transcript after one.

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
| `MAEL_AGENT_SOCKET` | `~/.maelstrom/agent-daemon.sock` | Control socket for `mael agent`. The daemon, the CLI and `mael orchestrator serve` (without `--socket`) all read it, so set it for all of them. Set it in a worktree's `.env` to give that environment its own daemon — see [agent-daemon.md](../dev/agent-daemon.md#a-daemon-per-environment). |
| `MAEL_AGENT_LOG` | `~/.maelstrom/agent-daemon.log` | Where an auto-started agent daemon writes its output. |
| `MAEL_AGENT_SPEC_DIR` | `~/.maelstrom/agents` | Where the daemon keeps one spawn record per agent. A daemon resumes the agents whose records it finds here, so a test daemon wants its own directory. |
| `MAEL_AGENT_NO_AUTOSTART` | unset | Set to `1` to stop `mael agent` starting a daemon it finds missing. Every auto-started daemon inherits it, so a daemon never spawns a daemon. |
| `ORCHESTRATOR_URL` | `http://localhost:8765` | Where the web dev server proxies `/api` to: the orchestrator's REST routes, its change stream and its per-agent sockets. Read by `vite.config.ts`, not by the bundle, so the built app carries no address. |
| `EDITOR` | `vi` | Editor for `mael task edit` and `mael task add --edit`. |
| `TMPDIR` | system temp | Scratch directory for artifact downloads. |

`GITHUB_TOKEN` is not read directly — `mael gh …` shells out to the `gh` CLI, which uses
its own authentication.

---

## See also

- [Configuration](configuration.md) — both config files.
- [Dev environments](../guide/dev-environments.md) — how ports are allocated.
- [Tasks](../guide/tasks.md) — how `MAEL_TASK_PARENT` builds chains.
