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

### `services:`

Each entry is a named service. Maelstrom infers the type: an `engine` makes it a
**container** service, otherwise it is a shell **command** service.

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `command` | string | command | The shell command to run. **Required** for a command service. |
| `dir` | string | command | Working directory, relative to the worktree root. |
| `engine` | string | container | `docker` or `apple-container`. Its presence makes the service a container. |
| `image` | string | container | Container image. **Required** for a container service. |
| `publish` | list of string | container | Host-to-container port mappings, e.g. `["${DB_PORT}:5432"]`. |
| `volume` | string | container | Mount path. The named volume derives from the container name. |
| `host_var` | string | container | Variable that receives the polled VM IP. Only valid for `apple-container`, and only meaningful when `shared: true`. |
| `ports` | list of string | both | Named ports this service owns. Each becomes `${<NAME>_PORT}`. |
| `env` | map | both | Extra environment variables for the service. |
| `shared` | bool | both | Default `false`. When true, the service is shared across worktrees in the project. |

Maelstrom rejects the config when a container service has no `image`, a command service has
no `command`, `engine` is not a known engine, `ports` is not a list of names, or `host_var`
is set on a non-`apple-container` service.

```yaml
services:
  frontend:
    ports: [FRONTEND, FRONTEND_HMR]      # -> ${FRONTEND_PORT}, ${FRONTEND_HMR_PORT}
    dir: frontend
    command: env PORT=${FRONTEND_PORT} node server-dev.ts

  server:
    ports: [SERVER]
    command: env PORT=${SERVER_PORT} uv run serve-dev

  worker:
    command: uv run serve-dev worker       # no ports, no dir

  db-shared:
    shared: true
    engine: apple-container
    image: pgvector/pgvector:pg16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]
    volume: /var/lib/postgresql/data
    host_var: DB_HOST
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
