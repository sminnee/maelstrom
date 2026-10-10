---
name: Maelstrom Orchestrator
description: Mission Control for a room full of agents — one lit surface where every unit reports and the one that needs orders says so.
colors:
  console-slate: '#0f1115'
  console-slate-raised: '#171a21'
  console-slate-sunken: '#0a0c10'
  hairline: '#2a2f3a'
  hairline-strong: '#3d4454'
  readout: '#d7dbe4'
  readout-muted: '#9aa3b5'
  readout-faint: '#5f6878'
  signal-blue: '#7aa2f7'
  alert-amber: '#ff9f43'
  fault-rose: '#f7768e'
  clear-green: '#9ece6a'
  phase-shape: '#b58cf6'
  phase-plan: '#6ea8fe'
  phase-build: '#2fc4b2'
  phase-land: '#f0b35a'
typography:
  large:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '18px'
    fontWeight: 600
    lineHeight: 1.3
  display:
    fontFamily: "'Inter Tight', 'Inter', system-ui, -apple-system, sans-serif"
    fontWeight: 600
    lineHeight: '24px'
  reading:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '16px'
    fontWeight: 400
    lineHeight: 1.5
  body:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '13px'
    fontWeight: 400
    lineHeight: 1.4
  control:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '14px'
    fontWeight: 400
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '12px'
    fontWeight: 500
  micro:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '12px'
    fontWeight: 500
    letterSpacing: '0.08em'
  mono:
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace'
    fontSize: '12px'
    fontWeight: 400
rounded:
  xs: '3px'
  sm: '6px'
  lg: '10px'
  pill: '999px'
spacing:
  half: '4px'
  unit: '8px'
  '2': '16px'
  '3': '24px'
  control: '32px' # 48px on the narrow layout
components:
  task-node:
    backgroundColor: '{colors.console-slate-raised}'
    textColor: '{colors.readout}'
    rounded: '{rounded.sm}'
    padding: '8px 10px'
    width: '220px'
    height: '76px'
  node-card:
    backgroundColor: '{colors.console-slate-raised}'
    textColor: '{colors.readout}'
    rounded: '{rounded.lg}'
    padding: '12px 16px'
    width: '440px'
  button:
    backgroundColor: '{colors.hairline}'
    textColor: '{colors.readout}'
    rounded: '{rounded.pill}'
    padding: '0 12px'
    height: '{spacing.control}'
  button-primary:
    backgroundColor: '{colors.signal-blue}'
    textColor: '{colors.console-slate-sunken}'
    rounded: '{rounded.pill}'
    padding: '0 12px'
    height: '{spacing.control}'
  attention-badge:
    backgroundColor: '{colors.alert-amber}'
    textColor: '{colors.console-slate-sunken}'
    rounded: '{rounded.pill}'
    size: '16px'
  panel-tab:
    backgroundColor: '{colors.console-slate}' # the body's ground; the strip is console-slate-raised
    textColor: '{colors.readout}'
    border: '1px solid {colors.hairline-strong}' # top and right; no bottom, where the tab opens
    borderLeft: '4px solid {colors.phase-build}' # the tab in view only; others reserve it clear
    rounded: '6px 6px 0 0'
    padding: '0 8px'
    height: '32px'
---

# Design System: Maelstrom Orchestrator

## Overview

**Creative North Star: "Mission Control"**

A room whose whole job is to know the state of many things at once. The walls are dark
because the readouts are the light. Nothing on the surface is decorative: every hue, every
dot, every glow is a channel reporting something, and an operator who has sat here a while
reads the room without focusing on any part of it.

The operator works at pace and flips constantly between three registers — sweeping the board
for what needs answering, pulling one artefact close to read it properly, and laying out what
runs next. The design serves that flip before it serves any single register. State is legible
at a glance; the thing that needs a decision escalates itself until it is dealt with; and the
board never rearranges itself under the operator's hands.

The system is flat, cool and dense. It is not a dashboard to be admired from a distance and
not an ambient display: it is an instrument the operator has their hands on all day, in both
light and dark, because the same surface is seen in both on the same day.

**Key Characteristics:**

- Colour is a channel, never a finish — a grey field is what makes a signal readable.
- Phase is a hue, set once from a data attribute and inherited everywhere beneath it.
- Flat by default; a shadow is earned by overlapping other content, not by importance.
- Dense and quiet at rest — only the two calls on the operator are allowed to be loud.
- Light and dark are equal citizens, both driven from one semantic token layer.

## Colors

An instrument palette: a cool blue-grey field, with saturated hues reserved entirely for
reporting state. The four phase hues run a deliberate spectrum — violet, blue, teal, amber —
so a task's position in its life is readable from hue alone.

### Primary

- **Signal Blue** (`--accent`): interactive affordance and nothing else. Links, panel links,
  the focus ring, the running-command line, the text selection wash, the On desk arrow,
  the square on the On desk toggle's desk.
  If it is blue, it can be clicked or it has the operator's focus.

### Secondary

The state channel. These never decorate; each one means one thing.

- **Alert Amber** (`--attention`): a node that needs the operator, the attention chip, the count
  badge, comment highlights.
- **Reply Yellow** (`--unanswered`): an idle agent that left a message on unfinished work. It
  takes the same ring, glow and wash as Alert Amber, so the two differ by colour alone. Only
  the ask takes the count badge.
- **Fault Rose** (`--danger`): an agent that exited or a failed command. Fault, not warning.
- **Clear Green** (`--ok`): finished and correct. Deliberately quiet — done work should recede.
  A cancelled task never takes it: cancelled work is terminal but not a success, so it draws
  the faint neutral dot instead.
- **Console amber and rose are never paired for emphasis.** Two loud channels at once is
  the operator failing to know which to deal with.

### Tertiary

The phase channel, set by `[data-phase]` and read everywhere as `--phase`. A phase name is an
imperative — the work to do — so it never reads as a state the agent is in.

A node without a phase draws neither: the bar falls back to the faint neutral and no label shows.
Two things have no phase — an agent with no task, and a task whose `command` nobody recognises.
Guessing a phase for either would state something the notebook never said.

- **Shape Violet** (`--phase-shape`): exploring a brief until tasks are agreed.
- **Plan Blue** (`--phase-plan`): producing a plan for one task, or a plan-mode build task until its
  plan is approved.
- **Build Teal** (`--phase-build`): building, reviewing, opening the PR.
- **Land Amber** (`--phase-land`): answering CI and review on an open PR.

Each phase also carries a drained form, `--phase-dormant`, for a node whose process has stopped.
It is mixed per phase in `base.css`, at the one ratio `--phase-drain` names, because a custom
property substitutes where it is declared: derived once against the root fallback it would drain
every phase to the same grey. **A new phase hue must add its `--phase-dormant` beside its
`--phase`**, or stopped nodes in that phase lose their edge.

### Neutral

- **Console Slate** (`--bg`): the field everything sits on.
- **Console Slate Raised** (`--bg-raised`): nodes, cards, bars — anything that is a surface
  rather than the room.
- **Console Slate Sunken** (`--bg-sunken`): the recessed ground beneath the field.
- **Hairline** (`--border`) and **Hairline Strong** (`--border-strong`): separation without
  weight. Structure is drawn with one-pixel lines, never with fills or heavy rules.
- **Readout** (`--fg`), **Readout Muted** (`--fg-muted`), **Readout Faint** (`--fg-faint`):
  three steps of text presence — the thing itself, its metadata, its scaffolding. On dark,
  `--fg` sits a step below the brightest neutral: 13.63:1 rather than 15.42:1, because these
  documents are read for minutes at a time and maximum contrast is a glare at that length.
- **Literal** (`--fg-literal`): an inline literal in prose. It is a register, not a rank, so it
  is neither `--fg-muted` (which reads as de-emphasised, and a literal is not less important
  than its sentence) nor a hue (which the Reporting Rule reserves for state, and which would
  make a literal look like a link). 8.87:1 on dark, 8.78:1 on light.

### Named Rules

**The One Source Rule.** No file outside `styles/tokens.css` names a colour. Not a hex, not an
`rgb()`, not a named CSS colour. A component that needs a colour the semantic layer does not
have adds it to the semantic layer.

**The Reporting Rule.** Every hue on screen reports state. Nothing is coloured because it looks
better coloured. When a new element needs emphasis, the answer is weight, size or space —
not a colour promoted out of the state channel. Two exceptions, both only in a control the user can click. The arrow of a desk icon reports
the direction of the act, not state: `--accent` onto the desk and `--tone-archival` off it. The On
desk toggle draws an `--accent` square on its desk icon when the task is on the desk. It reports
state in the affordance hue, not a state hue.

