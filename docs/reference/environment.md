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
| `PORT_BASE` | `300` | The worktree's allocated 3-digit port base (300-999). |
| `<NAME>_PORT` | `FRONTEND_PORT=3000` | One per named port, computed as `PORT_BASE * 10 + index`. |
| `SHARED_PORT_BASE` | `301` | The project's shared port base. Written only when shared ports are configured. |

Port names come from the `services:` block, or from the legacy `port_names` list. See
[dev-environments.md](../guide/dev-environments.md).

### Into service processes

| Variable | Set by | Meaning |
|---|---|---|
| `host_var` (the name you choose) | `mael env start` | The polled VM IP of a shared `apple-container` service, e.g. `DB_HOST`. It lands in the spawn environment of sibling services only, never in `.env`. |

### Into launched Claude sessions

`mael task run`, `mael task next --run` and `mael open` export these into the `claude`
process:

| Variable | Meaning |
|---|---|
| `MAEL_TASK_ID` | The launched task's id. `mael task status done` and `mael task log` fall back to it, so a session can close its own task without naming it. |
| `MAEL_TASK_PARENT` | The launching task's `parent`, or its own id when it has none. New tasks default their `--parent` to it, so a session's follow-ups continue the same chain and land in the same PR. |
| `MAEL_SESSION_ID` | The task's deterministic Claude session id. The session-channel records it in the `~/.maelstrom` registry, because Claude Code does not export `CLAUDE_SESSION_ID` to subprocesses. |

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
