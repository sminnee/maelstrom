---
name: Maelstrom Orchestrator
description: Mission Control for a room full of agents — one lit surface where every unit reports and the one that needs orders says so.
colors:
  console-slate: '#0f1115'
  console-slate-raised: '#171a21'
  console-slate-sunken: '#0a0c10'
  hairline: '#2a2f3a'
  hairline-strong: '#3d4454'
  readout: '#e6e8ee'
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
  title:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '16px'
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '14px'
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '12px'
    fontWeight: 500
    lineHeight: 1.35
  micro:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '12px'
    fontWeight: 500
    letterSpacing: '0.06em'
  mono:
    fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: '12px'
    fontWeight: 400
rounded:
  sm: '6px'
  lg: '10px'
  pill: '999px'
spacing:
  '1': '4px'
  '2': '8px'
  '3': '12px'
  '4': '16px'
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
    backgroundColor: '{colors.console-slate-raised}'
    textColor: '{colors.readout}'
    rounded: '{rounded.sm}'
    padding: '2px 8px'
  button-primary:
    backgroundColor: '{colors.console-slate-raised}'
    textColor: '{colors.signal-blue}'
    rounded: '{rounded.sm}'
    padding: '2px 8px'
  attention-badge:
    backgroundColor: '{colors.alert-amber}'
    textColor: '{colors.console-slate-sunken}'
    rounded: '{rounded.pill}'
    size: '16px'
  panel-tab:
    backgroundColor: '{colors.console-slate}'
    textColor: '{colors.readout}'
    rounded: '0'
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
- Dense and quiet at rest — one attention state is allowed to be loud.
- Light and dark are equal citizens, both driven from one semantic token layer.

## Colors

An instrument palette: a cool blue-grey field, with saturated hues reserved entirely for
reporting state. The four phase hues run a deliberate spectrum — violet, blue, teal, amber —
so a task's position in its life is readable from hue alone.

### Primary

- **Signal Blue** (`--accent`): interactive affordance and nothing else. Links, panel links,
  the focus ring, the active tab underline, the running-command line, the text selection wash.
  If it is blue, it can be clicked or it has the operator's focus.

### Secondary

The state channel. These four never decorate; each one means one thing.

- **Alert Amber** (`--attention`): the only colour permitted to interrupt. A node that needs
  the operator, the attention chip, the count badge, comment highlights.
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
- **Plan Blue** (`--phase-plan`): producing a plan for one task.
- **Build Teal** (`--phase-build`): building, reviewing, opening the PR.
- **Land Amber** (`--phase-land`): answering CI and review on an open PR.

### Neutral

- **Console Slate** (`--bg`): the field everything sits on.
- **Console Slate Raised** (`--bg-raised`): nodes, cards, bars, tab strips — anything that is
  a surface rather than the room.
- **Console Slate Sunken** (`--bg-sunken`): the recessed ground beneath the field.
- **Hairline** (`--border`) and **Hairline Strong** (`--border-strong`): separation without
  weight. Structure is drawn with one-pixel lines, never with fills or heavy rules.
- **Readout** (`--fg`), **Readout Muted** (`--fg-muted`), **Readout Faint** (`--fg-faint`):
  three steps of text presence — the thing itself, its metadata, its scaffolding.

### Named Rules

**The One Source Rule.** No file outside `styles/tokens.css` names a colour. Not a hex, not an
`rgb()`, not a named CSS colour. A component that needs a colour the semantic layer does not
have adds it to the semantic layer.

**The Reporting Rule.** Every hue on screen reports state. Nothing is coloured because it looks
better coloured. When a new element needs emphasis, the answer is weight, size or space —
not a colour promoted out of the state channel.

**The Single Interrupt Rule.** Alert Amber is the only channel allowed to escalate itself with
a glow. If a second thing starts glowing, the design has stopped ranking and started shouting.

## Typography

**Interface Font:** Inter (with `system-ui`, `-apple-system`, `Segoe UI`, sans-serif)
**Mono Font:** JetBrains Mono (with `ui-monospace`, `SFMono-Regular`, Menlo, monospace)

**Character:** Two neutral workhorses doing different jobs. Inter carries everything a human
wrote or a human reads. JetBrains Mono carries everything a machine produced — ids, branches,
paths, commands, tool calls. The switch is semantic, not stylistic: mono is how the interface
says "this is a literal string you may need to type or match".

### Hierarchy

- **Title** (600, 16px, 1.3): the task title on an expanded node. The one place type is
  allowed to be large.
- **Body** (400, 14px, 1.4): the default. Node titles, decision text, transcript prose, table
  rows, controls. This is the size the operator reads all day.
- **Label** (500, 12px, 1.35): metadata and secondary lines — the state line, the footer,
  filter fields, tab titles.
- **Micro** (500, 12px, `0.06em`, uppercase): the phase name, section heads such as "NOW", the
  lane label, table headers. Uppercase and tracked so it reads as a category, not a value.
