# Configuration reference

Maelstrom reads two files:

| File | Scope | Checked in |
|---|---|---|
| `.maelstrom.yaml` | One project | Yes — it describes the project. |
| `~/.maelstrom/config.yaml` | One machine | No — it holds your API keys. |

Maelstrom finds `.maelstrom.yaml` by searching upward from the current directory.

---

## Project configuration — `.maelstrom.yaml`

Put this file in your repository root.

### Top-level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `services` | map | `{}` | Structured service definitions. Preferred. See below. |
| `port_names` | list of string | `[]` | Legacy flat port names. Each gets a `<NAME>_PORT` variable. |
| `shared_port_names` | list of string | `[]` | Legacy flat port names shared across worktrees in the project. |
| `main_port_base` | int | — | Port base reserved for the `_main` worktree, making it the project's fixed environment. Must be 1-6552 and outside 300-999. See below. |
| `install_cmd` | string | `""` | Command that installs dependencies. Runs on worktree creation and on `mael env start`. |
| `start_cmd` | string | `""` | Fallback start command when there is no `services:` block and no Procfile. |
| `linear` | map | — | Linear settings. See below. |
| `sentry` | map | — | Sentry settings. See below. |
| `uptimerobot` | map | — | UptimeRobot settings. See below. |

When `services:` is present it supersedes `port_names`, `shared_port_names`, the Procfile
and `start_cmd`. Precedence is **`services:` → Procfile → `start_cmd`**.

The legacy flat form still works. Use it for a single-process project:

```yaml
install_cmd: "npm install"
start_cmd: "npm run dev"           # used only with no services: and no Procfile
port_names: [FRONTEND, SERVER]     # -> FRONTEND_PORT, SERVER_PORT
shared_port_names: [DB]            # -> DB_PORT, one copy for the whole project
```

`port_names` and `shared_port_names` still allocate ports and write `.env` on this path. Only
the start mechanism differs. `start_cmd` runs as one service named `app`. With no `services:`,
no Procfile and no `start_cmd`, `mael env start` fails.

### `main_port_base:`

`main_port_base` gives the `_main` worktree a port base that never changes, so the project has
one always-there environment:

```yaml
main_port_base: 277        # FRONTEND 2770, FRONTEND_HMR 2771, ORCHESTRATOR 2772
```

`_main` then gets a `.env` and answers to every `mael env` verb, addressed as
`<project>._main`. Ports come off the base as usual: `main_port_base * 10 + index`.

The base must fall from **1 to 6552**, and **outside 300-999**. Maelstrom rejects anything else
when the config loads. Inside 300-999 the allocator could hand the same base to a NATO worktree
without a word; above 6552 the derived ports pass 65535 and no service can bind.

