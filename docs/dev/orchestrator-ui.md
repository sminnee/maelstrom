# The orchestrator UI

A web app that shows every agent as a node on one canvas and captures the user's checkpoints in
the tool. It lives under `web/`. It reads the world from the orchestrator server's REST routes,
hears what changed on one change-notice stream, and follows each open agent's transcript on a
socket of its own.

The guiding metaphor is a real-time strategy game. Everything running is on one canvas, and a
unit that needs orders shows it on the canvas itself.

## The layers

`web/src` is four layers. Each one imports only from the layers below it.

| Layer | Directory | Holds | Imports |
|---|---|---|---|
| Protocol | `protocol/` | The entity and transcript types, `phase.ts`, `deskId.ts`, `time.ts` | Nothing |
| Backends | `api/`, `live/` | `api/`: the REST client, its query keys, the query cache, one hook per read and per command. `live/`: the change stream that keeps the cache fresh, and the per-agent transcript streams | Protocol |
| State | `store/`, `selectors/` | The query cache holds the fetched world; one zustand store holds UI state, the connection state and the open transcripts; `selectors/` are pure functions over a `WorldView` | Protocol |
| UI | `canvas/`, `tasklist/`, `newwork/`, `panel/`, `decisions/`, `session/`, `documents/`, `shell/`, plus the `ui/`, `markdown/` and `styles/` they share, and `test/` for shared test helpers | React components and CSS | State, Protocol |

The protocol has no React and no I/O. `protocol/phase.ts` reads a task's phase from its
`command` and decides whether a task is actionable. The phase is never sent on the wire, so this
is the one place the reading happens. An unrecognised command reads as no phase, so a typo in a
task's frontmatter shows as a node with no phase rather than one claiming a phase it never had.

`protocol/time.ts` writes a moment two ways. `ago` gives the age of one — `<1m`, `7m`, `2h`, `3d`
— for a live status; `clockTime` gives the time on the clock, for a transcript. Both take `now`
as an argument rather than reading the clock, so both are pure and a test pins the answer. An
unreadable stamp gives an empty string, and the caller draws nothing rather than a false age.

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
| Needs you · … | `in-progress` | awaiting, with an open attention item | running |
| Idle | `in-progress` | idle | running |
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
into the store's `transcripts` slice and moves the cursor. A drop reconnects from the cursor
with a doubling wait, a `4409` at once, and a `4404` ends the stream. Nothing closes every
stream at once, so a provider that unmounts and mounts again comes back with its streams
intact.

**Commands are mutations.** One hook per command in `api/` — `useApprove`, `useLaunch`,
`useSetStatus`, `useAddToDesk`, … — over one POST, PATCH or DELETE. Its `mutateAsync` resolves
with the result, or rejects with an `ApiError` carrying the code. On success the hook
invalidates the keys the command touched; the change notice invalidates them again a moment
later, so the screen is right while the stream reconnects too. The launch call waits two
minutes: the server opens the worktree first.

Every control that sends a command is an `AppButton` (`ui/AppButton.tsx`), and the button owns
what happens next. A handler that returns a promise puts the button in `processing`: disabled,
busy, with a spinner. A rejection puts it in `error`: it reads "Failed", the message is its
`title`, and a click retries. It is ready again after three seconds. So a refusal shows on the
button that asked, and no view keeps an error of its own. The status picker
(`ui/StatusPicker.tsx`) is the one control that is not a button; it shows its own refusal. The
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
the user chooses what the canvas draws.

The canvas draws a node when it is on the desk, or it has a live agent. The liveness half is
what makes running work always visible: an agent shows the moment it starts, before the server's
own desk entry arrives. It opens near-empty against the real server, because the world holds
about 700 tasks across every project and most of them are finished. The task list lists every
task with filters for status, project, branch and text, and each row toggles that task on or off
the desk. The top bar switches between the two views; the canvas filter bar shows on the canvas
only, since the task list carries its own.

