# The orchestrator UI

A web app that shows every agent as a node on one canvas and captures the user's checkpoints in
the tool. It lives under `web/`. It runs against the orchestrator server over one WebSocket, or
against a fake backend that simulates the world in the browser.

The guiding metaphor is a real-time strategy game. Everything running is on one canvas, and a
unit that needs orders shows it on the canvas itself.

## The layers

`web/src` is four layers. Each one imports only from the layers below it.

| Layer | Directory | Holds | Imports |
|---|---|---|---|
| Protocol | `protocol/` | Entity types, events, commands, the `Backend` interface, and the pure reducers | Nothing |
| Backends | `api/`, `live/`, `ws-backend/`, `fake-backend/` | `api/`: the REST client, its query keys and the query cache. `live/`: the change stream that keeps the cache fresh. `ws-backend/` and `fake-backend/`: the WebSocket client and the in-browser simulation, each implementing `Backend` | Protocol |
| State | `store/`, `selectors/` | The query cache holds the fetched world; one zustand store holds UI state, the connection state and the open transcripts; `selectors/` are pure functions over a `WorldView` | Protocol |
| UI | `canvas/`, `tasklist/`, `panel/`, `decisions/`, `session/`, `documents/`, `shell/`, `sim/`, plus the `ui/`, `markdown/` and `styles/` they share, and `test/` for shared test helpers | React components and CSS | State, Protocol |

The protocol has no React and no I/O. That is what lets the fake backend and the client share
one reducer, and what lets the orchestrator server apply the same reducer in Python.

`sim/` holds the FAKE chip and the debug drawer. Delete that directory with the fake backend.

## The protocol

One duplex channel carries two kinds of frame, kept apart:

- **Events** describe the world. Each carries a `seq` from one global counter. The client drops
  a frame whose `seq` is not newer than the last one it applied, so replay is idempotent.
- **Replies** answer one command. A reply never enters the event log.

The `desk` entity kind is the desk itself: one entry per task and per free agent on it, carrying
the entry's id and when it arrived. An id names its kind — see `CONTEXT.md`, "Desk" — and
`protocol/deskId.ts` builds and splits one. `desk.add` and `desk.remove`
are the two commands that edit it. The server adds an entry for every agent it sees running, so
a task launched from the UI joins the desk as well.

The events are `snapshot`, `upsert`, `remove`, `transcript.append`,
`transcript.update`, `transcript.truncated` and `error`. An upsert carries the whole entity, so the client never merges.
Each command is acked with `ok: true` or an error code mirroring the daemon's own refusals.
`protocol/commands.ts` lists every command and every code.

`store/useCommand.ts` sends one. Its promise resolves with the result, or rejects with a
`CommandError` carrying the code: the server's on a refusal, `transport` when the socket dropped.
Every control that sends a command is an `AppButton` (`ui/AppButton.tsx`), and the button owns
what happens next. A handler that returns a promise puts the button in `processing`: disabled,
busy, with a spinner. A rejection puts it in `error`: it reads "Failed", the message is its
`title`, and a click retries. It is ready again after three seconds. So a refusal shows on the
button that asked, and no view keeps an error of its own.

On reconnect the client sends the last `seq` it applied. The server replays from its ring buffer
when it can, else it sends a fresh snapshot.
[orchestrator-server.md](orchestrator-server.md) documents the wire format and the snapshot
epoch rule.

Two pure modules carry the rules a real backend must apply:

- `protocol/phase.ts` reads a task's phase from its `command`, decides whether a task is
  actionable, and decides how a node draws (`queued`, `ready`, `working`, `needs-attention`,
  `idle`, `done`, `cancelled`, `exited`). The phase is never sent on the wire, so this is the
  one place the reading happens. An unrecognised command reads as no phase, so a typo in a task's
  frontmatter shows as a node with no phase rather than one claiming a phase it never had.
- `protocol/normalise.ts` turns the daemon's raw stream-json into transcript items, agent
  upserts, documents and attention items. It follows `agent_model.apply_event` and is tested by
  replaying every fixture under `tests/fixtures/agent_events/`. It is the reference for the
  server's Python port; see [orchestrator-server.md](orchestrator-server.md), "Normaliser parity".

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
of the six statuses — native because the view scrolls under `overflow: auto`, which would clip a
popover. Choosing a status sends `task.setStatus`. The Edit button opens the task editor, which
holds title, content and branch, with command, mode, priority and model under a folded
"Advanced". Save sends `task.update` with the changed fields only. The editor renders from
`AppShell`, above both views, and its open task lives in the store, so the canvas can open the
same editor later.

A node is one of two kinds. A **task** node stands for a notebook task, with or without an
agent. A **freeAgent** node stands for an agent with no task, and takes its title, branch and
lane from the worktree it runs in. An agent linked to a task draws as that task's node, so
nothing appears twice. Edges come from `task.follows`, so a free agent is never an endpoint.

The task list lists tasks only. A free agent has no row, and is dismissed from its own expanded
card instead. That control is disabled while the agent runs, because a live agent is drawn
whatever the desk says; a remove that arrives anyway is accepted and takes effect once the agent
stops.

A node shows the bare notebook id, because its lane already names the project. A free agent
shows the head of its agent id, having no notebook id. A panel tab shows the qualified id,
because a tab exists to tell two projects' tasks apart.

