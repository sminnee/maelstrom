# Remote hosts

One orchestrator server can put worktrees and agents on a second machine. The parent (monolith,
always on) keeps the only world: tasks, agents, attention and documents. A child (cobalt) joins
when it is on, and runs worktrees and agents for the parent. This raises agent capacity past what
one machine allows.

The child runs `mael-orchestrator serve` in **runner mode** (`role: runner` in its
`~/.maelstrom/config.yaml`). A runner has no UI and no task store. Its agents use the parent for
task state.

Two seams carry all the traffic:

- **Worktree host.** The parent reverse-proxies each `/api/worktrees/{id}/…` route whose id
  starts with a host prefix, such as `cobalt:`, to the child's server. The child runs the same
  handlers on its own disk.
- **Agent host over HTTP.** The child exposes its `AsyncDaemonClient` boundary (`request` and
  `attach`) as two routes. The parent's `RemoteDaemonClient` implements the same interface.

The child runs its existing routes and agent boundary. The parent proxies them by the host prefix
on the id, so almost no parent handler changes.

```
monolith (parent)                                cobalt (runner)
┌──────────────────────────────┐   HTTPS/tailnet  ┌──────────────────────────────┐
│ world: tasks, agents,        │                  │ worktree source, gate watch, │
│ attention, documents         │                  │ env control, DaemonRouter    │
│                              │                  │                              │
│ /api/worktrees/cobalt:…  ────┼── proxy ────────►│ /api/worktrees/…             │
│ DaemonRouter                 │                  │                              │
│   └ RemoteDaemonClient  ─────┼── request ──────►│ /api/host/request            │
│                         ◄────┼── attach (WS) ───│ /api/host/agents/{id}/attach │
│ RemoteHost (SSE link)  ◄─────┼── events ────────│ /api/events                  │
└──────────────────────────────┘                  └──────────────────────────────┘
```

## Decisions

### A reverse proxy for worktrees

Every worktree route is already scoped by id: `changes`, `diff`, `file`, `tree`, `excerpt`,
`definition`, `raw`, `close`, `force-close`, `trash`, `sync`, `merge-pr`, `auto-merge`, `env`,
`comments`, and `DELETE` (see `orchestrator-api/src/mael_orchestrator/routes.py`). One prefix rule
covers all of them. This includes the language-server chain, which then runs on the child.

The alternative, one tunnelled endpoint per feature, adds a route each time a worktree feature is
added.

### Agents go through the daemon boundary, not a route proxy

The parent builds attention, documents, milestones and the agent-to-task link from each
transcript stream. It joins that state to its own task store. Document approval promotes drafts
into the parent's tasks.

When the parent attaches to the child's agents as it does to local ones, none of that code
changes. A proxy of `/api/agents/{id}/*` would make the child build that state, and the parent
would then have to merge it.

### Expose `AsyncDaemonClient`, do not change the Claude daemon to REST

Codex agents do not use the Claude daemon. `CodexDaemonClient` is an adapter that runs inside the
orchestrator process. It speaks JSON-RPC to a local `codex app-server proxy` (see
`codex_daemon.py` and `codex_bridge.py`). The orchestrator's `DaemonRouter` (`daemon_bridge.py`)
sits above both harnesses.

The child exposes the boundary at the `DaemonRouter` level, so Claude and Codex agents both run on
the child. The Unix-socket Claude daemon does not change. A REST Claude daemon would leave the
Codex adapter with no home on the child.

### The parent connects to the child

This is the same direction as the link from the server to the daemon. Both machines are on
the tailnet, so NAT does not matter.

### Static config and a held event stream, no registration

The parent's `~/.maelstrom/config.yaml` names each host:

```yaml
hosts:
  cobalt: https://cobalt.tailbaef49.ts.net:2772
```

The parent keeps the child's `GET /api/events` stream open, with reconnect and backoff:

| Stream state | Meaning |
|---|---|
| Open | The host is online. |
| Dropped | The host is offline. |
| A `change` notice arrives | The parent refetches the child's worktrees or gate jobs, and publishes the notice again with host-prefixed ids. |

When the child boots, the next reconnect finds it.

### Offline is not dead