**The Two Calls Rule.** Alert Amber and Reply Yellow are the only channels allowed to escalate
themselves with a glow. Both call the operator: one holds an ask, the other waits on a reply.
If a third thing starts glowing, the design has stopped ranking and started shouting.

## Typography

**Interface Font:** the platform's own UI face — `system-ui`, `-apple-system`, `Segoe UI`, sans-serif
**Display Font:** Inter Tight, for markdown headings only
**Mono Font:** the platform's own mono — `ui-monospace`, `SFMono-Regular`, Menlo, monospace

**Character:** Three neutral workhorses doing different jobs. The interface face carries
everything a human wrote or a human reads. Mono carries everything a machine produced — ids,
branches, paths, commands, tool calls. The switch is semantic, not stylistic: mono is how the
interface says "this is a literal string you may need to type or match". The display face
carries markdown headings, and gives a heading a rank the eye reads before the words — a size
step alone cannot do that at reading size.

The system fetches one webfont: the display face, Latin subset, at the single weight headings
use. It is about 22kB and it is served from the bundle, never from a font CDN. This app binds
local ports and is reached over a tailnet, so an operator console must not need the public
internet to draw its own headings.

Body text and mono still fetch nothing, so prose paints on the first frame. Headings paint in the
interface face and swap when the display face arrives. The swap moves glyphs within a heading but
never moves the text under it: every heading metric is a fixed pixel rather than a ratio, so a
heading occupies the same rows either way and the grid holds whether the face arrives or not.

The tokens name Inter and JetBrains Mono ahead of the system stack for anyone who has them
installed, but no metric depends on them.

### Hierarchy

Seven sizes, each with one job. Sizes are the `--text-*` tokens; no component names its own, and
`styles/fontSize.test.ts` fails on a literal. Steps 1px apart do not read as ranks, so rank
inside a size comes from weight, case and tone.

The ramp has two halves, because the app has two jobs. The chrome is scanned and must stay
dense; prose is read and must not.

| Token            | Wide | Narrow | Job                                                      |
| ---------------- | ---- | ------ | -------------------------------------------------------- |
| `--text-sm`      | 12px | 16px   | labels, metadata, ids, tool rows, chips, the micro-label |
| `--text-ui`      | 13px | 20px   | the chrome: node titles, table rows, links               |
| `--text-control` | 14px | 22px   | a button's label                                         |
| `--text-md`      | 16px | 20px   | prose, and markdown's `h3`                               |
| `--text-lg`      | 18px | 24px   | markdown's `h2`                                          |
| `--text-xl`      | 21px | 28px   | markdown's `h1`                                          |
| `--text-caption` | 11px | 8px    | the caption under a narrow button's icon                 |

Nothing on the wide layout draws `--text-caption`. A wide button keeps `--text-control`.

- **Reading** (`--text-md`, 400): markdown, wherever it appears — a transcript message, a
  document, the decision rail. Prose is read start to end, so it is set well above the chrome
  around it rather than on the same step.
- **Chrome** (`--text-ui`, 400, 1.4): the body size everything else inherits. The canvas keeps
  this size whatever prose does, because the board's job is to hold many units at once.
- **Control** (`--text-control`): a button's label. One step over the chrome on both layouts, so
  the text holds its fill.
- **Display** (`--text-lg`, `--text-xl`): markdown's own `h2` and `h1`. Nothing in the chrome
  uses them.
- **Section head** (`--text-md`, 600, display face): markdown's `h3`. It ranks over body by face
  and weight, not by size. Nothing in the chrome uses it.
- **Label** (`--text-sm`, 500): metadata and secondary lines — the state line, the footer,
  filter fields, tab titles. Also the mono step: task ids, branches, worktree paths, code, a
  tool call's summary row, the transcript's time gutter, the session head.
- **Chip** (`--text-sm`): the text of a chip, in the chrome's face, never mono.
- **Micro** (`--text-sm`, 500, `0.08em`, uppercase): the phase name, section heads such as
  "NOW", the `AGENT` label, a tool call's status. Uppercase and tracked so it reads as a
  category, not a value.

A mark is not text. A drift triangle beside a line keeps an `em` size relative to that line.

Leading is a token too, chosen by job rather than by a single ratio: `--leading-tight` (1.3) for
a heading, `--leading-ui` (1.4) for an interface line, `--leading-prose` (1.5) for a paragraph.
`--leading-prose` is `--prose-row` / `--text-md`, so it is 1.5 on both layouts.

### Measure

Prose is capped, because the panel is resizable and an uncapped column grows without limit as
the operator drags it wider. Two tokens, because the panel's two surfaces are styled separately:

- `--measure-prose` — the document tab, read start to end.
- `--measure-panel` — the transcript, scanned in blocks between tool rows, and already narrowed
  by the 3.5rem time gutter.

Both are 72ch. One measure, because a document and a transcript message are the same act of
reading and a reader moving between them should not meet two line lengths. 72ch rather than the
conventional 80, because these documents are read start to finish: at 80ch the sweep back to the
next line start is long enough to lose your place, and a line broken up by literals makes that
worse. Not shorter, because the transcript gives 3.5rem of its width to the time gutter, so a
message there already reads narrower than the number says.

### Rhythm

Reading surfaces advance on a grid of `--prose-row`: 24px on the wide layout, 30px on the narrow. The grid is not
laid over the text — it is the text: at 16px with 1.5 leading the line box is exactly 24px, so
the leading and the grid are one number. The narrow layout keeps the ratio: 20px at 1.5 is 30px.
The `--prose-gap*` tokens derive from the row, so the narrow layout changes one number. A paragraph break is one row, a list gap a half row, the space above a heading two rows.

`--u` is the chrome's scale and never sets prose spacing. That scale is tuned against 13px
chrome, so on a reading surface every step lands under the line box it is meant to separate. The
`--prose-gap*` tokens exist so that a gap can never be narrower than the leading it separates.

Three pieces of arithmetic keep blocks on the grid, and each fails silently if changed:

| Block                  | Rule                            | Why                                                                                         |
| ---------------------- | ------------------------------- | ------------------------------------------------------------------------------------------- |
| Heading                | `line-height: var(--prose-row)` | A ratio re-derives from the size, so a later size change walks the page off the grid        |
| Heading with a literal | mono drops to `1em`             | At `0.92em` the mono inline box overflows a one-row line and adds a pixel                   |
| Code block             | padding is half a row less 1px  | With the 1px border the box chrome is one row, so a block is `lines + 1` rows at any length |

The space above a heading comes from the heading's own top margin. That margin collapses with
the paragraph's bottom margin rather than adding to it, so raising the paragraph gap does not
widen the space above a heading.

Quiet prose (`--text-ui`) has its own row, `--quiet-row`: 20px on the wide layout. On the narrow layout quiet
prose is the reading size, so it shares `--prose-row`.

Two blocks sit off the grid on purpose. List items take a half row, because a full row makes a
list of short items read as separate paragraphs; a list with an even number of items therefore
ends half a row out, until the next heading re-anchors it. Table rows come to 32px, because a
table is an inset object read as a unit rather than as continuing prose.

This is a grid in effect, not true baseline alignment. Blocks advance in whole rows. Baselines
sit at a font-dependent offset inside the line box, so a heading in the display face is not
collinear with body text — and chasing that with nudges would break whenever the webfont fails.

### Named Rules

**The Prose Rhythm Rule.** Prose spacing is a function of the line box, never of the chrome's
spacing scale. A reading surface takes `--prose-gap*`; a gap narrower than the leading it
separates is the failure this prevents.

**The Legibility Floor Rule.** `--text-sm` is the smallest type in the system: 12px on the wide
layout, 16px on the narrow. The one exception is `--text-caption`, 8px under a narrow button's
icon: the icon carries the meaning, and the text stays the accessible name. Density is bought with tighter space and shorter lines, never by shrinking type below
the floor. Every step clears WCAG AA against its own ground in both schemes; the micro-label in
`--fg-faint` is the tightest, and it is measured, not assumed.

