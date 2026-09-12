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
| `linear_source.py` | storage | `cycle_issues` and `plan_fields`: the server's one door onto Linear |
| `daemon_bridge.py` | storage | `AsyncDaemonClient`: the agent-host protocol, its reply mapping, and a scripted fake. The socket client itself is `agent_transport.SocketAsyncDaemonClient` |
| `../desk_store.py` | storage | `DeskStore`: the desk, as a canonical table in the state database, as one JSON file, or in memory. The server runs the first; see [data-architecture.md](data-architecture.md) |
| `server.py` | service | `Orchestrator`: the world, the pollers, one watch per agent, the transcript logs, the commands, and the hubs it tells |
| `routes.py` | adapter | `build_app`: the aiohttp app that puts an `Orchestrator` on the network — every route, the error mapping — and `serving` / `serve_app` to run it |
| `../orchestrator_cli.py` | CLI | `mael orchestrator serve`, and the logging the server runs under |

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

## A loaded skill

Loading a skill injects the whole skill file as a user turn, so it becomes a `skill` item and
not a message: the web UI folds it behind the skill's name, and the TUI prints that name alone.
The turn opens with `Base directory for this skill:`, and that line is the only mark the stream
carries — the transcript file marks such a turn `isMeta`, but the daemon stream does not. The
skill's name is the last part of the path on that line. A harness that reworded that line would
silently return the body to the transcript as an ordinary message.

## A shell command

A `!` line in the composer runs a shell command on the agent host, and the host injects the
command and its output as two user turns. The normaliser folds that pair into one `shell` item,
which the web UI draws with the same card a `Bash` tool call gets. Folding here is the same
mechanism as a loaded skill: the tags on the turn are the only mark the stream carries.

The item appends on the input turn and updates on the output turn, the way a `tool_call` and its
`tool_result` already work. An output turn whose input turn the ring dropped still renders, so a
gap never swallows output.

