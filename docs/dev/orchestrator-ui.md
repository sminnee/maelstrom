# The orchestrator UI

A web app that shows every agent as a node on one canvas and captures the user's checkpoints in
the tool. It lives under `web/`. Today it runs against a fake backend that simulates the world in
the browser. A real backend later implements the same interface over a WebSocket.

The guiding metaphor is a real-time strategy game. Everything running is on one canvas, and a
unit that needs orders shows it on the canvas itself.

## The layers

`web/src` is four layers. Each one imports only from the layers below it.

| Layer | Directory | Holds | Imports |
|---|---|---|---|
| Protocol | `protocol/` | Entity types, events, commands, the `Backend` interface, and the pure reducers | Nothing |
| Fake backend | `fake-backend/` | The in-browser simulation that implements `Backend` | Protocol |
| State | `store/`, `selectors/` | One zustand store and the pure functions that derive views from it | Protocol |
| UI | `canvas/`, `panel/`, `summary/`, `session/`, `documents/`, `shell/`, `sim/` | React components | State, Protocol |

The protocol has no React and no I/O. That is what lets the fake backend and the client share
one reducer, and what lets a Python backend implement the same contract later.

`sim/` holds the FAKE chip and the debug drawer. Delete that directory with the fake backend.

## The protocol

One duplex channel carries two kinds of frame, kept apart:

- **Events** describe the world. Each carries a `seq` from one global counter. The client drops
  a frame whose `seq` is not newer than the last one it applied, so replay is idempotent.
- **Replies** answer one command. A reply never enters the event log.

The events are `snapshot`, `upsert`, `remove`, `transcript.append`,
`transcript.update` and `error`. An upsert carries the whole entity, so the client never merges.
Each command is acked with `ok: true` or an error code mirroring the daemon's own refusals.
`protocol/commands.ts` lists every command and every code.

On reconnect the client sends the last `seq` it applied. The server replays from its ring buffer
when it can, else it sends a fresh snapshot.

Two pure modules carry the rules a real backend must apply:

- `protocol/phase.ts` derives a task's phase from its `command`, decides whether a task is
  actionable, and decides how a node draws (`queued`, `working`, `needs-attention`, `idle`,
  `done`, `exited`).
- `protocol/normalise.ts` turns the daemon's raw stream-json into transcript items, agent
  upserts, documents and attention items. It follows `agent_model.apply_event` and is tested by
  replaying every fixture under `tests/fixtures/agent_events/`.

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

A real backend replaces `fake-backend/` with a WebSocket client and keeps everything else. On
the Python side it bridges four sources: the task notebook for tasks, the session registry and
`mael list-all` for worktrees and sessions, and the agent daemon for transcripts and waits. It
applies the same phase rule and the same normalisation.

## How to run it

```
mael env start web              # serves on the worktree's FRONTEND port
cd web && pnpm test             # vitest, jsdom
cd web && pnpm lint && pnpm typecheck && pnpm build
```

Without maelstrom, `cd web && pnpm dev` serves on port 5173.

The FAKE chip in the top bar plays, pauses, steps and sets the speed of the simulation. Its ⚙
opens the debug drawer, which forces a question, a permission, a plan, a finish or an exit on
any live agent.

## What the tests cover

Tests sit at the seams the plan agreed, never against internals:

- The pure modules: reducer, validation, phase, normalisation (fixture replay), the stepper, the
  graph and layout selectors, the tab and attention selectors, comment anchors.
- The `Backend` contract, through `createFakeBackend`.
- The app boundary, with Testing Library on a paused fake backend, advanced with `sim.step()`:
  opening tabs, approving, answering, filtering, commenting, forcing events.

Colours, glow, animation, pan and zoom, pixel positions and markdown fidelity are not tested.

Canvas nodes are clicked with `fireEvent.click`, not user-event — see `clickNode` in
`src/test/renderApp.tsx` for why. Everything outside the canvas uses user-event.

## Out of scope for the proof of concept

The WebSocket backend and its Python bridge, an embedded terminal, auth, persistence across
reloads, an elk layout, keyboard shortcuts, syntax highlighting in markdown, and answering a
plan review from the session tab (the summary and document tabs answer it).