`--fg-recessed` is the measured case this rule stops from regressing. It tracks `--fg-faint`,
which clears AA against `--bg`, `--bg-raised` and `--bg-sunken` in dark, and against `--bg` and
`--bg-raised` in light. On `--bg-sunken` in light it is 4.15:1, under the 4.5:1 floor. An
open `skill` or `raw_event` row sets that ground, and quiet prose can sit inside one, so the
failing case is real rather than theoretical. The surface that sets `--bg-sunken` re-points
`--fg-recessed` to `--fg-muted` rather than guarding every call site; do the same for a third
sunken surface, and check its contrast before trusting the token to carry it.

**The Mono Means Literal Rule.** Monospace marks a string the operator might copy, type or
match against something else. Prose never uses it, and a mono string is never truncated
without an ellipsis, because a half-shown id is worse than an obviously cut one.

A literal inside prose is marked by face and tone alone — mono and `--fg-literal`, with no
container. A filled chip works for the occasional literal in a transcript message and fails on a
plan document, where six or seven literals a paragraph turn a sentence into a row of boxes.

**The Operator's Words Rule.** State appears in words the operator already owns — "Needs you ·
plan review" — never a raw agent state, and never a term `CONTEXT.md` lists under `_Avoid_`.

**The Register Is One Value Rule.** The transcript's two registers — `prose` and `machinery` —
are computed once in `Transcript.tsx` and read as `data-register`. Spacing and chrome both derive
from it, so they cannot disagree. Keying either on the raw item type is how a shell command came
to draw one register and be spaced as the other — and how a `skill` or `compact_summary` item
once drew ledger chrome while returning `'prose'`, so it was spaced as the register it did not
draw.

`machinery` reads attention as well as type: an agent message whose every segment is
`<user-attention low>` is machinery by the agent's own marking, because the operator scans past
it exactly as they scan past a tool call. A mixed message leads with prose and stays prose, so a
card still carries exactly one register.

**The Rank Is Structural Rule.** Two registers on one surface are told apart by more than a
size step. Where prose and machinery sit side by side — the transcript is the case — prose
takes the page's baseline with no container, and the machinery takes the chrome. A single step
on the ramp is not enough to rank two things the eye must separate without reading. Prose takes
no label either: `AgentMessage` draws no visible role text, because labelling the baseline is
labelling the page. The speaker survives for a screen reader on the user turn alone, in a
visually-hidden span — the agent turn needs none, because it is the baseline everything else is
read against.

**The Two Ranks of Prose Rule.** An agent's prose has two ranks, and the agent marks working
detail. A `<user-attention low>` tag reads at `--text-ui` in `--fg-recessed`; other prose reads
at `--text-md` in `--fg`. The operator's own turn stays at the reading rank. The rank is carried
by size, tone **and extent**. In the transcript, a `<user-attention low>` block also clamps to
two lines behind a fade, because low attention is machinery under the Register Is One Value
Rule, and machinery is scanned past, not read in full. The tone drop and the clamp are one
commitment — neither ships without the other, or the block reads as demoted without reading as
skippable. The clamp is transcript-only. `Markdown`'s other callers — a document tab, the
decision rail, a node card's brief — take the unclamped tone, because each already owns its own
answer to "there is more here." No new hue for any of this: the Reporting Rule keeps colour for
state.

## Layout

The chrome has one unit and one control height. `styles/tokens.css` declares them, and no
component names a px value for a gap, a padding or a margin.

| Token       | Value              | Use                                                   |
| ----------- | ------------------ | ----------------------------------------------------- |
| `--u`       | 8px                | the gap between items, and the margin round a control |
| `--u-half`  | 4px                | inside one item: an icon and its label                |
| `--u-2`     | 16px               | between groups, and the inset of a screen or a card   |
| `--u-3`     | 24px               | between sections. Equal to the wide prose row         |
| `--bar-pad` | 8px                | the padding of every bar, on all four sides           |
| `--control` | 32px; narrow: 48px | the height of each button, link, field and tab        |
| `--icon`    | 16px; narrow: 24px | the size of an action icon in a button                |

A bar is a band of controls at the edge of a view: the top bar, the panel's worktree bar, a
session's head, a dock and the composer. Each of these pads with `--bar-pad` and no other value,
so a new bar cannot drift from the rest. A bar at the foot of a screen keeps
`max(var(--bar-pad), env(safe-area-inset-bottom))` at its bottom edge.

`--control` is set once, in `tokens.css`, and the narrow layout re-points it there. `base.css`
gives it to each button, field and link, so a component sets a width and never a height. A button
that is not a control opts out with `min-height: 0`: a diff gutter, a thumbnail, a link-variant
button in a line of text.

`src/styles/spacing.test.ts` is the gate. It fails on a px literal in `padding`, `margin` or
`gap`. Only a hairline (`1px`) passes.
The prose grid (`--prose-gap*`) and the canvas geometry are separate scales: see "Rhythm" and the
canvas grid below.

Two slots side by side under one bar. A top bar holds the brand, the menu, the filters, the
attention chip and **New**, which ends the bar. The menu has four items: Desk, Tasks, Worktrees
and Tabs. Each item has an anchor, left or right, and shows in the slot of its anchor. The bar
draws the left-anchored items beside the brand and the right-anchored items just before New, so
each group sits over its slot.

Beneath the bar the body splits. The left slot takes the remaining width, and the right slot is
resizable, with a 6px drag grip on its left edge. A click closes a slot and a shift-click moves
an anchor; see **Slot** and **Anchor** in `CONTEXT.md`. Tabs starts on the right and the three main views start on the left, so the opening layout
is a main view with the panel beside it.

The main views are the canvas, the task list and the worktree table. The canvas draws the desk
as horizontal lanes, one per project. The task list is a table with a sticky filter row.

The canvas grid is fixed and mechanical, which is what makes it scannable: nodes are 220×76,
separated by 56px horizontally and 14px vertically. The vertical gap grows where a worktree box
border sits between two rows. A lane has 42px of padding, for its worktree boxes, and 28px
between lanes. Every lane is as wide as the board, not as wide as its own content.

A lane's label sits on the lane's top border, as a fieldset legend does: mono, `--text-sm`, in the case
of the name, centred on the line. The label has the `--bg` ground and 2px of side padding, so the
border stops clear of the text. The label starts 28px from the lane's left border, clear of the
corner.

A lane draws a **Worktree box** round the nodes of each worktree: a 1px dashed outline in
`--border-strong`. The box's label sits on the box's top border in the same way as a lane's
label, and the two labels start at one x. One 16px gap separates the lane border from a box, a
box from its nodes, and a box from what sits above or below it: a node with no box, or a second
box. Two boxes with no column in common sit side by side, at least 24px apart, and their nodes
align on one row. The gap between two rows is the same
across the lane: 32px where a box meets a node with no box, 48px where two boxes meet. An open
worktree with no node draws as a 220×24 box in a strip below the lowest node and box, with its
label inside. The strip holds one box per column, and each starts where a box that holds a node
in that column starts. Two lines of the strip are 8px apart.

The box takes no fill and no pointer events. Its label is the one control: a button holding the
worktree name in `--fg-muted` and the branch in `--fg-faint`. Both lift to `--fg` on hover and
while the card is open. The branch gives way with an ellipsis, and the full branch is in the
`title`. A detached worktree reads `(detached)`. The label never runs past its box.

Horizontal position is progress first and dependency second:

- The board runs left to right in three zones — DONE, RUNNING, NOT STARTED.
- A zone boundary sits at the same x in every lane, so the board reads as three vertical stripes.
- Inside a zone a task sits one column right of the deepest task it follows in that zone.
- A zone no lane uses takes no columns and collapses, and draws no label.
- A zone's label spans the nodes of the zone, from the left edge of its first column to the right
  edge of its last. The text is centred on a faint tint of `--border`, with 4px above and below.
- When the two rules conflict — a done task that follows a running one — progress wins, and the
  follows edge draws backwards.
- A task sits on the row of the task it follows, across zones; a second follower branches below.
  In a project lane a follower in another worktree box keeps its box. It sits on the row of the
  task it follows when its box fits there, and it is never a branch.
  A run of followers reserves the columns it spans. A task that follows nothing fills the first
  free cell.
- A wire another path already implies is not drawn. The board shows what gates what, not every
  id on disk.

**The anchor is a dot, not an edge.** A node's two wire anchors are 7px dots on its left and
right centre lines, hidden until the node is hovered and lit while a wire is being dragged. The
left one sits clear of the 4px phase bar rather than replacing it: by the Left Edge Rule that
border carries phase and only phase, so an anchor that thickened or recoloured it would be a
second channel on one edge. A dot reads as a fitting on the card, which is what it is.

