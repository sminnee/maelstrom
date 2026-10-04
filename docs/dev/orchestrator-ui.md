# The orchestrator UI

A web app that shows every agent as a node on one canvas and captures the user's checkpoints in the
tool. It lives under `orchestrator-ui/`. It reads the world from the orchestrator server's REST
routes, hears what changed on one change-notice stream, and follows each open agent's transcript on
a socket of its own.

The guiding metaphor is a real-time strategy game. Everything running is on one canvas, and a
unit that needs orders shows it on the canvas itself.

## The layers

`orchestrator-ui/src` is four layers. Each one imports only from the layers below it.

| Layer | Directory | Holds | Imports |
|---|---|---|---|
| Protocol | `protocol/` | The entity and transcript types, `phase.ts`, `deskId.ts`, `time.ts`, and the hand-kept mirrors of Python rules — `planningLevel.ts` | Nothing |
| Backends | `api/`, `live/` | `api/`: the REST client, its query keys, the query cache, one hook per read and per command. `live/`: the change stream that keeps the cache fresh, and the per-agent transcript streams | Protocol |
| State | `store/`, `selectors/` | The query cache holds the fetched world; one zustand store holds UI state, the connection state and the open transcripts; `selectors/` are pure functions over a `WorldView` | Protocol |
| UI | `canvas/`, `tasklist/`, `newwork/`, `panel/`, `decisions/`, `session/`, `documents/`, `shell/`, plus the `ui/`, `markdown/` and `styles/` they share, `fake/` for the fake server and its scenarios, and `test/` for shared test helpers. `ui/useRetained.ts` holds unsubmitted text in the browser | React components and CSS | State, Protocol |

The protocol has no React and no I/O. `protocol/phase.ts` reads a task's phase from its
`command` and decides whether a task is actionable. The phase is never sent on the wire, so this
is the one place the reading happens. An unrecognised command reads as no phase, so a typo in a
task's frontmatter shows as a node with no phase rather than one claiming a phase it never had.

`protocol/time.ts` writes a moment two ways. `ago` gives the age of one — `<1m`, `7m`, `2h`, `3d`
— for a live status; `clockTime` gives the time on the clock, for a transcript. Both take `now`
as an argument rather than reading the clock, so both are pure and a test pins the answer. An
unreadable stamp gives an empty string, and the caller draws nothing rather than a false age.

`protocol/workCalendar.ts` measures an interval rather than writing a moment: how much working
time falls inside it, once nights and weekends are weighted down. The seven-day usage window
reads its elapsed time through this, so a budget does not recover overnight. It takes the zone
by name — `Pacific/Auckland` — rather than reading the runner's, because a reset is a zone-free
instant while "8am" is a wall clock, and a browser's zone would make the reading depend on where
the laptop is.

`ui/useNow` is the clock those two are given. It is one 30-second interval shared by every age on
screen, not one timer per card: an age has to advance without a message arriving, and a desk of
forty cards should not cost forty timers. The interval stops when the last age leaves the screen.

`protocol/progress.ts` decides how a node draws. One call to `progressOf` gives the state, the
state in words, and the drift between the task file and the agent observed on it. State and
words come from one traversal, so they cannot disagree. The drift kinds mirror `reconcile()` in
`task.py`, with one deliberate divergence: an agent on a closed task carries no mark.

### The life of a task

A task's node reads from two fields at once: the notebook status, and the agent observed on it.
The ordinary run through them, in order:

| Node reads | Task status | Agent | Zone |
|---|---|---|---|
| Queued | `todo` | none | not started |
| Ready to launch | `todo`, actionable | none | not started |
| Working | `in-progress` | processing | running |
| Subagents working | `in-progress` | delegating | running |
| Background tasks working | `in-progress` | background | running |
| Needs you · … | `in-progress` | awaiting, with an open attention item | running |
| Unanswered | `in-progress` | idle, with a last message | running |
| Idle | `in-progress` | idle, with no message | running |
| Finalising | `done` | still running | running |
| Done | `done` | stopped, or none | done |

`Finalising` is the step people miss. The task-completion flow pushes the PR, closes the task,
then runs `watch-pr` to take CI green — so a `done` task with a live agent is the normal tail of
the work, not a disagreement. It stays in the running zone, because the work is not settled
until CI is, and it moves to the done zone when the agent stops.

Two branches leave that line. `blocked` and `cancelled` read as themselves. And a status that
disagrees with the agent is drift: `in-progress` with no agent, or `in-progress` whose agent has
stopped. Drift draws an amber caret and names both values on the card. It never counts as
attention, because nothing is waiting on the user.

A stopped agent draws recessed and says `Finished`, and on an `in-progress` task the card offers
`done` as the fix. It stays in the running zone all the same: the session is resumable, so the
work is unfinished rather than history.

The card offers a fix only where the client can be sure of one. `finished` offers Mark done, and
`orphan-session` offers Mark in-progress. `never-ran` offers none: the client reads it from the
absence of an agent record, and a server that has just restarted has no record of an agent that
ran before it — so finished work can present this way, and `todo` would send it back to the
queue. The wire fix is a `hasRun` flag on the task row, from `has_claude_transcript`.

## The API

The server is the source of truth, and the app reaches it three ways.
[orchestrator-server.md](orchestrator-server.md) documents each.

**Reads are REST.** `api/http.ts` is the client: every failure is an `ApiError` with a code —
the server's, or `transport` and `timeout` for a request that got no answer. One hook per
resource (`api/tasks.ts`, `api/agents.ts`, …) wraps one GET; `api/useWorld.ts` composes the
seven list queries into a `WorldView` (`selectors/world.ts`): the tables keyed by id, with tasks
and documents as slim rows. A view that needs the prose fetches the detail: the editor fetches
its task, the document tab its document, and a decision fetches the agent's detail, which
carries the request it waits on.

**Changes are notices.** `api/queryClient.ts` sets the cache so nothing goes stale on its own;
`live/changeStream.ts` follows `GET /api/events` and invalidates what each notice names — the
list and the detail of every id a `task`, `agent` or `document` notice carries, the one key for
the rest — and a `reset` invalidates everything. TanStack refetches only the queries something
is showing, so a reset costs one GET per list on screen. The connection state lives in the
store, and `shell/ConnectionBanner.tsx` shows "Connecting…" before any data and "Reconnecting…
showing the last known state" once there is some. `shell/HostBanner.tsx` is its twin for the far
end: `api/host.ts` reads `GET /api/host`, and when the server says the agent host has stopped
answering the banner says since when, that the agents on screen are the last known ones, and
which command brings the host back. The agents themselves stay as they were — the server never
exits one for the host being away — so a daemon restart is a banner, not a canvas of exits.

**Transcripts are sockets.** `live/agentStreams.ts` keeps one socket per agent however many
views show it: a view acquires the agent through `useAgentStream`, the socket opens on the first
acquire and closes five seconds after the last release, so a tab that closes and reopens keeps
its stream. The opening frame is a snapshot, or a replay when the reconnect carried a cursor the
server's ring still held; every later frame is reduced by the pure `live/transcriptReducer.ts`
into the store's `transcripts` slice and moves the cursor. A `transcript.partial` has no seq:
`applyPartial` sets the text of a partial message and leaves the cursor. A drop reconnects from the cursor
with a doubling wait, a `4409` at once, and a `4404` ends the stream. Nothing closes every
stream at once, so a provider that unmounts and mounts again comes back with its streams
intact.

A sent message shows at once, before the daemon's own echo of the turn arrives: `sendLocal`
appends a `pending` stand-in item straight to the store, and the append/snapshot/replay paths
each drop the oldest matching stand-in once the real, non-pending item lands.

**Commands are mutations.** One hook per command in `api/` — `useApprove`, `useLaunch`,
`useSetStatus`, `usePutOnDesk`, … — over one POST, PATCH or DELETE. Its `mutateAsync` resolves
with the result, or rejects with an `ApiError` carrying the code. On success the hook
invalidates the keys the command touched; the change notice invalidates them again a moment
later, so the screen is right while the stream reconnects too. `useCreateWorktreeTerminal` is
the exception: it writes its reply into the cached worktree, so its control turns into a link
before the notice arrives. The launch call waits two minutes: the server opens the worktree
first.

Every control that sends a command is an `AppButton` (`ui/AppButton.tsx`), and the button owns
what happens next. A handler that returns a promise puts the button in `processing`: disabled,
busy, with a spinner. A rejection puts it in `error`: it reads "Failed", the message is its
`title`, and a click retries. It is ready again after three seconds. So a refusal shows on the
button that asked, and no view keeps an error of its own. The status picker
(`ui/StatusPicker.tsx`) is the one row control that is not a button; it shows its own refusal.
The task list's bulk bar also keeps its own error: a run over many rows can partly fail, and
"Failed" on one button cannot say which rows or why. The
comment and review controls call their mutations, get the server's 501, and read
"Not implemented yet".

**Loading and errors.** `useWorld` is `loading` until the six tables the canvas draws from have
data, so the canvas never draws nodes without lanes: it shows "Loading the world…" and the task
list "Loading…", never "No task matches". A required table that fails makes it `error`, and
both views show the message with a Retry. Once every table has data, a refetch that fails keeps
the data on screen. TanStack's structural sharing keeps an unchanged table the same object, so
a refetch that changed nothing re-renders nothing.

