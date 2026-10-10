# The orchestrator server

The server builds the world the orchestrator UI shows — tasks, worktrees, agents,
attention — from the task notebook, `list-all` and the agent host, and serves it over HTTP:
resources by REST, change notices on one stream, and one socket per open agent transcript.
`mael-orchestrator serve` runs it.

The server owns the business model. The agent host owns the agent processes. The server only
reaches the host through the host's own client protocol, never by importing its internals, so a
host on another machine later is the same protocol over TCP.

## The layers

The server is the `mael_orchestrator` package, in the workspace member `orchestrator-api/`. The
package and the wire modules in `mael_domain` follow
[architecture-patterns.md](architecture-patterns.md). The wire types are `TypedDict`s in the wire's own camelCase, so an entity is the dict the socket
carries and nothing maps between a dataclass and the wire.

| File | Layer | Holds |
|---|---|---|
| `mael_domain/protocol.py` | pure | The wire types, `empty_world`, and `apply_event`, the one way the world changes |
| `mael_domain/normalise.py` | pure | The stream-json normaliser: the daemon's raw events to transcript items, agent upserts, documents and attention |
| `validate.py` | pure | Command validation: the rules the server applies before it asks the host |
| `world.py` | pure | `WorldState`: the tables, and `apply` as their only writer |
| `world_build.py` | pure | Entity builders from a task, a `list-all` row and an agent row; `link_agent`; `diff_kind`; `task_key` |
| `desk.py` | pure | The desk table: `add`, `remove`, `prune`, each returning a new table, and the desk id helpers |
| `notices.py` | pure | `notices_for`: which change notices a batch of events amounts to |
| `transcript_log.py` | pure | `TranscriptLog`: one agent's items, its seq, and the ring of frames a resume replays |
| `hubs.py` | adapter | `NoticeHub`: change notices to every open notice stream, coalesced per subscriber. `TranscriptHub`: transcript frames to every socket open on an agent, bounded per socket |
| `sources.py` | storage | `TaskSource` and `WorktreeSource`, over the notebook and `list_all.build_list_all_data` |
| `linear_source.py` | storage | `cycle_issues` and `plan_fields`: the server's one door onto Linear |
| `daemon_bridge.py` | storage | `DaemonRouter`: the agent-host protocol and its reply mapping. The socket client is `agent_transport.SocketAsyncDaemonClient`, and its fake is `agent_transport.ScriptedAsyncDaemonClient` |
| `codex_bridge.py`, `codex_daemon.py` | storage | The Codex agent host, run in process. See [agent-daemon.md](agent-daemon.md) |
| `mael_domain/desk_store.py` | storage | `DeskStore`: the desk, as a canonical table in the state database or in memory. Each backend subclasses it. The server runs the first; see [data-architecture.md](data-architecture.md) |
| `server.py` | service | `Orchestrator`: the world, the pollers, one watch per agent, the transcript logs, the commands, and the hubs it tells |
| `routes.py` | adapter | `build_app`: the aiohttp app that puts an `Orchestrator` on the network — every route, the error mapping — and `serving` / `serve_app` to run it |
| `cli.py` | CLI | `mael-orchestrator serve`, the composition root `build_orchestrator`, and the logging the server runs under |

`mael_domain.task_launch` holds the launch plan and its two guards, shared with
`mael task run`. `mael_domain.list_all` holds the rows both `mael list-all` and the server read.

## The normaliser and its goldens

The Python normaliser is the one the wire carries. `lib/domain/tests/test_orchestrator_normalise.py`
replays every recorded daemon stream under `agent-daemon/fixtures/agent_events/` into one seed agent
and holds the result to a golden under `lib/domain/fixtures/normalised/`. That test owns the goldens:
`UPDATE_GOLDEN=1 uv run pytest lib/domain/tests/test_orchestrator_normalise.py` re-records them, so a
normaliser change is a deliberate re-record and never a silent drift.

The tool cards run the other way. `classify_tool_call` and `tool_call_title` in `agent_view.py` are
a hand port of `orchestrator-ui/src/session/toolCards.ts`, which renders in the browser and stays
the reference. `orchestrator-ui/fixtures/tool-cards.json` records what it makes of each tool;
`UPDATE_GOLDEN=1 pnpm test` in `orchestrator-ui/` re-records it, and the Python test replays it.

## A loaded skill

Loading a skill injects the whole skill file as a user turn, so it becomes a `skill` item and
not a message: the web UI folds it behind the skill's name, and the TUI prints that name alone.
The turn opens with `Base directory for this skill:`, and that line is the only mark the stream
carries — the transcript file marks such a turn `isMeta`, but the daemon stream does not. The
skill's name is the last part of the path on that line. A harness that reworded that line would
silently return the body to the transcript as an ordinary message.

## A shell command

A `!` line in the composer runs a shell command on the agent host. When the command ends, the
host injects the command and its output as one user turn of two blocks. The normaliser folds the
pair into one `shell` item, which the web UI draws with the same card a `Bash` tool call gets.
Folding here is the same mechanism as a loaded skill: the tags on the turn are the only mark the
stream carries.

The input block appends the item and the output block updates it, the way a `tool_call` and its
`tool_result` already work. Older transcripts hold the pair as two turns, and the same rule reads
them. An output block whose input the ring dropped still renders, so a gap never swallows output.