Spacing runs on a 4px base with four steps in use: 4, 8, 12, 16. Component padding uses the
scale; the canvas uses its own constants because it positions in absolute pixels.

Density is the point. The operator wants many units visible at once, so containers are tight
and gaps are small. The drag grip between the two slots is how the operator trades one slot against
the other.

### The medium layout

The upper breakpoint is 1600px. Below it a main view and the panel do not both fit at a width
each can be read at. So the medium layout, from 840px to 1599px, has one slot. The menu is one
group of four items beside the brand, and a click shows the item in place of the one in front. A
panel link brings Tabs to the front. No item has a side, so there is no shift-click and no grip.

### The narrow layout

The lower breakpoint is 840px. Below it the board does not fit: the canvas needs room for a node
and a card beside it, and under 840px it is a sliver rather than a board. So the narrow layout
does not shrink the wide one — it replaces it.

The deck list takes the canvas's place. The three zones run left to right on a board as vertical
stripes; on a phone the same three run as tabs, opening on running. The model does not change
between the surfaces, only the axis. A row keeps the task node's three registers — the title, the
state in words, then the identity — and its whole state vocabulary, drawn as a full-width band
with the phase bar still on its left edge.

One thing owns the screen. There is no panel and no tab strip: a node's detail, a session and a
document each take the viewport. The wide layout's floating card has no
place here, so the detail is flat — it overlaps nothing, and the Overlap Test says it earns no
shadow.

These rules hold below the break:

**The Thumb Floor Rule.** Anything a finger presses is at least 48px high. `--control` is that
height below the break, so a control meets the floor with no rule of its own. Density is bought
back with space, never by going under the floor. The text of each field is at least
`--text-sm` (16px), because iOS zooms the page on text under 16px and does not zoom back.
`base.css` sets both.

**The Phone Type Rule.** A phone is held further from the eye than its pixels suggest, so the
narrow layout re-points all seven `--text-*` tokens (§ Hierarchy): prose is 20px, and every size
but `--text-caption` (8px) is at least 16px. The chip height `--chip` goes from two units to three with them. At this scale the top bar
does not hold the brand, so the brand is hidden visually and kept for a screen reader.

**The Still Screen Rule.** The app is the visible area and does not move.
`layout/visualViewport.ts` writes the visual viewport's height to `--vvh` and its top to
`--vvt`, once a frame at most. `#root` is fixed at `--vvt` and `--vvh`, so a soft keyboard
shrinks the app from the bottom and the app ends on the keyboard. It is not fixed to
`bottom: 0`: on iOS the keyboard shrinks the visual viewport, not the layout viewport, so the
bottom of the layout viewport is behind the keyboard. `html` and `body` take the same height, so
iOS has no document to scroll toward a field. A modal dialog is outside `#root`, so it anchors to
`--vvt` and `--vvh` itself. iOS scrolls toward a focused field before the keyboard shrinks the
box, so a dialog scrolls itself to its focused field once more, a frame after each resize. The
document has `overscroll-behavior: none`, each full-screen scroller has `contain`, and a double
tap does not zoom. Pinch zoom stays: while the page is zoomed `--vvh` holds, so the zoom pans
over a still app.

**The One Strip Rule.** A pushed screen has one row of chrome, the screen strip. It holds `×`
(Close), the screen's title, up to two actions of the screen, the attention chip while something waits,
and `⋯` (More). The content gets the rest of the screen. Everything else is one tap away, in the
side sheet that More opens from the right edge:

| Screen   | Strip                         | Side sheet                                                 |
| -------- | ----------------------------- | ---------------------------------------------------------- |
| Changes  | the rev; `‹` `›` for a commit | the branch and Refresh, the rev list, the file tree        |
| Session  | the agent; Stop               | the state, the mode, the meta line, Compact, the subagents |
| Document | the title                     | the task, phase, version, status, Session link, siblings   |

The readings and New are at the top of the More sheet. A pick in the sheet that navigates closes
it. Every side sheet starts its head row with a bare × named Close. It has no caption, as the
strip's Close and More have none. A tap on the backdrop closes the sheet too, but a phone user cannot see that.
The review dock stays on the Document screen, because it is the terminal act (§ Review Dock).

**The Wide Content Rule.** Only a table or a code block is wider than its view, and it scrolls in
its own box. A title for one item, on a surface that can grow, wraps, and so does a branch or a
path beside it. A title in a row of fixed height, or in chrome that a wrap would push down,
truncates, with its full text in the `title` attribute. An id stays on one line and is cut only
with an ellipsis (the Mono Means Literal Rule). A flex or grid child that holds text needs
`min-width: 0`, or its text sets the child's least width. The `wrap` and `truncate` utilities set
it (§ Text utilities). The `long-text` fake scenario is the check: at 390px no view scrolls
sideways.

**The Quiet List Rule.** A row cannot glow without lighting its neighbours, so needs-attention
draws as a field wash and an amber rule rather than the board's glow. An unanswered row draws
the same in Reply Yellow.

**The Nothing Hidden Rule.** Every command the wide layout offers stays reachable: approve, deny,
answer, set status, launch, add to and remove from the desk, edit, and start new work. The one
thing dropped is the comment margin, which draws nothing today.

Reachable is not the same as on screen. A command behind one tap counts. What the rule forbids is
a command the narrow layout cannot reach at all.

Reading matter is held to a second test: it earns its band when it is notable. The usage chips are
the worked case. The wide bar shows both windows always; the narrow bar shows a window only when
its budget quotient has turned the tone amber or red, and withholds a stale reading whatever its
tone.

The staleness rule is a trade the narrow bar makes, not a correctness claim. A usage reading
arrives only while an agent takes a turn, so a quiet desk holds an ageing one — which means the
narrow bar stays quiet in the case the operator most often opens it. The wide bar is where a
stale figure is read: `SplitChip` greys it and its title gives its age. The narrow bar buys a
quiet row at that cost.

The chrome is two rows on the deck. The first row holds the brand, the readings, the attention
chip, Filters and New. Filters opens the side sheet with the filters of the view on screen, one
control a row. Its caption counts the filters in force, as "Filters · 2", so a filtered view
never looks like the whole: the Nothing Hidden Rule. The sheet stays open while a filter changes. The second row holds Desk, Tasks and Worktrees at equal thirds. That row is
navigation, not chrome, so the deck keeps it. A pushed screen replaces both rows with the screen
strip (The One Strip Rule).

The detail screen draws the node card's body as a screen. Each link and each document is a
full-width row, `--control` high, with a hairline under it. The commands are a bar pinned to the
bottom of the screen: a row of captioned icons, each as wide as its caption, that wraps to a
second row. A split button's menu
opens upward there. This is how the detail screen meets the Nothing Hidden Rule.

The task list and the worktree table do not fit as tables. Below the break each row is a stack:
the name leads, the quiet facts follow, and the commands end the row.

### Named Rules

**The Swipe Reveal Rule.** A row's swipe action shows behind the row as the row moves: its icon
and its label, quiet. At the threshold the reveal fills with the accent and its icon steps up.
Only a release past the threshold acts. A release before it springs the row back and does
nothing, so a swipe started by mistake costs nothing.

**The Fixed Board Rule.** A card moves only when its own work moves: it changes zone when it
starts or finishes, and the cards behind it close up. Its lane never changes, and its order
against the other cards in its zone never changes. The board reports progress and nothing else.
In a project lane the order holds inside a worktree box: a card keeps its box, and its order
against the other cards of that box. The order of the boxes follows their columns: the box whose
first card is leftmost sits highest.

## Elevation & Depth

The system is flat and tonal. Depth is normally made with one step of background tone plus a
one-pixel hairline: `--bg-raised` against `--bg`, separated by `--border`. That is how bars,
nodes, tab strips and table headers all sit forward without a shadow.

A shadow is earned by overlapping other content. The expanded node card is the only element
that currently qualifies — it floats over the canvas, over other nodes, and must read as
detached from the board rather than part of it. Menus, popovers and dragged elements will
qualify on the same grounds when they arrive.

Attention is a separate channel from elevation. The glow on a node that needs the operator is
a signal, not a lift: it does not mean the node is closer, it means it is asking. Reduced-motion
users get a static ring in place of the pulse, so the signal survives without the animation.

### Shadow Vocabulary

- **Card lift** (`--shadow-card`: `0 16px 40px rgba(0,0,0,0.55), 0 1px 3px rgba(0,0,0,0.5)`):
  a floating surface over the board. Two layers — a wide soft cast for separation, a tight dark
  one for the contact edge. Retuned for the light scheme rather than reused.

