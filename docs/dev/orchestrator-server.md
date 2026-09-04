# The orchestrator server

The server builds the world the orchestrator UI shows — tasks, worktrees, agents,
attention — from the task notebook, `list-all` and the agent host, and serves it over HTTP:
resources by REST, change notices on one stream, and one socket per open agent transcript.
`mael orchestrator serve` runs it.

The server owns the business model. The agent host owns the agent processes. The server only
reaches the host through the host's own client protocol, never by importing its internals, so a
host on another machine later is the same protocol over TCP.

## The layers

`src/maelstrom/orchestrator/` follows [architecture-patterns.md](architecture-patterns.md). The
wire types are `TypedDict`s in the wire's own camelCase, so an entity is the dict the socket
carries and nothing maps between a dataclass and the wire.

| File | Layer | Holds |
|---|---|---|
| `protocol.py` | pure | The wire types, `empty_world`, and `apply_event`, the one way the world changes |
| `normalise.py` | pure | The stream-json normaliser: the daemon's raw events to transcript items, agent upserts, documents and attention |
| `validate.py` | pure | Command validation: the rules the server applies before it asks the host |
| `world.py` | pure | `WorldState`: the tables, and `apply` as their only writer |
| `world_build.py` | pure | Entity builders from a task, a `list-all` row and an agent row; `link_agent`; `diff_kind`; `task_key` |
| `desk.py` | pure | The desk table: `add`, `remove`, `prune`, each returning a new table, and the desk id helpers |
| `notices.py` | pure | `notices_for`: which change notices a batch of events amounts to |
| `transcript_log.py` | pure | `TranscriptLog`: one agent's items, its seq, and the ring of frames a resume replays |
| `hubs.py` | adapter | `NoticeHub`: change notices to every open notice stream, coalesced per subscriber. `TranscriptHub`: transcript frames to every socket open on an agent, bounded per socket |
| `sources.py` | storage | `TaskSource` and `WorktreeSource`, over the notebook and `list_all.build_list_all_data` |
| `daemon_bridge.py` | storage | `AsyncDaemonClient`: the socket client for the agent host, and a scripted fake |
| `../desk_store.py` | storage | `DeskStore`: the desk as one JSON file at `~/.maelstrom/desk.json`, or in memory |
| `server.py` | service | `Orchestrator`: the world, the pollers, one watch per agent, the transcript logs, the commands, and the hubs it tells |
| `routes.py` | adapter | `build_app`: the aiohttp app that puts an `Orchestrator` on the network — every route, the error mapping — and `serving` / `serve_app` to run it |
| `../orchestrator_cli.py` | CLI | `mael orchestrator serve` |

`task_launch.py` at the top level holds the launch plan and its two guards, shared with
`mael task run`. `list_all.py` holds the rows both `mael list-all` and the server read.

## The normaliser and its goldens

The Python normaliser is the one the wire carries. `tests/test_orchestrator_normalise.py`
replays every recorded daemon stream under `tests/fixtures/agent_events/` into one seed agent
and holds the result to a golden under `normalised/`. That test owns the goldens:
`UPDATE_GOLDEN=1 uv run pytest tests/test_orchestrator_normalise.py` re-records them, so a
normaliser change is a deliberate re-record and never a silent drift.

The tool cards run the other way. `classify_tool_call` and `tool_call_title` in `agent_view.py`
are a hand port of `web/src/session/toolCards.ts`, which renders in the browser and stays the
reference. `tests/fixtures/agent_events/tool-cards.json` records what it makes of each tool;
`UPDATE_GOLDEN=1 pnpm test` in `web/` re-records it, and the Python test replays it.

## Keeping the world fresh

The server holds one `WorldState`. Every change to the world is an event applied through
`apply_event`, then turned into the change notices and transcript frames the clients hear.
Nothing mutates the world directly. The apply, the notices and the frames are one synchronous
step on the loop, so no client sees a half-applied batch or misses a frame between a socket's
snapshot and its first live one.

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
a second time. The re-attached backlog is relayed with the ids it already had, so a client that
holds those items applies nothing new.

Every attach opens with the host's `mael_agent_detail` frame, which says what the agent is
waiting on. A wait it reports that the world does not already hold is raised here, so a wait that
opened before this server attached is still answerable.

Adoption waits for the host's replayed backlog to end. A backlog the size of the host's window
(200 events) publishes a `transcript.truncated` event.

The server normalises the host's stream into transcript events and keeps one `TranscriptLog`
per agent: the items as they stand, a seq per frame, and a ring of the last 2000 frames. The
log outlives the agent's exit, because a resume keeps the agent id. "Transcript streams" below
says how a client reads it.

