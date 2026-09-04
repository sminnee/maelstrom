# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Experienced developers who run several Claude Code agents at once on one machine. The primary
user today is one such developer working alone, fluent in git, worktrees and Claude Code, and
fluent in maelstrom's own vocabulary (`CONTEXT.md` at the repo root is that vocabulary).

Small development teams are the intended audience the design should aim at. This is a
direction, not a built capability: today there is one desk, no auth and no identity. Design
work must not assume per-user desks, attribution of who answered a checkpoint, or shared
presence exist.

## Product Purpose

The orchestrator UI shows every agent as a node on one canvas, and captures the user's
checkpoints in the tool instead of in a terminal. It exists because one agent on one branch is
easy and several at once is not: without it the user must find the right cmux pane to learn
that an agent is blocked, and answer it there.

Success is that the user drives many agents from one surface — sees what is running, sees what
is waiting on them, answers it, and orders the next work — without leaving the page to find out
what is happening.

## Positioning

The guiding metaphor is a real-time strategy game: everything running is on one canvas, and a
unit that needs orders shows it on the canvas itself. This metaphor is a binding commitment.

What a neighbouring tool could not truthfully copy is the notebook underneath it. Maelstrom
already models order of work (`follows`), grouping into one pull request (`parent`) and the
four phases of a task's life. The UI shows agents against that model, so it can say what work
is blocked on what, and by whom, rather than listing running processes.

## Operating Context

The UI is used on the main monitor as the primary working surface, at high intensity. The user
constantly flips between three registers:

- **Deep review** — reading one artefact closely: a plan, a diff, a document, a transcript.
- **Scanning** — sweeping the canvas for the lighter inputs that need answering, and clearing
  them fast.
- **Assembling** — kicking new work off, and ordering the chain of dependencies that decides
  what runs in what order.

These three are not separate sessions. The user moves between them many times an hour, and the
cost of the switch is the thing the design must attack. This is a high-octane power tool, not
an ambient dashboard.

The surrounding tools are cmux (workspaces and panes), git worktrees (code isolation), Claude
Code (the agent), and the `mael` CLI, which does everything the UI does and more. The UI never
replaces the CLI; it is the surface for watching and answering.

The world it draws is large: roughly 700 tasks across every project, most of them finished.
The canvas therefore opens empty and draws only the desk — the tasks the user has put on it.
The task list is where a task joins the desk or leaves it.

## Capabilities and Constraints

Confirmed, and documented in `docs/dev/orchestrator-ui.md` and `docs/dev/orchestrator-server.md`:

- **Canvas** — draws the desk as swimlanes of task nodes, grouped by project, branch or none.
  Clicking a node expands it in place; one node is expanded at a time.
- **Task list** — a full-width view of every task the server knows, filtered by status, project,
  branch and text. Each row toggles that task on or off the desk.
- **Panel** — a right-hand panel holding session and document tabs, opened by panel links.
- **Decision** — the block shown when an agent waits on the user: the last three things the
  agent said or did, then the prompt. Three wait kinds: a question, a permission, a plan review.
  Deny sends the reason back as the tool result and the agent carries on with it.
- **Attention item** — one thing waiting on the user. Raised and cleared by the backend, never
  inferred by the UI.
- **Comments** — anchored to a span of one document version; one drag to create.

Constraints:

- React 19, Vite, zustand, `@xyflow/react` for the canvas, CSS modules. Four layers, each
  importing only from below: protocol → backends → state → UI.
- One WebSocket to the orchestrator server, or a fake backend that simulates the world in the
  browser. The fake backend is a design surface, not throwaway.
- Against the real server, documents, comments, task creation and shaping answer `invalid`.
  The server serves agents only.
- The desk persists on the server. Open tabs, filters and the expanded node do not.
- No embedded terminal, no auth, no global keyboard shortcut layer today.

Terminology is fixed by `CONTEXT.md` at the repo root, including each term's `_Avoid_` list.
Use desk, canvas, task list, expanded node, decision, panel link, phase, attention item,
document, comment, brief.

Undecided: whether cancelling a task should release or block the tasks that follow it.

## Brand Commitments

The real-time strategy metaphor is binding.

Nothing else is. The current palette, the hue-per-phase scheme, Inter and JetBrains Mono, and
the token set in `web/src/styles/tokens.css` are all provisional and may be replaced.

Two structural rules survive any redesign. No file outside `tokens.css` names a hex colour.
And light and dark are both first-class: the app follows the operating system preference, so
every design must hold contrast and read well in both. Dark is not the default and light is not
an afterthought — the primary user runs a night-time dark schedule, so the same surface is seen
in both schemes on the same day.

## Evidence on Hand

- `CONTEXT.md` — the domain glossary. Authoritative for every term.
- `docs/dev/orchestrator-ui.md` — the four layers, the event and command protocol, the canvas,
  the panel, the fake backend.
- `docs/dev/orchestrator-server.md` — the server behind it and the wire protocol.
- `web/src/fake-backend/` — a running simulation of the world, usable for design work without
  a live server. `pnpm dev` runs against it on port 5173.
- `tests/fixtures/agent_events/` — recorded stream-json from real agents.

There are no users beyond the author, no testimonials, no benchmarks, no pricing and no
public launch. Future work must not invent them.

## Product Principles

1. **The canvas decides, the panel reads, the list chooses.** Each view has one job. Work that
   belongs to one must not migrate into another.
2. **Answering must be faster than switching.** The user is on this page instead of hunting a
   cmux pane. Every checkpoint must be answerable in the flow the user is already in.
3. **Keyboard-first, for speed.** This is a power tool. Every frequent action must be reachable
   and fast from the keys, and hands should not need to leave them to scan, answer and launch.
4. **Say it in the user's words.** State shows in words the user already owns — "Needs you ·
   plan review" — never a raw agent state, and never a term `CONTEXT.md` puts on an `_Avoid_`
   list.
5. **Density serves the flip.** The design serves a user switching between deep reading,
   scanning and assembling many times an hour. Reducing the cost of that switch beats making
   any one register prettier.
6. **The backend states what needs attention.** The UI draws what it is told and never infers
   attention for itself.

## Accessibility & Inclusion

WCAG 2.2 AA is the committed baseline: contrast, focus visibility and keyboard operability.
Contrast is measured in both colour schemes, not one.

The stronger requirement is developer speed, not conformance. Full keyboard operation is a
product requirement in its own right, because the user works at pace with hands on the keys.
Where the two pull apart, keyboard speed is the one that must not be compromised.