### Named Rules

**The Overlap Test.** Before adding a shadow, ask whether the element overlaps content it is
not part of. If it does not, it is layered with tone and a hairline instead. Importance alone
never earns a shadow — that is what colour and position are for.

## Shapes

A restrained, rectilinear form language. Two radii carry almost everything: 6px on small
controls and nodes, 10px on the elements that read as panels or cards. The step between them
is the only size cue the corner language gives.

Pills (999px) mark three things: a button, a chip and a dot or count badge. Fill tells them
apart: a button is filled, a chip is outlined. A field is the exception to the round corner: it
takes `--radius-field` (4px).

Panel tabs round their top corners only, at `--radius`. They are rounded where they leave the
strip and square where they join the body, in the manner of an editor's tabs, and the tab in
view is marked by a hairline outline rather than by a fill.

The signature form is the phase bar: a 4px left border in `--phase` on every task node and
every expanded card. It is the one place the system uses a heavy line, and it turns a rectangle
into a labelled unit — the same trick a file tab or a log line uses, read at a glance from the
edge rather than the content.

### Named Rules

**The Left Edge Rule.** The 4px left border carries phase and only phase. A border on any other
edge means something else — a full border colour is node state, and a shifted border colour
means attention or fault. The rule governs which channel the edge carries, not how brightly it
burns: a stopped node drains its phase hue toward the border and the edge is still phase.

### Brand mark

The mark is a twister made of lines of code, between a half-height `}` at the top right and a
half-height `{` at the bottom left. The twister curves in an S, so its tip reaches the `{`. It is
one colour, the same as the word `maelstrom` beside it: `--n8` on the dark theme and `--n2` on the
light theme. `logo.svg` switches between the two itself, with `prefers-color-scheme`, because an
`<img>` does not inherit the page's colour.

The home-screen icons put the mark on console-slate. The maskable icon keeps the mark inside the
centre 80 %, which an Android launcher never crops.

## Components

### Text utilities

`styles/text.css` holds three global classes. A component adds one as a plain string, as it adds
`srOnly`: ``className={`${styles.title} wrap`}``.

| Class      | Sets                                            | Use it on                                     |
| ---------- | ----------------------------------------------- | --------------------------------------------- |
| `nowrap`   | one line                                        | a pill, a chip, a short fixed label           |
| `wrap`     | wraps, breaks an unbroken token, `min-width: 0` | a title for one item, on a surface that grows |
| `truncate` | one line, cut with an ellipsis, `min-width: 0`  | a field in a fixed-height row, chrome, an id  |

A module class does not repeat what the utility sets. It keeps the font, the colour and the flex.
A rule inside a media or container query stays in the module, because the utility applies at all
widths. See the Wide Content Rule.

### Task Node

The unit on the board. A fixed 220×76 raised surface, 6px radius, hairline bordered, with the
4px phase bar down its left edge. Three registers, read top to bottom: the title, then a status
dot and the state in words, then a footer of identity — the id, and the phase at the right edge.
The footer is pushed to the bottom, so the gap above it separates identity from the decision.

Every field on the node holds one line and truncates with an ellipsis. A field that wraps costs
the node its fixed height and pushes the title out of view.

The node names neither its project nor its worktree. The lane names the project, and the
**Worktree box** round the node names the worktree and its branch. The deck list has no lane and
no box, so a deck row names both.

- **Rest:** hairline border, full opacity.
- **Working:** border takes the phase hue and a 2.4s box-shadow pulse breathes outward. Under
  `prefers-reduced-motion` the pulse becomes a static 2px phase ring.
- **Needs attention:** Alert Amber border, a 1px ring and a 14px amber glow.
- **Ready:** a hollow dot in the phase hue. Hollow means the work has not started and filled
  means it runs, so the shape tells ready from working even though both take the phase hue.
- **Idle:** 0.8 opacity. **Queued:** dashed border, 0.65 opacity.
- **Unanswered:** the needs-attention treatment in Reply Yellow: border, 1px ring and 14px glow.
  It has no count badge. The words say `Unanswered`, so the state does not depend on colour
  alone.
- **Stopped:** the surface drops to `--bg-sunken`, the phase bar drains to `--phase-dormant`, the
  dot goes hollow in `--tone-dormant` and the title steps to `--fg-muted`. It recedes by sinking
  rather than by fading, because a stopped session is resumable: fading it to done's 0.5 would
  file it as history when it is unfinished work in the running zone. Opacity rises to 0.9 — the
  surface already carries the backgrounding, and the operator still has to read the card to
  decide whether to resume it. The expanded card takes the dot alone: it is open because the
  operator chose to read it, so backgrounding it would fight the act of opening it.
- **Finalising:** a hollow Clear Green dot. The task is closed and an agent is still carrying
  the PR, so the node reads as closed but not yet settled.
- **Done:** 0.5 opacity, Clear Green dot. **Cancelled:** 0.5 opacity, faint dot — terminal, but
  not a success.
- **Exited:** Fault Rose border and ring.
- **Focused:** 2px Signal Blue outline, 2px offset — the same ring as `:focus-visible`.
- **Expanded:** children fade to 0 over 120ms while the card grows in its place.

The status dot restates the state in colour, so state is carried twice — position and hue —
and neither alone is load-bearing.

- **Drift:** a small amber caret beside the state, never a border or a glow — a note on the
  state, not a state of its own. A second amber dot would read as a competing state; a different
  shape reads as a note. The Two Calls Rule keeps the border and the glow for work that
  calls the operator, and the card carries the explanation.

### Node Card (expanded node)

The board unit opened in place: 440px wide, 10px radius, strong hairline, phase bar retained,
lifted on `--shadow-card`, capped at 70vh with internal scroll. Title at 16px/600, then the
identity block — id, phase, and a mono line of model, permission mode and cost — then a status
line, the brief, the decision block, and a footer. The task's settable status sits at the right
end of the id line. A hairline opens each band from the one above.

The footer reads in three steps: the Session link and the documents, the agent's commands, then
the worktree area. The order follows the reading path: what the agent says, what to do with the
agent, then where the work lives.

The worktree area is the last band of the card. One hairline and one `WORKTREE` head set it
apart, in the style of a document kind head, with no nested box. Under the head sit the name and
branch in mono, the links — Changes, the PR chip, the dev env, cmux — and then Sync, the
environment control and the close control, right-aligned. The branch wraps rather than
truncates, as the mono line does.

A pull request that is ready to merge adds a Merge button to the worktree area, first among its
commands, before Sync. It is the area's one primary button.

The **Worktree card** is the same area under a header: the worktree name at 16px/600 mono, the
project below it, and the close button. It is 360px wide and has no phase bar, because a worktree
has no phase. Start free agent is its one primary button, alone on the last row.

On both cards the close button is a bare glyph that lifts from faint to full on hover.

The node card's mono line wraps rather than truncates. The card is content-sized, so it can spend the
height a second line costs. An ellipsis cannot: it takes the end of the line, where the cost
sits, and the cost is the reading the operator opened the card for. This is the opposite of the
small node's rule above, because the small node holds a fixed height and the card does not. The
model reads as its alias — `opus`, not the `claude-opus-5` a running agent reports — so the line
needs a second one less often. Check the wrap in a browser — see "Seeing a change".

The brief is the task's own content, rendered as markdown at card scale. It clamps to about four
lines and fades out at the cut, with a More control that opens it in place. A brief of four lines
or fewer shows whole and offers no control. The card measures itself when its size changes, so
opening a long brief pans the card back into view.

An agent past a stage gets one line under the status strip, in the identity register: the stage
in Clear Green, then its cost and its age — `built · 19k · $0.24 · 6m ago`. The latest stage
only, and nothing at all when there is none. No hairline: the band gap separates it, as it
separates the status line from the Now block, and a rule would rank a closed stage above the
brief. See **Milestone bar**.

A task node with follows relations gets one more band above the footer: Follows, then Followed
by, each headed in the Now block's register. A row puts the title over the id and status, with
its desk toggle at the right. The title of a task off the desk takes `--fg-muted`.

A drifting task gets its own band under the status strip: the amber caret, a sentence naming
both the task status and what the agent is doing, and a button that applies the fix where there
is one. The sentence takes `--fg-muted`, because an open attention item is a real block and
drift is a bookkeeping note — only the caret is amber.

When the node needs attention the card's border takes Alert Amber — but the left edge stays
the phase hue. Two channels, two edges, no conflict.