The `desk` is its own resource: one entry per task and per free agent on it, carrying the entry's
id and when it arrived. An id names its kind — see `CONTEXT.md`, "Desk" — and
`protocol/deskId.ts` builds and splits one. The server adds an entry for every agent it sees
running, so a task launched from the UI joins the desk as well.

## The canvas, the task list and the panel

The canvas is where the user decides. The panel is where the user reads. The task list is where
the user chooses what the canvas draws. The worktree table is where the user manages the
worktrees themselves.

The canvas draws a node when it is on the desk, or it has a live agent. The liveness half is
what makes running work always visible: an agent shows the moment it starts, before the server's
own desk entry arrives. It opens near-empty against the real server, because the world holds
about 700 tasks across every project and most of them are finished. The task list lists every
task with filters for status, project, branch and text, and each row toggles that task on or off
the desk. The top bar shows Desk, Tasks, Worktrees and Tabs; see "The three layouts". There is one
filter bar, and it draws the controls of each main view on screen. Project applies to all three
main views and is always drawn.
Branch applies to Desk and Tasks only: its options are built from tasks, so a worktree on a branch
no task names would silently vanish from a table meant to show every one of them. Desk has an
Agent status control, Tasks has status and text controls, and Worktrees has "show closed".

`View` is a union nothing switches on exhaustively; its docstring in `store/uiSlice.ts` lists the
sites to edit by hand when it widens.

### Which PR a task shows

A pull request belongs to a branch, and a branch outlives the session that made its PR. After a
merge, every later task on the branch resolves to the merged PR, including tasks not started. So
the node, the deck row and the expanded card show a PR only where the task's own session could
have made it. `selectors/cardPr.ts` holds the rule:

| The task | Shows |
|---|---|
| In the not-started zone | No PR |
| It started after the PR merged | No PR |
| It started before the PR merged | The merged PR |
| Its branch has an open PR, and the task has started | The open PR |
| Finished, with no start time | The merged PR |

An open PR is never wrong: a branch has at most one. The rule compares the worktree's
`prMergedAt` with the task's `startedAt`, else the card's agent's `startedAt`.

A task's `startedAt` is when its first agent started. The task source reads it from the Agent
records, ended ones included, matched to the task by task session id. So a finished card, whose
agent has left the world, keeps it, and a task run again after its PR merged still shows that PR.
The source folds in only the records written since its last read, and sends a task again when its
first start appears. The agent's start stands in only until that read.

Both times come from Agent records, not the daemon, because a resume resets the daemon's start
time. That reset would hide the agent's own PR. The worktree table does not use the rule: it is
about the branch, not a session.

The rule has one known gap. An agent the orchestrator adopted rather than started has the
adoption time as its start, so a PR that merged before the adoption is hidden.

### The worktree table

`worktrees/WorktreeTable.tsx` draws every worktree, grouped by project, one table per project.

It is the only surface that shows a closed worktree, and the only one that deletes one. The
canvas draws each open worktree as a **Worktree box**, and the box's label opens the same sync,
environment and close controls. `selectors/worktrees.ts` reads the rows from the world, so a
worktree with no node still draws.

The columns are worktree, branch, dirty, local, remote, PR, app and agents. "Remote" is `prCommits`
once a pull request is open and `pushedCommits` before one is, which is what commits waiting on the
remote means for a branch waiting on a new PR — `cli.pr_display` reads the same two fields, so the
table and the terminal give one reading. "Agents" is the world's agent rows joined by `worktreeId`,
minus subagents and exited rows; it is not `sessionCount`, which is a process sweep and counts a
shell as readily as an agent.

A closed worktree is listed only when "show closed" is ticked, and reads as parked. `_main` sorts
first, because it holds the branch the others are cut from.

Each row carries its operations. Sync, the environment control and the close control are split
buttons. The close control is `worktrees/CloseControl.tsx` — see "The worktree area". Delete is
`ui/ConfirmButton.tsx`, because it asks before it acts. `_main` is offered neither close nor
delete — it holds the main checkout — but it still syncs.

The sync control is `worktrees/SyncControl.tsx`. Its main segment, "Sync", sends `plain`,
so a conflict aborts the rebase and leaves the worktree as it was. Its menu adds "Sync & squash"
(`squash`) and "Sync & autorepair" (`autorepair`, which starts a repair session on a conflict).
`_main` gets "Sync" alone. The chevron is named "More sync actions", apart from the other
split buttons' "More actions", so a row with two menus stays readable to a screen reader.

The merge control is `worktrees/MergeControl.tsx`. It draws "Merge" only when the worktree's
**PR state** is `ready` and the pull request is not a draft. It is a `ConfirmButton`, because a
merge cannot be undone. `WorktreeCommands` draws it first, so the expanded node, the narrow
layout's pushed screen and the panel's worktree bar all carry it. The Worktrees table does not.
GitHub's refusal shows as the confirming button's title, and the question stays open.