The task list opens on `todo`, `in-progress` and `blocked`, for the same reason the canvas opens
near-empty. Ticking `done`, `cancelled` or `template` brings that work back; unticking every
status shows every task.

The task list also writes. A row's status is a button until it is clicked, then a native select
of the six statuses. The expanded card carries the same control, at the right end of its state
strip, so a decision taken on the canvas does not need the list. Both use `ui/StatusPicker.tsx`,
which says why the select is native. Choosing a status posts the new one, and a refusal shows
beside the control. The Edit button
opens the task editor, which holds title, content and branch, with command, mode, priority and
model under a folded "Advanced". Save patches the changed fields only. The editor renders from
`AppShell`, above both views, and its open task lives in the store, so the canvas can open the
same editor later.

A node is one of two kinds. A **task** node stands for a notebook task, with or without an
agent. A **freeAgent** node stands for an agent with no task, and takes its title, branch and
lane from the worktree it runs in. A `mael add` or `mael open` session is exactly that: the
launch starts a driven agent and passes no task, so the session draws as a freeAgent node. An
agent linked to a task draws as that task's node, so nothing appears twice. Edges come from
`task.follows`, so a free agent is never an endpoint. A subagent, an agent with a `parent`, is
never a node's agent and never a node: it is reached through its parent's session tab.

The task list lists tasks only. A free agent has no row, and is dismissed from its own expanded
card. That control is disabled while the agent runs, because a live agent is drawn whatever the
desk says; a remove that arrives anyway is accepted and takes effect once the agent stops. A task
is removed from its own card too, under the label its task list row uses. That control is hidden
rather than disabled, because the task list row remains as the other way off the desk.

A node shows the bare notebook id, because its lane already names the project. A free agent
shows the head of its agent id, having no notebook id. A panel tab shows the qualified id,
because a tab exists to tell two projects' tasks apart.