An unanswered node's card takes a Reply Yellow border, and replaces the Now line with a box on
the chassis of a decision's prompt: a 1px `--unanswered` border and an 8% wash. The box holds
the heading `Last said`, the agent's last three messages, and a reply field. The list scrolls past 22em, so three long messages cannot push the reply out of reach.

### Buttons

- **Shape:** a filled pill (`--radius-pill`) on `--bg-control`, with no visible border,
  `--control` high. The side padding is `--control-pad`: 12px at 32px, 18px at 48px. The label is
  `--text-control` (14px; narrow: 22px). No component sets a button's padding or its font size.
  A button with an icon shows a caption on the narrow layout: see § The narrow caption.
- **Filled, not outlined:** a button is filled and a chip is outlined. The two never share a
  look, so the eye can tell what to press from what to read. One exception: the On desk toggle
  drops its fill off the desk, so the off state reads quieter than the on state. Hover brings the
  fill back.
- **Hover:** the fill steps to `--bg-control-hover`. Nothing moves.
- **Primary:** a Signal Blue fill with `--fg-on-hue` text at 600 weight.
- **Quiet:** muted text on the same fill.
- **Split button:** two segments in one fill, divided by a hairline in the label colour. The
  label segment has 8px on its flat side; the chevron segment's padding is 0 8px 0 6px.
- **Link:** no chassis — no border, no background, no padding. Accent-coloured text, underlined
  only on hover. For a control that reads as prose, not a box, e.g. "Show less" beside a body
  that is already its own expand control.
- **Disabled:** 0.5 opacity, default cursor.
- **One line:** a pill holds its label on one line. `AppButton` adds `nowrap` to every variant but
  Link; a raw `<button>` drawn as a pill adds it itself. `base.css` does not set it on `button`,
  because a `<button>` means clickable, not a pill. A `<button>` that draws a title or a row
  follows the Wide Content Rule.
- **Focus:** the global 2px Signal Blue ring at 2px offset. Never removed.

#### Icons

An action has one icon, and `ui/actionIcons.ts` holds the whole set. A call site names a verb,
such as `actionIcon('start')`, and never imports from lucide itself. So two buttons that do the
same thing cannot draw it two ways. The icons are lucide's, at its 2px stroke on a 24px box. At
`--icon` (16px) that draws at about 1.3px.

Two kinds of icon stay hand-drawn. The desk icons have no lucide equivalent, and their arrow
takes its own colour: see the Reporting Rule. The On desk toggle draws the same desk without an
arrow, because it shows a state, not an act (`shell/DeskStateIcon.tsx`). On hover it draws the
arrow icon of the act a click takes. The GitHub mark stays too, because lucide's brand
icons are deprecated.

Every icon is decorative. Lucide marks it `aria-hidden`, and the button's text stays its
accessible name. An icon outside a button is `1em` by default (`base.css`). A link's inline icon,
a panel tab's cross and a file tree's folder keep 12px. A chip's icon takes `--text-sm`.

#### The narrow caption

A button with an `icon` shows its text after the icon on the wide layout. On the narrow layout it
shows the icon over a caption. The button keeps the 48px height of the Thumb Floor Rule and stays
a pill. Its width is only the caption's, so five actions fit one row at 390px. A button with no
icon keeps the pill above, with its text at `--text-control`.

#### Dialog footers

A dialog footer ranks its actions. The primary and the secondary buttons sit on the right, with
icons. The exception actions — Cancel, Clear, Close, Keep editing — sit on the left as links,
with no icon. So Clear never sits beside Start at the same weight. A link in the footer keeps
the control height, so it is still a 48px target on the narrow layout. The comment boxes end with
the same `DialogFooter`.

### Chips

- **Attention chip:** a button, so it is filled: Alert Amber text at 600 weight on a fill
  mixed 18% with amber. At zero it drops to faint text on the plain fill — present, unlit, not
  hidden. Its count
  is the number of `needs-attention` nodes drawn. A second count follows in `--unanswered`,
  behind a 6px dot, when an unanswered node is drawn. The chip is disabled only when both
  counts are zero.
- **Height and type:** a chip is not a control, except the agents chip on the Desk. A hue chip is `--chip` high: 16px on the wide
  layout, 24px on the narrow. Outlined, with `--text-sm` text in the chrome's face. A chip never
  takes the mono face; tabular digits hold its width. The attention chip is a button, so it is
  `--control` high and filled. A split chip is also `--control` high, so the readings, the
  attention chip and the tabs in the top bar are one height.
- **Tab chip:** a mono task id, one step back from the label. The smallest possible restatement
  of "which agent is this". Phase is not repeated here — it runs down the tab's leading edge.
- **Count badge:** a 16px amber pill, 700 weight, on the sunken ground. Circular by construction.
- **Split chip:** one box in two rows, with an 8px radius — what is measured, over what it
  reads. The label is two thirds of `--text-sm` (8px on the wide layout) with 3px below it, so
  the value leads. On the narrow layout the label is about 11px. It is the one text under the
  Phone Type Rule's floor: a fixed word the eye does not read twice. Sunken ground, so it sits _in_ the raised bar; tabular value, so the chip holds
  its width as the number ticks. Only the value takes the tone. Both halves read at rest,
  unlike the hue chip: a reading nobody hovers is a reading nobody has. A reading too old to
  vouch for drops to faint and dashes its outline, and the chip itself gives up the tone.
  On a usage chip the tone reads pace, not the number beside it. A high percentage near a
  reset stays quiet; a low one early in a window can sit amber. The title carries both figures
  — what is consumed and what the window allows for by now — because colour alone cannot
  explain a tone the value contradicts, and the gap between the two numbers is the reading.
- **Agents chip:** a split chip that is also a button while the Desk shows. A click sets the
  agent status filter to the next of All, Working + Idle and Working. The chip greys the count
  that the filter leaves out: `6/` for Working + Idle, `/11` for Working. The filter is the
  only state, so the chip and the Agent status dropdown always agree, and they share their
  labels. A status outside the cycle, such as Planned, greys nothing, and a click goes on as
  from All. Off the Desk the chip is a plain reading: the filter's effect is drawn on the Desk,
  so a click elsewhere would change nothing in view.

### Shell command

A command the operator asked for with a `!` line. `CONTEXT.md` keeps it apart from a Bash tool
call: the host runs it on the operator's behalf and injects it as user turns, so it is something
they did, not something the agent did.

It therefore takes the interactive channel rather than the agent's ledger — a Signal Blue wash
and border, with a tracked `you ran` label — and reads as a quieter sibling of a user turn. A
tool call recedes; a shell command does not, because the operator put it there.

### Milestone bar

The full-width rule the transcript draws where a stage of the work closed, holding the stage and
what it cost: `built · 95k · $2.10`. It takes the compact rule's shape — a hairline with the words
sitting in it — and, unlike a compact, a hue: Clear Green at 40% for the rule and full strength
for the stage name, with the figures left muted so the stage leads.

`--ok` rather than a new token, because a closed stage is exactly what Clear Green already means,
and because done work should recede. The Reporting Rule allows the hue only as a report of state,
and "which stage closed" is state. A milestone is never permitted Signal Amber and never a glow:
the Two Calls Rule keeps both for a node that calls the operator, and a milestone asks
for nothing. Beside an amber attention prompt it must read as a boundary, not a second alert.

The figures are the stage's own delta, not the running total the session header already carries. A
stage name the flow does not declare drops to the compact register and gains `(?)`. It is shown,
because a typo must be visible, but it has closed no stage anyone can price, so it does not take
the lit rule.

The node card carries the same reading as one line in the identity register, with the stage's
age: see **Node Card**.

### Panel Tabs

A horizontally scrolling strip of tabs on `--bg-raised`, divided by hairlines, 32px minimum
height, each tab as wide as what it holds. A tab leads with its identity: the qualified task id,
mono at `--text-sm`, or a free agent's own id in the same slot.

A session tab carries nothing else. The id alone says which session it is, and a real qualified
id — `maelstrom/2026-09-22.1` — is long enough that a word beside it squeezes to a letter. Only
a document adds a label, its own title, in the interface face at `--text-sm`. The id never
truncates, by the Mono Means Literal Rule, so a long document title takes the ellipsis; neither
ever wraps, because a wrapped tab costs the strip its height.

The task's own title goes in the tab's native tooltip, which is the one place the strip has room
for prose. The accessible name is pinned with `aria-label` to the same id and label the eye
reads: a computed name would take the contents _and_ the close button's label, announcing
"NORT-7 Close NORT-7".

