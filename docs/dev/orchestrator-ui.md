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
| Backends | `ws-backend/`, `fake-backend/` | The WebSocket client and the in-browser simulation, each implementing `Backend` | Protocol |
| State | `store/`, `selectors/` | One zustand store and the pure functions that derive views from it | Protocol |
| UI | `canvas/`, `tasklist/`, `panel/`, `decisions/`, `session/`, `documents/`, `shell/`, `sim/`, plus the `markdown/` and `styles/` they share, and `test/` for shared test helpers | React components and CSS | State, Protocol |

The protocol has no React and no I/O. That is what lets the fake backend and the client share
one reducer, and what lets the orchestrator server apply the same reducer in Python.

`sim/` holds the FAKE chip and the debug drawer. Delete that directory with the fake backend.

## The protocol

One duplex channel carries two kinds of frame, kept apart:

- **Events** describe the world. Each carries a `seq` from one global counter. The client drops
  a frame whose `seq` is not newer than the last one it applied, so replay is idempotent.
- **Replies** answer one command. A reply never enters the event log.

The `desk` entity kind is the desk itself: one entry per task on it, carrying the wire task id
and when the user put it there. `desk.add` and `desk.remove` are the two commands that edit it.
A task launched from the UI joins the desk as well.

The events are `snapshot`, `upsert`, `remove`, `transcript.append`,
`transcript.update`, `transcript.truncated` and `error`. An upsert carries the whole entity, so the client never merges.
Each command is acked with `ok: true` or an error code mirroring the daemon's own refusals.
`protocol/commands.ts` lists every command and every code.

On reconnect the client sends the last `seq` it applied. The server replays from its ring buffer
when it can, else it sends a fresh snapshot.
[orchestrator-server.md](orchestrator-server.md) documents the wire format and the snapshot
epoch rule.

Two pure modules carry the rules a real backend must apply:

- `protocol/phase.ts` derives a task's phase from its `command`, decides whether a task is
  actionable, and decides how a node draws (`queued`, `working`, `needs-attention`, `idle`,
  `done`, `exited`).
- `protocol/normalise.ts` turns the daemon's raw stream-json into transcript items, agent
  upserts, documents and attention items. It follows `agent_model.apply_event` and is tested by
  replaying every fixture under `tests/fixtures/agent_events/`. It is the reference for the
  server's Python port; see [orchestrator-server.md](orchestrator-server.md), "Normaliser parity".

## The canvas, the task list and the panel

The canvas is where the user decides. The panel is where the user reads. The task list is where
the user chooses what the canvas draws.

The canvas draws the desk: the tasks the user has put on it, and nothing else. It opens empty
against the real server, because the world holds about 700 tasks across every project and most
of them are finished. The task list lists every task with filters for status, project, branch
and text, and each row toggles that task on or off the desk. The top bar switches between the
two views; the canvas filter bar shows on the canvas only, since the task list carries its own.

A node shows the bare notebook id, because its lane already names the project. A panel tab shows
the qualified id, because a tab exists to tell two projects' tasks apart.

Clicking a task node expands it in place, showing the state in words ("Needs you · plan
review", never a raw agent state). Esc, the close button and a click on the canvas collapse
it. The attention chip expands the next node that needs the user.

A decision shows the last three things the agent said or did, then the prompt. A question
follows AskUserQuestion's shape; `session/cards/QuestionPrompt.tsx` says why every answer
sends together. A permission shows the tool input with Approve and Deny. A plan review links
to the plan with Approve and Deny. Deny sends the reason as the agent's tool result, and the
agent carries on with it. The expanded node and the document tab render the same
`DecisionCard`, so the two agree.

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

The server applies the same phase rule and the same normalisation, in Python. It bridges three
sources: the task notebook for tasks, `list-all` for projects and worktrees, and the agent host
for agents, transcripts and waits. [orchestrator-server.md](orchestrator-server.md) describes it.

## How to run it

```
mael env start                  # this repo's services: the web app and the orchestrator server
cd web && pnpm dev              # the web app alone, on port 5173, against the fake backend
cd web && pnpm test             # vitest, jsdom
cd web && pnpm lint && pnpm typecheck && pnpm build
```

Under maelstrom the `web` service always points at the `orchestrator` service, so start both. A
worktree whose `.env` has no `ORCHESTRATOR_PORT` needs `mael env reset` once to add it. Without
maelstrom, `VITE_ORCHESTRATOR_URL=ws://localhost:8765 pnpm dev` runs against a
`mael orchestrator serve`.

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
server serves agents only.

The desk is the exception to persistence: it lives on the server and survives a restart. The
open tabs, the filters and the expanded node do not.

Also out of scope: an embedded terminal, auth, an elk layout, a global keyboard shortcut layer
(Esc on the card and the question's digit keys are local to their components), syntax
highlighting in markdown, and answering a plan review from the session tab (the expanded node
and the document tab answer it).
