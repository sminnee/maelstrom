# Dev environments

Each worktree gets its own services and its own ports, so agents never collide.

## The problem

Two agents, two worktrees, two dev servers — both hardcoded to port 3000. The second one
fails to bind, or worse, the first one serves the second one's requests.

Maelstrom gives each worktree a **port base** and derives every service port from it. You
declare services once; maelstrom allocates, starts, tracks and stops them per worktree.

## Ports

Each worktree gets a `PORT_BASE`: a 3-digit number in the range **300-999**. Every named
port is `PORT_BASE * 10 + index`.

With `PORT_BASE=300` and ports `[FRONTEND, SERVER, DB]`:

```bash
PORT_BASE=300
FRONTEND_PORT=3000
SERVER_PORT=3001
DB_PORT=3002
```

Bravo gets 300, charlie 301, and their frontends land on 3000 and 3010. No collisions.

Maelstrom checks that the whole range is free before assigning it, and records the
allocation in `~/.maelstrom/port_allocations.json`. The allocation survives `mael close`, so
a recycled worktree keeps its ports.

The first **web-facing** port becomes the app URL that `mael list` and `mael env start` show.
Maelstrom treats a port named `APP` or `FRONTEND` as web-facing; with neither, a worktree has
no app URL.

Shared ports are allocated off a separate per-project base, exposed as `SHARED_PORT_BASE`, so
they do not consume slots from the worktree's own base.

> **Ceiling.** Because ports are `PORT_BASE * 10 + index`, there are only **10 slots per
> base** (index 0-9). A worktree needing more than 10 named ports in one scope would collide
> across bases. Shared ports come off a separate shared base, so they do not count against
> the local ten.

## Declaring services

Put a `services:` map in `.maelstrom.yaml`. Each service either runs a shell `command`, or
declares an `engine` and becomes a container maelstrom owns end to end.

```yaml
install_cmd: "uv sync"

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
```

Each service lists the **named ports it owns**. Maelstrom allocates them and exposes
`${NAME_PORT}` for use in commands, container mappings and service `env:` blocks.

Service type is inferred: an `engine:` makes it a container, otherwise it is a command
service. There is no `type:` key.

## Containers

Both `docker` and `apple-container` are supported. Maelstrom synthesises the
`rm -f … ; run …` boilerplate, so you declare the shape and it owns the lifecycle.

```yaml
services:
  db-shared:
    shared: true
    engine: apple-container                # or "docker"
    image: pgvector/pgvector:pg16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]           # host:container
    volume: /var/lib/postgresql/data       # named volume from the container name
    host_var: DB_HOST                      # apple-container: receives the VM's IP address
    env:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

### Apple `container` and `host_var`

Apple `container` runs each container as its own virtual machine (VM) on a private
`192.168.64.x` network, so `localhost` does not reach it. Set `host_var:` and maelstrom polls
`container inspect` for the VM's IP address at start time. Maelstrom then injects the address
into the **process environment of sibling services**, so a command service's `env:` can
reference `${DB_HOST}`.

The IP flows into the spawn environment only, never into `.env`, because it changes between
starts. If the IP never resolves (about a 10s poll), the start **fails loudly** rather than
leaving services pointed at the wrong host.

`host_var` is only meaningful for shared apple-container services. Maelstrom rejects it
elsewhere.

## Shared services

Some services should not be duplicated per worktree — a database is the usual case. Mark
them `shared: true`:

```yaml
  db-shared:
    shared: true
    engine: docker
    image: postgres:16
    ports: [DB]
    publish: ["${DB_PORT}:5432"]
```

The first worktree to start one starts it; later worktrees **subscribe** to the running
copy. Late subscribers reuse the already-discovered `host_var` IP rather than inspecting
again. Stopping one worktree's environment leaves a shared service running while another
worktree still uses it.

## Running

```bash
mael env start                 # install_cmd, then start every service
mael env start --skip-install  # skip the install step
mael env status                # PIDs, status, log paths
mael env logs                  # recent logs
mael env logs -f               # follow
mael env logs myproject.bravo web -n 50   # one service, 50 lines
mael env restart               # stop then start
mael env restart --install     # ...running install_cmd first
mael env stop                  # SIGTERM, then SIGKILL after 10s
```

Across worktrees and projects:

```bash
mael env list          # running environments in this project
mael env list-all      # every running environment
mael env stop-all      # stop them all
```

**Stop environments during heavy editing.** File watchers rebuild on every save, which is
wasted work when an agent is rewriting many files. Start again when you want to test.

## The `.env` file

`mael add` writes `.env` in the worktree. It merges the **project root's** `.env` — used as
a template, with `$VAR` substitution — with the generated variables:

```
# Maelstrom port allocations
FRONTEND_PORT=3000
PORT_BASE=300
WORKTREE=bravo
WORKTREE_NUM=1
# End Maelstrom port allocations
```

The marked block is maelstrom's; anything outside it is yours and survives regeneration.
Add `.env` to `.gitignore` — it is per-worktree and generated.

After changing ports in `.maelstrom.yaml`:

```bash
mael env reset
```

## Procfile fallback

With no `services:` block, maelstrom reads a `Procfile`:

```
web: npm run dev
worker: python manage.py worker
redis: redis-server --port $REDIS_PORT
```

On this path, a service whose name ends in `-shared` is shared across worktrees. Ports come
from the flat `port_names` and `shared_port_names` lists.

With neither, maelstrom falls back to `start_cmd`.

Precedence is **`services:` → Procfile → `start_cmd`**. All three paths stay supported, so
migrate projects one at a time.

## See also

- [Configuration reference](../reference/configuration.md) — every key.
- [Environment variables](../reference/environment.md) — what is set and read.