A shell command does not move the agent to `processing`: its turn carries no request. An
assistant event that follows moves the state on its own. See
[agent-daemon.md](agent-daemon.md#running-a-shell-command) for the wire format.

## A turn that ends while a subagent runs

The world takes a top-level agent's state from its stream, not from the row poll. So the
normaliser follows the same rule as the row: `NormaliseContext.running_subagents` holds the
`tool_use_id` of each subagent that `task_started` (`local_agent`) opened and no
`task_notification` has ended. A `result` sets `delegating` while that set holds an id, else
`idle`. The last notification sets `idle` again. See
[agent-daemon.md](agent-daemon.md#a-turn-that-ends-while-a-subagent-runs).

A subagent's ask arrives on the parent's stream as the parent's own wait. When it is answered,
`NormaliseContext.turn_ended` decides what comes back: `delegating` if the `result` came first,
else `processing`.

A server that attaches after the ring lost the `task_started` does not know the subagent runs.
The adopt reads `delegating` from the row, but the replayed `result` then writes `idle` over it.
The agent reads `idle` until its next turn.

## A turn that ends while a background shell runs

Background shells follow the same pattern with one more flag.
`NormaliseContext.shells_running` holds whether the last `background_tasks_changed` listed a
shell. Each such event also writes the list to the agent's `backgroundShells`, subagents left out.
After a turn, the state is `delegating` if a subagent runs, else `background` if a shell runs,
else `idle`. The `result`, the last subagent's notification and an answered ask use this rule. A
snapshot uses it when the agent reads `idle` or `background`, whatever order the events came in,
as the row does. An exit empties `backgroundShells`. See
[agent-daemon.md](agent-daemon.md#a-turn-that-ends-while-a-background-shell-runs).

The adopt maps the row's `background` to `backgroundShells`. The poll does not refresh it: the
stream does.

A server that attaches after the ring lost the last `background_tasks_changed` starts with
`shells_running` false, and the replayed `result` writes `idle`. The detail frame that follows the
backlog carries the row's `background`, and `apply_agent_detail` takes it as a snapshot. So the
agent reads `background` again once the attach is done.

## A task notification

Two wire shapes say background work finished, and they are deliberately not symmetric.

A **subagent** reports through `system`/`task_notification`. `agent_model._end_subagent` consumes
it to end the subagent, and its status and summary land on the subagent's own row, so the
normaliser draws no transcript item for it — unrecognised `system` subtypes are dropped by
omission.

A **background `Bash`** reports through a `user` turn whose content is one `<task-notification>`
tag. The lookup that ends a subagent cannot match it: its `tool_use_id` was never given a dotted
id. That turn folds rather than dropping, because a normaliser drop is irreversible — the server
keeps no transcript, so no UI toggle recovers it.

The fold keeps `status` and `summary`, read from the notification's own body so that a `<summary>`
nested in another child cannot win over the real one. Roughly one turn in fifteen names no
`status`; one naming neither field is not worth a row, and falls back to a message. The turn still
moves the agent to `processing`, unlike a shell pair — the agent acts on a notification.

## A tagged document

An agent puts a document in front of the user by writing a marker in the text of an ordinary
assistant message. The normaliser reads that marker and mints a document, exactly as it mints a
plan document from `ExitPlanMode`. **The agent host does not change**: it relays assistant
messages untouched already, so it carries no document payload and learns nothing new.

One document tag, read by `document_tags.read_tags`:

```
<doc-file kind="tasks" filename=".drafts/iter1.md, .drafts/tail.md" title="Iteration 1">
```

Every document is a file. `<doc-file>` names files the server reads, comma-separated, and each
file becomes **one document** with a `draft_file` source: its registry id and its path. The files
of one tag form a **review group** — `group: {id, title, position}` on each member — and the order
is kept: a task set is one chain, and approving it promotes in that order. The tag's `title` names
the group, and defaults to the first filename. A member's own title is its draft's `title:` for a
`tasks` file, the tag title when the tag names one file, and the path otherwise. `kind` is one of
`plan`, `tasks`, `pr`, `review`, `verification` and `other`; an unrecognised kind reads as `other`, so a typo
shows a document rather than dropping it. One message may carry several tags.

There is no inline form. A `<doc-content>` is left in the text as written: a document with no path
cannot be presented again, so it cannot be versioned.

The tag names are **frontend-agnostic**: another frontend may render these its own way, so
nothing in a tag name is maelstrom's. Only a `kind` value may be.

Every tag is cut out of the message the transcript shows. The user reads a document in its own
tab, so raw tag syntax on the transcript would only be noise.

Another marker mints no document at all:

```
<note>Rebasing onto main, then re-running the failing port test</note>
```

The note is what the agent says it is doing now, and it lands on the agent as `lastNote` rather
than in the transcript. It replaces: the latest note wins, and a message carrying none leaves the
standing one alone, because a note describes work in progress and silence is not the end of that
work. It is cut like a document tag, being a field rather than prose. A subagent writes none, for
the reason it mints no document.

Both readers cut the tag. The daemon cuts it in `agent_model` so the note never also stands as the
agent's last message, and the orchestrator cuts it here so the transcript shows no raw syntax. Each
holds its own copy of the pattern rather than sharing one: the daemon sits below the orchestrator,
and a shared module would invert that. A test asserts the two patterns still agree.

Another mints no document either:

```
<milestone>built</milestone>
```

A milestone names a stage of the work the agent has just reached, from the vocabulary in
`CONTEXT.md`. The latest one in a message wins, as a note's does, and a subagent writes none.

Unlike every other tag, a milestone changes no world entity, so it mints no `upsert`: adding one
for a write that moves nothing would put a ledger row in the client's reducer. It rides out on
`Normalised.milestone` instead, and `server._record_milestone` does the write. That keeps
`normalise` a pure function, which is what the goldens rest on.

The figures come off the world rather than off the raw event, and the snapshot waits for the
declaring turn's `result`. The agent writes the marker on an `assistant` event, but that turn's
tokens only reach the world when its `result` lands — and that turn is usually the stage's most
expensive one, so a snapshot taken at the tag would push it onto the next stage. An agent the
world does not know writes nothing: there would be no totals to record.

The write also appends a `milestone` transcript item, the bar the session panel draws. A
`transcript.append` is not a world event — `apply_event` returns the state untouched for the three
transcript kinds, because each agent's `TranscriptLog` keeps them — so the bar still puts nothing
in the reducer. `MilestoneStore.record` returns the row it wrote, and `normalise_milestone` turns
that row into the item. The delta the bar reports is therefore the one `agent_store._snapshot`
computed, so the panel and `mael agent cost` cannot disagree.

The bar lands on the `result` for the same reason the snapshot does: the item is minted where the
figures are. A bar drawn where the marker was read would sit above the turn it is pricing.

A PR link rides out the same way, on `Normalised.pr_link`:

```
<link rel="gh-pr">118</link>
```

`server._register_pr` writes it to the agent's task through `TaskSource.register_pr`. A bare
number takes its URL from the `origin` remote of the agent's worktree. A live tag always
registers. A replayed tag registers only on a task with no Registered PR: a tag the agent wrote
while the server was down must still land, but an older tag must not undo a later `link-pr`.

One milestone does not wait, and the asymmetry is deliberate rather than an oversight. `planned`
is Maelstrom's own marker, not an agent's: the normaliser mints it when a plan approval is
allowed, and `_normalise` records it the moment it reads it. Approving a plan interrupts the
agent and clears its context, so no `result` for the planning turn is ever coming — and the next
`result` belongs to the build turn, which would price the planning stage at the build turn's
tokens. Recording at once is safe because the world's totals are poll-fed every 2s, so they are
already current when the approval lands. The `caught_up` guard matters more here than for an
agent's own marker: a re-attach replays the `control_response` verbatim, so without it every
reconnect would append a second `planned` row.

One row comes from no marker at all. An agent goes on spending after its last stage — `/present`,
the PR push, the CI watch — and `_exit` closes the ledger with a **closing row** named `<final>`
for that spend, before the exit is applied and while the world still holds the totals the agent
finished with. It is a real ledger row rather than a synthesis on read, so `mael agent cost` stays
a printer over the table and a stopped agent reports the same figures a live one did. An agent
that spent nothing since its last stage gets none: an empty row would report a stage that cost
nothing. The bar needs a watch to append to, and an agent whose watch has already gone still gets
the row — the ledger outlives the transcript.

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
that raises an attention item, of kind `document_review` — one per group, on its first member.
A re-present clears the group's open item before it raises the next, so items do not stack.

The path is the identity. A path presented again, in the same scope, becomes the **next version
of the same document** whatever its status, so a revision replaces its card entry rather than
adding one. The scope is the agent's task, or the agent when it has none: `.drafts/pr.md` is a
new pull request for each task. The re-present joins the group of the first path that matched,
and a member of that group the new tag leaves out becomes `superseded`. The plan document keeps
its own rule, `_Emitter.previous_version`: a plan re-sent after `changes-requested` is its next
version, and a plan is a group of one.

A document is **not persisted**. It lives in the world and dies with a server restart, exactly as
a plan document does. A document store is out of scope.

### Approving a task set

A draft's inertness is the approval gate — see `CONTEXT.md`, "Draft". A cmux session gates on the
user saying yes in the chat. The orchestrator UI has no chat, so the document's Approve button is
what runs the promote; otherwise approval would be advice the agent may ignore rather than a gate.

A verdict names the document the user clicked, and settles every current member of its review
group. So approving a member of a group whose members are all `tasks` documents with a
`draft_file` source promotes every member, in `group.position` order, in one call. Every other
group stays the verdict alone. A `source` names a registry id, so the paths come back from the
registry that validated them when the tag was read — see "The file registry". A member with no id
was unreadable, and refuses the promote with its path.
The first task follows the end of its parent's child-chain — `--follow-end '*'`, as the skill
wires it by hand — and each later one follows the one before, so the chain lands as the document
listed it. The reply carries the created ids,
because an approve that reports nothing reads as an approve that did nothing.

The head is **not** launched: the task list and node cards already offer Launch. The agent is told
what was created, so a session that planned the chain does not promote it a second time.

`NotebookTaskSource.promote` is the storage-layer step, over `task.promote_draft` — the same
function `mael task promote` calls, so the CLI stays canonical and this is a second surface onto
it, not a reimplementation. Three things make the failure path safe:

- **One transaction.** Several drafts are several task writes, and a failure part-way would
  leave some tasks created and some not. `TaskTable.transact` gives the set a true rollback, so
  an invalid draft leaves the notebook untouched and the document still `awaiting-review`. The
  refusal names the file, since the user is looking at the document and needs to know which one
  to fix.
- **One store.** A task's prose is in its row, so there is no second place a rolled-back write
  can survive in. That is what the cache beside the notebook used to cost, and why promote had
  to work around it.
- **Deferred deletion.** `promote_draft(consume=False)` leaves each file, and the set is deleted
  only once the transaction commits. A rollback restores the rows but cannot restore a deleted
  file, so deleting as it went would leave the user a half-deleted plan.

The task refresh is forced afterwards, as every other notebook write forces it: the poll is 2 s
away, and the user who approved would otherwise see nothing until it came round.

## A shown image

An agent shows the user a picture with another tag, which mints no document:

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

The registry is not persisted. It dies with a server restart, and a re-normalised transcript
registers its files again. An image URL from before a restart is a 404. An **Attached document** does
not depend on the registry: its media are served from the notebook.

| Route | Serves |
| --- | --- |
| `GET /api/files/{id}` | The bytes of one registered file, or `unknown_id` |

The lookup is the whole authorisation step: the handler parses no path and joins no string. A file
since deleted reads as `unknown_id` too — the id was real, the bytes are not.

## Media in a document

A document body names an image or a video as a markdown image ref with a worktree-relative
target:

```
![The login flow](test-results/login/video.webm)
```

`document_tags.media_refs` finds these refs. `tagged_document` registers each target with the file
registry and points the ref at `/api/files/{id}`, exactly as `show_image` does for an `<image>`
tag. A target the registry refuses becomes the same "could not be shown" prose. This applies to
every `<doc-file>` kind.

The target resolves against the worktree root, as `<image src>` does. It does not resolve against
the document's directory.

`media_refs` reads the markdown with two regular expressions, not with a markdown parser. It
skips a URL, an absolute path, a ref inside a closed fence that starts at column 0, and a ref
inside a single-backtick code span. It does not know an indented fence, an indented code block or
a double-backtick span, and it does not match a target that holds a space or a `)`.

Video takes image syntax, so no new marker is needed. See `docs/dev/orchestrator-ui.md` for how
the UI draws it.

## An attached document

A document lives in the server's memory, and the media it names live in a worktree. A
**Attached document** outlives both: `task_attachments.attach` copies it into the notebook.

```
agent                 normaliser              server                  notebook
  | <doc-file kind="verification">               |                        |
  |--------------------->| upsert document ----->| attach() ------------->| media into the bucket
  |                      |                       |                        | row into task_attachments
  |                      |                       |<-- stored row ---------|
  |                      |                       | upsert: row version,   |
  |                      |                       | media from the bucket  |
  |          (server restart, or the agent ends) |                        |
  |                      |                       |<-- every row, at start-|
```

Two events attach a document, and both arrive as a document upsert from the normaliser:

| Document | Attached when | Body | Path |
| --- | --- | --- | --- |
| Verification | The agent's document tag shows it | Read from the worktree file | The file's worktree-relative path |
| Plan | The user approves it | The document's markdown | `planFilePath` |

Only a document with a task is attached. The task's notebook id is the **Bucket** its media go
to, and a free agent has no bucket that outlives it.

`attach` does three things:

1. It takes a digest of the source body and of the bytes of each media file the body names. When
   the stored row has the same digest, kind and title, `attach` returns that row and writes nothing.
   A recording made again under the same name changes the digest, so it is a new version.
2. It copies each media file into the bucket with `attachments.save_media`, which accepts an image
   or a video up to 50 MB. The bucket filename carries a digest of the bytes, so an unchanged file
   is stored once and two files of one name do not collide.
3. It rewrites each ref to a `{{MAEL_TASK_DIR}}` token and upserts the row. A file that is refused
   becomes "could not be shown" prose, and the log holds the reason.

`attach` returns `None` only when the document's file cannot be read. The world's document then
keeps the "could not be read" prose the normaliser gave it.

The row's id is a digest of `(task_key, path)`. One row holds one document: a new version replaces
the body and raises `version`. History is not stored, and no code removes a row.

### A replay only repairs

After a restart the server attaches to each agent again and the backlog replays, so the same tag
reaches the server a second time.
A verification is read from the worktree as it stands at that moment, which is not what the agent
showed when the event first ran. So a replayed event attaches a document only when it has no
row. A row that exists is replaced only by a live event.

### The row is the authority

After it attaches a verification, the server upserts the world's verification again with two fields from the row:

- **`version`.** At start the server seeds each row into the world as a document. A replayed tag
  then finds that document by path and counts one more version. The server writes the row's
  version back.
- **`markdown`.** The body reads its media from `/api/attachments/…`, not from `/api/files/…`. The
  document then stays readable when the worktree closes while the server runs.

This second upsert follows the normaliser's by one `await`, which can copy tens of megabytes. In
that interval a client holds the normaliser's version.

A seeded verification has the `draft_file` source, which is what lets `tagged_document` match it
by path. A seeded plan has the `attached` source, so nothing offers a review for it. When a
replayed plan review mints the live plan again, the server removes the seeded entry.

A document that fails to attach is logged, and the stream continues. The document is still in the world.

`GET /api/attachments/{project}/{bucket}/{name}` returns a `web.FileResponse`, which answers a
range request with `206`, so a browser can seek a video.

A bucket file is swept into the next notebook commit. The 50 MB cap limits one file and not the
total, and a superseded version's media stay in the bucket. Video grows the notebook's git tree.

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
| Landings | Inside the worktree read, under the same lock: refresh each tracked task's PR, then record each **Landing** step it reached. Each done tracked task is then read again, and the ones whose landing moved are pushed | with the worktree read |
| Comms | Read the `comms` rows written since the last read. A comm's `taskIds` are derived from the world's tasks | 2 s, on the task tick |
| Desk | Read once at start, pruned on every task refresh, joined by every live agent, and written through on change | — |
| Host | One entity, `agent-host`, saying whether the agent host answers. Set by every agent poll; published only when it changes | with the agent poll |

Blocking reads run on one worker thread. The SQLite index behind the notebook is bound to the
thread that opens it, so a pool of one keeps every read on the same connection.

Worktree operations run on a second, wider pool. They touch git, ports and the process table and
never the notebook, so they have no reason to queue behind a task read — and a fetch would stall
one for seconds.

The pool is handed to each operation's sequence rather than applied at the call site: an operation
is a coroutine that awaits its steps, and it is each blocking step that needs a thread. Its width
is for overlap, not for correctness — what must not run at once is named by each step's own scope
and held with a cross-process lock. See `docs/dev/worktree-steps.md`.

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

### What a landing read costs

The landing sync runs at the end of each worktree read, after its rows are applied and before
the lock is released. So it shares that read's cadence, its catch-up on arrival, `refresh_now`
and the stand-off. A worktree read that was rate-limited skips the sync. A `RateLimited` from the
sync's merge read sets the stand-off, as the worktree read's own does. Any other failure is
logged.

The sync reads only the pull requests that can still move. A PR is settled when it was closed
unmerged, or when it is merged and `landed` in every environment the project's `deploy:` block
names. `landed` is final. For each
project with an unsettled PR, one tick costs:

| Read | Calls | When |
|---|---|---|
| Merge: `gh api graphql`, one alias per PR | 1 | Some PR is not merged |
| Deploy: `deployments?environment=<env>&ref=main`, then each deployment's newest status until one is `success` | 2 per environment, usually; at most 6 | Some merged PR is not `landed` there |
| Ancestry: `compare/<merge sha>...<deploy sha>` | 1 per PR and environment | The deploy sha is new, or the last state was `unknown` |

A `not_yet` on an unchanged deploy sha is kept without a compare. For the token the deploy reads
need, see `deploy:` in `docs/reference/configuration.md`.

### The landing on a task

A wire task carries `landing`: `None` until the task is `done`, then `{status, envs}`.
`Landings.landing_of` builds it from the cached `pull_requests` row, and reads nothing from
GitHub. `status` is the highest step reached, through the same rule as `mael comms landings`.
`envs` names each deploy step the project deploys to, with its state. A task with no
Registered PR has empty `envs`, because it has nothing to land.

A landing change writes no task row, so the task poll would never see it. So after each sync
`refresh_worktrees` reads every done tracked task again through `TaskSource.read_some`, and
pushes the ones whose entity moved. That covers an env state that changes with no new step, such
as `unknown` to `not_yet`. The read is the table and the cached PR rows; it asks GitHub nothing.

`landing_of` asks each project's config once per done task. The composition root caches that
read for one worktree poll, so a whole task read parses each `.maelstrom.yaml` once. The sync
shares the cache, so an edit to `deploy:` reaches it within one poll.

### Comms

The server holds a `CommStore` over the canonical `comms` table, and polls it on the task tick
with `changed_since`. A comm written by `mael comms` in another process reaches the world on the
next tick, and a comm command reads it at once.

A comm's `taskIds` are not stored: each task names its comms in its own `comms` field. So
`_apply` checks every task event before it applies. When a task's `comms` change, the comms on
both sides — the old list and the new — are derived again from the world's tasks, and their
upserts join the same batch. A link change is therefore one task write that sends a task notice
and a comm notice together.

A link is `PATCH /api/tasks/{project}/{id}` with `comms`. The list replaces the task's own, and
each id must name a comm the world holds. `task.create` takes `comms` too, so a task made from
a comm is linked in the same write. Without `comms`, a new task inherits its parent task's comms.

A comm's `category` and `project` are plain fields of `comm.create` and `comm.update`. `project`
is `""` or a project the world holds. The **Default project** of a category is derived in the
client, in `selectors/comms.ts`, which mirrors `comms.default_project`.

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
everyday daemon was gone before anything else did, and `mael-agent-daemon serve` under `uv run`
resolved to that worktree's `.venv`. The banner is the whole response now, and
`mael self-env start` is what brings the daemon back.

Each agent row carries the child's `pid` while it is alive, so a client can name the process an
agent is and `mael-agent-daemon list` can be read against the canvas.

An exited id that comes back live is the same agent again, not a new one: a resume keeps the
agent id. The server clears the exit code, clears the attention item the exit raised, and attaches
a second time. The re-attached backlog is relayed with the ids it already had, so a client that
holds those items applies nothing new.

A stop the host refuses with `no such agent` still ends the record, and the router answers ok: the
agent is gone, which is what the stop asked for. The record stays revivable, because a daemon at
another root may still hold the agent. A resume the host accepts puts the agent back in the
router's live set, and the server refreshes the world at once, so the card goes live on the reply.
A resume the host refuses as `is running` succeeds when the host's `list` names the agent live:
the router takes it back in.

What an agent waits on is not stored. Both the host and the server derive it by running the same
function over the same events: a `control_request` opens the wait, and a `control_response`, a
`control_cancel_request` or a `result` ends it. The host reads those events with no stream in
between, so the host's state is never behind the server's. Where the host disagrees with the
world, the server takes the host's answer. A `list` reply is the exception: it is a copy of that
state, and the stream can move past it while it is in transit. The reconciliation of waits below
allows for that.

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

The row judges only the waits the world held when the server sent `list`. The host reduces an
event before it relays it, so those waits are in the state the row was built from. A wait that the
stream raises while the reply is in transit is newer than the row. The row says `processing` for
it, and closing it would hide the prompt until the turn ends. The next poll judges that wait.

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
runs them, then hands the host a `start`. Neither refusal runs on the loop: the live-session sweep
is awaited, and the worktree opens on the worktree pool.

The server opens one worktree per project at a time, for a launch and a free agent alike.
`create_worktree` picks a NATO name and adds the worktree with no scope held, so two opens in one
project could pick the same name.

A task that has already run owns its session id, and claiming that id again is refused. So the
launch asks `has_claude_transcript` whether the worktree holds a transcript for it, and sets
`resume` on the `start` when it does. That is the same switch `mael task run` makes for a pane.

A start the host refuses rolls the task back to the status it had. A second launch of a task
still in flight is refused. A launched task also joins the desk, so the node the user just
started is drawn on the canvas.

A newly adopted live agent joins the desk: `task:` when the agent has a task, `agent:` when it
does not. The canvas draws running work whether or not the desk names it, so the entry is not
what makes an agent visible — it is what keeps it visible after the agent stops, until the user
takes it **Off desk**. The join runs once, at adoption, so a later poll cannot re-add an entry the
user has taken off the desk.

An `agent:` entry is never pruned during a run: an agent stays in the world once seen, so the
entry always has an entity to draw. A restart is the exception. The world's agents are rebuilt
from the host, so `_load_desk` drops a stored `agent:` entry naming an agent the host no longer
lists — it would draw nothing, and the user could never take it off the desk. That is why the desk loads
after the first agent read.

## Creating work

Four commands write new work.

- **`task.infer`** names a task from its prose, through `infer_task_names` in
  `task_metadata_generator.py`. It makes one HTTP call to OpenAI's `gpt-6-luna` and falls back to
  a deterministic name. With no `OPENAI_API_KEY` it makes no call and returns the slug. The call
  blocks for up to 30 seconds — two 15-second attempts — so it runs on the executor and the UI
  shows a wait.
  `mode` comes from the inferred command through `task.mode_for_command`. Inference writes
  nothing.
- **`task.create`** writes the task the user edited. It is a separate call so the UI can show the
  inferred fields first. An explicit branch makes `model.create` skip generation, so no second
  model call runs behind the user's edit. With `launch` set it runs the launch path
  unchanged, as `mael task add --run` does. A launch that fails leaves the task written and on
  the desk, so the refusal names it — without that the client cannot tell the case from "nothing
  was written", and a retry writes the task twice.
- **`linear.plan`** plans a Linear issue: the equivalent of `mael linear plan`.
  `linear_source.plan_fields` fetches the issue and returns the task fields the CLI would write,
  through `integrations.linear.build_plan_task` — the CLI command calls the same function, so the
  two cannot drift. The branch comes from `task.infer` over the same brief, not from
  `build_plan_task`'s own generation, so the server spends one model call instead of two.
  The fields then go through `task.create`, which files the task and launches it. `parent` and
  `post_action` bind the task to its issue and are not in `validate.EDITABLE`, so they reach the
  notebook through `create`'s `extra` argument — the server's own to set, never a client's.
- **`agent.start`** starts an agent tied to no task. `TaskSource.worktree_for` opens the branch's
  worktree through the same collaborator a launch uses, so a branch with no worktree gets one
  provisioned. The `start` payload carries no `session` and no `env`: the host mints its own
  session id, and nothing exports `MAEL_TASK_*`. Those two absences are the whole definition of
  a free agent.

  `investigate: true` starts an investigation. The payload names `investigation_prompt_file()`
  in place of `agent_prompt_file()`: one file that holds the markers and the no-code rules,
  because the daemon takes one file. `validate.py` refuses `investigate` with `mode: "plan"`.
  When a shared prompt file is missing, the server refuses the start before it opens the
  worktree: without its rules an investigation is an ordinary free agent.

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

An ordinary close never forces. A worktree with unmerged commits or a dirty tree is refused, and
the refusal carries the model's own message, so the UI reads what the command would have printed.

Forcing is its own command, which the UI calls **Shelve**. It writes a `wip: uncommitted changes`
commit and keeps the branch, so nothing is lost. It is a decision, not a retry, so the UI asks
before it sends. `_main` is refused by `validate.py` for every teardown, before any git call runs.

A forced close that went over unmerged work also writes a task to reopen the branch.
`add_reopen_task` in `worktree_close.py` writes it, and `mael close --force` calls the same
function. A worktree that was already detached gets no task, because it has no branch to reopen.
The handler reads the tasks again after the close, so the new task reaches the UI. A task write
that fails is logged and does not fail the close: the worktree is closed by then.

Trash is also its own command, and the UI asks before it sends. A trash that fails partway may
already have stopped agents, so the handler refreshes the world whichever way it ends.

Six operations share that shape: close, force close, trash, remove, sync and env. Each is one optional
callable on `WorktreeSource`, so a source built without one serves the world read-only for that
operation rather than half-doing it. Each is a step sequence — close and remove in
`worktree_close.py`, trash in `worktree_trash.py`, sync and env in `worktree_ops.py` — so each takes the worktree scope and
cannot reach a checkout another operation is rewriting. `sync` takes a mode — `plain`, `autorepair` or `squash` —
because it is one operation with the three settings `mael sync` has, not three operations. `env`
takes an action, and `restart` is `stop` then `start` rather than a third code path. `env` also
takes an optional `service`, which names one optional service to start or stop alone. The
validator refuses a `service` that is not an optional service in the worktree's `env.services`,
and refuses `restart` with a `service`.

Each blocks for tens of seconds, so each runs on the worktree pool. The refresh runs whichever way
the operation ends: a close that fails partway has still stopped agents and freed ports.

`worktree.mergePr` is a seventh optional callable, `WorktreeSource.merge`. It is not a step
sequence and takes no worktree scope, because it does not touch the checkout.

- `validate.py` refuses a pull request whose **PR state** is not `ready`, a draft, and a
  worktree whose **PR match** is `differ`.
- The source takes the number and the head commit from its PR cache, not from the command. It
  checks the state again there, because a read can land after the validation. It also reads the
  local `HEAD` again and refuses when it is not the PR head, or cannot be read: a local commit
  can land after the last read.
- It runs `gh pr merge <n> --rebase --match-head-commit <oid>`. The world can be 60 s old, so
  GitHub refuses a head that moved.
- The `list-all` row carries the head commit as `pr_head_oid`, because the cache refills from
  those rows. It also carries the local `HEAD` as `head_oid`. `world_build.py` compares the two
  into `prMatch`, and does not copy either sha to the wire.
- A sync re-reads with the synced branch added to the active branches, so GitHub is asked for
  its new head even when the branch is not on the desk. That re-read waits out a read already in
  flight, because the read in flight chose its branches without the synced one.
- The merge token, when set, reaches that one call as `GH_TOKEN` — see
  [configuration.md](../reference/configuration.md#api-keys).
- The handler re-reads the worktrees whichever way the merge ends.

`worktree.createTerminal` runs the `ensure_terminal` port. It makes the worktree's workspace
when it is missing, and returns the `cmux://` link to its terminal. The server focuses nothing;
see [cmux.md](cmux.md#terminal-links).
It writes the link into that one row and does not re-read: a re-read asks GitHub and takes
seconds, and the UI follows the link only after the reply. When cmux is not running, or cannot
make the terminal, the command is refused `invalid`. A closed worktree is refused too.

Each worktree read also runs the `terminal_urls` port for every open worktree, and puts each link
in `Worktree.shellUrl`. A worktree with no terminal has `''`. A pane closed since the last read
leaves a dead link until the next read; cmux ignores it.

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
`orchestrator-ui/src/protocol/deskId.ts`. The task half carries the wire id, so two projects may
each keep their own `2026-06-11.1`. A desk written before ids carried a kind held bare task ids; the
desk ladder's import rung rewrites those to `task:` ids as it reads `desk.json`.

## Reading the world

The world is served over REST, from memory, once the first source reads have finished. Every
route is under `/api` and answers JSON. A task id is two path segments, because the wire id is
`<project>/<notebookId>`.

| Route | Returns |
|---|---|
| `GET /api/projects` | `{projects: [Project]}` |
| `GET /api/linear/issues?project=` | `{issues: [{id, title, status}]}` — the project's current Linear cycle. Refused unless the project sets `linear.team_id` |
| `GET /api/worktrees` | `{worktrees: [Worktree]}`. A worktree's `env` is `{state, services}`: its env state, and its per-worktree services as `{name, optional, running, url}` — declared ones in config order, or for a Procfile project its tracked services and a synthetic `app`. `running` reads the tracked pid, not a port probe |
| `GET /api/worktrees/{id}/changes` | `WorktreeChanges`: `{dirtyFiles: [{path, status}], base, commits: [BranchCommit]}`. Read from git on each request. See "A worktree's changes" |
| `GET /api/worktrees/{id}/diff?rev=` | `{rev, files: [FileDiff]}`: the diff one rev names, as files, hunks and numbered lines. Compressed |
| `GET /api/tasks` | `{tasks: [TaskRow], version}`. A row is a task without `content` and `log`. The `ETag` changes with every task change; `If-None-Match` answers 304. Compressed |
| `GET /api/tasks/{project}/{id}` | `TaskDetail`: the whole `Task`, prose included, plus `displayContent`. See "Attachments" |
| `GET /api/comms` | `{comms: [Comm]}`, open and closed. A comm is `{id, title, content, recipients, createdAt, closedAt, taskIds}`. The `ETag` changes with every comm change; `If-None-Match` answers 304 |
| `GET /api/comms/{id}` | The `Comm` |
| `GET /api/agents` | `{agents: [Agent]}` |
| `GET /api/agents/{id}` | The `Agent`, plus `pendingRequests`: the question, permission request and plan review items it waits on, oldest first, empty when it waits on none. A decision renders from this alone |
| `GET /api/agents/{id}/milestones` | The agent's `AgentCost`: its totals, and a `stages` list saying what each stage cost. Served through `agent_cost.build_cost_report`, the report `mael agent cost` prints. An agent that reached no stage gets that report with `stages: []`, not a 404 |
| `GET /api/attention?open=1` | `{attention: [Attention]}`; `open` keeps only items not yet cleared |
| `GET /api/documents` | `{documents: [Document]}` without `markdown` |
| `GET /api/documents/{id}` | The `Document`, `markdown` included |
| `GET /api/desk` | `{desk: [DeskEntry]}` |
| `GET /api/host` | `{host: Host \| null, loop: {maxGapMs, lastStallAt}}`: whether the agent host answers, since when, and on which socket. `null` until the first agent poll has settled. `loop` is the longest gap between two event-loop ticks, and the time of the last stall; see "Loop stalls" |

### A worktree's changes

The two changes routes are **Pass-through** (see [data-architecture.md](data-architecture.md)).
Each request runs git in the worktree, with `--no-optional-locks`, so a read never takes the
index lock from an agent's commit. `mael_domain.worktree_changes` holds the reads and the diff
parser.

A rev is one of three values:

| Rev | The diff |
|---|---|
| `uncommitted` | The working tree against `HEAD`, with each untracked file as an addition |
| `branch` | The merge-base with the **Base** to `HEAD`: every commit on the branch as one diff |
| A commit sha | That commit against its first parent |

The **Base** resolves as it does for a review: `origin/<base>` first, then `origin/main` when the
base has merged and been pruned. `base` in the reply is the branch that resolved. `dirtyFiles` and
the uncommitted diff hold **Dirty files** only. `commits` is oldest first, the order the work was
done in.

Both routes answer 404 `unknown_id` for a worktree the world does not hold, and for a **Closed**
worktree, which has a detached HEAD and so no branch. The diff route also answers 404 for a sha
that is not in `commits`, so the route cannot read arbitrary history, and 400 `invalid` with no
`rev`. A git read that fails answers 400 `invalid` with git's message, never an empty diff.

A file keeps its first 5,000 lines. A longer file is marked `truncated: true`, and its
`additions` and `deletions` still count every line. A binary file is marked `binary: true` and
has no hunks.

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

The kinds are `project`, `worktree`, `task`, `agent`, `attention`, `document`, `desk`, `host`
and `comm`. A
worktree's changes have no kind of their own; see orchestrator-ui.md, "The Changes tab". A
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
{"type": "transcript.partial", "agentId": "…", "itemId": "…", "markdown": "…"}
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

### A partial message

The daemon sends a message in chunks while the agent writes it; see "A partial message" in
[agent-daemon.md](agent-daemon.md). The normaliser turns the chunks into one transcript item that
grows. The append and the close are seq'd frames. The growth between them is a top-level
`transcript.partial` frame with no seq:

```json
{"seq": 4190, "event": {"type": "transcript.append", "agentId": "…",
                        "item": {"id": "ag1-9", "type": "message", "role": "assistant",
                                 "markdown": "All true", "partial": true}}}
{"type": "transcript.partial", "agentId": "…", "itemId": "ag1-9",
 "markdown": "All true tea comes from one plant"}
{"seq": 4191, "event": {"type": "transcript.update", "agentId": "…", "itemId": "ag1-9",
                        "patch": {"markdown": "…", "partial": false, "ts": "…"}}}
```

Six rules hold it:

1. **The whole text so far, never a chunk.** Each partial replaces `markdown`. A partial that is
   skipped, or dropped between the server and a browser, loses nothing.
2. **No seq, no ring slot, no replay.** `TranscriptLog.record` sets the item's text in place, so a
   snapshot is current. A replay is not: the next partial, or the close, brings the text up to
   date. The log and the browser apply a partial only to an item that still has `partial: true`,
   so a partial that follows the close does nothing.
3. **Only the newest partial waits for a slow socket.** `TranscriptSubscriber` keeps seq'd frames
   in its queue and one partial beside it. A newer partial takes the place of the older one. A
   partial never fills the queue, so it never makes a socket `4409 lagging`.
4. **One partial per chunk that changes the text.** Claude Code sends its chunks in bursts, about
   400 ms apart, so each chunk goes out at once and the card is current when a burst ends. A
   chunk inside a held-back tag changes nothing on screen, and sends nothing.
5. **The whole message takes the same item id.** `NormaliseContext.partial` holds the id the
   first text was appended under, keyed by the message's own id. The `assistant` branch then
   updates that item and appends nothing. With nothing held it appends, as it does for a stream
   with no chunks. The closing update carries `ts`, so the item ends equal to the appended one.

6. **A tag has no effect before the message is complete.** `document_tags.partial_text` cuts each
   complete marker and holds back a half-written one, with everything after it. A `<note>`, a
   `<milestone>` or a `<link>` shows nothing until it closes. A chunk emits transcript events only: documents,
   the note, the milestone and the PR link come from the `assistant` event. A marker in a code span or a fence
   is not held back, so a message that quotes `<note>` keeps growing.

A turn that ends with no whole message closes the item with `partial: false` and keeps its text.
So does an agent that exits, and so does an attach stream that stops with no exit marker
(`close_partial_message`): a daemon restart replays no chunk, and the next attach has a new
context that does not know the item. A chunk with no `message_start` before it is ignored: an attach that
began in the middle of a message cannot say which message the text belongs to.

A subagent's stream and a Codex agent carry no partial message.

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
| `DELETE /api/tasks/{project}/{id}` | | `task.delete` | `{}` |
| `POST /api/comms` | `{title, content?, recipients?}` | `comm.create` | `{id}` |
| `PATCH /api/comms/{id}` | any of `{title, content, recipients, closed}` | `comm.update` | `{}` |
| `POST /api/desk` | `{id}`, a desk id | `desk.add` | `{}` |
| `DELETE /api/desk/{deskId}` | the desk id, URL-encoded | `desk.remove` | `{}` |
| `POST /api/documents/{id}/approve` | `{version}` | `document.approve` | `{taskIds}` |
| `POST /api/documents/{id}/request-changes` | `{version, summary}` | `document.requestChanges` | `{}` |
| `POST /api/worktrees/{id}/close` | | `worktree.close` | `{}` |
| `POST /api/worktrees/{id}/force-close` | | `worktree.forceClose` | `{}` |
| `POST /api/worktrees/{id}/trash` | | `worktree.trash` | `{}` |
| `POST /api/worktrees/{id}/sync` | `mode` | `worktree.sync` | `{}` |
| `POST /api/worktrees/{id}/merge-pr` | | `worktree.mergePr` | `{}` |
| `POST /api/worktrees/{id}/env` | `action`, `service` | `worktree.env` | `{}` |
| `POST /api/worktrees/{id}/terminal` | | `worktree.createTerminal` | `{shellUrl}` |
| `POST /api/worktrees/{id}/comments` | `comments`, a list of change comments | `worktree.comment` | `{agentIds, refused}` |
| `POST /api/worktrees/{id}/feedback` | the feedback: `type`, then the type's fields | `worktree.feedback` | `{agentIds, refused}` |
| `DELETE /api/worktrees/{id}` | | `worktree.remove` | `{}` |
| `POST /api/worktrees/refresh` | | `worktree.refresh` | `{}` |

## Attachments

An image reaches an agent as a file in the task notebook, whatever brought it in. `mael linear
plan` already worked this way; the orchestrator UI uses the same mechanism through
`mael_domain.attachments`, so a pasted screenshot is not a second way to put an image in the
notebook.

Two routes carry the bytes. Neither is a command: nothing about the world changes.

| Route | Body | Returns |
|---|---|---|
| `POST /api/attachments` | multipart: `project`, `bucket`, `file` | `{markdown, url}` |
| `GET /api/attachments/{project}/{bucket}/{name}` | | the image or video bytes |

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

A stored ref is not fetchable, so the server rewrites it before a browser sees it.
`attachments.attachment_urls` changes the prefix of each ref's target to
`/api/attachments/<project>/`. It maps two forms:

- The token, which names the project the caller passes. Task content and a message typed in
  the UI hold this form.
- The absolute path under `tasks_root()`, which names its project itself. A task's first prompt
  holds this form, because `build_prompt` expanded the token.

Two readers use it:

- `GET /api/tasks/{project}/{id}` adds `displayContent`, the content with fetchable refs.
  `content` stays raw, because the task editor saves it back.
- The server passes it to the normaliser as `show_refs`, which rewrites each user `message`
  item with the agent's project. `mael agent attach` passes nothing, so a terminal shows the
  path the agent was given.

An upload is separate from the send. A task edit that is never saved leaves an orphan file
rather than a half-written task, and all four UI surfaces share one path.

The size cap is 5 MB, and the bytes must sniff as PNG, JPEG, GIF or WEBP. Both refusals answer
400 `invalid`.

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
notebook and not on the host. Each names one document and acts on its review group's current
members — every member not `superseded`. **Approve** moves them to `approved`, then tells the agent
with an `agent.say` that names the group title, and for a task set the created ids (see
"Approving a task set"). The agent asked for the verdict and waits on it. The message is
best-effort: a host that refuses it does not turn the approval into a refusal, because a lost
approval loses none of the user's words. The cost is an agent that still waits, so the server logs
the refusal and the user has to tell the agent. **Request changes** moves them
to `changes-requested` and relays the summary to the agent once, as an `agent.say` naming the
group title, so the agent hears what to fix. The relay comes first: a summary the host refuses
never reached the agent, so the group stays awaiting a review nobody has answered. Both retire the
attention items the members raised.
Neither touches the notebook. Both **refuse a plan document**: a plan review is the agent's own
wait, ended by `agent.approve` or `agent.deny` on the request it blocks. Settling the document
instead would flip its status, retire the attention item pointing the user at it, and leave the
child blocked on a control request nobody could now answer. The UI draws no review bar on a plan
document for the same reason — its decision card answers the wait.

`worktree.comment` posts **Change comments**. `format_change_comments` builds one message from
the list, and the server sends it as a `say` to each top-level agent in the worktree that has not
exited. `agents_in_worktree` in `validate.py` selects them. The server quotes the lines the
client sent and does not read git, because the diff the user commented on can be stale.

The command refuses an empty list, a comment that lacks a field of the message, a comment with
an empty body, and a worktree with no such agent. It answers ok when one agent or more took the message. `agentIds` names those agents, and
`refused` names each agent that the host refused, with the host's message. A refusal there would
make the client keep the comments, and a retry would post twice to the agents that have them.
When every agent refuses, the reply is the first refusal.

`worktree.feedback` sends **Feedback**: the body of the post, typed by its `type`. The route
passes the body through, and `validate.py` judges it, so a new type is a validator and a formatter.
The command reaches the same agents as `worktree.comment`, and answers the same reply. It refuses
a body that is not an object, a type it does not know, and a worktree with no such agent.

| Type | Fields | Message |
|---|---|---|
| `monkeypatch` | `css`, `note` (optional) | `format_monkeypatch` quotes the CSS and the note, and tells the agent to delete `.drafts/monkeypatch.css` when it has applied the change. Blank CSS is refused. |

`comment.add` and `comment.resolve` still answer 501 — an anchored selection has its own storage
question.

A create adds its task to the desk whether or not it launches: work the user has just ordered
belongs on the canvas either way. A launch adds its task to the desk too. A second
`POST /api/desk` for an entry already on the desk answers `{}` and raises no notice. A `DELETE`
for a running agent's entry is accepted, but the canvas keeps drawing the node until the agent
stops.

### The host owns the control plane

The commands that write to the child are pure relays: `agent.approve`, `agent.deny`,
`agent.answer`, `agent.say`, the `say` a `document.requestChanges` sends, and each `say` a
`worktree.comment` or a `worktree.feedback` sends. The server validates, asks the host, and returns. It builds no
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
| `wrong_wait_kind` | An answer to a permission, or an approve of a question. A deny suits every kind |
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
uv run mael-orchestrator serve                         # http://127.0.0.1:8765
uv run mael-orchestrator serve --port 3072 --log-level warning
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
| `warning` | A refused attach, a missing backlog marker, an unreachable host, a loop stall |
| `error` | A failed refresh, a failed command, and anything that escapes a task |

`--log-level` sets it; the default is `info`.

```bash
uv run mael-orchestrator serve --log-level warning     # drop the per-command trace
mael env logs orchestrator                             # tail the running server
```

Two rules keep the file worth reading:

- **The log is appended, never truncated.** A service that dies is restarted at once. A restart
  that truncated the log destroyed the record of the crash that caused it. A restart rolls the
  log over at 20 MB instead, keeping the previous file as `.log.1`, so growth stays bounded
  without losing the run before.
- **The HTTP access log stays off.** The notice stream pings every client every 15 seconds, so an
  access line per request would bury what the log is read for.

Logging is configured in `cli.setup_logging`, not in `build_app`. The test suite runs
the real app, and a global logging setup inside `build_app` would follow it into every test.

### Loop stalls

The server has one event loop. A sync call on it — a `subprocess.run`, a `flock`, a `urllib`
request — freezes every client until it returns. `loop_watch.LoopWatch` measures this. It sleeps
100 ms in a loop and records the gap between two wake-ups.

- A gap over 250 ms is a stall. The server logs a warning with the gap and the stack of every task.
- `GET /api/host` serves `loop.maxGapMs`, the longest gap since start, and `loop.lastStallAt`. A
  gap includes the 100 ms sleep, so after the first tick `maxGapMs` is never below 100. It never
  resets, so compare it across restarts.

A change that moves work off the loop shows its effect as a lower `maxGapMs`. The stacks in the
warning show where each task waits after the stall, not the call that blocked. Read them with the
log lines just before the warning to name the blocking call.

## Open risks

- A partial message can draw unclosed markdown oddly until it closes: `**`, a fence, half a table.

- A launch opens its worktree on the worktree pool. `setup_worktree_for_branch` can take tens of
  seconds, and the launch reply waits for it. Every launch pays that cost, including a reopen.
  Other clients are served meanwhile.
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
- An agent that stopped before the server started is not in the world, so its node card offers no
  Resume. `mael agent resume <id>` is the only way to bring it back.