- **Mono** (400, 12px): task ids, branch names, worktree paths, tool calls, running commands.

### Named Rules

**The Legibility Floor Rule.** 12px is the smallest type in the system, and it is only ever
used for tracked micro-labels and mono metadata. Body text is 14px. Density is bought with
tighter space and shorter lines, never by shrinking type below the floor.

**The Mono Means Literal Rule.** Monospace marks a string the operator might copy, type or
match against something else. Prose never uses it, and a mono string is never truncated
without an ellipsis, because a half-shown id is worse than an obviously cut one.

**The Operator's Words Rule.** State appears in words the operator already owns — "Needs you ·
plan review" — never a raw agent state, and never a term `CONTEXT.md` lists under `_Avoid_`.

## Layout

Two surfaces side by side under one bar. A 40px top bar holds the brand, the view switch and
the attention chip. Beneath it the body splits: the main view takes the remaining width, and
the panel is a resizable right-hand column with a 6px drag grip on its left edge.

The main view is either the canvas or the task list; the top bar switches between them. The
canvas draws the desk as horizontal lanes, one per group when grouped by project or branch,
and none when grouped by `none`. The task list is a full-width table with a sticky filter row.

The canvas grid is fixed and mechanical, which is what makes it scannable: nodes are 220×76,
separated by 56px horizontally and 14px vertically. A lane has 20px of padding, a 30px header,
and 28px between lanes. Every lane is as wide as the board, not as wide as its own content.

Horizontal position is progress first and dependency second. The board runs left to right in
three zones — DONE, RUNNING, NOT STARTED — and a zone boundary sits at the same x in every lane,
so the board reads as three vertical stripes whatever each lane holds. Inside a zone a task sits
one column right of the deepest task it follows in that same zone, so a long finished history
spreads across several DONE columns and a queue of dependent work spreads across several NOT
STARTED columns. A zone no lane uses takes no columns and collapses. When the two rules conflict
— a done task that follows a running one — progress wins, and the follows edge draws backwards.

Spacing runs on a 4px base with four steps in use: 4, 8, 12, 16. Component padding uses the
scale; the canvas uses its own constants because it positions in absolute pixels.

Density is the point. The operator wants many units visible at once, so containers are tight
and gaps are small. There is no responsive breakpoint system: this is a main-monitor tool and
the panel's drag grip is how the operator trades one surface against the other.

### Named Rules

**The Fixed Board Rule.** A card moves only when its own work moves: it changes zone when it
starts or finishes, and the cards behind it close up. Its lane never changes, and its order
against the other cards in its zone never changes. So the board reports progress and nothing
else, and everything else the operator learned about where to look stays true.

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

Pills (999px) are reserved for two things: status dots and count badges. A pill therefore
always means "one small piece of state", never a button or a tag.

Panel tabs are deliberately square. They are a strip of contiguous surfaces divided by hairlines
and marked active by a 2px inset underline in Signal Blue, in the manner of an editor's tabs.

The signature form is the phase bar: a 4px left border in `--phase` on every task node and
every expanded card. It is the one place the system uses a heavy line, and it turns a rectangle
into a labelled unit — the same trick a file tab or a log line uses, read at a glance from the
edge rather than the content.

### Named Rules

**The Left Edge Rule.** The 4px left border carries phase and only phase. A border on any other
edge means something else — a full border colour is node state, and a shifted border colour
means attention or fault.

## Components

### Task Node

The unit on the board. A fixed 220×76 raised surface, 6px radius, hairline bordered, with the
4px phase bar down its left edge. Three registers, read top to bottom: the title, then a status
dot and the state in words, then a footer of identity — the id, and the phase at the right edge.
The footer is pushed to the bottom, so the gap above it separates identity from the decision.

Every field on the node holds one line and truncates with an ellipsis. A field that wraps costs
the node its fixed height and pushes the title out of view.

The node names its project only when nothing else on screen does. The lane header names it when
the board groups by project, and the filter bar names it when the operator filters to one.

The footer names the worktree while an agent runs, as its NATO name. Two agents on one board are
told apart by where they run, so the worktree sits beside the id rather than only on the card.

- **Rest:** hairline border, full opacity.
- **Working:** border takes the phase hue and a 2.4s box-shadow pulse breathes outward. Under
  `prefers-reduced-motion` the pulse becomes a static 2px phase ring.
- **Needs attention:** Alert Amber border, a 1px ring and a 14px amber glow. The one loud state.
- **Ready:** a hollow dot in the phase hue. Hollow means the work has not started and filled
  means it runs, so the shape tells ready from working even though both take the phase hue.
- **Idle:** 0.8 opacity. **Queued:** dashed border, 0.65 opacity.
- **Done:** 0.5 opacity, Clear Green dot. **Cancelled:** 0.5 opacity, faint dot — terminal, but
  not a success.