The tab in view is ranked structurally rather than decorated, and it is marked out by a line
rather than by a ground of its own. It takes the panel body's `--bg`, which the status row
under it takes too, so the three read as one surface the strip is cut away from. A
`--border-strong` hairline outlines the three edges that face the strip, and nothing is drawn
along the bottom, where the tab opens onto what it heads. It rounds its top corners at
`--radius`, the radius `base.css` gives every button, and leaves its bottom square. That is the
tab idiom: rounded where it leaves the strip, square where it joins the body. Its text goes to
`--fg` and its id brightens with it.

The line under the strip is drawn per tab, not across the strip and then masked. Each tab owns
its own segment as a `border-bottom`, the tab in view sets that segment transparent, and a
`::after` on the strip carries the line past the last tab to the panel's edge. Masking is not
available: `.strip` has `overflow-x: auto`, which clips on both axes, so no tab can escape the
strip's box to paint over a line below it.

Phase runs down that tab's leading edge at 4px, the Left Edge Rule's own width, as a node card
and a desk row draw it. `data-phase` is set on the tab and inherited, so the edge reads
`--phase` and no component looks a hue up. Only the tab in view draws one: a phase edge on every
tab at once read as a row of swatches and cost the strip its rank. Every tab reserves the 4px,
transparent, so the strip does not shift as the view moves.

The strip pads 4px above its tabs, so the tab in view is a shape standing in the band rather
than a block filling it.

The status row is its own surface, not the top of the body: the session's `.head` and the
document's `.header` each hold the controls for what the tab opened, and the reading below them
is content. They share the tab in view's ground, so the seam the reader sees is the status
row's own bottom hairline.

The workbench story builds that shape rather than a flat stack — a scrolling box holding a
status row and a reading, as `Panel.tsx` does. The nesting is what makes the strip legible: a
flat shell let a tab appear to escape its strip, which the real panel's `overflow: auto` never
permits.

The focus ring is the global one and is never removed, but a tab has to redraw it on an inset
layer. A tab's edges sit flush against its neighbours', so a ring outside the box is clipped,
and one 2px inside lands on the phase border — hiding the phase on the one tab the keyboard is
on. Drawn inside the padding box, both channels read at once.

The close control is the `close` icon from `actionIcons.ts` at 12px, the size of the panel and
external link icons beside it. On the tab in view it sits in the flow and is
always visible, so that tab pays for its width. On every other tab it is absolute and overlays
the id's last characters, so the tab does not change width under the pointer. The id does not
ellipsise under it — the cross's fill covers the characters outright, which is a live conflict
with the Mono Means Literal Rule and is recorded as such rather than settled.

An inactive tab reveals its cross only under the cross itself, not anywhere on the tab: a whole
row lighting up as the pointer crossed it read as a row of controls rather than a row of tabs.
The revealed cross takes a circular ring and a `--bg-raised` fill, which is what holds it off
the id underneath. The ring is the hover treatment alone — the tab in view keeps a plain cross,
and every tab reserves the ring's width so nothing shifts as it appears.

The control fades rather than hides: `visibility: hidden` takes an element out of the focus
order, which would leave an inactive tab with no keyboard route to closing it, and an invisible
button keeps the hit area the cross's own hover needs. The strip is one tab stop; arrows move
between tabs, and Tab reaches every close button in turn.

### Session header

One mono line above the transcript, on a hairline, holding two groups. The live reading comes
first: the agent id, its state in words, the permission chip, and what it waits on — the state
takes the accent, a wait takes Alert Amber. Standing context follows in faint text: worktree,
branch, model, context size and cost, dot-joined, with quiet Stop and Compact buttons at the
right, in that order — Stop is the more urgent act and reads first. An empty field drops out
rather than showing a zero. The model reads as its alias, as on the node
card.

The rank is the point, not the row count. A reader watches the live reading and consults the
standing context, so the second group recedes a step in colour and never competes for the same
glance. Below 30rem the row breaks into two, live reading above: at the panel's 320px minimum one
row would truncate both groups to nothing. 30rem is the Transcript's breakpoint, so the panel
changes shape once rather than twice. Everything truncates rather than wraps: a wrapped head
would push the transcript down.

The size is the **context** — what the prompt last held — not the session's running token total,
which cannot answer whether to compact. See `docs/dev/agent-daemon.md`, "A turn". The cost beside
it is what says how much work the session has done.

The break is a container query, so jsdom cannot compute it. Verify it by dragging the panel to
its 320px minimum in a browser.

### Table (task list)

Hairline-separated rows, no zebra, no vertical rules. Headers are tracked uppercase micro-labels
in faint text. Ids and branches are mono. The title wraps, and so does a long branch; an id stays
on one line. The filter row is sticky on a raised ground so the
controls stay reachable through a long list.

### Fields

A field has a 4px radius (`--radius-field`), set once in `base.css`. It reads as a place to type
beside a pill, which is a thing to press.

Selects and text inputs share one chassis: field background, hairline border, 6px radius, tight
padding, capped at 180px so a long branch name cannot push the filter bar apart. They inherit
the interface font — a form control never falls back to the browser's own.

A multi-line field grows to fit its text (`ui/TextArea` with `grow`), and its container scrolls.
A field with no scrolling container, such as the conversation input or a review dock field, caps
at half the visible height and scrolls itself. The visible height is `--vvh`: `main.tsx` writes
the visual viewport's height there, because a soft keyboard does not shrink `dvh`. On a phone the
conversation input may take the visible height less four controls.

A text field does not share its row with the buttons that submit it. The field takes the full
width, and its buttons sit in a row under it, at the right edge. A field between two buttons gets
a sliver of a phone's width and reads as a stray control on a wide screen. One shape on every
layout also means the field does not move when the layout changes, so a draft keeps its caret.
The Review Dock shows the rule; the session composer puts Send in the Attach row.

### Decision

The block shown when an agent waits. The expanded node and the document tab render one component,
so the two can never drift, but each reads it for a different reason. A card is read; a dock is
acted on. The variant says which.

**On a card** the decision is the reading. A context rail — 2px strong hairline on the left,
muted text — carries the last three things the agent said, under an uppercase micro
heading. Each is prose. The prompt follows, in its bordered amber box.

The rail is context, so it never outranks what it is context for. Three items is a small count,
but one item may be a whole message, so the count alone does not bound the height: the rail
clamps to about ten lines and fades at the cut, with a control that opens it in place — the same
idiom the node card uses for a long brief. The heading is also a fold, so a rail the operator
has already read can be put away entirely. It opens by default and the state does not persist,
because the panel keeps no view state across renders.

### Question

A question the agent asked reads four ways, depending on what has happened to it.

**Answered** is the operator's voice, so it takes the operator's wash rather than the amber
`.prompt` chassis. The question sits as a caption
at `--text-sm` `--fg-muted`; the answer reads on its own line at the reading rank in `--fg`.

**Open** carries Answer as the one primary button. A plain split button at the row's right end
holds **Decline** and, in its menu, **Decline & stop**. It is plain because refusing is the rare
act, and it sits apart from Answer so that a slip does not refuse. It shows on every step because
a refusal needs no answer.

**Declined** is also the operator's voice, so it takes the same wash as an answer. The questions
sit as captions, and the one word "Declined" takes the answer's line. The daemon also denies a
question on the operator's behalf, as an interrupt does. That reason follows the word as a
caption, so the transcript does not claim a refusal the operator did not make.

**Stale** — nobody answered before the ask closed — is not the operator's voice, so it takes no
wash. It reads in the `.note` register: `--text-sm`, `--fg-faint`, mono, the same reading the
session's own remarks about itself take elsewhere in the transcript.

**A permission** keeps `.prompt`, whether decided or stale. This is the deliberate asymmetry: a
settled question reads as a user turn because it _is_ a structured message the operator composed,
while a settled permission is a yes/no on a piece of machinery, never a message. Do not "fix" a
permission into the settled-question wash — the two are answering different questions about what
happened.

### Review Dock

The band under a document, holding whatever waits on the operator there. One dock, one place,
whoever is asking: an agent's own wait takes it, and the document's own review route takes it
when no wait holds it. The two share a chassis, so a reader answers in one place and learns one
shape.

The dock sits below the document because the document is what the reader came for. A decision
above the thing being decided makes the reader scroll past the ask to reach the plan, and on a
phone it costs most of the plan's screen. The dock is also the terminal act, so it belongs at the
end of the reading path and under the thumb.