Clicking a task node expands it in place, showing the state in words ("Needs you · plan
review", never a raw agent state). The state strip also carries the task's notebook status, which
the user can set from there. Where the words would only restate the status — `done`, `cancelled`,
and `blocked` with no agent — the card drops them and the status stands alone. The collapsed node
keeps them: it has no status control, so there the words are the only reading. A free agent has
no task, so its card has no status control. Esc, the close button and a click on the canvas
collapse it — but with the status picker open, Esc closes the picker only. The attention chip
expands the next node that needs the user. `selectors/attention.ts` ranks the open items: plan
reviews, then document reviews, then questions, then permissions, then the rest, oldest first
within each.

The session tab head carries a mode chip naming the agent's permission mode. A click moves the
agent to the next mode: plan, then auto, then normal. The chip shows the mode the child last
announced, so a refused change leaves it where it was.

A session tab on an agent with subagents draws a strip under the transcript: one link per
subagent, with a state dot that pulses while it runs, its description, what it waits on when it
is blocked, and its summary once it has ended. A blocked subagent says so here rather than in the
parent's stream, so the ask sits beside the subagent that raised it; the decision itself is made
on the parent, whose pipe takes the reply. The link opens the subagent as a session tab of its own, `session:X.1`. That tab is
the same component, read-only: it heads with the id and the description, and has no message
input, no mode chip and no decision handlers, because a subagent's asks are answered through the
parent.
Opening the tab opens the transcript socket, which is what makes the server attach to the
subagent; closing it releases the socket after the usual 5-second grace, and the server detaches.

Every tool card starts folded. The summary line names the tool, its title and its status, and a
click opens the body. An agent that makes hundreds of calls is a list, not a wall of text. A
loaded skill folds the same way, under the skill's name. A shell command draws with the same
card a `Bash` tool call gets, because a `!` line and a `Bash` call are the same thing to the
reader — see [orchestrator-server.md](orchestrator-server.md#a-shell-command).

A decision shows the last three things the agent said or did, then the prompt. A question
follows AskUserQuestion's shape; `session/cards/QuestionPrompt.tsx` says why every answer
sends together. A permission shows the tool input with Approve and Deny. A plan review links
to the plan with Approve and Deny. Deny sends the reason as the agent's tool result, and the
agent carries on with it. The expanded node and the document tab render the same
`DecisionCard`, so the two agree.

A prompt reads one of three ways: open, answered, or stale — see `CONTEXT.md`, "Stale prompt". The
transcript keeps a stale prompt, showing what was asked and reading "no longer pending", with no
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
prompt in full. `selectors/transcript.ts` skips the same call when it builds the context before a
wait.

The panel holds session and document tabs only. A panel link opens a session or a document as
a tab; `shell/PanelLink.tsx` says why links, not buttons. Every tab carries a phase chip and
its task id, so two agents' tabs are told apart. A node card lists every document its node has,
whatever raised it — a plan review, or a tag the agent wrote in its own message.

The same links row carries two external links, which open a new browser tab instead of a
panel tab. `shell/ExternalLink.tsx` is the control, and its arrow-leaving-a-box icon is the
whole difference a reader sees. The wire carries a ready `prUrl`, so the card links a pull
request without joining two fields; a worktree with no PR draws none. The dev env link draws
only while the environment runs, and the 15-second worktree poll makes it appear and
disappear on its own.

The PR link says its state as well as its number — `PR #278 · CI running`. The state is one of
six values the server decides, listed under **PR state** in `CONTEXT.md`, so the UI never
re-derives it from raw GitHub fields. `selectors/status.ts` turns the value into words.
`shell/PrChip.tsx` draws the same reading as a `#278` chip with a coloured dot on a collapsed
node and a deck row; the chip's `title` repeats the state in words, because colour alone must
not carry it. The same 15-second poll moves the label from `CI running` to `ready to merge` on
its own.

A node resolves its worktree from its agent first, then from the open worktree on its branch,
which is what keeps a finished task showing its pull request. `selectors/graph.ts` holds both
steps.

Group by `project`, `branch` and `worktree` draw one hairline lane per group. Group by `none`
draws no lanes. Whatever the grouping, the board runs left to right in three progress zones — done,
running, not started — whose boundaries line up across every lane. One strip of labels names
them above the board. `canvas/columns.ts` assigns the zone and the column; it is pure, it sees
one lane at a time, and `canvas/layout.ts` aligns the zones and packs the rows.

A **plan** document draws no review bar either, and for a different reason: its verdict is the
agent's wait, so the decision card answers it and a bar would leave the agent blocked. See
`orchestrator-server.md`, "Commands".

A **draft** document draws no review bar at all. It is something to read: nothing waits behind
it, so `documents/ReviewActions.tsx` returns nothing rather than a bar refusing a review nobody
asked for. Every other status keeps the bar — `awaiting-review` offers Approve and Request
changes, and the rest read "This version is {status}." See `CONTEXT.md`, "Document tag", for how
an agent asks for one status or the other.

`worktree` is the one grouping whose lanes come from the world rather than from the nodes. Every
other grouping derives a lane from the nodes in it, so a lane holding nothing never appears. Here
the empty lane is the point: an open worktree with no agent in it is what the user cannot
otherwise see. A closed worktree draws no lane. The project filter drops another project's lanes;
the branch filter only empties them, because the lanes are what the mode is for.

A task reaches its worktree through the open worktree on its branch, since a task names a branch
and not a worktree. A free agent names its worktree outright. Work that resolves to neither falls
to `Unallocated`, which is created only when something needs it.

A worktree lane's header carries a close, which runs the same close `mael close` runs — see
[orchestrator-server.md](orchestrator-server.md), "Closing a worktree". It never forces. `_main`
never closes and is offered no button.

Commenting on a document takes one drag. Selecting text shows a "Comment on selection" control
level with the selection. Clicking it paints the selection in a stronger highlight and opens the
composer with the textarea focused. The server does not serve comments yet, so the margin holds
none and the button says so.

## Starting new work

The top bar's "New" control opens `newwork/NewWork.tsx`, in both views so the affordance never
moves. The form is two steps in one dialog.

- **Step 1** takes a project, a kind — task, free agent or Linear — and the prose that says what
  the work is. The prose is the only field a task needs. A free agent also names a branch, a mode
  and a model.
- **Step 2, tasks only.** "Next" calls `useInferTask`; the step shows the inferred title, branch
  and command, every one editable. A task's model starts unset, so it launches on `opus` until
  Advanced names one. The prose becomes the task's content unchanged. "Save" writes
  the task as `todo`; "Start" writes it and launches it. Both put it on the desk. "Back" returns
  to step 1 with the prose intact.
- **Free agents skip step 2.** The branch combobox offers the branches of open worktrees in the
  chosen project and keeps anything else typed, so a branch with no worktree gets one provisioned.
  Mode and model are dropdowns, starting on `plan` — a new task's own default — and `opus`, the
  UI's shortlist default. "Start" runs `useStartAgent`.
- **The Linear kind skips step 2 too.** The kind shows only for a project whose `.maelstrom.yaml`
  names a `linear.team_id`, which reaches the UI as `hasLinear` on the wire project. One combobox
  offers the current cycle's issues, each row showing the issue id and its title; the field carries
  the id. "Save" and "Start" both run `useCreateLinearTask`, which writes the same planning task
  `mael linear plan` writes. There is nothing else to fill in: the brief, the branch, the command
  and the mode all come from the issue.

The Linear kind is walled off in `newwork/LinearFields.tsx` and `api/linear.ts`, because the
Linear integration is expected to go once the task notebook covers the same ground.

`ui/Dialog.tsx` and `tasklist/TaskFields.tsx` are shared with the task editor, so the two
surfaces cannot drift on what a task's fields are.

`ui/ComboBox.tsx` is the combobox the Branch, Command and Issue fields all use: a text field that
offers a list and keeps anything else typed. A row can carry a label apart from its value, so the
Issue field offers an issue by its title and submits its id.

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

New work has no id to group its images under, so the dialog mints a bucket and holds it across
the step 1 to 2 move. An image attached to the prose lands beside one attached to the content,
and the task's own commit sweeps both in.

Each label uses an explicit `htmlFor`. `AttachField` sits between the label and the field, so a
wrapping label would leave the field with no accessible name.

Colour comes from `styles/tokens.css`, which holds both the primitive and the semantic layer
and documents the rule: no file outside it names a hex colour. One `[data-phase]` rule in
`styles/base.css` sets `--phase` from a phase attribute.

## Showing an image

Images travel the other way too. An agent writes an `<image>` tag and the server turns it into a
markdown ref, so a picture an agent showed and one the user pasted reach the browser the same way —
as an ordinary ref in the message text. See `docs/dev/orchestrator-server.md`, "A shown image".

`markdown/Markdown.tsx` gives `react-markdown` its own `img`, which draws `ui/ImageLightbox.tsx`:
a thumbnail capped at 240px tall, and a click opens it full size in a `ui/Dialog`. Every surface
that renders markdown gets this, not only the transcript.

The height cap, the portal to the body, and the `Dialog` `className` each carry their reason at
their own site.

## The two layouts

The app draws one of two layouts, chosen by viewport width. At 840px and wider it is the
main-monitor tool this document describes: the canvas or the task list, with the panel beside it.
Below 840px it is the deck list, one screen at a time. `web/DESIGN.md` says why the break sits
there.

`layout/useLayoutMode.ts` makes the choice. The decision is read in TypeScript rather than only in
a media query, because `web/vite.config.ts` sets `css: false` — a media query is invisible to the
suite, and a hook the components branch on is a decision the app-boundary tests can assert. The
CSS carries cosmetic sizing only. `renderApp({ viewport: 'narrow' })` renders the narrow layout,
and `test/setup.ts` stubs `matchMedia` from one settable width.

`AppShell` branches first, so the narrow layout mounts no `ReactFlowProvider`, no canvas and no
panel. React Flow is absent rather than hidden: a phone renders no board it cannot use, and
d3-zoom never competes with the page for a touch. Any component calling `useReactFlow` must
therefore be wide-only. `AttentionChip` is split into `NarrowChip` and `WideChip` for that reason
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

Three things differ below the break beyond layout. The document tab draws no comment margin. Dialogs are full-bleed and top-anchored, measured in `dvh` so a soft keyboard shrinks the
box rather than covering the focused field. And Enter makes a newline in the message input, since
a soft keyboard sends no other key; the Send button sends.

## How to run it

```
mael self-env start             # the always-there instance: web on 2770, orchestrator on 2772
mael env start                  # this worktree's own copy, on its floating ports
cd web && pnpm dev              # the web app alone, on port 5173, against localhost:8765
cd web && pnpm test             # vitest, jsdom
cd web && pnpm lint && pnpm typecheck && pnpm build
bin/knip-check                  # dead code, both passes
```

`mael self-env` runs the app from maelstrom's own `_main` worktree, on a reserved port base, so
one instance is always at the same address whatever a NATO worktree is doing. See
[the fixed environment](../guide/worktrees.md#the-fixed-environment).

Under maelstrom the `web` service always points at the `orchestrator` service, so start both. A
worktree whose `.env` has no `ORCHESTRATOR_PORT` needs `mael env reset` once to add it.

Everything the app reaches is same-origin: the dev server proxies `/api` to the orchestrator,
WebSockets included. `ORCHESTRATOR_URL` names it — see
[environment.md](../reference/environment.md). `pnpm build` produces a page with no proxy behind
it: serving `web/dist` needs the orchestrator on the same origin, and nothing does that yet.

The app has no fake mode. `pnpm dev` with no server behind it shows "Loading the world…" and a
"Reconnecting…" banner until one appears.

## What the tests cover

Tests sit at the seams the plan agreed, never against internals: the pure modules; the API
client over a fake `fetch`; the change stream and the agent streams over a fake `EventSource`
and a fake socket; each resource and mutation hook over the fake server; the question prompt
and the button at their props seams; and the app boundary with Testing Library.

`test/fakeServer.ts` is the orchestrator server faked at the wire: a `fetch` that answers every
route from a world, an `EventSource` factory whose sources open at once, and a `WebSocket`
factory whose sockets open with a transcript snapshot. A command changes the world the way the
server would and sends the notices. A test moves the world with `server.change`, which mutates
and sends the notice the real server would, and the transcripts with `server.append` and
`server.patch`. `test/seedWorld.ts` is the world the app tests open on. The transcript component
renders items from the goldens `tests/test_orchestrator_normalise.py` owns, so one fixture set
feeds both suites.

Colours, light mode, glow, the grow animation, pan and zoom, pixel positions and markdown
fidelity are not tested.

Canvas nodes are clicked with `fireEvent.click`, not user-event — see `clickNode` in
`src/test/renderApp.tsx` for why. Everything outside the canvas uses user-event.

## Out of scope

Against the server, documents can be read but not reviewed: comments, review actions and
shaping answer `not_implemented`, and the controls say so. The server drives agents, edits the
desk, and writes a task's status, its fields and new tasks. It does nothing else to the
notebook.

The desk is the exception to persistence: it lives on the server and survives a restart. The
open tabs, the filters and the expanded node do not.

Also out of scope: an embedded terminal, auth, an elk layout, a global keyboard shortcut layer
(Esc on the card and the question's digit keys are local to their components), syntax
highlighting in markdown, and answering a plan review from the session tab (the expanded node
and the document tab answer it).