### Links

| Field | From |
|---|---|
| `worktreeId` | The worktree whose path is the agent's `cwd`. The project follows from it |
| `taskId` | The task whose task session id the agent reports as its session |

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

## Reading the world

The world is served over REST, from memory, once the first source reads have finished. Every
route is under `/api` and answers JSON. A task id is two path segments, because the wire id is
`<project>/<notebookId>`.

| Route | Returns |
|---|---|
| `GET /api/projects` | `{projects: [Project]}` |
| `GET /api/worktrees` | `{worktrees: [Worktree]}` |
| `GET /api/tasks` | `{tasks: [TaskRow], version}`. A row is a task without `content` and `log`. The `ETag` changes with every task change; `If-None-Match` answers 304. Compressed |
| `GET /api/tasks/{project}/{id}` | The whole `Task`, prose included |
| `GET /api/agents` | `{agents: [Agent]}` |
| `GET /api/agents/{id}` | The `Agent`, plus `pendingRequest`: the question, permission request or plan review item it waits on, or null. A decision renders from this alone |
| `GET /api/attention?open=1` | `{attention: [Attention]}`; `open` keeps only items not yet cleared |
| `GET /api/documents` | `{documents: [Document]}` without `markdown` |
| `GET /api/documents/{id}` | The `Document`, `markdown` included |
| `GET /api/desk` | `{desk: [DeskEntry]}` |

The task list ships every task as a slim row and the client filters. The list already filters in
memory, and a server-side filter would fragment the client's cache.

Every error is `{"error": {"code", "message"}}`. The codes are the command codes below plus
`not_implemented`, and each has one status: `unknown_id` 404, `invalid` 400, the five conflict
codes 409, `not_implemented` 501. A route that does not exist is 404 `unknown_id`; a body that
is not JSON is 400 `invalid`. The document comment and review routes, task creation and shaping
answer 501: the UI keeps its controls, and the button shows the refusal.

## Change notices

`GET /api/events` is a `text/event-stream`. It opens with a `reset`, then sends one `change` per
kind that changed, and a `: ping` comment every 15 s:

```
event: reset
data: {"epoch": "5f1c2a9e"}

event: change
data: {"kind": "task", "ids": ["northwind/NORT-7"]}
```

The kinds are `project`, `worktree`, `task`, `agent`, `attention`, `document` and `desk`. A
notice names what changed and nothing else: no entity travels on it. A remove and an upsert both
put the id in `ids`, and the client refetches and finds the entity present or gone. Transcript
events raise no notice; they have their own stream.

Notices coalesce for 50 ms per subscriber, so one poll that changes ten tasks is one `change`
with ten ids. Each subscriber holds a pending set per kind, bounded by the number of entities,
never a queue: a slow reader cannot fall behind and be dropped.

There is no `id:` field and no `Last-Event-ID`. REST is the source of truth, so the answer to
"you may have missed notices" is the one `reset` a fresh connection gets. `epoch` is minted at
server start, so a client can tell a restart from a reconnect.

## Transcript streams

A transcript never travels with the world. `GET /api/agents/{id}/transcript` answers
`{agentId, items, truncatedBefore, seq}`, and `GET /api/agents/{id}/stream?from=<seq>` is a
WebSocket on the same log: an opening frame, then one frame per event.

```json
{"type": "transcript.snapshot", "seq": 4183, "items": [...], "truncatedBefore": false}
{"type": "transcript.replay",   "seq": 4183, "frames": [{"seq": 4181, "event": {...}}]}
{"seq": 4184, "event": {"type": "transcript.append", "agentId": "…", "item": {...}}}
{"seq": 4185, "event": {"type": "transcript.update", "agentId": "…", "itemId": "…", "patch": {...}}}
{"seq": 4186, "event": {"type": "transcript.truncated", "agentId": "…"}}
```

The cursor is the agent's transcript seq, not an item index, because `transcript.update` patches
an earlier item. With `from` inside the ring the opening frame is a `replay` of what was missed;
without `from`, or with one too old, it is a `snapshot`. The subscribe and the snapshot are one
synchronous step on the loop, so no frame lands between them.

Agent state, attention and documents never travel on this socket: they change through an
upsert, a notice, and a GET. The socket stays open across an exit and a revive. An unknown agent
closes it `4404`. A reader that falls 500 frames behind is closed `4409 lagging` and comes back
with `from`; nothing is lost, because the ring holds what it missed. The socket pings every 20 s.

## Commands