Omit the key and `_main` keeps no ports and no `.env`. See
[the fixed environment](../guide/worktrees.md#the-fixed-environment).

### `services:`

Each entry is a named service. Maelstrom infers the type: an `engine` makes it a
**container** service, otherwise it is a shell **command** service.

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `command` | string | command | The shell command to run. **Required** for a command service. |
| `dir` | string | command | Working directory, relative to the worktree root. |
| `engine` | string | container | `docker` or `apple-container`. Its presence makes the service a container. |
| `image` | string | container | Container image. **Required** for a container service. |
| `args` | list of string | container | Arguments appended after the image, e.g. `["-c", "max_locks_per_transaction=1024"]`. Each element becomes one shell word, so it must not contain a space or a shell metacharacter. |
| `publish` | list of string | container | Host-to-container port mappings, e.g. `["${DB_PORT}:5432"]`. |
| `volume` | string | container | Mount path. The named volume derives from the container name. |
| `host_var` | string | container | Variable that receives the polled VM IP. Only valid for `apple-container`, and only meaningful when `shared: true`. |
| `ports` | list of string | both | Named ports this service owns. Each becomes `${<NAME>_PORT}`. |
| `env` | map | both | Extra environment variables for the service. |
| `shared` | bool | both | Default `false`. When true, the service is shared across worktrees in the project. |
| `optional` | bool | both | Default `false`. When true, `mael env start` skips the service. Start it with `mael env start <name>`. |

Maelstrom rejects the config when any of these is true:

- A container service has no `image`, or a command service has no `command`.
- `engine` is not a known engine.
- `ports` is not a list of names.
- `args` is not a list of strings.
- `args` is set on a command service.
- `host_var` is set on a service that is not `apple-container`.
- A service is both `shared` and `optional`.

```yaml
services:
  frontend:
    ports: [FRONTEND, FRONTEND_HMR]      # -> ${FRONTEND_PORT}, ${FRONTEND_HMR_PORT}
    dir: frontend
    command: env PORT=${FRONTEND_PORT} node server-dev.ts
    env:
      VITE_ORCHESTRATOR_URL: ws://localhost:${ORCHESTRATOR_PORT}   # a sibling's port, expanded at start

  server:
    ports: [SERVER]
    command: env PORT=${SERVER_PORT} uv run serve-dev

  worker:
    command: uv run serve-dev worker       # no ports, no dir

  orchestrator:
    ports: [ORCHESTRATOR]
    command: uv run mael orchestrator serve --port ${ORCHESTRATOR_PORT}

  ladle:
    optional: true                         # skipped by `mael env start`
    ports: [LADLE_APP]
    command: env PORT=${LADLE_APP_PORT} npx ladle serve

  db-shared:
    shared: true
    engine: apple-container
    image: pgvector/pgvector:pg16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]
    volume: /var/lib/postgresql/data
    host_var: DB_HOST
    args: ["-c", "max_locks_per_transaction=1024"]
    env:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

### `linear:`

| Key | Type | Meaning |
|---|---|---|
| `team_id` | string | Linear team UUID. Required for Linear commands. |
| `workspace_labels` | list of string | Labels that map to worktree names, e.g. `[alpha, bravo, charlie]`. |
| `product_label` | string | Label assigned to tasks. `mael linear release` promotes issues carrying it. |

### `sentry:`

| Key | Type | Meaning |
|---|---|---|
| `org` | string | Sentry organisation slug. |
| `project_id` | string | Sentry project slug or id. |

> These keys are **nested under `sentry:`**. Flat `sentry_org:` / `sentry_project:` keys are
> not read and the integration stays silently unconfigured.

### `uptimerobot:`

| Key | Type | Meaning |
|---|---|---|
| `monitors` | list of string | Monitor ids this project cares about. With none set, the commands fall back to every monitor on the account. |

Run `mael uptimerobot monitors` once to discover the ids.

### Full example

```yaml
install_cmd: "uv sync"

services:
  frontend:
    ports: [FRONTEND]
    command: npm run dev
  db-shared:
    shared: true
    engine: docker
    image: postgres:16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]

linear:
  team_id: "your-team-uuid"
  workspace_labels: [alpha, bravo, charlie]
  product_label: "YourProduct"

sentry:
  org: "your-org"
  project_id: "your-project-slug"

uptimerobot:
  monitors: ["796748268", "796748269"]
```

---

## Global configuration — `~/.maelstrom/config.yaml`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `projects_dir` | path | `~/Projects` | Base directory for projects. `~` expands. |
| `open_command` | string | `code` | Editor command that `mael ide` and `mael add --open` run. |
| `linear.api_key` | string | — | Linear API key. |
| `sentry.api_key` | string | — | Sentry API key. |
| `uptimerobot.api_key` | string | — | UptimeRobot API key. |
| `slack.webhooks` | map | `{}` | Named Slack webhook URLs. The first entry is the default channel for `mael slack post`. |

```yaml
projects_dir: ~/Projects
open_command: "cursor"

linear:
  api_key: "lin_api_xxx"

sentry:
  api_key: "sntrys_xxx"

uptimerobot:
  api_key: "u796748-xxx"

slack:
  webhooks:
    alerts: "https://hooks.slack.com/services/T000/B000/xxx"
    releases: "https://hooks.slack.com/services/T000/B001/yyy"
```

`slack.webhooks` preserves the order you write it in — the first entry is what
`mael slack post` uses when you pass no `--channel`.

The legacy path `~/.maelstrom.yaml` still loads if `~/.maelstrom/config.yaml` is absent.

### API keys

Each API key resolves in this order:

1. The environment variable — `LINEAR_API_KEY`, `SENTRY_API_KEY`, `UPTIMEROBOT_API_KEY`.
2. A `.env` file, searched upward from the current directory.
3. The matching key in `~/.maelstrom/config.yaml`.

This file holds plaintext secrets. `mael doctor` checks its permissions and tightens them.

---

## Procfile (legacy fallback)

With no `services:` block, maelstrom reads a `Procfile` in the repository root:

```
web: npm run dev
worker: python manage.py worker
redis: redis-server --port $REDIS_PORT
```

On this path, a service whose name ends in `-shared` is shared across worktrees. Migrate
projects to `services:` one at a time. All three paths stay supported.

---

## See also

- [Dev environments](../guide/dev-environments.md) — how services, engines and ports work.
- [Environment variables](environment.md) — what maelstrom reads and sets.