Clicking a task node expands it in place, showing the state in words ("Needs you · plan
review", never a raw agent state). Esc, the close button and a click on the canvas collapse
it. The attention chip expands the next node that needs the user.

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
never works out which of the three applies: the backend marks the item stale and takes the request
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
its task id, so two agents' tabs are told apart.

Group by `project` and `branch` draw one hairline lane per group. Group by `none` draws no
lanes.

Commenting on a document takes one drag. Selecting text shows a "Comment on selection" control
level with the selection. Clicking it paints the selection in a stronger highlight and opens the
composer with the textarea focused.

Colour comes from `styles/tokens.css`, which holds both the primitive and the semantic layer
and documents the rule: no file outside it names a hex colour. One `[data-phase]` rule in
`styles/base.css` sets `--phase` from a phase attribute.

## The fake backend and a real one

The fake backend is not throwaway. It is where the frontend and backend APIs are designed, and
it exercises the same path a real backend would.

The fake is event-sourced. The seed world is the first event in its log. The simulation never
mutates the world: it emits events, applies them through the client's reducer, and appends them
to the log with the next `seq`. A command validates with `protocol/validate.ts`, then becomes
the daemon's own reply shapes, a `control_response` or a `user` turn, and passes through
`normalise.ts` like any other stream event.

Each simulated agent plays a script of beats. `ask`, `permission` and `plan` park the agent in
the matching wait and raise one attention item. `finish` moves the task to `done`, unblocks its followers and launches each
one that became actionable. Every random choice goes through a seeded generator, so the same
seed yields the same events.

The real backend is `ws-backend/wsBackend.ts` in front of the orchestrator server. It replaces
`fake-backend/` one for one and keeps everything else. `main.tsx` picks it when
`VITE_ORCHESTRATOR_URL` is set, and the fake otherwise. The FAKE chip feature-detects the fake,
so its absence says the real server is behind the page.

The server applies the same normalisation, in Python. It bridges three
sources: the task notebook for tasks, `list-all` for projects and worktrees, and the agent host
for agents, transcripts and waits. [orchestrator-server.md](orchestrator-server.md) describes it.

## How to run it

```
mael self-env start             # the always-there instance: web on 2770, orchestrator on 2772
mael env start                  # this worktree's own copy, on its floating ports
cd web && pnpm dev              # the web app alone, on port 5173, against the fake backend
cd web && pnpm test             # vitest, jsdom
cd web && pnpm lint && pnpm typecheck && pnpm build
```

`mael self-env` runs the app from maelstrom's own `_main` worktree, on a reserved port base, so
one instance is always at the same address whatever a NATO worktree is doing. See
[the fixed environment](../guide/worktrees.md#the-fixed-environment).

Under maelstrom the `web` service always points at the `orchestrator` service, so start both. A
worktree whose `.env` has no `ORCHESTRATOR_PORT` needs `mael env reset` once to add it. Without
maelstrom, `VITE_ORCHESTRATOR_URL=ws://localhost:8765 pnpm dev` runs against a
`mael orchestrator serve`.

The REST routes and the change stream are same-origin: the dev server proxies `/api` to the
orchestrator named by `ORCHESTRATOR_URL` (default `http://localhost:8765`), WebSockets
included. `vite.config.ts` reads that variable, not the bundle, so the built app carries no
address. `pnpm build` produces a page with no proxy behind it: serving `web/dist` needs the
orchestrator on the same origin, and nothing does that yet.

`api/http.ts` is the client: every failure is an `ApiError` with a code — the server's, or
`transport` and `timeout` for a request that got no answer. One hook per resource
(`api/tasks.ts`, `api/agents.ts`, …) wraps one GET; `api/useWorld.ts` composes the seven list
queries into a `WorldView` (`selectors/world.ts`): the tables keyed by id, with tasks and
documents as slim rows. A view that needs the prose fetches the detail: the editor fetches its
task, the document tab its document, and a decision fetches the agent's detail, which carries
the request it waits on.

**Loading and errors.** `useWorld` is `loading` until the six tables the canvas draws from have
data, so the canvas never draws nodes without lanes: it shows "Loading the world…" and the task
list "Loading…", never "No task matches". A required table that fails makes it `error`, and
both views show the message with a Retry. Once every table has data, a refetch that fails keeps
the data on screen. TanStack's structural sharing keeps an unchanged table the same object, so
a refetch that changed nothing re-renders nothing. `api/queryClient.ts` sets the cache
so nothing goes stale on its own; `live/changeStream.ts` invalidates what each notice names, and
a `reset` invalidates everything. Its connection state lives in the store, and
`shell/ConnectionBanner.tsx` shows "Connecting…" before any data and "Reconnecting… showing the
last known state" once there is some.

The FAKE chip in the top bar plays, pauses, steps and sets the speed of the simulation. Its ⚙
opens the debug drawer, which forces a question, a permission, a plan, a finish or an exit on
any live agent.

## What the tests cover

Tests sit at the seams the plan agreed, never against internals: the pure modules, the
`Backend` contract through `createFakeBackend` and through `createWsBackend` over an injected
fake socket, the question prompt at its props seam, and the app boundary with Testing Library on
a paused fake backend advanced by `sim.step()`.

Colours, light mode, glow, the grow animation, pan and zoom, pixel positions and markdown
fidelity are not tested.

Canvas nodes are clicked with `fireEvent.click`, not user-event — see `clickNode` in
`src/test/renderApp.tsx` for why. Everything outside the canvas uses user-event.

## Out of scope

Against the real server, documents, comments, task creation and shaping answer `invalid`. The
server drives agents, edits the desk, and writes a task's status and fields. It does nothing else
to the notebook.

The desk is the exception to persistence: it lives on the server and survives a restart. The
open tabs, the filters and the expanded node do not.

Also out of scope: an embedded terminal, auth, an elk layout, a global keyboard shortcut layer
(Esc on the card and the question's digit keys are local to their components), syntax
highlighting in markdown, and answering a plan review from the session tab (the expanded node
and the document tab answer it).