- **Exited:** Fault Rose border and ring.
- **Focused:** 2px Signal Blue outline, 2px offset — the same ring as `:focus-visible`.
- **Expanded:** children fade to 0 over 120ms while the card grows in its place.

The status dot restates the state in colour, so state is carried twice — position and hue —
and neither alone is load-bearing.

### Node Card (expanded node)

The board unit opened in place: 440px wide, 10px radius, strong hairline, phase bar retained,
lifted on `--shadow-card`, capped at 70vh with internal scroll. Title at 16px/600, then the
identity block — id, phase, and a mono line of branch, worktree, model and cost — then a status
line, the brief, the decision block, and a footer of panel links and commands. A hairline opens
each band from the one above. The close button is a bare glyph that lifts from faint to full on
hover.

The brief is the task's own content, rendered as markdown at card scale. It clamps to about four
lines and fades out at the cut, with a More control that opens it in place. A brief of four lines
or fewer shows whole and offers no control. The card measures itself when its size changes, so
opening a long brief pans the card back into view.

When the node needs attention the card's border takes Alert Amber — but the left edge stays
the phase hue. Two channels, two edges, no conflict.

### Buttons

- **Shape:** 6px radius, raised surface, hairline border, 2px/8px padding. Small and quiet:
  a button is a control, not a call to action.
- **Hover:** border strengthens to `--border-strong`. Nothing moves, nothing fills.
- **Primary:** Signal Blue border and text at 600 weight. Emphasis by colour and weight, never
  by a filled block.
- **Quiet:** muted text on the same chassis.
- **Disabled:** 0.5 opacity, default cursor.
- **Focus:** the global 2px Signal Blue ring at 2px offset. Never removed.

### Chips

- **Attention chip:** a button in Alert Amber at 600 weight with a 50%-mixed amber border.
  At zero it drops to faint text and a plain hairline — present, unlit, not hidden.
- **Tab chip:** an 8px phase swatch at 2px radius beside a mono task id. The smallest possible
  restatement of "which agent is this".
- **Count badge:** a 16px amber pill, 700 weight, on the sunken ground. Circular by construction.

### Panel Tabs

A horizontally scrolling strip of square tabs on a raised ground, divided by hairlines, 32px
minimum height. Inactive tabs are muted text; the active tab takes the field background, full
text, and a 2px Signal Blue inset underline. Each tab carries a phase swatch, the qualified
task id and a close glyph.

### Table (task list)

Hairline-separated rows, no zebra, no vertical rules. Headers are tracked uppercase micro-labels
in faint text. Ids and branches are mono. The filter row is sticky on a raised ground so the
controls stay reachable through a long list.

### Fields

Selects and text inputs share one chassis: field background, hairline border, 6px radius, tight
padding, capped at 180px so a long branch name cannot push the filter bar apart. They inherit
the interface font — a form control never falls back to the browser's own.

### Decision

The block shown when an agent waits. A context rail — 2px strong hairline on the left, muted
text — carries the last three things the agent said or did, with an uppercase micro heading.
Said lines are prose; did lines are mono with a bolded tool name and ellipsis truncation. The
prompt follows. The same component renders inside the expanded node and inside a document tab,
so the two can never drift.

## Do's and Don'ts

### Do:

- **Do** put every new colour in `styles/tokens.css` as a semantic token, and read it by role.
- **Do** set phase with a `data-phase` attribute and let `--phase` inherit. Never look up a
  phase hue in a component.
- **Do** carry state in two channels — hue and something structural (a dot, a border, an
  opacity step) — so no state depends on colour alone.
- **Do** use `color-mix(in srgb, var(--token) N%, transparent)` for washes, glows and
  highlights, so they follow the scheme automatically.
- **Do** keep body text at 14px and never go below the 12px floor.
- **Do** give every interactive element a visible `:focus-visible` ring, and make every action
  reachable from the keyboard — this is a power tool and hands stay on the keys.
- **Do** check contrast in both schemes. Light is not a courtesy mode.
- **Do** provide a static fallback for anything that signals by animation, under
  `prefers-reduced-motion`.
- **Do** truncate with an ellipsis and keep ids on one line.

### Don't:

- **Don't** name a colour outside `tokens.css` — no hex, no `rgb()`, no named CSS colour.
- **Don't** colour anything that is not reporting state. Emphasis is weight, size and space.
- **Don't** let a second channel glow. One interrupt at a time.
- **Don't** add a shadow to something that does not overlap other content.
- **Don't** use a pill radius for anything but a dot or a count badge.
- **Don't** put the phase hue on any edge but the left one.
- **Don't** use mono for prose, or the interface font for an id.
- **Don't** show a raw agent state, or any term `CONTEXT.md` lists under `_Avoid_`.
- **Don't** let the board reflow because an agent progressed.
- **Don't** hardcode a font size in a component — read the `--text-*` scale.
