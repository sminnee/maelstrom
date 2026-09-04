# The orchestrator server

The server builds the world the orchestrator UI shows — tasks, worktrees, agents, transcripts,
attention — from the task notebook, `list-all` and the agent host, and serves it to every
browser over one WebSocket. `mael orchestrator serve` runs it.

The server owns the business model. The agent host owns the agent processes. The server only
reaches the host through the host's own client protocol, never by importing its internals, so a
host on another machine later is the same protocol over TCP.

## The layers

`src/maelstrom/orchestrator/` follows [architecture-patterns.md](architecture-patterns.md). The
wire types are `TypedDict`s in the wire's own camelCase, so an entity is the dict the socket
carries and nothing maps between a dataclass and the wire.

| File | Layer | Holds |
|---|---|---|
| `protocol.py` | pure | The wire types, `empty_world`, and `apply_event`, the reducer the browser also runs |
| `normalise.py` | pure | The stream-json normaliser, a port of `web/src/protocol/normalise.ts` |
| `validate.py` | pure | Command validation, a port of `web/src/protocol/validate.ts` |
| `event_log.py` | pure | The seq-stamped log: `append`, `replay_from`, `snapshot_frame`, a ring of 5000 frames |
| `world_build.py` | pure | Entity builders from a task, a `list-all` row and an agent row; `link_agent`; `diff_kind`; `task_key` |
| `desk.py` | pure | The desk table: `add`, `remove`, `prune`, each returning a new table, and the desk id helpers |
| `sources.py` | storage | `TaskSource` and `WorktreeSource`, over the notebook and `list_all.build_list_all_data` |
| `daemon_bridge.py` | storage | `AsyncDaemonClient`: the socket client for the agent host, and a scripted fake |
| `../desk_store.py` | storage | `DeskStore`: the desk as one JSON file at `~/.maelstrom/desk.json`, or in memory |
| `server.py` | adapter | `Orchestrator`: the log, the pollers, one watch per agent, the commands, the socket |
| `../orchestrator_cli.py` | CLI | `mael orchestrator serve` |

`task_launch.py` at the top level holds the launch plan and its two guards, shared with
`mael task run`. `list_all.py` holds the rows both `mael list-all` and the server read.

## Normaliser parity

The TypeScript normaliser is the reference. `UPDATE_GOLDEN=1 pnpm test` in `web/` writes one
golden replay per fixture to `tests/fixtures/agent_events/normalised/`. The Python tests replay
the same fixtures into the same seed agent and must produce the same world. A change to one
normaliser without the other fails that test rather than drifting.

The tool-card port is held the same way. `classify_tool_call` and `tool_call_title` in
`agent_view.py` are a hand port of `web/src/session/toolCards.ts`, and
`normalised/tool-cards.json` records what the reference makes of each tool, so a one-sided
change fails rather than drifting.

## Keeping the world fresh

The server holds one `EventLog`. Every change to the world is an event appended to it, applied
through `apply_event`, and sent to every client as a frame. Nothing mutates the world directly.

| Source | How | Interval |
|---|---|---|
| Tasks | Poll the notebook's git HEAD. On change, read every project's tasks and diff | 2 s |
| Worktrees and projects | Re-read `build_list_all_data`, one read in flight at a time | 15 s |
| Agents | Reconcile the host's `list` against the world | 2 s |
| Desk | Read once at start, pruned on every task refresh, joined by every live agent, and written through on change | — |

Blocking reads run on one worker thread. The SQLite index behind the notebook is bound to the
thread that opens it, so a pool of one keeps every read on the same connection.

`diff_kind` turns two readings of one table into upserts and removes. An unchanged entity yields
nothing, so a poll that finds no change is silent.

### Agents

On start the server lists the host's agents and attaches to every one. A new id in a later `list`
is adopted and attached. An id that is gone has exited: the host drops a stopped agent, so
`exited(0)` is the state it left in. A row reporting `exited(N)` that the stream never showed is
applied as-is.

An exited id that comes back live is the same agent again, not a new one: a resume keeps the
agent id. The server clears the exit code, clears the attention item the exit raised, and attaches
a second time. The re-attached backlog re-normalises into the same transcript, which still holds
the turns from before the exit.

Adoption waits for the host's replayed backlog to end, so the next snapshot already holds the
agent's transcript and the wait it is in. A backlog the size of the host's window (200 events)
marks the transcript `truncatedBefore` through a `transcript.truncated` event.