The environment control is `worktrees/EnvControl.tsx`, a split button over the env state; its
options are listed in [the guide](../guide/dev-environments.md#the-environment-in-the-orchestrator).
The expanded card draws both controls when its task has an open worktree.

The task list opens on `todo`, `in-progress` and `blocked`, for the same reason the canvas opens
near-empty. Ticking `done`, `cancelled` or `template` brings that work back; unticking every
status shows every task.

The task list also writes. A row's status is a button until it is clicked, then a native select
of the six statuses. The expanded card carries the same control, at the right end of its state
strip, so a decision taken on the canvas does not need the list. Both use `ui/StatusPicker.tsx`,
which says why the select is native. Choosing a status posts the new one, and a refusal shows
beside the control. A click anywhere on a row opens the task editor, which holds title, status,
content and branch, with command, mode, base, priority and model under a folded "Advanced". A
task with an agent has a panel link in its state cell, to the agent's session. The row's click
guard skips links, so that link does not open the editor. The
title cell carries a real button, because a table row reaches no keyboard. The dialog opens
read-only with Edit, Delete and Close; Edit unlocks the fields and restores Cancel and Save. Every
read-only or disabled field draws with a transparent background and a fainter border, the one
global rule in `base.css` rather than a per-field affordance. A read-only dialog has nothing
unsaved, so it always closes at once. Delete asks first, from the dialog and from the row alike.
Save patches the changed fields, and a changed status goes out through the same route the list's
own picker uses rather than the batched PATCH, since status is folder-derived. The header also
carries Prev/Next, which step to the adjacent task in the list's own filtered, sorted order,
disabled at either end. Both go through the same unsaved-changes guard as the ×, Escape and the
backdrop. The editor renders from `AppShell`, above both views, and its open task lives in the
store, so the canvas can open the same editor later.

Each row also has a checkbox, and the header has one that ticks every listed row. When a row is
ticked, a bar above the table offers three bulk actions: set a status, On desk, and Off
desk. The server has no batch route, so `tasklist/BulkActions.tsx` calls the per-task routes
one at a time: each status write re-reads the notebook, and parallel re-reads race. A row already
in the target state sends nothing. A refused row does not stop the others. The bar then shows
the failed count and the first reason, and keeps only the refused rows ticked. The ticked set is
local state in `TaskList`, and holds listed rows only: a row the filter hides is unticked, so the
bar never acts on a row the user cannot see.

A node is one of two kinds. A **task** node stands for a notebook task, with or without an
agent. A **freeAgent** node stands for an agent with no task, and takes its title, branch and
lane from the worktree it runs in. A `mael add --daemon` session is exactly that: the
launch starts a driven agent and passes no task, so the session draws as a freeAgent node. An
agent linked to a task draws as that task's node, so nothing appears twice. Edges come from
`task.follows`, so a free agent is never an endpoint. A subagent, an agent with a `parent`, is
never a node's agent and never a node: it is reached through its parent's session tab.

The canvas also rewires. Each node carries two anchors, hidden until the node is hovered or
focused and lit on every node while a wire is dragged, and a drag between them writes the
target's `follows` — the direction an edge is built in, followed to follower. A drag across projects or onto the node itself is declined before it lands, and the
server refuses the same pair again along with a cycle. Nothing is optimistic: the wire appears
when the refetched world carries it, so a refusal simply leaves the board as it was. `follows` is
the one field the canvas writes that the task editor does not, so it is not part of the editor's
draft. Hovering a wire draws it thicker and darker, and offers the button that cuts it, which
writes the target's `follows` without the id.

The board draws fewer wires than the notebook holds. `canvas/reduce.ts` drops an edge that a path
of unfinished work already implies: when C follows both A and B, and B follows A and has still to
finish, B gates C on A's behalf and A→C says nothing new. Only `done` releases a follower, the
rule `is_actionable` applies, so a cancelled intermediate still hides the edge and a done one
brings it back. The reduction is display-only — `layoutSwimlanes` takes the full edge set, so no
column moves, and `follows` on disk keeps every id. A hidden wire cannot be cut from the board,
which is the accepted cost.

The expanded card's Now block shows the agent's note when it wrote one, and its last message
otherwise: a note is the agent's own account of its work, where a last message is whatever prose
ended a turn. The block is still dated from the last message, never the note, because the age
drives the silent-agent colouring and silence means the agent said nothing. An agent that noted
once would otherwise look alive for ever.

An unanswered node replaces the one line with a box: the agent's recent messages under the
heading `Last said`, then a reply field. `recentMessages` in `selectors/transcript.ts` selects the
messages: the last three agent messages, of any rank, with no tool calls.
`session/RecentMessages.tsx` draws them, and a decision shows the same list. Until the transcript
arrives, the box shows the agent's `lastMessage`, which the server cuts short.

The reply field is the session tab's `MessageInput`, drawn `inline`. `useSendMessage` in
`session/useSendMessage.ts` gives both surfaces the same send. Each holds its unsent text under
its own key, because both can be mounted for one agent. A reply starts a turn, so the node reads
`working` and the box goes.

Under the status band the card shows the stage the agent last reached, its cost, and how long ago it
closed. The latest stage only, and nothing when the agent has reached none. See
`orchestrator-ui/DESIGN.md`, "Node Card".

The band reads `GET /api/agents/{id}/milestones`, not the transcript's bars. A restarted server
keeps the ledger and drops the transcript, and the host's window rolls the older bars away in a
long run, so the card would go quiet on exactly the agents that have been running longest. A bar
arriving on the transcript is still the live signal: nothing about the ledger moves the world, so
no change notice fires, and the count of bars is what refetches the route.

A task node's card lists every task its task follows and every task that follows it, direct and
indirect, nearest first (`selectors/follows.ts`). Each row has an On desk or Off desk
button. The canvas draws a follows edge only when both ends are on the desk, so without this list
the user must find each related task in the task list. The list reads `world.tasks`, which
holds every task, so it needs no route of its own.

The task list lists tasks only. Every node card ends its commands with one end-of-work control,
`ui/SplitButton.tsx`. A click on its label runs the usual act, and its chevron opens the longer
chains:

| Node state | Click | Menu |
|---|---|---|
| Live agent | Terminate | Terminate · Terminate & take off desk · Terminate, take off desk & close `<nato>` · Terminate, take off desk & trash `<nato>` |
| No live agent | Off desk | Off desk · Take off desk & close `<nato>` · Take off desk & trash `<nato>` |

Every desk act draws a desk icon before its label (`shell/OnDeskIcon.tsx`,
`shell/OffDeskIcon.tsx`):

- The arrow points down for On desk and up for Off desk.
- The arrow takes a hue by direction. See the Reporting Rule in `orchestrator-ui/DESIGN.md`.
- A disabled menu item mutes the arrow with its text.
- In the menu, each item that takes the node off the desk draws the Off desk icon. Plain
  Terminate draws none.

The close and trash items are left out when the worktree is `_main`, is closed, or does not exist.
With one item left, the control is a plain button. The close and trash items are disabled while
another top-level agent runs in the same worktree, and the second line says how many. A subagent
is not counted, because it stops with its parent.

The trash item asks first, beside the control, because it closes a PR and renames a branch. Only
the confirming answer sends anything. A `SplitOption` with `confirm` draws the question, in the
style of `ui/ConfirmButton.tsx`, and holds both segments while it is open.

A chain with a close or a trash sends that command first and no stop, because the server stops
every agent in the worktree. That command is also the step that can refuse: a close on a dirty
tree or unmerged commits, a trash on a stacked branch or an existing `trash/<branch>`. Sent
first, a refusal leaves the node on the desk, and the control shows the reason in its title. A
live node can draw with no desk entry, so a chain skips Off desk when there is none to take.
"Terminate & take off desk" sends the stop first: `agent.stop` records the exit before it
replies, so the node can go off the desk at once.

The session head in the panel draws the same control, from `session/AgentControls.tsx`. There Off
desk also closes the session tab. The agent's document tabs stay open.

A node shows the bare notebook id, because its lane already names the project. A panel tab
shows the bare id too, because the panel sidebar beside the strip names the project.

An agent with no task shows its own agent id in that same slot, on the node and on the tab both —
the **Failover id** rule, which `CONTEXT.md` defines.

Clicking a task node expands it in place, showing the state in words ("Needs you · plan
review", never a raw agent state). The state strip also carries the task's notebook status, which
the user can set from there. Where the words would only restate the status — `done`, `cancelled`,
and `blocked` with no agent — the card drops them and the status stands alone. The collapsed node
keeps them: it has no status control, so there the words are the only reading. A free agent has
no task, so its card has no status control. Esc, the close button and a click on the canvas
collapse it — but with the status picker open, Esc closes the picker only.

The attention chip reads the drawn nodes, so the chip and the canvas show one reading. Its count
is the number of nodes in state `needs-attention` under the current filters, and a click expands
the next one. `selectors/attention.ts` orders them by each node's best-ranked open item: plan
reviews, then document reviews, then questions, then permissions, then the rest, oldest first
within each. A task off the desk with no live agent draws no node, so the chip does not count it.

The chip shows a second, yellow count when it is above zero: the nodes in state `unanswered`. A
click visits those after the `needs-attention` nodes, oldest last message first. `nodeState` in
`protocol/progress.ts` derives the state, so no attention item exists for it.

The session tab head carries a mode chip naming the agent's permission mode. A click moves the
agent to the next mode: plan, then auto, then normal. The chip shows the mode the child last
announced, so a refused change leaves it where it was.

Beside it on the same row — under it once the panel is dragged below 30rem — the head names the
worktree, the branch, the model, how full the context is and what the session has cost. An empty
field drops out. This matters most for a free agent: it has no task, so its own transcript is the
only place that says where it runs. The turn lines in the transcript carry no money: their
`costUsd` is the session's running total rather than the turn's, so the header says it once
instead.

The size is `contextTokens`, what the prompt last held, not the cumulative `totalTokens` — see
`docs/dev/agent-daemon.md`, "A turn", for why the cumulative figure cannot answer whether to
compact. It falls when the agent compacts.

A Stop button at the right of that group sends `agent.interrupt` — see `CONTEXT.md`,
"Interrupt". It is offered only while the agent is `processing` with no ask open. A waiting
agent's button is disabled, and its title sends the user to the ask, because an interrupt would
deny that ask and the route's reply would not say so. A question prompt offers that interrupt
itself, as **Decline & stop**, where the label says what it does to the ask. An exited agent's title says it has gone.
The node card's Terminate is the other act: it sends `agent.stop`.
Once the agent has exited, the node card offers Resume beside its Off desk. Resume sends
`agent.resume`, as `mael agent resume <id>` does, and it covers a terminated agent and a crashed
one.

A Compact button at the right of that group sends `/compact` to the agent. This is a `say`, not a
command of maelstrom's own: a slash command reaches Claude Code as the text of a user turn. The
button is disabled unless the agent is idle with nothing pending, because a compact sent mid-turn
queues behind the work. A subagent gets neither the line nor either button: it has no
session, no worktree and no pipe of its own.

The button stays busy until the compact ends, which `awaitCompact` decides. The `say` resolves
when the server accepts the relay, and the relay is all that route does — a compact runs for
10s–130s after it. So the wait watches the transcript, and settles four ways:

- The boundary arrives. The button clears.
- The turn ends without one. That is the refusal path, and it reports an error.
- The agent exits. It appends no transcript item when it goes, so the wait reads its state.
- Five minutes pass. Past that a spinner lies rather than waits.

See `docs/dev/agent-daemon.md`, "A compact", for why the refusal cannot be read any other way.

The items already on the transcript are remembered by id, not by count. A re-snapshot, a dropped
transcript and the host's own ring each renumber a positional marker.

The tab takes the last 50 events. A button above them reads "Show 50 earlier events" and adds
another 50, so the first render is capped at 50 cards and each reveal adds that many again. The
server keeps 5000 (`TRANSCRIPT_ITEMS` in `transcript_log.py`) and sends them all, which is cheap.
Drawing them is not: every message parses markdown, and every tool call builds a disclosure.
Reading old events is occasional, so a long session opens on the recent ones.

The button names what a click reveals, not what is left behind it. A button offering 3000 would
promise a reveal it does not perform. It counts *events*, the word the two existing notes use.

That button is not the truncation note above it. "Earlier events were not kept." means events the
host dropped, which exist nowhere. The button means events that exist, one click away. A `gap`
item's "N earlier events were dropped here." is the third: events the host dropped mid-stream,
named where they fell. Both notes can show at once, and the lost ones read first because they are
older.

`gutterMarks` runs over the drawn window rather than the whole session, so the first visible row
takes a time mark wherever it carries a readable stamp, even where the row above it in the full
transcript held the same minute.

The window anchors on the id of the oldest revealed event, not on its index. A count of drawn rows
would shrink the window from the top on every append, and an index does not survive a re-snapshot:
the server drops from the front of its own list past 5000, and a lagging reconnect replaces the
array outright. An anchor that is no longer in the transcript falls back to the tail.

The transcript follows the tail only when the reader already sits at it. The scroll event measures
that into a ref, rather than the render path, where the new event is already in the layout and
every reader would measure as being at the bottom. It allows a few pixels of slack: sub-pixel
rounding leaves a fully scrolled container a fraction short, and an exact test stops the
transcript following. A new event does not drag a reader away from the history they are reading.

A partial message is a `message` item with `partial: true`; see "A partial message" in
[orchestrator-server.md](orchestrator-server.md). The session tab draws it as follows:

- `AgentMessage` marks the card `data-partial`, and the stylesheet draws a caret at the end of
  its last block.
- The scroll effect also keys on the length of the partial message, because it grows and adds no
  item. It is not always the last item: a message sent while the agent writes lands after it.
- `MarkdownContent` is memoised on its source, so an unchanged card skips the markdown parse.
- `recentMessages` skips a partial message, so the node card and the decision card show whole
  messages.
- A message that opens with `<user-attention low>` grows inside a quiet block clamped to two
  lines, so most of its growth is hidden.

The scroll effect keys on the whole transcript's length, never on the drawn slice's. Keying on
the slice would scroll the reader to the bottom on every Show more, which is what the click asked
to leave.

Prepending a window of rows leaves the reading position to the browser's `overflow-anchor`,
which is `auto` by default and made for this case.

The tab is keyed on its agent id, as the document tab is. It holds a scroll position, a window
anchor and a pending compact wait, and all three belong to one agent: a reused fiber opens the
next agent at the last one's state.

The transcript draws a full-width rule at the boundary, naming the fall: `compacted · 23k → 3k
ctx`. The tool row deliberately carries no rule, because a run of them read as ruled paper. That
holds where a rule falls on every call. A compact happens a handful of times in a session, and a
boundary is the one thing a rule is for.

A milestone takes the same rule, in `--ok`: `built · 95k · $2.10`. The figures are the stage's
own delta, and a name the flow does not declare keeps the compact register and gains `(?)`. See
`orchestrator-ui/DESIGN.md`, "Milestone bar".

Two turns the harness injects follow that rule. The summary it writes to carry the conversation on
folds under "carried over", the way a loaded skill body does: it runs to thousands of characters,
and read as an ordinary turn it buries the boundary above it. The host's own
`<local-command-stdout>` echo is dropped, because the rule already reports the compact.

A session tab on an agent with subagents draws a strip under the transcript: one link per
subagent, with a state dot that pulses while it runs, its description, and what it waits on when
it is blocked. A blocked subagent says so here rather than in the
parent's stream, so the ask sits beside the subagent that raised it; the decision itself is made
on the parent, whose pipe takes the reply. The link opens the subagent as a session tab of its own, `session:X.1`. That tab is
the same component, read-only: it heads with the id and the description, and has no message
input, no mode chip and no decision handlers, because a subagent's asks are answered through the
parent.
Opening the tab opens the transcript socket, which is what makes the server attach to the
subagent; closing it releases the socket after the usual 5-second grace, and the server detaches.

The strip lists the running subagents only, because it says what is happening now, and the strip
goes with the last of them. A fold under it counts the finished ones and opens to the same links.
The strip is the only way into a subagent's tab, so the fold is what keeps a finished subagent's
transcript reachable; it stays closed and unadorned, because an escape hatch must not compete
with the running work above it. The state is read as it stands, so a subagent that speaks after
its notification comes back to the strip — see [agent-daemon.md](agent-daemon.md).

Every tool card starts folded. The summary line names the tool, its title and its status, and a
click opens the body. An agent that makes hundreds of calls is a list, not a wall of text. A
loaded skill folds the same way, under the skill's name. A shell command draws with the same
card a `Bash` tool call gets, because a `!` line and a `Bash` call are the same thing to the
reader — see [orchestrator-server.md](orchestrator-server.md#a-shell-command). A task
notification draws as a bare line instead of a fold, because the fold keeps only its status and
summary and leaves no body to hide — see
[orchestrator-server.md](orchestrator-server.md#a-task-notification).

A decision shows the last three things the agent said, then the prompt. A question
follows AskUserQuestion's shape; `session/cards/QuestionPrompt.tsx` says why every answer
sends together. A split button at the row's right end refuses the question — see `CONTEXT.md`, "Decline".
Decline sends `agent.deny` with a fixed reason. Decline & stop sends `agent.interrupt`. A permission shows the tool input with Approve and Deny. A plan review links
to the plan with Approve and Deny. Both use one control, `session/cards/DecideRow.tsx`. Deny
sends the reason as the agent's tool result, and the
agent carries on with it. The expanded node and the document tab render the same
`DecisionCard`, so the two agree. A `variant` prop says which surface it draws on: `block` is the
card, where the decision is read and the context rail is inline and open; `dock` is the band
under a document, where the context becomes a control and the prompt loses its own border. See
`orchestrator-ui/DESIGN.md`, "Review Dock".

A prompt reads one of three ways: open, answered, or stale — see `CONTEXT.md`, "Stale prompt". A
question has a fourth, declined, which reads as answered — see `orchestrator-ui/DESIGN.md`,
"Question". The transcript keeps a stale prompt, showing what was asked and reading "no longer pending", with no
buttons. The expanded node and the document tab drop it, because both draw from the agent's
pending request and that is now clear. A plan document's review bar is the exception: it reads
the document, so a stale plan review takes the document to the `stale` status to close it. The UI
never works out which of the three applies: the server marks the item stale and takes the request
off the agent row.

One request has one live prompt. The expanded node owns the prompt while it is open on the
waiting agent, because the node is where the user makes small adjustments. The session tab then
echoes the wait, showing what was asked and reading "Answering on the canvas", with no buttons.
With no node expanded, or one expanded on another agent, the session tab owns the prompt and
carries its controls. `selectors/transcript.ts` decides which surface owns the prompt.

The call that raises a wait draws no card. `AskUserQuestion` and `ExitPlanMode` classify as the
`wait` kind, and the transcript gives them no row: the wait item that follows renders the same
prompt in full.

The panel holds three tab kinds: session, document and changes. It is the top bar item `Tabs`, and
it shows in a slot as a main view does; see "The three layouts". A panel off screen is hidden with
the `hidden` attribute; `shell/AppShell.tsx` says why not an unmount. A panel link opens a tab, and
shows the panel too; `shell/PanelLink.tsx` says why links, not buttons. Every session and document tab carries a phase chip and
its task id, so two agents' tabs are told apart. A node card lists every document its node has,
whatever raised it — a plan review, or a tag the agent wrote in its own message. It lists them under a heading per
kind — Plans, Verifications, then Other — and by review group under each heading
(`selectors/documents.ts`): a group of one is one row, and a larger group is its title and status
over a link per member. A `superseded` member is left out. A member's tab shows
its place in the group, `2 of 3`, with links to its siblings, so each file reads on its own.

The panel groups its tabs by worktree. `panel/PanelSidebar.tsx` lists the groups under their
projects, and the strip shows the tabs of the group in view. `groupTabs` in `selectors/tabs.ts`
derives the groups from the open tabs on each render, so a group holds no state and shows while
one of its tabs is open:

- A session tab belongs to its agent's worktree.
- A document tab belongs to its own agent's worktree, else to the worktree of the agent that runs
  its task.
- A changes tab belongs to its own worktree.
- A tab the world cannot place goes to its project's "no worktree" group, or to "Other".

The group in view is the active tab's group, so no selection is stored. `ui.tabRecency` holds tab
keys, most recently active first. Selecting a sidebar row activates that group's most recent tab.
When the active tab closes, the most recent tab left in its group takes over, else the most
recent tab left anywhere. The group comes first because a tab from another group would switch
the sidebar under the reader. A row's close button closes every tab in the group.

`panel/WorktreeBar.tsx` sits above the strip and draws the group's worktree controls: the
`worktrees/WorktreeControls.tsx` pieces and the close control, as the worktree area does.

The same links row carries external links, which open a new browser tab instead of a panel
tab. `shell/ExternalLink.tsx` is the control, and its arrow-leaving-a-box icon is the whole
difference a reader sees. The wire carries a ready `prUrl`, so the card links a pull request
without joining two fields; a worktree with no PR draws none. `worktrees/DevEnvLinks.tsx` draws
a link per running web-facing service, and the worktree poll makes each appear and disappear on
its own.

### The worktree area

Five things belong to a worktree and not to an agent: the branch, the changes, cmux, the
environment and sync. `worktrees/WorktreeSection.tsx` draws them as one area: a head, the name
and branch, the links, then the commands. The node card ends with this area, and the Worktree
card is this area under a header. Both draw the one component, so the two cannot drift.

`worktrees/CloseControl.tsx` is the area's close: a split button with three options.

| Option | Sends | Asks first |
|---|---|---|
| Close | `worktree.close` | no — the server refuses unmerged work |
| Shelve | `worktree.forceClose` | yes |
| Trash | `worktree.trash` | yes |

A close stops every agent in the worktree. The control is therefore held while an agent runs
there, and each item says so. The node card's Terminate chains are the way to end live work. The
control is absent on `_main` and on a closed worktree.

### The Worktree card

`canvas/WorktreeCard.tsx` opens at the top-left corner of the box whose label was clicked. It
adds two things to the worktree area: a header with the name, the project and a collapse, and
Start free agent.

Start free agent opens the new-work form with a seed of kind, project and branch, held in
`ui.newWorkSeed`. The form lays the seed over its held draft and keeps the prose. It then drops
the seed, so a second mount of the form does not undo what the user changed. The seeded fields
stay in the held draft after a cancel, as typed fields do. The
server reuses the open worktree on that branch, so the agent starts in the worktree the card
stands for. A detached worktree has no branch to start on, so the button is disabled and says
why.

`canvas/CanvasCard.tsx` is the shell both cards share: the viewport portal, the grow animation,
the pan into view and the Esc handler. The canvas shows one card at a time. `ui.expandedNodeId`
and `ui.expandedWorktreeId` clear each other, and Esc or a click on the pane clears both. A
card whose node or box no longer draws collapses, once the world has loaded.

### The Changes tab

`changes/ChangesTab.tsx` draws the **Changes tab**. The expanded node's `Changes` link opens it
while the worktree is open. The tab chip names the
worktree by its id, `<project>-<nato> changes`, because every project has a `delta`. It draws
no phase, because a worktree has no task of its own.

A strip beside the diff chooses the rev. It lists one entry per commit, oldest first, then "All
commits" and "Uncommitted". The server sends the commits in that order, and the tab does not sort
them. "All commits" draws when the branch has commits, and "Uncommitted" draws when there are
**Dirty files**. With neither, there is no strip. The tab opens on the first commit, or on
Uncommitted when the branch has no commits. A picked entry that is no longer drawn gives way to
that default.

Under the revs, the strip draws the files of the diff in view as a tree. `fileTree` in
`changes/tree.ts` builds the tree, and `changes/FileTree.tsx` draws it. A directory shows a folder
icon, `shell/FolderIcon.tsx`, which is shut or open. A click on a directory opens or shuts it, and
a click on a file scrolls the diff to that file. The open directories last for one rev: the tree
is keyed on the rev, so each rev opens with every directory shut.

In a panel narrower than 40rem, the strip stacks above the diff and hides the tree. The strip is
short there, and the file list in the scroll already jumps to a file. A container query sets
this, because the panel's width, not the window's, decides.

The scroll holds five parts, in this order:

| Part | Content | Sticks |
|---|---|---|
| Title line | The commit's subject, then Prev and Next | Yes, at the top |
| Commit message | The body and the author. The body is drawn as Markdown, which joins git's hard wraps | No |
| Stats line | The file count and the line totals | Yes, under the title line |
| File list | One entry per file, which jumps to that file | No |
| Files | Each file as a card, with a head | The head, under both lines |

Uncommitted and All commits name no single commit, so they draw no title line and no commit
message.

Prev and Next step through the commits only. Prev goes to the earlier commit, which is up the
strip. Each button is disabled where there is no commit to go to.

The subject is a button that folds the commit message, and the stats line is a button that folds
the file list. `ChangesTab` holds both fold states, so a fold stays from one commit to the next.
The scroll is keyed on the rev, so each rev opens at the top, and state inside the scroll would
open again.

The title line and the stats line are direct children of the scroll. A sticky element holds only
inside its parent, so a line inside a foldable parent would leave the view with that parent. Each
line has a fixed height, which `--title-h` and `--stats-h` hold. A file head sticks at the sum of
the two, and `scroll-margin-top` lands a jump at the same offset. With no title line,
`--title-h` is zero. The tab's header and the strip sit outside the scroll, so the title line
sticks at zero.

The rows are `ui/DiffRow.tsx`, which the Edit card also draws. The Changes tab adds the old and
new line numbers.

A diff can hold more than 1000 rows, so a state change must not draw them all. `DiffRow`,
`FileBlock`, `Files` and `FileTree` are each a `memo`, and the props they get keep their identity.
`useLineSelection` gives each file one handler object, and each handler takes the row index.

| Event | Rows that draw |
|---|---|
| A key in a comment box | None |
| A drag over a row | The rows whose selection changes |
| A world change, or a fold of the commit message or the file list | None |

A new object or function in a prop of one of these components makes every row draw again. The
"row draws" tests in `App.changes.test.tsx` count the draws.

#### Change comments

The user selects lines, writes a **Change comment** on them, and posts every comment in one
message. The code is in `changes/comments/`.

| Step | What the user does | Code |
|---|---|---|
| Select | Drags down the line numbers, clicks one, or Shift+clicks to extend | `useLineSelection` |
| Write | Types in the box below the last selected row, then **Add comment** | `ChangeCommentBox` |
| Post | **Post comments** in the dock, or **Clear** | `CommentDock` |

Each row's line numbers are one button. The handlers read the row that an event is on and no
coordinates, so jsdom can drive a drag. The window hears the pointer-up, because the pointer can
come up anywhere. Shift extends on the click and not on the pointer-down. The mouse-down that
follows a pointer-down moves focus to the button, away from a box opened that early. A touch pointer is captured by the element it starts on, so a touch drag does
not extend. A tap and Shift+click still select.

A span counts in new line numbers, because those are the lines an agent can open. A selection of
removed rows alone has no new number, so it counts in old ones and the message says `old line`.

The comments and the open box are **Held text**: one set per worktree, under
`retainedKey.changeComments`. The set is not per rev, because one post carries the comments of
every rev. A post that the server accepts removes the comments it sent, and **Clear** removes
everything. A refused post keeps the set.

`comments/held.ts` holds every change to the set, as pure functions. There is one open box, and
it keeps its text until **Add comment** or **Cancel**. A new selection moves the box with its
text. **Edit** is disabled while another box holds text. A post leaves the open box in place.

`placeOf` in `selection.ts` puts a held comment on the diff in view. `FileBlock` passes it only
the comments of the rev and the file in view. It needs the same line numbers, and rows that read
as the quoted lines do. A comment with no place is not drawn. It stays in the dock's count and it is posted, and its quoted lines keep it
readable. Line numbers alone would draw a comment on whatever now has that number.

The dock shows only while comments are held. It names the agents that the post reaches, which
`trackedAgents` selects by the same rule as the server. With no agent in the worktree, the
button is disabled and the comments stay held. When some agents refuse the message, the post
still clears the set, and a line under the diff names the agents it did not reach.

The comment box sits inside a block that scrolls sideways. The block is a size container, and
the box is `position: sticky; left: 0` with a width of `100cqi`, so it keeps the block's width
and its place while a long line scrolls. jsdom computes no layout, so no test covers this.

Diff rows draw in syntax colour, in the Changes tab and in the Edit card, which takes its path
from the tool call's `file_path`. `ui/highlight.ts` holds one Shiki highlighter with the
JavaScript regex engine, so there is no WASM. Shiki's core and each language's grammar are
separate chunks, fetched when the first diff in that language draws. `languageFor` picks the
language from the file's path; a file with no known language draws plain. A hunk interleaves two
files, so the old side (context and remove rows) and the new side (context and add rows) are
highlighted as two texts. A comment or string that spans lines then keeps one colour. The hunk
is all the highlighter sees, so a hunk that opens inside a comment or a string draws wrong.

The theme is Shiki's CSS-variable theme, so each token colour is a `var(--syntax-*)`. The
values are in `styles/tokens.css`, which also sets the light scheme's `--syntax-shade` for
contrast. A highlighted row keeps its add or remove ground, and only its sign keeps the add or
remove hue. The rows draw plain first and the colour replaces them when it is ready. A hunk over
2,000 rows, or with a line over 1,000 characters, stays plain. A failed load leaves the rows
plain and logs a warning, and the next diff tries again.

The two reads are in `api/worktreeChanges.ts`. A diff has no change notice of its own, so
`invalidationsFor` refetches a worktree's changes on its `worktree` notice. That notice comes
from the worktree poll, up to 60 s late, and only when a row field moves: a new dirty file or a
new commit. A second edit to a file that is already dirty moves no field, so the tab keeps the
old diff until Refresh reads it again.

The row ends with `worktrees/CmuxControl.tsx`, the worktree's terminal in cmux. While
`shellUrl` is set it is an `ExternalLink` with `newTab={false}`. With `shellUrl` empty it is a button that makes the terminal, then
follows the returned link. See [cmux.md](cmux.md#terminal-links).

A pull request draws as one chip wherever it appears — a collapsed node, a deck row, the card's
footer — so the same PR reads the same everywhere. `shell/PrChip.tsx` is that chip: `#278` in the
colour GitHub gives the same fact, behind the GitHub mark, linking to the PR. The state is one of
six values the server decides, listed under **PR state** in `CONTEXT.md`, so the UI never
re-derives it from raw GitHub fields. The same worktree poll moves a chip from
amber to green on its own.

`selectors/status.ts` turns the value into words and into a **tone**, the reading a colour stands
for. Six tones cover seven states: merged is `special` and ready is `good` — settled is not the
same as your turn; a failed check and a conflict are both `bad`, because both are the same demand
on the operator. Colour is never the only channel — the chip's `aria-label` and `title` name the
state in words at every size, and the large chip opens to show it.

`ui/HueChip.tsx` is the chip underneath, and it knows nothing about pull requests: a caller hands
it a tone, a word, and the mark of the service it points at. The large chip opens on hover **and**
on keyboard focus to say its state, growing a grid track from `0fr` to `1fr` so no width is ever
named and a long state like `merge conflicts` cannot clip. The word is pinned to the track's right
edge, so it slides in from the left as the track opens rather than unwrapping letter by letter.

The small chip does not open: a canvas node is 220px wide and
clips, and its meta line already truncates four things against each other, so a chip that grew
there would cut the task id under the pointer. Where there is no room to open into, the colour
carries the reading and the word waits behind the pointer.

A node resolves its worktree from its agent first, then from the open worktree on its branch,
which is what keeps a finished task showing its pull request. `selectors/graph.ts` holds both
steps.

The canvas draws one hairline lane per project. The board runs left to right in three progress
zones — done, running, not started — whose boundaries line up across every lane. One strip of labels names
them above the board. `canvas/columns.ts` assigns the zone and the column; it is pure, it sees
one lane at a time. `canvas/rows.ts` packs the rows, also pure and one lane at a time, and
`canvas/layout.ts` aligns the zones, sets the row gaps and derives the boxes.

An agent's wait and the document's own review route share one place: the dock under the document.
`documents/DocumentTab.tsx` renders one `.dock` wrapper and gives it to whichever is waiting.

A **plan** document's verdict is the agent's wait, so the dock draws `DecisionCard` with
`variant="dock"` and the answer goes to the agent. See `orchestrator-server.md`, "Commands", for
why the document's own route is refused.

A **draft** document draws nothing. It is something to read: nothing waits behind it, so
`documents/ReviewActions.tsx` returns nothing rather than a bar refusing a review nobody asked
for. Every other status keeps the bar — `awaiting-review` offers Approve and Request changes, and
the rest read "This version is {status}." One verdict settles the whole review group, so for a
group of more than one the labels count it: "Approve all 3", "Approve and create 3 tasks",
"Request changes on all 3", and "All 3 are {status}." See `CONTEXT.md`, "Document tag", for how an agent asks
for one status or the other.

Each lane shows the project's worktrees as **Worktree boxes**. A task reaches its worktree
through the open worktree on its branch, since a task names a branch and not a worktree. A free
agent names its worktree outright. A node that resolves to neither sits in no box.

`deriveGraph` gives a lane its `emptyWorktrees`: the open worktrees of the project that no node
of the lane names. `_main` is never one of them, because every project has a `_main` and an
empty box for it says nothing. These lanes come from the world, not from the nodes: a project
with an open worktree and no node on the desk still gets a lane, so its empty boxes draw. A
project whose only open worktree is `_main` gets none. A branch or agent status filter hides
nodes, so a worktree with no node drawn can still hold work: `emptyWorktrees` is `[]` while
either is set.

`canvas/rows.ts` packs a lane in one pass. `canvas/layout.ts`
gives each node its worktree as its `box`, and the engine packs the nodes of one box as one
block. Two boxes with no column in common share rows. The doc comment of `assignRows` gives
the rule.

All nodes of a lane share one row grid. `layout.ts` derives each box from the cells of its
nodes, and sets the gap between two rows from what meets there. The widest need of any column
sets the gap for the whole lane. `orchestrator-ui/DESIGN.md` gives the figures. Columns stay lane-wide, so the
zones still align. The empty worktrees form a strip of boxes below the lowest node and box. Each
is one node wide and one label high. The strip holds one box per column, so each starts where a
box that holds a node in that column starts.

`canvas/WorktreeBoxNode.tsx` draws each box behind the nodes. The box takes no pointer events;
its label does. The label is a button that names the worktree and its branch, and opens the
**Worktree card** — see "The Worktree card".

Commenting on a document takes one drag. Selecting text shows a "Comment on selection" control
level with the selection. Clicking it paints the selection in a stronger highlight and opens the
composer with the textarea focused. The server does not serve comments yet, so the margin holds
none and the button says so.

## Starting new work

The top bar's "New" control opens `newwork/NewWork.tsx`, in both views so the affordance never
moves. The form is one step: everything the work needs is on one surface.

- **Every kind** takes a project, a kind — task, free agent or Linear — and the prose that says
  what the work is. The prose is the only field a task needs.
- **A task** also shows its title, its branch, its planning level, and Advanced. "Save" writes the
  task as `todo`; "Start" writes it and launches it. Both put it on the desk. The prose becomes
  the task's content unchanged, so the surface shows no second content field. Advanced's Model and
  Execute Model sit side by side. Execute Model defaults to "(Same as plan)" — unset, which keeps
  the session on its plan's model.
- **"Suggest"**, beside Branch, calls `useInferTask`. On a task it fills the title and branch from
  the reply; on a free agent, the branch only. It never changes the planning level or the mode:
  the user picks those, so the UI does not read the reply's command and mode. It is a button
  rather than a gate: a submit names empty fields itself, so Suggest is for seeing the names
  first. It is an `AppButton`, so its wait shows on the control that started it — see "Commands
  are mutations". See "Naming a task from its prose" below.
- **A free agent** names a branch, a mode, a model and an execute model instead. The branch
  combobox offers the branches of open worktrees in the chosen project and keeps anything else
  typed, so a branch with no worktree gets one provisioned. The branch may stay empty: Start then
  names it from the prose. Mode and model start on `plan` — a
  new task's own default — and `opus`, the UI's shortlist default. Model and Execute Model sit
  side by side, the same as a task's Advanced row — defaulting the same way too, though a free
  agent has no plan to default "same as" from. "Start" runs `useStartAgent`.
- **The Linear kind** shows only for a project whose `.maelstrom.yaml` names a `linear.team_id`,
  which reaches the UI as `hasLinear` on the wire project. One combobox offers the current cycle's
  issues, each row showing the issue id and its title; the field carries the id. "Save" and "Start"
  both run `useCreateLinearTask`, which writes the same planning task `mael linear plan` writes.
  There is nothing else to fill in: the brief, the branch, the command and the mode all come from
  the issue.

The Linear kind is walled off in `newwork/LinearFields.tsx` and `api/linear.ts`, because the
Linear integration is expected to go once the task notebook covers the same ground.

### The project radios

`newwork/ProjectField.tsx` offers a radio per project the canvas is drawing, plus "Other" for the
rest. `selectors/projectsInView.ts` reads those projects by calling `deriveGraph`, the same call
the canvas and the deck list make, so the radios follow the filter bar rather than holding an
opinion of their own.

One project in view is selected outright, so the common case is no click at all. A project outside
the view sits behind "Other". A canvas drawing nothing offers every project instead.

A held project the world no longer has is dropped before it is used, because the fallback picks the
*first* offered project and a stale name would silently write the work against another one.

### The planning level

Three radios say how much planning the work gets before it is built. The level is a reading over
the task's `command` and `mode`, never a field of its own:

| Level | `command` | `mode` | What it means |
|---|---|---|---|
| High | `plan-task` | `normal` | A planning session, ending at its plan review. |
| Regular | *(empty)* | `plan` | The task itself, proposing before it edits. |
| None | *(empty)* | `auto` | The task itself, unattended. |

The form opens on **Regular**, the middle of the three: no planning session is asked for, and
nothing runs unattended. It is the level most tasks get, because it is the one nobody chose.

`protocol/planningLevel.ts` holds the mapping both ways, and carries why the level is a UI-side
reading rather than a wire field.

Advanced keeps the Command combobox and the Mode select. The level and the two fields are one value
read two ways, so choosing a level writes both fields and editing either re-derives the level. A
pair no level stands for reads as N/A — see `newwork/PlanningLevelField.tsx`.

### Naming a task from its prose

A task needs a title and a branch, and the prose field is the only one the user must fill in. So
Save and Start first call `useInferTask` when the title or the branch is empty. A free agent's
Start does the same when its branch is empty. Each empty field takes the reply's value; a typed
value always wins.

The submit's inference is its own mutation, apart from Suggest's. So its wait shows as the footer
spinner, and Suggest never shows a wait it did not start.

The server's **Task metadata generator** does the naming — see `task.infer` in
[the orchestrator server](orchestrator-server.md). With no key the branch is a slug of the first
line, and prose in a non-Latin script slugs to nothing, so it falls back to `feat/task`.

`ui/Dialog.tsx` and `tasklist/TaskFields.tsx` are shared with the task editor, so the two
surfaces cannot drift on what a task's fields are. New work composes the parts rather than
rendering the whole: it interleaves Suggest beside Branch and the planning radios above Advanced,
and it shows a title without a content field.

`ui/ComboBox.tsx` is the combobox the Branch, Command and Issue fields all use: a text field that
offers a list and keeps anything else typed. A row can carry a label apart from its value, so the
Issue field offers an issue by its title and submits its id. `ui/MultiComboBox.tsx` extends it for
the Follows field, which picks several values shown as removable chips instead of one.

### The top layer

Two controls draw outside the page's own stacking: the dialog, and the combo box offer inside it.
Both use the browser's top layer, because a dialog scrolls and an ordinary element cannot escape a
scrolling ancestor whatever its `z-index`.

`ui/Dialog.tsx` is a native `<dialog>` opened with `showModal()`. That is what supplies the
backdrop and the focus trap, so neither is written here. Escape arrives as `cancel`, which a
control inside can stop first — the combo box does, so one press dismisses its offer and a second
closes the dialog.

A click on the backdrop closes the dialog by the browser's light dismiss (`closedby="any"`). It
arrives as `cancel`, the same as Escape, so a caller's unsaved-work guard sees one path. A click
needs its press and its release outside the box. A descendant, such as a combo box offer that
draws past the box, is inside by the DOM tree. The box's own padding is inside by its rect. A
browser without `closedby` (Safari) ignores it: the backdrop does nothing, and Escape and × still
close.

A caller that fills the viewport has no outside, so it needs a close control of its own: below
839px the box is the full screen. `ui/ImageLightbox.tsx` is the one such caller and carries a
Close button for it.

The combobox offer is a `popover`, anchored to its field by CSS. The pattern has two halves,
`ui/useAnchorName.ts` and `ui/anchoredPopover.module.css`, and it is only correct when both are
applied. Each carries its reasoning; read them before you add a third popover.

CSS anchor positioning is not in Firefox or Safari yet. They fall back to ordinary absolute
positioning, which reads about right; Chromium is where this is exact.

Inference, a launch and a Linear read can each take tens of seconds, so every one of these hooks
takes `SLOW_CALL_TIMEOUT_MS`. A refusal shows in the form, which stays open holding what was
typed — the one place besides the task list's status select where a view keeps an error of its
own, because a dialog outlives the button's three-second window. A create whose launch failed
says so and stops offering to write the task again.

## Attaching an image

`ui/AttachField.tsx` wraps a text field and adds a file picker, a clipboard paste handler and a
strip of thumbnails. It wraps rather than replaces, so each surface keeps its own textarea, its
own value and its own submit — and the four cannot drift on how attaching works.

Four surfaces use it: the chat box (`session/MessageInput.tsx`), the new-work prose field, and
task create and task edit, which are one component (`tasklist/TaskFields.tsx`).

The upload runs as the image arrives, not at submit. So the caller gets a markdown ref to append
to its text, and a refusal shows while the user is still looking at the field. A failed upload
names the file: a screenshot that never arrived is otherwise invisible, and the words would go
believing it went. A paste is intercepted only when it carries an image, so pasted prose still
reaches the textarea.

The ref is appended to the text rather than held beside it. For a task the content is what the
notebook stores and what `build_prompt` sends, so a ref kept outside it would never reach the
agent. It also keeps `TaskEditor`'s `changed()` diffing strings.

The chat box sends the bytes as well, as an image block on the user turn, so the model sees the
picture on the turn rather than after choosing to read a file. A task cannot: its prompt is a
plain string. Either way the ref in the text is what renders in the transcript.

New work has no id to group its images under, so the dialog mints a bucket once and holds it for
the dialog's whole life. Every image the form attaches lands in that one directory, and the task's
own commit sweeps them in.

Each label uses an explicit `htmlFor`. `AttachField` sits between the label and the field, so a
wrapping label would leave the field with no accessible name.

Colour comes from `styles/tokens.css`, which holds both the primitive and the semantic layer
and documents the rule: no file outside it names a hex colour. One `[data-phase]` rule in
`styles/base.css` sets `--phase` from a phase attribute.

## Holding what was typed

The prose field is the one field a task needs, and the one a user spends minutes on. The dialog is
mounted conditionally, so a backdrop click, Escape, the × or Cancel used to unmount it and destroy
what was in it. The chat box goes the other way: the panel draws one `SessionTab` for whichever
session is active, so a tab switch re-renders it rather than unmounting — the key change is what
stands in for a close there. `ui/useRetained.ts` holds the text through both.

The hook is `useState`'s tuple plus one verb: `[value, setValue, release]`. A caller swaps
`useState` for `useRetained`, adds a key from `ui/retained.ts`, and calls `release()` once the work
is submitted. Every key lives in that one table, the way `api/keys.ts` holds every query key.

**What is held, and what clears it.** Closing is not submitting: the backdrop, Escape, the ×,
Cancel and a refused submit all hold. A resolved submit clears, and so does the **Clear** control on
the dialog. Clear is an ordinary affordance, shown whether the text was restored or just
typed, so a stale value has a one-click remedy; Cancel has never meant discard on this dialog, and
giving it that meaning with no undo would be a separate decision. The chat box has no Clear: a `!`
command line releases on running, which is the one clear that is not a submit.

**Written, not on every keystroke.** A trailing 300 ms debounce, plus a flush on two events: the
component unmounting, and its key changing. `setItem` serialises JSON on the thread the transcript
socket needs, so a write per keystroke is wasteful; an interval would lose the word of a user who
types and clicks the backdrop within it. The unmount flush is what makes a close safe whatever is
still in flight, and the key-change flush does the same for a tab switch, which never unmounts.

**The prose, its attachments and their bucket are one value.** They cannot be three keys.
`withoutRef` matches on a ref that embeds the bucket, so a re-minted bucket makes removing a
thumbnail a silent no-op — the thumbnail goes, the ref stays, and the agent gets a link to an image
it was never sent. A re-minted bucket also sends the next image to a directory the task's commit
does not sweep. So the bucket is restored, never re-minted.

A held project is checked against the world before it is used. `chosen` falls back to the *first*
project, so a project that has since gone would otherwise write the work against another one
silently.

The task's own fields are held as well. They were not while the form had two steps: the dialog
reopened on step 1 and Next re-inferred, so restoring them would have restored edits to fields
inferred from prose the user may since have changed. One surface has no re-inference to fall back
on, and a typed title lost on a close is the same loss the prose case guards against.

**Two tabs.** Last write wins, and the hook does not listen for the `storage` event. Live-syncing
two open dialogs would let one tab's keystrokes overwrite the other's textarea mid-sentence, which
is worse than the problem. Each tab keeps its own value while open; a new mount reads whatever was
written last.

**Where there is no storage.** Safari's private mode throws from `setItem`, and some embedded views
throw from the getter. The hook probes once at module scope and falls back to a module-level `Map`,
the way `layout/useLayoutMode.ts` falls back to `NO_MEDIA`. Holding then works across a close and
fails only across a reload — no error and no banner, which is how the app behaved before any of
this existed. A quota refusal drops the key being written and carries on.

The version sits in the key prefix, not in the stored value: bumping `v1` to `v2` makes every older
key invisible at once, and the hook sweeps them on its first mount rather than migrating them.

`localStorage`, not `sessionStorage`: both survive a reload, and this also survives closing the
tab. Not zustand's `persist` middleware either — the draft is deliberately not store state (see
`store/uiSlice.ts`), a `partialize` allow-list would put "do not persist view state" one spread
away from persisting the canvas, and `reset()` would write initial state back to storage.

## Showing an image

Images travel the other way too. An agent writes an `<image>` tag and the server turns it into a
markdown ref, so a picture an agent showed and one the user pasted reach the browser the same way —
as an ordinary ref in the message text. See `docs/dev/orchestrator-server.md`, "A shown image".

`markdown/Markdown.tsx` gives `react-markdown` its own `img`. A path that ends in `.webm`, `.mp4`
or `.mov` draws `<video controls>`. Every other path draws `ui/ImageLightbox.tsx`: a thumbnail
capped at 240px tall, and a click opens it full size in a `ui/Dialog`. Every surface that renders
markdown gets this, not only the transcript.

A surface shows an attachment only if its markdown holds a URL the server serves. For this
reason the node card renders the task's `displayContent`, not its `content`. A user message
arrives with the URL already in it. See "Attachments" in `docs/dev/orchestrator-server.md`.

The height cap, the portal to the body, and the `Dialog` `className` each carry their reason at
their own site.

## The three layouts

The app draws one of three layouts, chosen by viewport width.

| Layout | Width | Body |
| --- | --- | --- |
| Wide | 1600px and wider | Two slots, left and right |
| Medium | 840px to 1599px | One slot |
| Narrow | below 840px | The deck list, one screen at a time |

`orchestrator-ui/DESIGN.md` says why each break sits there.

**Slots and anchors.** The top bar has four items: Desk, Tasks, Worktrees and Tabs. Each item has
an anchor, left or right, and shows in the slot of its anchor. `ui.anchors` holds the anchors and
`ui.slots` holds the item in each slot. `ui.paneRecency` lists the items, most recently selected
first. `selectors/slots.ts` holds the transitions as pure functions: `showPane`, `togglePane`,
`moveAnchor` and `showing`.

The wide layout calls `togglePane` on a click and `moveAnchor` on a shift-click. The medium layout
has one slot that cannot close, so a click calls `showPane`. The medium layout draws the front of
`paneRecency`, and the narrow layout draws its first main view.

`SlotShell` in `shell/AppShell.tsx` draws the wide and the medium layout. It places each item with
the CSS `order` property, not by JSX position, so an item that changes side moves and does not
remount. The left slot takes the remaining width. The right slot takes `ui.panelWidth` when both
slots are open, and the full width when it is alone. The shell owns the drag grip and the width
clamp, because the right slot can hold any item.

`layout/useLayoutMode.ts` makes the choice. The decision is read in TypeScript rather than only in a
media query, because `orchestrator-ui/vite.config.ts` sets `css: false` — a media query is invisible
to the suite, and a hook the components branch on is a decision the app-boundary tests can assert.
The CSS carries cosmetic sizing only. `renderApp({ viewport: 'narrow' })` renders the narrow layout,
`viewport: 'medium'` the medium layout, and the default is the wide layout. `test/setup.ts` stubs
`matchMedia` from one settable width.

`AppShell` branches first, so the narrow layout mounts no `ReactFlowProvider`, no canvas and no
panel. React Flow is absent rather than hidden: a phone renders no board it cannot use, and
d3-zoom never competes with the page for a touch. Any component calling `useReactFlow` must
therefore be absent from the narrow layout. `AttentionChip` is split into `NarrowChip` and `WideChip` for that reason
rather than branching inside one component.

**The deck list** (`deck/`) tabs the desk by zone and opens on running. `selectors/deck.ts`
derives it from `deriveGraph`, so the list and the canvas draw the same nodes in the same states.
`deck/DeckRow.tsx` carries the canvas node's `data-state` and `data-phase`, so the state
vocabulary is one.

**One screen at a time.** `ui.mobileStack` holds what is pushed over the deck list; empty is the
deck itself. A row pushes a node's detail, and the detail's links push a session or a document.
Back pops one level. `selectors/navStack.ts` holds the transitions. `shell/PanelLink.tsx` is the
only control that opens a session or a document, so branching it there carries every link at once.

The detail screen renders `canvas/NodeCardBody.tsx`, which the canvas card also renders. Only the
shell around it was ever canvas-bound — the viewport portal, the absolute transform, the 440px
width and the grow animation.

Three things differ below the 840px break beyond layout. The document tab draws no comment margin. Dialogs are full-bleed and top-anchored, measured in `dvh` so a soft keyboard shrinks the
box rather than covering the focused field. And Enter makes a newline in the message input, since
a soft keyboard sends no other key; the Send button sends.

## The tuning jig

The **Jig** lets the user change CSS on the live page and send the result to the agent. It lives
in `vite-plugin-mael-tune/`, and `vite.config.ts` adds it to the dev server. Ladle loads the same
config, so every story has the jig too. So does the everyday UI that `mael self-env` serves. A
build and a vitest run do not load it, and outside a git checkout it switches itself off.

| Part | File | What it does |
|---|---|---|
| Plugin | `plugin.ts` | Injects `client.ts` into each page. Serves `GET`/`PUT /__mael/tuning` on `.drafts/tuning.css`. Watches the file and sends the `mael-tune:update` event over HMR. Proxies `POST /__mael/send`. |
| Overlay | `overlay.ts` | Draws the `Tune` pill and the panel in a shadow root, so app CSS does not reach it. Writes the text into one `<style>` at the end of `<head>` on each keystroke, then `PUT`s it after 300 ms. |
| Command | `worktree.tune` | Sends the **Tuning CSS** to the worktree's agents. See [orchestrator-server.md](orchestrator-server.md). |

**The file is the shared state.** Each page applies the file when it loads and when the event
arrives, so the app, every story and an edit by the agent stay in step. The event names the page
that wrote the file, and that page ignores it. Otherwise an echo of an older write would replace
what the user typed since.

**The jig's `<style>` stays last in `<head>`.** Vite appends a module's `<style>` when the module
loads or hot-updates. A `MutationObserver` moves the jig's element back to the end, so a tuning rule
wins a tie of specificity.

**Send finds the worktree by path.** The plugin asks `GET /api/worktrees` for the row whose `path`
is its git top level, then posts to `/api/worktrees/{id}/tuning`. It reads `ORCHESTRATOR_URL` with
no default: unset, Send is off and live CSS still works. The `ladle` service sets it for that
reason.

CSS modules hash their class names, so a rule matches on part of the name:
`[class*="_chip_"] { padding: 6px; }`.

## How to run it

```
mael self-env start             # the always-there instance: web on 2770, orchestrator on 2772
mael env start                  # this worktree's own copy, on its floating ports
mael env start ladle            # the component workbench, alone, on this worktree's LADLE_APP port
mael env start web-fake         # the fake mode, alone, on this worktree's WEB_FAKE port
cd orchestrator-ui && pnpm dev  # the web app alone, on port 5173, against localhost:8765
cd orchestrator-ui && pnpm test # vitest: the app in jsdom, the jig in Node
cd orchestrator-ui && pnpm lint && pnpm typecheck && pnpm build
bin/knip-check                  # dead code, both passes
```

**Start Ladle through `mael env`, not `pnpm ladle`.** The `ladle` service is `optional: true`, so
`mael env start` leaves it alone and you ask for it by name. It takes the worktree's allocated
`LADLE_APP_PORT`, which is what lets several worktrees serve their own catalogue at once — a hardcoded
port collides with the next worktree that tries.

`mael env list` shows it under stopped services, and `mael env stop ladle` ends it.

`mael self-env` runs the app from maelstrom's own `_main` worktree, on a reserved port base, so
one instance is always at the same address whatever a NATO worktree is doing. See
[the fixed environment](../guide/worktrees.md#the-fixed-environment).

Under maelstrom the `web` service always points at the `orchestrator` service, so start both. A
worktree whose `.env` is missing a port a service needs — `ORCHESTRATOR_PORT`, or `LADLE_APP_PORT`
on a worktree opened before the port had that name — needs `mael env reset` once to add it.

The dev server proxies `/api` to the orchestrator, WebSockets included. `ORCHESTRATOR_URL` names it
— see [environment.md](../reference/environment.md). `pnpm build` produces a page with no proxy
behind it: serving `orchestrator-ui/dist` needs the orchestrator on the same origin, and nothing
does that yet.

**The change stream is the one exception: it skips the proxy.** `eventsUrl` builds its address
from the page's own protocol and hostname, plus the orchestrator's port. A proxied stream never
closes — the dev server holds the connection for its own life, so the orchestrator counts a
watcher whether or not a page is open, and its worktree poll keeps asking GitHub for a room nobody
is in. Dialling the orchestrator directly means only a real page subscribes, so closing the last
tab stops the poll.

The port reaches the bundle through `VITE_ORCHESTRATOR_PORT`, which `vite.config.ts` derives from
`ORCHESTRATOR_URL`. The port alone travels, never the host: the dev server binds every interface
so the tailnet reaches it, and an address pinned to `localhost` would leave a remote page dialling
its own machine. `GET /api/events` echoes the request's `Origin` back, so the cross-origin read is
allowed.

The dev server accepts the dev host. `vite.config.ts` puts `DEV_HOST` in `server.allowedHosts`,
and Ladle loads the same file. Ladle pins its HMR socket to `localhost`, so `.ladle/config.mjs`
sets `hmrHost` to the dev host. See
[Open an environment from another device](../guide/dev-environments.md#open-an-environment-from-another-device).

**The fake mode needs no server.** The `web-fake` service runs the same dev server with
`FAKE_MODE=1`. It answers `/` with `preview.html`, which mounts the production `App` on the fake
server through `AppDeps`. `src/fake/main.tsx` is the entry. The index lists the scenarios of
`src/fake/scenarios.ts`, and `?scenario=<name>` opens one. `src/fake/deepLink.ts` lists the
parameters that open a screen. `pnpm build` reads `index.html` only, so the fake does not ship.

**The icons are static files in `public/`.** Vite serves them at `/` and copies them into the
build. `logo.svg` is the one source. The PNG files and `favicon.ico` are committed, made from it
by `orchestrator-ui/bin/render-icons`. Run it after `logo.svg` changes; CI does not.

`pnpm dev` with no `FAKE_MODE` and no server behind it shows "Loading the world…" and a
"Reconnecting…" banner until one appears.

## What the tests cover

Tests sit at the seams the plan agreed, never against internals: the pure modules; the API
client over a fake `fetch`; the change stream and the agent streams over a fake `EventSource`
and a fake socket; each resource and mutation hook over the fake server; the question prompt
and the button at their props seams; and the app boundary with Testing Library.

`fake/fakeServer.ts` is the orchestrator server faked at the wire: a `fetch` that answers every
route from a world, an `EventSource` factory whose sources open at once, and a `WebSocket`
factory whose sockets open with a transcript snapshot. A command changes the world the way the
server would and sends the notices. A test moves the world with `server.change`, which mutates
and sends the notice the real server would, and the transcripts with `server.append` and
`server.patch`. `fake/seedWorld.ts` is the world the app tests open on, and `fake/moves.ts` holds
the moves of it that several tests share. The transcript component
renders items from the goldens `lib/domain/tests/test_orchestrator_normalise.py` owns, so one fixture set
feeds both suites.

`fake/scenarios.test.tsx` is the coverage gate of the scenarios. It writes each closed set of the
protocol as a record, so a new member fails the typecheck until it is named. It then fails the
suite until a scenario shows the member. The same test mounts each scenario on the narrow and the
medium layout and fails on a logged error.

`styles/spacing.test.ts` reads each CSS file under `src/`. It fails on a px literal in `padding`,
`margin` or `gap`. See `orchestrator-ui/DESIGN.md`, "Layout". `styles/fontSize.test.ts` reads each
CSS and TSX file. It fails on a font size that is not a `--text-*` token, an `em` value or
`inherit`. See "Hierarchy". Both gates share `test/sourceGate.ts`.

The jig is its own vitest project, which runs in Node by default. `plugin.test.ts` starts a real
Vite dev server and drives `/__mael/tuning`, the HMR event and Send against a fake orchestrator.
`overlay.test.ts` opts into jsdom, which cascades the declared values of a `<style>` rule into
`getComputedStyle` without layout.

Colours, light mode, glow, the grow animation, pan and zoom, pixel positions and markdown
fidelity are not tested.

Neither is anything React Flow draws from a measurement. jsdom lays nothing out, so an edge
never renders in a test — a bare mount and the whole app both draw their nodes and no edges —
and a connection drag never resolves, because v12 hit-tests the pointer against
`getBoundingClientRect`. So the drawn wire, its hover target and the drag gesture are checked in
a browser, and `FollowsEdge` is reachable by no test: it renders only when an edge does.

The anchors are not in that category. A `Handle` renders whatever the layout, so
`App.canvas.test.tsx` asserts a task node draws its two, which is what `nodesConnectable` buys.
The rest is tested away from the canvas: `canvas/reduce.ts` and `canvas/connect.ts` are pure and
carry the rules a wire is drawn, refused and cut by, and the write itself is covered at
`useUpdateTask` and at the PATCH route.

Canvas nodes are clicked with `fireEvent.click`, not user-event — see `clickNode` in
`src/test/renderApp.tsx` for why. Everything outside the canvas uses user-event.

## Out of scope

Against the server, documents can be read but not reviewed: comments, review actions and
shaping answer `not_implemented`, and the controls say so. The server drives agents, edits the
desk, and writes a task's status, its fields, new tasks and deletions. It does nothing else to
the notebook.

The desk is the exception to persistence: it lives on the server and survives a restart. The
open tabs, the filters and the expanded node do not. The distinction is deliberate rather than
incidental: view state is not kept, but unsubmitted text is — see "Holding what was typed".

Also out of scope: an embedded terminal, auth, an elk layout, a global keyboard shortcut layer
(Esc on the card and the question's digit keys are local to their components), syntax
highlighting in markdown, and answering a plan review from the session tab (the expanded node
and the document tab answer it).