A shell command does not move the agent to `processing`: the two turns carry no request. An
assistant event that follows moves the state on its own. See
[agent-daemon.md](agent-daemon.md#running-a-shell-command) for the wire format.

## A tagged document

An agent puts a document in front of the user by writing a marker in the text of an ordinary
assistant message. The normaliser reads that marker and mints a document, exactly as it mints a
plan document from `ExitPlanMode`. **The agent host does not change**: it relays assistant
messages untouched already, so it carries no document payload and learns nothing new.

Two forms, both read by `document_tags.read_tags`:

```
<doc-content kind="other" title="Changelog draft">
## 1.4.0
- the markdown body, inline
</doc-content>

<doc-file kind="tasks" filename=".drafts/iter1.md, .drafts/tail.md" title="Iteration 1">
```

`<doc-content>` carries the body inline, so the server reads no file and the form works for a
document that is not a file at all. `<doc-file>` names files the server reads, comma-separated.
Several names make **one** document holding the set, bodies headed by filename, and the order is
kept: a task set is one chain, and approving it promotes in that order. `kind` is one of `plan`,
`tasks`, `pr`, `review` and `other`; an unrecognised kind reads as `other`, so a typo shows a
document rather than dropping it. `title` defaults to the first filename, then to the kind. One
message may carry several tags, and each yields one document.

The tag names are **frontend-agnostic**: another frontend may render these its own way, so
nothing in a tag name is maelstrom's. Only a `kind` value may be.

Every tag is cut out of the message the transcript shows. The user reads a document in its own
tab, so raw tag syntax on the transcript would only be noise.

A `<doc-file>` resolves against the agent's own `cwd` — the worktree the agent row already
carries — **and nothing outside it**. `document_tags.stays_within` refuses a path that escapes,
before anything is read, so a tag can never show a file elsewhere on the machine. A file that
cannot be read yields a document whose body says so, rather than no document at all: silence
would leave the agent believing it showed something.

Reading that file is the normaliser's one piece of I/O, and it is injected as `read_file`, so a
golden does not depend on a directory this machine has.

A `tasks` document is rendered rather than shown raw, by `normalise._as_plan`: each draft becomes
its title, its recipe, and its plan. Every other kind is shown as written. Only a task file has a
recipe to read off, and a changelog opening with a horizontal rule would lose its newest entry to
a blind frontmatter strip.

A tagged document opens at `draft`, not `awaiting-review`: a plan review is `awaiting-review`
because a real wait blocks behind it, and a tag blocks nothing. A changelog the user was asked to
read must not present as a decision. `review="true"` is how an agent asks for a verdict, and only
that raises an attention item, of kind `document_review`.

A document minted again by the same agent with the same `kind` and `title`, while the previous
one is `changes-requested`, becomes the **next version of the same document**, so its comments
stay attached. That is the plan document's rule, shared as `_Emitter.previous_version`. Anything
else starts at version 1.

A document is **not persisted**. It lives in the world and dies with a server restart, exactly as
a plan document does. A document store is out of scope.

### Approving a task set

A draft's inertness is the approval gate — see `CONTEXT.md`, "Draft". A cmux session gates on the
user saying yes in the chat. The orchestrator UI has no chat, so the document's Approve button is
what runs the promote; otherwise approval would be advice the agent may ignore rather than a gate.

So approving a `tasks` document whose `source` is `draft_files` promotes every path it names, in
order, and every other kind stays the verdict alone. A `source` names registry ids, so the paths
come back from the registry that validated them when the tag was read — see "The file registry".
The first task follows the end of its parent's child-chain — `--follow-end '*'`, as the skill
wires it by hand — and each later one follows the one before, so the chain lands as the document
listed it. The reply carries the created ids,
because an approve that reports nothing reads as an approve that did nothing.

The head is **not** launched: the task list and node cards already offer Launch. The agent is told
what was created, so a session that planned the chain does not promote it a second time.

`NotebookTaskSource.promote` is the storage-layer step, over `task.promote_draft` — the same
function `mael task promote` calls, so the CLI stays canonical and this is a second surface onto
it, not a reimplementation. Three things make the failure path safe:

- **One transaction.** Several drafts are several notebook writes, and a failure part-way would
  leave some tasks created and some not. `store.transaction` gives the set a true rollback, so an
  invalid draft leaves the notebook untouched and the document still `awaiting-review`. The
  refusal names the file, since the user is looking at the document and needs to know which one
  to fix.
- **No index.** The task index is a cache outside that transaction, so a row written during a
  rolled-back promote would outlive the rollback and leave a task that exists only in the cache.
  Promote passes `index=None`, and `_stamped` skips its restamp when the block raises, so reads
  scan the store until the next complete build.
- **Deferred deletion.** `promote_draft(consume=False)` leaves each file, and the set is deleted
  only once the transaction commits. Git can roll the notebook back but not a file beside it, so
  deleting as it went would leave the user a half-deleted plan.

The task refresh is forced afterwards, as every other notebook write forces it: the poll is 2 s
away, and the user who approved would otherwise see nothing until it came round.

## A shown image

An agent shows the user a picture with a third tag, which mints no document:

```
<image src="docs/shot.png" alt="The failing dialog">
```

The tag becomes a markdown image ref **in place**, so the picture sits where the agent wrote it. A
document tag is cut out because the user reads a document in its own tab. An image is read in the
flow of the message, so cutting it out would lose the point of it. `attachments.markdown_ref` mints
the ref, so an image an agent showed and one the user pasted cannot drift apart.

An image is not a document. A document is versioned, reviewed and commented on; a picture is none
of those. More to the point, a document's body **is** world data: `Document.markdown` holds the
whole file, and the world goes to every connected client and stays in memory for the session. A
screenshot cannot go there — see the base64 comment in `normalise.py`. So an image's bytes stay on
disk and only a pointer goes in the world.

### The file registry

The pointer is an **id**, never a path.

One rule covers every file an agent names, whichever tag named it: the path is validated once, and
the registry is the only way that file's bytes reach the client. `<doc-file>` registers its file
too. One place answers "may this file be shown", so a later fix to that answer cannot reach one tag
and miss the other.

A URL that carried a path would be a capability to read any file, and its safety would rest on
re-deriving the guard correctly on every fetch — for every `..`, symlink and absolute path, now and
later. An id inverts that. A file nobody registered is unreachable because it is **absent**, not
because a check caught it on the way out.

`FileRegistry.register` validates with `document_tags.stays_within` and requires the file to be
there, so an escaping path, an unwalkable one and a typo all yield no id. The message then says so
in place of the picture.

An id is the item id and the filename — `ag1-3-shot.png` — so a URL in a log says which file it
was. `resolve` is a dict lookup and **never a path join**. That is the property the design protects.

The registry is keyed on the **resolved path**, so registering one file twice returns the id it
already has. A replayed transcript would otherwise add an entry per replay, and the same picture
would change id under a re-attach.

Ids are guessable on purpose. An id is a counter and a name, not a secret. A guessed id names a file
some agent already chose to show, and no id can name a file nobody registered. Do not make ids
random and then rely on them being unguessable.

The registry is not persisted. It dies with a server restart, exactly as a document does, and a
re-normalised transcript registers its files again. An image URL from before a restart is a 404.

| Route | Serves |
| --- | --- |
| `GET /api/files/{id}` | The bytes of one registered file, or `unknown_id` |

The lookup is the whole authorisation step: the handler parses no path and joins no string. A file
since deleted reads as `unknown_id` too — the id was real, the bytes are not.

## Keeping the world fresh

The server holds one `WorldState`. Every change to the world is an event applied through
`apply_event`, then turned into the change notices and transcript frames the clients hear.
Nothing mutates the world directly. The apply, the notices and the frames are one synchronous
step on the loop, so no client sees a half-applied batch or misses a frame between a socket's
snapshot and its first live one.

| Source | How | Interval |
|---|---|---|
| Tasks | Poll the notebook's git HEAD. On change, read every project's tasks and diff | 2 s |
| Worktrees and projects | Re-read `build_list_all_data`, one read in flight at a time, and only while a client is watching | 60 s |
| Agents | Reconcile the host's `list` against the world | 2 s |
| Desk | Read once at start, pruned on every task refresh, joined by every live agent, and written through on change | — |
| Host | One entity, `agent-host`, saying whether the agent host answers. Set by every agent poll; published only when it changes | with the agent poll |

Blocking reads run on one worker thread. The SQLite index behind the notebook is bound to the
thread that opens it, so a pool of one keeps every read on the same connection.

`diff_kind` turns two readings of one table into upserts and removes. An unchanged entity yields
nothing, so a poll that finds no change is silent.

### What the worktree poll costs

The worktree read asks GitHub for the pull request on each branch. GitHub charges GraphQL by the
number of nodes a query asks for, not by the number of calls, and the budget is 5000 points an
hour. The query asks for 20 pull requests per branch, so a wide project costs many times a narrow
one. Four rules keep that cost inside the budget, one spends a read where it is worth it, and a
last one reads the PR state the query cannot.

**The poll asks only about the branches on the desk.** `desk.active_branches` joins each desk
entry to its branch: a `task:` entry through the notebook, an `agent:` entry through its
worktree. `worktreeId` carries that join, and the agent poll relinks it; between a worktree
appearing and that relink the id is empty, so the agent's `cwd` answers instead.

A `task:` entry contributes two names: the branch the notebook records, and the branch its agent
is really on. A task that recorded none is given a generated name, so the notebook's answer is a
guess, while the node draws the worktree its agent runs in. Ask about the guess alone and a row
draws a branch nobody looked up, so its pull request never appears. The agent is reached through
`taskId`, because the world keys agents by their own id.

A branch off the desk is *not asked about*, which is not the same as *having no pull request*.
`ListAllWorktreeSource` keeps the last pull request it saw, by project and then by branch, and
answers from it. Read the two the same way and a live pull request would vanish from its row the
moment the poll stopped asking about its branch. The key holds the project because branch names
are not unique across projects — every project's `_main` sits on `main`.

**The poll idles while nobody watches.** The UI never polls; it is told what changed. A read with
no client subscribed therefore buys nothing. A client that subscribes triggers the read at once,
so the first paint is current rather than up to a minute old.

Only a browser counts as a watcher. The web client dials `GET /api/events` directly rather than
through the dev server's `/api` proxy, which would otherwise hold a stream open with no page —
see [orchestrator-ui.md](orchestrator-ui.md).

**An arrival inside one interval of the last arrival read is served what that read found.** Every
subscriber that arrives to an empty hub is a first subscriber, so a stream that drops and returns
would read on each return: a second poll running at the reconnect rate rather than at
`WORKTREE_POLL_SECS`. Only a read an arrival triggered counts, so the first client still reads at
once — `start` reads before it serves anyone, and the poll reads for whoever is already watching.
The floor therefore bounds the cost of a flapping stream at one read per client turnover, not at
one per interval.

**An event reads straight away.** `POST /api/worktrees/refresh` is how a command says the world
moved: `mael gh create-pr` posts to it, because the pull request it opened is in no world until
something looks it up and the next poll is up to `WORKTREE_POLL_SECS` away. `refresh_now` skips
the freshness floor above — that floor guards against a flapping stream, and an event is not a
reconnect — but still honours the stand-off below, because a spent budget is spent whoever asks.

The read is scheduled, not awaited, so the route answers at once. It shells out per worktree
across every project and takes seconds; the caller is a command holding a terminal open, and the
UI hears the result on its own notice stream. A read already scheduled absorbs a second event
rather than replacing it, which would leave a task nothing can cancel at stop.

A read already *in flight* is waited out rather than skipped. That read chose its branches, and
may have asked GitHub, before the event happened, so returning there would drop the news — in
exactly the case the event exists for, a poll tick landing while `create-pr` finishes.

**A refused read stands off for 10 minutes.** GitHub reports a spent budget with HTTP 200 and an
error in the body, so `parse_open_prs` reads the payload and raises `RateLimited`. Other read
failures fall back to one lookup per branch; a rate limit must not, because that turns one
refused call per project into one call per worktree and keeps the budget spent.

`build_list_all_data` builds every row before it reports the refusal, so a spent budget costs the
pull request column rather than the read. `ListAllWorktreeSource` sets `rate_limited`, and the
server holds one stand-off deadline that both the poll and an arriving client check — the web
client retries a dropped notice stream every 30 seconds, and without the shared deadline each
retry would ask GitHub again. The cooldown is fixed rather than read from the reset header: `gh`
does not pass that header back, and an exhausted hourly window has been seen reporting a reset 84
seconds away.

**A refused check rollup falls back to Actions.** `statusCheckRollup` needs the `checks=read`
token permission, which GitHub no longer offers in the fine-grained PAT UI, so some repositories
refuse it and answer `null` — the same answer a repository with no CI gives. `rollup_refused`
tells the two apart from the per-field errors beside the data.

Where the rollup was refused, `get_open_prs` spends one REST call on
`GET /actions/runs?event=pull_request`, which the grantable `Actions` permission covers.
`parse_run_states` reduces the runs to one state per head commit, and `_pr_state` matches on the
pull request's own head: a run against an earlier push is no answer, or a replaced commit would
draw a green tick. One page per repository covers every branch — 100 runs reached 30 branches over
nine days on one repository here, and 50 over six days on another — and REST is metered apart from
the GraphQL budget. A repository that grants the rollup never pays for this.

A run that reached no verdict answers for nothing. `cancelled`, `skipped`, `neutral`,
`action_required` and `stale` are skipped rather than ranked, so a sibling run that did reach one
still answers and a commit whose every run ended that way stays absent. Calling those green would
put a tick on a commit nothing finished checking — the false green the head-commit match exists to
prevent, by another door. Cancelling is the common one: a push superseding the last, or a
concurrency group, and 39 of 100 runs on one repository here ended that way.

The page is also a window in time, not a set of branches. So four cases read `checks-unreadable`:
a commit whose build has fallen off the end of the page, a commit nothing has run yet, a commit
whose runs all reached no verdict, and a repository that grants neither permission. All four are
the same honest answer — nothing looked these checks up — and `mael doctor` names the last, which
is the one a user can fix.

### Agents

On start the server lists the host's agents and attaches to every one. A new id in a later `list`
is adopted and attached. An id that is gone has exited: the host drops a stopped agent, so
`exited(0)` is the state it left in. A row reporting `exited(N)` that the stream never showed is
applied as-is.

One agent is followed once. A launch adopts the agent it started, and the poll adopts every
row the host lists, so both reach one new agent when a poll lands in the gap the launch leaves
between starting the agent and adopting it. A second watch replays the same backlog into the
same transcript under fresh ids, so every item draws twice. An attach returns early for an
agent a watch already holds. A revive drops its watch first, so it still re-attaches.

The host being away is not an exit. A `list` the host does not answer changes no agent: the
second consecutive failed poll upserts the `agent-host` entity as unreachable, with when, and the
first successful poll after upserts it reachable again. One failed poll raises nothing, because a
daemon restart costs exactly one dropped connection. So a restart shows in the UI as a banner for
a few seconds and then the same agent ids, revived, with every client's cursors intact — never as
a canvas full of exits. `GET /api/host` serves the entity; a `host` change notice names it.

**The server never starts the daemon.** It polls, and reports what it finds. A server that
started one served its own worktree's code to every session on the machine: it noticed the
everyday daemon was gone before anything else did, and `mael agent daemon serve` under `uv run`
resolved to that worktree's `.venv`. The banner is the whole response now, and
`mael self-env start` is what brings the daemon back.

Each agent row carries the child's `pid` while it is alive, so a client can name the process an
agent is and `mael agent daemon list` can be read against the canvas.

An exited id that comes back live is the same agent again, not a new one: a resume keeps the
agent id. The server clears the exit code, clears the attention item the exit raised, and attaches
a second time. The re-attached backlog is relayed with the ids it already had, so a client that
holds those items applies nothing new.

What an agent waits on is not stored. Both the host and the server derive it by running the same
function over the same events: a `control_request` opens the wait, and a `control_response`, a
`control_cancel_request` or a `result` ends it. The host reads those events with no stream in
between, so the host is never behind the server. Where the host disagrees with the world, the
server takes the host's answer.

Every attach opens with the host's `mael_agent_detail` frame. The frame always carries
`request_id`, empty when the agent waits on nothing, so the frame reports the whole of what the
host derived, not only the waits it can raise. The server reads it that way:

| The frame says | The world holds | The server does |
|---|---|---|
| a request | no wait | raises the wait, so a request older than the replay window is still answerable |
| a request | the same request | nothing — the backlog replayed it, and a second item would duplicate the decision |
| no request | a wait | ends the wait, and marks its transcript item stale |

Adoption waits for the host's replayed backlog to end. The server keeps a cursor per agent — the
`mael_seq` of the last event it read, and the `epoch` the host's backlog marker named — and it
outlives the watch. A re-attach after a dropped stream sends both, so the host replays only what
the server missed and no item lands twice. A revive forgets the cursor: the resumed agent is a
new life, and the host would not honour it anyway.

A `mael_truncated` marker before any item publishes `transcript.truncated`: the transcript's
start is not the agent's. Mid-stream the marker appends a `gap` item saying how many events are
gone.

Every reconciliation of a live agent compares the wait the world holds against the host's row.
A row still reporting `awaiting-` holds its wait, so only a row that has moved on ends one. A
wait the row no longer shows is over, and the server ends it as the child ends one it withdraws.
The check needs no marker, because it never asks how the events went missing. A gap ate them,
the cursor is past them, or a daemon restart took them. The row settles it either way.

The server normalises the host's stream into transcript events and keeps one `TranscriptLog`
per agent: the items as they stand, a seq per frame, and a ring of the last 2000 frames. The
log outlives the agent's exit, because a resume keeps the agent id. "Transcript streams" below
says how a client reads it.

A subagent is an agent of its own in the world. The host lists one under a dotted id with
`parent` naming the agent it runs inside and `description` naming what it was asked to do; a
top-level agent carries `parent: ""`. The row carries the parent's session and cwd, so the links
resolve to the parent's task and worktree. A subagent joins no desk entry, and the canvas draws
none: the parent's session tab is the way to it.

A subagent is not attached at adoption. `ensure_attached` attaches it when a transcript route is
read, and waits for the backlog, so the snapshot holds it. When the last transcript socket on a
subagent closes, the watch is dropped 5 seconds later. The cursor outlives the watch, so the next
attach asks the host for what was missed, and what the host's ring dropped meanwhile lands as a
`gap` item, as for a parent. A subagent that exits raises no attention: the parent gets the
notification and reports it. A subagent whose row comes back live is revived without an attach.

On a subagent's stream the normaliser changes nothing about the agent but `lastMessage` and
`lastMessageAt`, ignores a `control_request`, and writes no document. On a parent's stream it
drops any event carrying a `parent_tool_use_id`, so a host that had not split the streams would
still yield a parent-only transcript.

Every item carries a `ts`: when its source event happened, taken from the `mael_ts` the daemon
stamped. A conversation turn therefore keeps Claude's own time, and a `system` or `result` item
keeps the daemon's, because those frames carry no clock of their own. This is what lets a
reattach replay an hour of backlog without every item reading as "just now". An item with no
source event — a gap, an exit, the detail frame — is stamped with the server's clock, because it
really is happening then.

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

## Creating work

Four commands write new work.

- **`task.infer`** names a task from its prose, through `infer_task_names` in `branch_name.py`.
  It shells out to `claude -p` and falls back to a deterministic name. The call blocks for up to
  40 seconds — two 20-second attempts — so it runs on the executor and the UI shows a wait.
  `mode` comes from the inferred command through `task.mode_for_command`. Inference writes
  nothing.
- **`task.create`** writes the task the user edited. It is a separate call so the UI can show the
  inferred fields first. An explicit branch makes `model.create` skip generation, so no second
  `claude -p` call runs behind the user's edit. With `launch` set it runs the launch path
  unchanged, as `mael task add --run` does. A launch that fails leaves the task written and on
  the desk, so the refusal names it — without that the client cannot tell the case from "nothing
  was written", and a retry writes the task twice.
- **`linear.plan`** plans a Linear issue: the equivalent of `mael linear plan`.
  `linear_source.plan_fields` fetches the issue and returns the task fields the CLI would write,
  through `integrations.linear.build_plan_task` — the CLI command calls the same function, so the
  two cannot drift. The branch comes from `task.infer` over the same brief, not from
  `build_plan_task`'s own generation, so the server spends one `claude -p` call instead of two.
  The fields then go through `task.create`, which files the task and launches it. `parent` and
  `post_action` bind the task to its issue and are not in `validate.EDITABLE`, so they reach the
  notebook through `create`'s `extra` argument — the server's own to set, never a client's.
- **`agent.start`** starts an agent tied to no task. `TaskSource.worktree_for` opens the branch's
  worktree through the same collaborator a launch uses, so a branch with no worktree gets one
  provisioned. The `start` payload carries no `session` and no `env`: the host mints its own
  session id, and nothing exports `MAEL_TASK_*`. Those two absences are the whole definition of
  a free agent.

Not built: the opencode harness, and the cmux placement the CLI does.

## Closing a worktree

`worktree.close` runs the whole close `mael close` runs, from `close_worktree_fully` in
`worktree_close.py`:

- stop the environment;
- stop the daemon agents;
- stop the live sessions;
- rescue new `.env` vars back to the parent;
- close the worktree;
- close the cmux workspace.

`worktree.py` does the git half — sync, verify, detach, free the ports. The sequence sits above
both adapters, as `task_launch.py` does for a launch, because `env.py` already imports
`worktree.py`.

The server never forces. A worktree with unmerged commits or a dirty tree is refused, and the
refusal carries the model's own message, so the UI reads what the command would have printed.
`--force` writes a `wip: uncommitted changes` commit and a reopen task, which stays with the
CLI. `_main` is refused by `validate.py`, before any git call runs.

The close blocks for tens of seconds, so it runs on the executor as a launch does. The refresh
runs whichever way the close ends: one that fails partway has still stopped agents and freed
ports.

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
`JsonDeskStore.load` rewrites those to `task:` ids as it reads them, and the one-time import
into the state database goes through it so the fix still applies.

## Reading the world

The world is served over REST, from memory, once the first source reads have finished. Every
route is under `/api` and answers JSON. A task id is two path segments, because the wire id is
`<project>/<notebookId>`.

| Route | Returns |
|---|---|
| `GET /api/projects` | `{projects: [Project]}` |
| `GET /api/linear/issues?project=` | `{issues: [{id, title, status}]}` — the project's current Linear cycle. Refused unless the project sets `linear.team_id` |
| `GET /api/worktrees` | `{worktrees: [Worktree]}` |
| `GET /api/tasks` | `{tasks: [TaskRow], version}`. A row is a task without `content` and `log`. The `ETag` changes with every task change; `If-None-Match` answers 304. Compressed |
| `GET /api/tasks/{project}/{id}` | The whole `Task`, prose included |
| `GET /api/agents` | `{agents: [Agent]}` |
| `GET /api/agents/{id}` | The `Agent`, plus `pendingRequests`: the question, permission request and plan review items it waits on, oldest first, empty when it waits on none. A decision renders from this alone |
| `GET /api/attention?open=1` | `{attention: [Attention]}`; `open` keeps only items not yet cleared |
| `GET /api/documents` | `{documents: [Document]}` without `markdown` |
| `GET /api/documents/{id}` | The `Document`, `markdown` included |
| `GET /api/desk` | `{desk: [DeskEntry]}` |
| `GET /api/host` | `{host: Host \| null}`: whether the agent host answers, since when, and on which socket. `null` until the first agent poll has settled |

The task list ships every task as a slim row and the client filters. The list already filters in
memory, and a server-side filter would fragment the client's cache.

Every error is `{"error": {"code", "message"}}`. The codes are the command codes below plus
`not_implemented`, and each has one status: `unknown_id` 404, `invalid` 400, the five conflict
codes 409, `not_implemented` 501. A route that does not exist is 404 `unknown_id`; a body that
is not JSON is 400 `invalid`. The document comment and review routes and shaping answer 501: the
UI keeps its controls, and the button shows the refusal.

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
closes it `4404`. Opening the transcript of a subagent, by GET or by socket, is what makes the
server attach to it; see "Agents" above. A reader that falls 500 frames behind is closed
`4409 lagging` and comes back with `from`; nothing is lost, because the ring holds what it
missed. The socket pings every 20 s.

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
| `POST /api/agents/{id}/say` | `{text, attachments?}` | `agent.say` | `{}` |
| `POST /api/agents/{id}/set-mode` | `{mode}` | `agent.setMode` | `{}` |
| `POST /api/agents/{id}/interrupt` | | `agent.interrupt` | `{}` |
| `POST /api/agents/{id}/stop` | | `agent.stop` | `{}` |
| `POST /api/agents/{id}/resume` | `{text?}` | `agent.resume` | `{}` |
| `POST /api/tasks/{project}/{id}/launch` | `{model?}` | `agent.launch` | `{agentId}` |
| `POST /api/tasks/infer` | `{project, draft}` | `task.infer` | `{title, branch, command, mode}` |
| `POST /api/tasks` | `{project, title, content?, branch?, command?, mode?, priority?, model?, launch?}` | `task.create` | `{taskId, agentId?}` |
| `POST /api/agents` | `{project, branch, prompt, mode, model?}` | `agent.start` | `{agentId}` |
| `POST /api/linear/tasks` | `{project, issueId, launch?}` | `linear.plan` | `{taskId, agentId?}` |
| `POST /api/tasks/{project}/{id}/status` | `{status}` | `task.setStatus` | `{}` |
| `PATCH /api/tasks/{project}/{id}` | the fields to write | `task.update` | `{}` |
| `POST /api/desk` | `{id}`, a desk id | `desk.add` | `{}` |
| `DELETE /api/desk/{deskId}` | the desk id, URL-encoded | `desk.remove` | `{}` |
| `POST /api/documents/{id}/approve` | `{version}` | `document.approve` | `{taskIds}` |
| `POST /api/documents/{id}/request-changes` | `{version, summary}` | `document.requestChanges` | `{}` |

## Attachments

An image reaches an agent as a file in the task notebook, whatever brought it in. `mael linear
plan` already worked this way; the orchestrator UI uses the same mechanism through
`maelstrom.attachments`, so a pasted screenshot is not a second way to put an image in the
notebook.

Two routes carry the bytes. Neither is a command: nothing about the world changes.

| Route | Body | Returns |
|---|---|---|
| `POST /api/attachments` | multipart: `project`, `bucket`, `file` | `{markdown, url}` |
| `GET /api/attachments/{project}/{bucket}/{name}` | | the image bytes |

Multipart, because the payload is bytes. Base64 in a JSON body would inflate it by a third for
nothing.

The reply carries two refs, and they are not interchangeable:

- `markdown` holds the portable `{{MAEL_TASK_DIR}}` token. A task stores this, and
  `build_prompt` expands it to an absolute path the agent can `Read`.
- `url` points at this server. A browser can fetch only this one, so it is what the thumbnail
  and the transcript show.

**The reply never names a filesystem path.** A client that knew one could send it back on a
`say`, and the agent host would read whatever file it named. So a `say` carries attachment
URLs, and the server resolves each one to a stored file itself. A URL that does not resolve to
an attachment this server serves is dropped rather than forwarded.

An upload is separate from the send. A task edit that is never saved leaves an orphan file
rather than a half-written task, and all four UI surfaces share one path.

The size cap is 5 MB, and the bytes must sniff as PNG, JPEG, GIF or WEBP. Both refusals answer
400 `invalid`.

| `POST /api/worktrees/{id}/close` | | `worktree.close` | `{}` |
| `POST /api/worktrees/refresh` | | `worktree.refresh` | `{}` |

`agent.setMode` is a pure relay. The child announces its new mode in its own `system`/`status`
event, so the world changes when that arrives, and a mode the child refuses never reaches the
world at all.

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

The two document commands are the server's own too: a document lives in the world, not in the
notebook and not on the host. **Approve** moves the document to `approved` and tells nobody — the
agent asked for a verdict, and the answer is on the document. **Request changes** moves it to
`changes-requested` and relays the summary to the agent as an `agent.say`, so the agent hears what
to fix. The relay comes first: a summary the host refuses never reached the agent, so the document
stays awaiting a review nobody has answered. Both retire the attention item the document raised.
Neither touches the notebook. Both **refuse a plan document**: a plan review is the agent's own
wait, ended by `agent.approve` or `agent.deny` on the request it blocks. Settling the document
instead would flip its status, retire the attention item pointing the user at it, and leave the
child blocked on a control request nobody could now answer. The UI draws no review bar on a plan
document for the same reason — its decision card answers the wait.

`comment.add` and `comment.resolve` still answer 501 — an anchored selection has its own storage
question.

A create adds its task to the desk whether or not it launches: work the user has just ordered
belongs on the canvas either way. A launch adds its task to the desk too. A second
`POST /api/desk` for an entry already on the desk answers `{}` and raises no notice. A `DELETE`
for a running agent's entry is accepted, but the canvas keeps drawing the node until the agent
stops.

### The host owns the control plane

The commands that write to the child are pure relays: `agent.approve`, `agent.deny`,
`agent.answer`, `agent.say`, and the `say` a `document.requestChanges` sends. The server validates, asks the host, and returns. It builds no
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
| `invalid` | Anything else: an empty reason, a task that is not actionable, an out-of-scope command, or a driving command on a subagent (`X.1 is a subagent of X; drive X`) |

`wrong_wait_kind` reads the kind off the request the reply names, never off the agent's state —
see `CONTEXT.md`, "Wait kind". A question and a permission open together are each answerable. When
the transcript no longer holds the item, the server sends the reply and lets the host judge it.
The log is a bounded ring, so refusing would strand a wait nobody could answer.

The host's own refusals map to the same codes: "no such agent" to `unknown_id`, "has exited" to
`agent_exited`, "not waiting" to `not_waiting`, "not waiting on a question" to `wrong_wait_kind`,
anything else to `invalid`.


## Running it

```bash
mael orchestrator serve                                # http://127.0.0.1:8765
mael orchestrator serve --port 3072 --log-level warning
mael env start                                         # in this repo: web and orchestrator together
```

The server is one aiohttp app, built by `routes.build_app`. It binds the port first, so a port
in use fails at once, then reads every source once, then serves.

The agent host is the daemon `MAEL_AGENT_ROOT` names, so a worktree's orchestrator talks to that
worktree's daemon. There is no flag for it.

The first command that needs the agent host starts one, as `mael agent` does.

## Diagnostics

The server writes timestamped logs to stderr. Under `mael env` that stream lands in
`~/.maelstrom/logs/<project>/<worktree>/orchestrator.log`, which `mael env logs` tails.

| Level | What it carries |
| --- | --- |
| `debug` | Everything below, plus aiohttp's and asyncio's own detail. Maelstrom logs nothing at this level yet |
| `info` | Every shell-out, as `shell.py` records it |
| `warning` | A refused attach, a missing backlog marker, an unreachable host |
| `error` | A failed refresh, a failed command, and anything that escapes a task |

`--log-level` sets it; the default is `info`.

```bash
mael orchestrator serve --log-level warning            # drop the per-command trace
mael env logs orchestrator                             # tail the running server
```

Two rules keep the file worth reading:

- **The log is appended, never truncated.** A service that dies is restarted at once. A restart
  that truncated the log destroyed the record of the crash that caused it. A restart rolls the
  log over at 20 MB instead, keeping the previous file as `.log.1`, so growth stays bounded
  without losing the run before.
- **The HTTP access log stays off.** The notice stream pings every client every 15 seconds, so an
  access line per request would bury what the log is read for.

Logging is configured in `orchestrator_cli.setup_logging`, not in `build_app`. The test suite runs
the real app, and a global logging setup inside `build_app` would follow it into every test.

## Open risks

- Blocking work runs on the worker thread. `setup_worktree_for_branch` can take tens of seconds,
  and the launch reply waits for it. Every launch pays that cost, including a reopen.
- The host's watcher queue drops the oldest event at 1000. The drop is marked, so the transcript
  shows a gap, but the dropped events themselves are gone. A lost answer is closed on the next
  reconciliation, whether or not the marker arrives.
- Agents started outside the server attach with a 200-event backlog, and link to a task only when
  started with the task's session id.
- A client that connects after the server started gets the transcript the server has built since
  it attached, not the agent's whole history. Reading Claude's own session transcript back through
  the normaliser would fix that, and is not built.
- A resume is a new life of the agent, so the host replays the new child's own output — Claude's
  replay of the conversation — into a transcript that already holds the last life's turns.
- `stop` removes the agent from the host. The server marks it `exited(0)` on the ok reply.
- `agent.resume` starts an exited agent again. No UI drives it yet, so a crashed agent is brought
  back with `mael agent resume <id>`.