A command is one POST, PATCH or DELETE under `/api`. Each route builds the command dict the
world socket carried and runs it through `handle_command`, so `validate_command` and the
host-refusal mapping apply unchanged. The reply is the command's result as JSON, or the error
shape above at the code's status. A body that is not JSON, and a field the validator did not
check being missing, both answer 400 `invalid`.

| Route | Body | Command | Returns |
|---|---|---|---|
| `POST /api/agents/{id}/approve` | `{requestId}` | `agent.approve` | `{}` |
| `POST /api/agents/{id}/deny` | `{requestId, reason}` | `agent.deny` | `{}` |
| `POST /api/agents/{id}/answer` | `{requestId, answers}` | `agent.answer` | `{}` |
| `POST /api/agents/{id}/say` | `{text}` | `agent.say` | `{}` |
| `POST /api/agents/{id}/stop` | | `agent.stop` | `{}` |
| `POST /api/agents/{id}/resume` | `{text?}` | `agent.resume` | `{}` |
| `POST /api/tasks/{project}/{id}/launch` | `{model?}` | `agent.launch` | `{agentId}` |
| `POST /api/tasks/{project}/{id}/status` | `{status}` | `task.setStatus` | `{}` |
| `PATCH /api/tasks/{project}/{id}` | the fields to write | `task.update` | `{}` |
| `POST /api/desk` | `{id}`, a desk id | `desk.add` | `{}` |
| `DELETE /api/desk/{deskId}` | the desk id, URL-encoded | `desk.remove` | `{}` |

A command that changes the world answers after the change is in it, so a GET right after the
reply is current, and the notice that follows is one more refetch. A refused command changes
nothing and raises no notice; `agent.launch` is the exception, moving its task in-progress before
it asks the host and rolling that back on a refusal. The launch reply waits for the host's start,
and the client gives that one call a longer timeout.

The desk commands and the task commands never reach the host. Both desk commands carry a desk
id, not a bare task id, and the desk is the server's own table. A task write goes to the
notebook: a status change moves the task through `move_with_actions`, so the status actions fire
as `mael task status` fires them, and a patch writes the fields it is given. Both force a task
refresh, as a launch does, so the change is in the world before the reply.

A launch also adds its task to the desk. A second `POST /api/desk` for an entry already on the
desk answers `{}` and raises no notice. A `DELETE` for a running agent's entry is accepted, but
the canvas keeps drawing the node until the agent stops.

### The host owns the control plane

The four commands that write to the child — `agent.approve`, `agent.deny`, `agent.answer` and
`agent.say` — are pure relays. The server validates, asks the host, and returns. It builds no
reply of its own.

This works because the host records the `control_response` it writes onto the child's event
stream, so the wait resolves when that event arrives on the attach stream, like any other. A
`say` is not recorded: the child replays a user turn itself.


Two things follow. An answer made anywhere reaches the UI, `mael agent approve` included. And the
server holds no opinion about how a wait is answered, so the reply shapes live in one place, the
daemon.

A wait can also end with no answer at all — see `CONTEXT.md`, "Stale prompt". A turn's `result`,
a `control_cancel_request` and an agent exit all end the wait, and the normaliser marks the
transcript item stale. A stale plan review also takes its plan document to the `stale` status,
because the document's review bar reads the document and not the item. Both carry that truth, so
no component has to guess whether a prompt is still live.

### Error codes

The world is validated before the host is asked.

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


## Running it

```bash
mael orchestrator serve                                # http://127.0.0.1:8765
mael orchestrator serve --port 3072 --socket /tmp/agent.sock
mael env start                                         # in this repo: web and orchestrator together
```

The server is one aiohttp app, built by `routes.build_app`. It binds the port first, so a port
in use fails at once, then reads every source once, then serves.

The first command that needs the agent host starts one, as `mael agent` does.

## Open risks

- Blocking work runs on the worker thread. `setup_worktree_for_branch` can take tens of seconds,
  and the launch reply waits for it.
- The host's watcher queue drops the oldest event silently at 1000. Reconciliation catches an
  exit the stream missed, and nothing else.
- Agents started outside the server attach with a 200-event backlog, and link to a task only when
  started with the task's session id.
- A client that connects after the server started gets the transcript the server has built since
  it attached, not the agent's whole history. Reading Claude's own session transcript back through
  the normaliser would fix that, and is not built.
- A resume replays the host's window into a transcript that already holds it, so a revived agent
  shows its last turns twice until the attach stream carries a cursor.
- `stop` removes the agent from the host. The server marks it `exited(0)` on the ok reply.
- `agent.resume` starts an exited agent again. No UI drives it yet, so a crashed agent is brought
  back with `mael agent resume <id>`.