The server keeps the full transcript from the moment it attaches. The host keeps only 200 raw
events, so a server restart loses what came before those.

### Links

| Field | From |
|---|---|
| `worktreeId` | The worktree whose path is the agent's `cwd`. The project follows from it |
| `taskId` | The task whose task session id the agent reports as its session |
| `phase` | The task's phase; `executing` when there is no task |

A launch pins `session_id_for(project, task.id)` on the agent, so the task lookup is exact. An
agent started outside the server links to a task only if it was started with that session id.
Links are re-resolved on every reconciliation, so a task or worktree that arrives after the agent
still finds it.

## Launch

`agent.launch` reuses the model steps `mael task run` takes, from `task_launch.py`: the same
session id, environment, permission mode, branch and prompt, and the same two refusals — a live
session already holds the task, or the worktree's rebase failed. `NotebookTaskSource.launch`
runs them, then hands the host a `start`.

A task that has already run owns its session id, and claiming that id again is refused. So the
launch asks `has_claude_transcript` whether the worktree holds a transcript for it, and sets
`resume` on the `start` when it does. That is the same switch `mael task run` makes for a pane.

A start the host refuses rolls the task back to the status it had. A second launch of a task
still in flight is refused. A launched task also joins the desk, so the node the user just
started is drawn on the canvas.

A newly adopted live agent joins the desk: `task:` when the agent has a task, `agent:` when it
does not. The canvas draws running work whether or not the desk names it, so the entry is not
what makes an agent visible — it is what keeps it visible after the agent stops, until the user
dismisses it. The join runs once, at adoption, so a later poll cannot re-add an entry the user
has dismissed.

An `agent:` entry is never pruned during a run: an agent stays in the world once seen, so the
entry always has an entity to draw. A restart is the exception. The world's agents are rebuilt
from the host, so `_load_desk` drops a stored `agent:` entry naming an agent the host no longer
lists — it would draw nothing, and the user could never dismiss it. That is why the desk loads
after the first agent read.

Not built: the opencode harness, and the cmux placement the CLI does.

## Task ids on the wire

A notebook id such as `2026-06-11.1` is unique inside its project and repeats across projects.
The wire therefore qualifies it: a task's `id` is `<project>/<notebook id>`, built by `task_key`
and split back by `split_task_key`. The bare id travels beside it as `notebookId`, because the
launch and the agent link both need the notebook's own id.

A task's `follows` entries are qualified with the task's own project, since a task only follows
a task beside it in the notebook. Its `parent` is left bare: nothing in the UI resolves a parent,
and a parent is often virtual, naming no real task.

A desk id names what its entry stands for — see `CONTEXT.md`, "Desk". `desk_id_for_task`,
`desk_id_for_agent` and `split_desk_id` build and split one, mirrored in
`web/src/protocol/deskId.ts`. The task half carries the wire id, so two projects may each keep
their own `2026-06-11.1`. A desk written before ids carried a kind held bare task ids;
`JsonDeskStore.load` rewrites those to `task:` ids as it reads them.

## The wire protocol: UI ↔ orchestrator server

JSON text frames on one WebSocket. A message's kind is the key it carries: `seq`, `reply` or
`ready` from the server; `type` or `id` from the client.

### Hello and ready

The client's first message is a hello:

```json
{"type": "hello"}
{"type": "hello", "resumeFrom": 4180}
```

The server answers with replay frames, or one `snapshot` frame, then `ready`:

```json
{"ready": {"seq": 4183}}
```

With `resumeFrom`, the server replays every frame after that seq when its ring still holds them.
Otherwise it sends a snapshot. A message before the hello, or a second hello, gets a refusal with
code `invalid`.

### Frames

Every event travels as a frame:

```json
{"seq": 4181, "ts": "2026-09-03T10:00:00Z", "event": {"type": "upsert", "kind": "task", "entity": {...}}}
```

Every event in the table below is the `event` value of such a frame.

| Event | Carries |
|---|---|
| `snapshot` | `world` and `transcripts`, whole |
| `upsert` | `kind` and the whole `entity` |
| `remove` | `kind` and `id` |
| `transcript.append` | `agentId` and the `item` |
| `transcript.update` | `agentId`, `itemId` and a `patch` |
| `transcript.truncated` | `agentId`: older items were dropped by the host |
| `error` | `message`, and `agentId` when one agent is concerned |

Every client receives every frame. Transcripts are not filtered per connection.

### Commands and replies

A command carries an id the reply echoes:

```json
{"id": "c7", "command": {"type": "agent.approve", "agentId": "1761dcf6", "requestId": "bf4483ca-…"}}
{"reply": {"id": "c7", "ok": true, "result": {}}}
{"reply": {"id": "c7", "ok": false, "error": {"code": "not_waiting", "message": "Agent 1761dcf6 is not waiting"}}}
```

A command's consequences are published as frames before its reply arrives.

| Command | Reaches the host as | Result |
|---|---|---|
| `agent.approve` | `approve` | `{}` |
| `agent.deny` | `deny` with `reason` | `{}` |
| `agent.answer` | `answer` with `answers` | `{}` |
| `agent.say` | `say` with `text` | `{}` |
| `agent.stop` | `stop` | `{}` |
| `agent.launch` | `start`, after the launch steps above | `{"agentId": "…"}` |
| `desk.add` | nothing | `{}` |
| `desk.remove` | nothing | `{}` |
| `task.setStatus` | nothing | `{}` |
| `task.update` | nothing | `{}` |

The desk commands and the task commands never reach the host. Both desk commands carry `id`: a
desk id, not a bare task id, and the desk is the server's own table. A task write goes to the
notebook: `task.setStatus` moves the task through `move_with_actions`, so the status actions fire
as `mael task status` fires them, and `task.update` writes the fields it is given.

Both task commands force a task refresh, as `agent.launch` does, so the upsert reaches the client
before the reply.

`agent.launch` also adds its task to the desk. A second `desk.add` for an entry already on the
desk answers `ok` and publishes nothing. A `desk.remove` for a running agent is accepted, but the
canvas keeps drawing the node until the agent stops.

`document.*`, `comment.*`, `task.create` and `shaping.start` answer `invalid`. `updatedInput` on
`agent.approve` is ignored: the host approves a call with its input as proposed.

The server applies the `control_response` or `user` turn the host wrote through the normaliser,
so the world shows the answer at once. The host echoes its own replies into the stream as well,
so both copies arrive. The duplicate is harmless: the normaliser ignores a response for a request
no longer pending.

A wait can also end with no answer at all — see `CONTEXT.md`, "Stale prompt". A turn's `result`,
a `control_cancel_request` and an agent exit all end the wait, and the normaliser marks the
transcript item stale. The item carries that truth, so no component has to guess whether a prompt
is still live.

### Error codes

The world is validated before the host is asked, with the same rules as the fake backend.

| Code | When |
|---|---|
| `unknown_id` | No agent, task or document has that id, or no desk entry does |
| `agent_exited` | The agent has exited |
| `not_waiting` | The agent has no pending request |
| `stale_request` | The request id is not the pending one |
| `wrong_wait_kind` | An answer to a permission, or an approve of a question |
| `stale_version` | The document version is not current |
| `invalid` | Anything else: an empty reason, a task that is not actionable, an out-of-scope command |

The host's own refusals map to the same codes: "no such agent" to `unknown_id`, "has exited" to
`agent_exited`, "not waiting" to `not_waiting`, "not waiting on a question" to `wrong_wait_kind`,
anything else to `invalid`.

### The snapshot epoch rule

The client drops a frame whose seq is not newer than the last it applied. A snapshot is the
exception: it lands whatever its seq and resets the guard to it. A restarted server counts from 1
again, so without this rule a client that had seen seq 4000 would drop every frame the new server
sent. With it, reconnecting to a restarted server is one snapshot away from working.

## Running it

```bash
mael orchestrator serve                                # ws://127.0.0.1:8765
mael orchestrator serve --port 3072 --socket /tmp/agent.sock
mael env start                                         # in this repo: web and orchestrator together
```

The first command that needs the agent host starts one, as `mael agent` does.

## Open risks

- Blocking work runs on the worker thread. `setup_worktree_for_branch` can take tens of seconds,
  and the launch reply waits for it.
- The host's watcher queue drops the oldest event silently at 1000. Reconciliation catches an
  exit the stream missed, and nothing else.
- Agents started outside the server attach with a 200-event backlog, and link to a task only when
  started with the task's session id.
- A server restart loses every transcript beyond the host's 200 events. Claude's own session
  transcript holds the whole conversation, but nothing reads it back yet.
- The snapshot carries every task in every project, content included. A large notebook makes a
  large snapshot; a client library with a receive limit must raise it.
- `stop` removes the agent from the host. The server marks it `exited(0)` on the ok reply.
- `agent.resume` starts an exited agent again. No UI drives it yet, so a crashed agent is brought
  back with `mael agent resume <id>`.