The agents and worktrees of an offline host keep their last state, with an `offline` mark beside the host name.
The parent changes none of them until the host answers again. A host that sleeps must not lose
its agents.

### Runner mode is a config key

`role: runner` in `~/.maelstrom/config.yaml` turns off the UI and the task source. The runner
answers every route outside its surface with `not_implemented`. The worktree source, the gate
watch, env control and the daemon router stay on. To make the machine a normal orchestrator
again, remove the key and restart.

It is a config key, not an environment variable, because three readers need it. The server
needs it. The agents that the runner's daemon starts need it, so `mael task log` refuses. And
each later `mael self update` needs it, so it keeps the stored `orchestrator_url:`. The config
file reaches all three, and no unit environment has to change.

### The child's agents use the parent for task state

On the child, `orchestrator_url:` points at the parent. The commands that
[cli-over-api.md](cli-over-api.md) already moved reach the parent with no change: `task show`,
`task status`, `task add`, `task promote`, and the `gh create-pr` tell.

`task log`, `task next` and `task edit` still write the local database. In runner mode, each one
fails with a clear message until it moves to the API. A runner refuses to start when
`orchestrator_url:` is not set.

Production derives `orchestrator_url:` from `dev_host:`, which on the child names the child
itself. So in runner mode, production reads the key from the file instead, and `mael self setup`
keeps it.

### A task sticks to its host

The worktree and the session transcript live on one host. A relaunch or a resume defaults to the
host of the task's last session. The split launch button can override this. A session started on
the other host is a new session.

### Ids carry the host

| Entity | Local id | Remote id |
|---|---|---|
| Worktree | `<project>-<nato>` | `<host>:<project>-<nato>` |
| Agent | `<id>` | `<host>:<id>` |

Local ids do not change, so the current world, URLs and stored data are not affected. The proxy
and the router remove the prefix before they call the child.

### No auth beyond the tailnet

This is the same as the UI. A runner can start processes, so it binds only to its tailnet
address (`--public-host`) and to loopback. The `/api/host/` routes refuse a request with an
`Origin` header: a parent server sends none, and a browser always does. So a page on another site
cannot start an agent on the runner.

## The child's surface

| Route | Status | Purpose |
|---|---|---|
| `/api/worktrees/{id}/…` | Exists | Reached through the parent's proxy. |
| `GET /api/worktrees` | Exists | The parent merges the child's worktrees into its world, with host-prefixed ids. |
| `GET /api/gate-jobs` | Exists | The parent merges the child's gate jobs. |
| `GET /api/events` | Exists | The online signal and the change notices. |
| `POST /api/host/request` | New | One `AsyncDaemonClient.request` payload in, one reply out. |
| `GET /api/host/agents/{id}/attach` | New | WebSocket. The `attach` stream frame for frame, with the same `mael_seq` and epoch cursor. |
| `POST /api/host/worktrees/open` | New | Runs `setup_worktree_for_branch` and `write_env_file` (`lib/domain/src/mael_domain/worktree.py`). Returns `{setup, envLines, hasTranscript}`. |

For a remote launch, the `open_worktree` and `write_env` steps in `_task_launch_steps`
(`server.py`) call `POST /api/host/worktrees/open` in place of the local calls.

## The parent's side

- `RemoteHost` holds the base URL, the event-stream link and the online state.
- `RemoteDaemonClient` implements `AsyncDaemonClient` (`lib/agent/src/mael_agent/agent_transport.py`)
  over the two host routes.
- `DaemonRouter` chooses the host by the id prefix first, then the harness.
- The worktree proxy is one aiohttp handler wrapper in `routes.py`, applied to the worktree
  routes. It streams the body in both directions.
- Document file reads (`/api/files/{id}`) for a remote worktree go through the proxied worktree
  `raw` and `file` routes.

## Not covered

- A host that the parent's config does not name. There is no registration on boot.
- Moving a task's worktree or session from one host to another.
- Scheduling and capacity limits, such as choosing the less busy host automatically.
- A terminal on the parent for a remote worktree. `/api/worktrees/{id}/terminal` opens cmux on
  the machine that serves the route, so through the proxy it opens on the child.
- The child's own setup: `gh` auth, git identity, Codex and Claude login, and project checkouts.
  `mael self setup` and `check-host` cover these.