The prompt stops being a card here. The band already carries the rule and the wash, so a second
border around the same message reads as a box inside a box. The heading goes too, and so does
the sentence naming the ask: Approve and Deny say the act, and a sentence above them is a kicker
above a heading. A permission's tool input goes with them, because a band is no place to read a
block of JSON.

**The field, then Approve, as the primary.** The field takes the full width (§ Fields). Under it,
at the right edge, Approve leads the negative act — Deny, or Decline. Every approval in the dock
reads the same way round, whether it answers the agent or the document. The band's whole claim
is that the reader learns one shape. Button rank carries the affirmative act; colour does not,
so Approve takes no green of its own. The tab order is the visual order: the field, Approve, then
the negative act.

The context is offered, not spent. `Before this · 3` leads the band and opens the rail as a sheet
over the document rather than pushing it — the document never reflows for a decision. The sheet
overlaps content it is not part of, so the Overlap Test earns it the card lift. Escape closes it.
Below 30rem of panel the control drops to a line of its own, because four controls do not fit
one row on a phone.

- **Waiting:** the top rule takes Alert Amber and the ground takes an 8% amber wash. The Quiet
  List Rule holds here as it does on a deck row: a docked band signals with a rule and a wash,
  never a glow.
- **Settled:** the plain hairline and the raised ground, because nothing is asking.
- **Narrow:** every control is `--control` high, and a field takes `--text-md` so iOS does not zoom.

A plan review answers the agent, never the document. Approving the document would flip it and
retire the attention item pointing at it, leaving the agent blocked on a request nothing had
answered. The dock therefore offers the agent's Approve and withholds the document's
request-changes route. `Read the plan` is dropped where it would point at the document already
open: in the plan's own tab the link leads nowhere, and on a phone it pushes a second copy of
that screen onto the stack.

### Comment Dock

The band under the Changes tab while change comments are held. It takes the Review Dock's chassis
in its settled state: the plain hairline and the raised ground. Nothing waits on the operator
here, so the dock takes no amber.

**Post comments leads, as the primary.** Then the count and the agents that the post reaches, then
Clear. The dock names the recipients because the post goes to every agent in the worktree, and
the operator must see that before the click. With no agent in the worktree, the dock says so and
Post is disabled.

The dock is absent when no comment is held. An empty band under a diff reads as a control that
does nothing.

A comment box sits in the diff, below the last line of its range. A selected row takes an accent
wash over its add or remove ground, so the row keeps its kind.

## Seeing a change

There are two ways to see a change with no orchestrator, no daemon and no live agent.

| Tool      | Start it                  | Use it for                                 |
| --------- | ------------------------- | ------------------------------------------ |
| Fake mode | `mael env start web-fake` | the whole app on a scenario, at any width  |
| Ladle     | `mael env start ladle`    | one component's states, drawn side by side |

The fake mode is the production `App` on the fake server of the test suite. Its index lists the
scenarios, and `/scenario/<name>/desk` opens one. Every URL of the app works under that base, so
`/scenario/detail/desk/task/NORT-12?panel=changes/northwind-delta` opens a card and a tab: copy
the URL from the app itself. `?hold=1` keeps each reply back, for a loading state.
`?refuse=<pattern>` fails each route that the pattern matches, for an error state. A phone on the
tailnet opens the same URL.

A test fails when a value of a protocol set has no scenario, so a new state arrives with a world
that shows it. `src/fake/scenarios.ts` holds the scenarios.

`UI / Controls` in Ladle draws each control on one board. Use it to check that a row of mixed
controls has one height.

Use them before a visual change and after. jsdom computes no layout, so the test suite cannot
answer whether prose ranks above a tool row, where a measure wraps, how a run of calls reads,
whether a docked control clears the thumb floor, or whether a stopped node reads as quieter than
an idle one. A live session is a slow and unrepeatable way to ask, and on the board it can only
show the states its agents happen to be in.

Stories come in two shapes:

| Shape     | Fixture                                                                                                 | Use it for                             |
| --------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| Component | `src/session/transcript.fixture.ts`, `src/canvas/taskNode.fixture.ts`, `src/panel/panelTabs.fixture.ts` | one component's states, drawn directly |
| Whole app | `src/fake/FakeApp.tsx` on a scenario of `src/fake/scenarios.ts`                                         | a surface reached by navigating        |

The whole-app shape mounts the real `App` on the fake server, through the same `deps` injection
`renderApp` uses in the suite. `FakeApp` takes a scenario and an `amend` function for a story's
own entities. A story therefore runs the production tree rather than a stand-in
that can drift from it. `Documents / Review dock` is the worked example.

A component fixture that stands for a state builds it with the production reader, never by hand:
`taskNode.fixture.ts` reads every node's progress through `progressOf`, and `panelTabs.fixture.ts`
supplies a world and a tab list so the strip reads every tab through `tabAttribution` itself. A
story that hand-rolled one could draw a state the code cannot produce, which is the one thing a
fixture must not do.

The stories carry the states worth checking: prose against tool calls, a long ledger run, the
truncation note, the narrow layout under the 30rem container query, a wide panel, every markdown
element at panel width, the review dock waiting and settled, and every node state side by side —
and, for the quiet-block clamp: a two-line clamp with its fade, a block opening with each markdown
element in turn (heading, list, fence, table — the highest-risk case, since the clamp is a plain
`max-height` that cannot know where a block boundary falls), a one-line block that offers no
control, quiet prose on the `--bg-sunken` ground inside an open `skill` row (point a contrast tool
at this one in the light scheme — it is the 4.15:1 case `--fg-recessed` exists for), the three
gap sizes end to end, and an answered question, a stale question and a real user turn side by
side so the two washes can be compared directly. For the tab strip: one tab, four tabs of
different phase — arrow along them, because only the tab in view draws its edge — a session
beside its own plan, a free agent beside a task's, a tab whose entity has gone, a long label
truncating, and four tabs at the panel's 320px minimum, where the label truncates away entirely
and the ids alone tell four agents apart. Hover an inactive tab: its close control overlays the
id rather than widening the tab, so watch that the tab does not move. For the canvas:
`Canvas / Worktree boxes` draws a project lane with four boxes, two of them side by side, nodes with no
box, and a strip of empty boxes that wraps.

Ladle's width control drives the layout break, so the same story at 390px is the phone. Check both
schemes; light is not a courtesy mode. Ladle's theme control switches its own chrome, but a story
renders in an iframe that follows the operating system, so switch the scheme there — or launch a
browser with the scheme forced — rather than trusting the toggle.

## Do's and Don'ts

### Do:

- **Do** put every new colour in `styles/tokens.css` as a semantic token, and read it by role.
- **Do** set phase with a `data-phase` attribute and let `--phase` inherit. Never look up a
  phase hue in a component.
- **Do** carry state in two channels — hue and something structural (a dot, a border, an
  opacity step) — so no state depends on colour alone.
- **Do** use `color-mix(in srgb, var(--token) N%, transparent)` for washes, glows and
  highlights, so they follow the scheme automatically.
- **Do** keep the wide layout's chrome at 13px (`--text-ui`). Nothing goes below `--text-sm`.
- **Do** read a gap, a padding or a margin from `--u`, `--u-half`, `--u-2` or `--u-3`. A bar pads
  with `--bar-pad`.
- **Do** give every interactive element a visible `:focus-visible` ring, and make every action
  reachable from the keyboard — this is a power tool and hands stay on the keys.
- **Do** check contrast in both schemes. Light is not a courtesy mode.
- **Do** provide a static fallback for anything that signals by animation, under
  `prefers-reduced-motion`.
- **Do** truncate with an ellipsis and keep ids on one line: the Wide Content Rule, with
  `truncate`.

### Don't:

- **Don't** name a colour outside `tokens.css` — no hex, no `rgb()`, no named CSS colour.
- **Don't** colour anything that is not reporting state. Emphasis is weight, size and space.
- **Don't** let a third channel glow. Alert Amber and Reply Yellow are the two calls.
- **Don't** add a shadow to something that does not overlap other content.
- **Don't** outline a button or fill a chip.
- **Don't** put the phase hue on any edge but the left one.
- **Don't** use mono for prose, or the interface font for an id.
- **Don't** show a raw agent state, or any term `CONTEXT.md` lists under `_Avoid_`.
- **Don't** let the board reflow because an agent progressed.
- **Don't** hardcode a font size in a component — read the `--text-*` scale.
- **Don't** let a title push a view wider than the screen.
