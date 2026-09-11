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
  label:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '12px'
    fontWeight: 500
  small:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '11px'
    fontWeight: 400
  micro:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: '10px'
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

Each phase also carries a drained form, `--phase-dormant`, for a node whose process has stopped.
It is mixed per phase in `base.css`, at the one ratio `--phase-drain` names, because a custom
property substitutes where it is declared: derived once against the root fallback it would drain
every phase to the same grey. **A new phase hue must add its `--phase-dormant` beside its
`--phase`**, or stopped nodes in that phase lose their edge.

### Neutral

- **Console Slate** (`--bg`): the field everything sits on.
- **Console Slate Raised** (`--bg-raised`): nodes, cards, bars, tab strips — anything that is
  a surface rather than the room.
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
not a colour promoted out of the state channel.

**The Single Interrupt Rule.** Alert Amber is the only channel allowed to escalate itself with
a glow. If a second thing starts glowing, the design has stopped ranking and started shouting.

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

Five steps, each with one job. Sizes are the `--text-*` tokens; no component names its own.

The ramp has two halves, because the app has two jobs. The chrome is scanned and must stay
dense; prose is read and must not.

- **Reading** (`--text-md`, 400, 16px, 1.5): markdown, wherever it appears — a transcript
  message, a document, the decision rail. Prose is read start to end, so it is set well above
  the chrome around it rather than on the same step.
- **Chrome** (`--text-ui`, 400, 13px, 1.4): the body size everything else inherits. Node titles,
  table rows, controls, the task list. The canvas keeps this size whatever prose does, because
  the board's job is to hold many units at once.
- **Display** (`--text-lg` 18px, `--text-xl` 21px): markdown's own `h2` and `h1`. Nothing in the
  chrome uses them.
- **Section head** (`--text-md-plus`, 17px): markdown's `h3`. One step over body, because at
  reading size a section head separated by weight alone does not rank. Nothing in the chrome
  uses it.
- **Label** (500, 12px): metadata and secondary lines — the state line, the footer, filter
  fields, tab titles. Also the mono step: task ids, branches, worktree paths, code.
- **Small** (400, 11px): the dense mono register — a tool call's summary row, the transcript's
  time gutter, the session head.
- **Micro** (500, 10px, `0.08em`, uppercase): the phase name, section heads such as "NOW", the
  `AGENT` label, a tool call's status. Uppercase and tracked so it reads as a category, not a
  value.

Leading is a token too, chosen by job rather than by a single ratio: `--leading-tight` (1.3) for
a heading, `--leading-ui` (1.4) for an interface line, `--leading-prose` (1.5) for a paragraph.

### Measure

Prose is capped, because the panel is resizable and an uncapped column grows without limit as
the operator drags it wider. Two measures, because the panel's two surfaces read differently:

- `--measure-prose` — the document tab, read start to end.
- `--measure-panel` — the transcript, scanned in blocks between tool rows, and already narrowed
  by the 3.5rem time gutter.

Both are 66ch. One measure, because a document and a transcript message are the same act of
reading and a reader moving between them should not meet two line lengths. 66ch rather than the
conventional 80, because these documents are read start to finish: at 80ch the sweep back to the
next line start is long enough to lose your place, and a line broken up by literals makes that
worse.

### Rhythm

Reading surfaces advance on a 24px grid. The grid is not laid over the text — it is the text:
at 16px with 1.5 leading the line box is exactly 24px, so the leading and the grid are one
number. A paragraph break is one row, a list gap a half row, the space above a heading two rows.

`--space-*` is the chrome's scale and never sets prose spacing. That scale is tuned against 13px
chrome, so on a 16px surface every step lands under the line box it is meant to separate. The
`--prose-gap*` tokens exist so that a gap can never be narrower than the leading it separates.

Three pieces of arithmetic keep blocks on the grid, and each fails silently if changed:

| Block                  | Rule                               | Why                                                                                           |
| ---------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- |
| Heading                | `line-height: 24px`, a fixed pixel | A ratio re-derives from the size, so a later size change walks the page off the grid          |
| Heading with a literal | mono drops to `1em`                | At `0.92em` the mono inline box overflows a 24px line and adds a pixel                        |
| Code block             | `padding: 11px`                    | With the 1px border the box chrome is one row, so a block is `(lines + 1) × 24` at any length |

The space above a heading comes from the heading's own top margin. That margin collapses with
the paragraph's bottom margin rather than adding to it, so raising the paragraph gap does not
widen the space above a heading.

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

**The Legibility Floor Rule.** 10px is the smallest type in the system, and it is only ever
used for a tracked uppercase micro-label — never for prose, and never for a sentence. Chrome is
13px and prose is 16px. Density is bought with tighter space and shorter lines, never by shrinking type below
the floor. Every step clears WCAG AA against its own ground in both schemes; the 10px label is
the tightest, and it is measured, not assumed.

**The Mono Means Literal Rule.** Monospace marks a string the operator might copy, type or
match against something else. Prose never uses it, and a mono string is never truncated
without an ellipsis, because a half-shown id is worse than an obviously cut one.

A literal inside prose is marked by face and tone alone — mono and `--fg-literal`, with no
container. A filled chip works for the occasional literal in a transcript message and fails on a
plan document, where six or seven literals a paragraph turn a sentence into a row of boxes.

**The Operator's Words Rule.** State appears in words the operator already owns — "Needs you ·
plan review" — never a raw agent state, and never a term `CONTEXT.md` lists under `_Avoid_`.

**The Register Is One Value Rule.** The transcript's two registers — prose and ledger — are
computed once in `Transcript.tsx` and read as `data-register`. Spacing and chrome both derive
from it, so they cannot disagree. Keying either on the raw item type is how a shell command came
to draw one register and be spaced as the other.

**The Rank Is Structural Rule.** Two registers on one surface are told apart by more than a
size step. Where prose and machinery sit side by side — the transcript is the case — prose
takes the page's baseline with no container, and the machinery takes the chrome. A single step
on the ramp is not enough to rank two things the eye must separate without reading.

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

Horizontal position is progress first and dependency second:

- The board runs left to right in three zones — DONE, RUNNING, NOT STARTED.
- A zone boundary sits at the same x in every lane, so the board reads as three vertical stripes.
- Inside a zone a task sits one column right of the deepest task it follows in that zone.
- A zone no lane uses takes no columns and collapses, and draws no label.
- When the two rules conflict — a done task that follows a running one — progress wins, and the
  follows edge draws backwards.

Spacing runs on a 4px base with four steps in use: 4, 8, 12, 16. Component padding uses the
scale; the canvas uses its own constants because it positions in absolute pixels.

Density is the point. The operator wants many units visible at once, so containers are tight
and gaps are small. The panel's drag grip is how the operator trades one surface against the
other.

### The narrow layout

One breakpoint, at 840px. Below it the board does not fit: the canvas needs room for a node, a
card beside it and a 320px panel, and under 840px it is a sliver rather than a board. So the
narrow layout does not shrink the wide one — it replaces it.

The deck list takes the canvas's place. The three zones run left to right on a board as vertical
stripes; on a phone the same three run as tabs, opening on running. The model does not change
between the surfaces, only the axis. A row keeps the task node's three registers — the title, the
state in words, then the identity — and its whole state vocabulary, drawn as a full-width band
with the phase bar still on its left edge.

One thing owns the screen. There is no panel and no tab strip: a node's detail, a session and a
document each take the viewport, and a back arrow returns. The wide layout's floating card has no
place here, so the detail is flat — it overlaps nothing, and the Overlap Test says it earns no
shadow.

Three rules hold below the break:

**The Thumb Floor Rule.** Anything a finger presses is at least 44px (`--touch`). Density is
bought back with space, never by going under the floor. The 12px type floor still holds. A form
field in a dialog goes to 16px, because iOS zooms the page on a smaller one and does not zoom
back; the other fields have not been brought to that floor yet.

**The Quiet List Rule.** A row cannot glow without lighting its neighbours, so needs-attention
draws as a field wash and an amber rule rather than the board's glow. It is still the one loud
state, and still the only interrupt.

**The Nothing Hidden Rule.** Every command the wide layout offers stays reachable: approve, deny,
answer, set status, launch, add to and remove from the desk, edit, and start new work. The one
thing dropped is the comment margin, which draws nothing today.

Reachable is not the same as on screen. A command behind one tap counts; reading matter that
costs a band does not. What the rule forbids is a command the narrow layout cannot reach at all.

### Named Rules

**The Fixed Board Rule.** A card moves only when its own work moves: it changes zone when it
starts or finishes, and the cards behind it close up. Its lane never changes, and its order
against the other cards in its zone never changes. The board reports progress and nothing else.

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
means attention or fault. The rule governs which channel the edge carries, not how brightly it
burns: a stopped node drains its phase hue toward the border and the edge is still phase.

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
  shape reads as a note. The Single Interrupt Rule keeps the border and the glow for work that
  is really blocked, and the card carries the explanation.

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

A drifting task gets its own band under the status strip: the amber caret, a sentence naming
both the task status and what the agent is doing, and a button that applies the fix where there
is one. The sentence takes `--fg-muted`, because an open attention item is a real block and
drift is a bookkeeping note — only the caret is amber.

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
- **Split chip:** one pill in two halves, divided by a hairline — what is measured, then what it
  reads. Sunken ground, so it sits _in_ the raised bar; mono tabular value, so the chip holds
  its width as the number ticks. Only the value takes the tone. Both halves read at rest,
  unlike the hue chip: a reading nobody hovers is a reading nobody has. A reading too old to
  vouch for drops to faint and dashes its divider, and the chip itself gives up the tone.

### Shell command

A command the operator asked for with a `!` line. `CONTEXT.md` keeps it apart from a Bash tool
call: the host runs it on the operator's behalf and injects it as user turns, so it is something
they did, not something the agent did.

It therefore takes the interactive channel rather than the agent's ledger — a Signal Blue wash
and border, with a tracked `you ran` label — and reads as a quieter sibling of a user turn. A
tool call recedes; a shell command does not, because the operator put it there.

### Panel Tabs

A horizontally scrolling strip of square tabs on a raised ground, divided by hairlines, 32px
minimum height. Inactive tabs are muted text; the active tab takes the field background, full
text, and a 2px Signal Blue inset underline. Each tab carries a phase swatch, the qualified
task id and a close glyph.

### Session header

Two mono lines above the transcript, on a hairline. The first is the live reading: the agent id,
its state in words, the permission chip, and what it waits on — the state takes the accent, a
wait takes Alert Amber. The second is standing context in faint text: worktree, branch, model,
session size and cost, dot-joined, with a quiet Compact button at the right. An empty field drops
out rather than showing a zero.

The split is the point. A reader watches the first line and consults the second, so the second
recedes a step in colour and never competes for the same glance. Both truncate rather than wrap:
the panel narrows to 320px, and a wrapped head would push the transcript down.

### Table (task list)

Hairline-separated rows, no zebra, no vertical rules. Headers are tracked uppercase micro-labels
in faint text. Ids and branches are mono. The filter row is sticky on a raised ground so the
controls stay reachable through a long list.

### Fields

Selects and text inputs share one chassis: field background, hairline border, 6px radius, tight
padding, capped at 180px so a long branch name cannot push the filter bar apart. They inherit
the interface font — a form control never falls back to the browser's own.

### Decision

The block shown when an agent waits. The expanded node and the document tab render one component,
so the two can never drift, but each reads it for a different reason. A card is read; a dock is
acted on. The variant says which.

**On a card** the decision is the reading. A context rail — 2px strong hairline on the left,
muted text — carries the last three things the agent said or did, under an uppercase micro
heading. Said lines are prose; did lines are mono with a bolded tool name and ellipsis
truncation. The prompt follows, in its bordered amber box.

The rail is context, so it never outranks what it is context for. Three items is a small count,
but one item may be a whole message, so the count alone does not bound the height: the rail
clamps to about ten lines and fades at the cut, with a control that opens it in place — the same
idiom the node card uses for a long brief. The heading is also a fold, so a rail the operator
has already read can be put away entirely. It opens by default and the state does not persist,
because the panel keeps no view state across renders.

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
above a heading.

The context is offered, not spent. `Before this · 3` is a control on its own line, and it opens
the rail as a sheet over the document rather than pushing it — the document never reflows for a
decision. The sheet overlaps content it is not part of, so the Overlap Test earns it the card
lift. Escape closes it.

- **Waiting:** the top rule takes Alert Amber and the ground takes an 8% amber wash. The Quiet
  List Rule holds here as it does on a deck row: a docked band signals with a rule and a wash,
  never a glow.
- **Settled:** the plain hairline and the raised ground, because nothing is asking.
- **Narrow:** every control clears `--touch`, and a field goes to 16px so iOS does not zoom.

A plan review answers the agent, never the document. Approving the document would flip it and
retire the attention item pointing at it, leaving the agent blocked on a request nothing had
answered. The dock therefore offers the agent's Approve and withholds the document's
request-changes route. `Read the plan` is dropped where it would point at the document already
open: in the plan's own tab the link leads nowhere, and on a phone it pushes a second copy of
that screen onto the stack.

## Seeing a change

`pnpm ladle`, or `mael env start ladle`, serves a workbench of the components. It needs no
orchestrator, no daemon and no live agent.

Use it before a visual change and after. jsdom computes no layout, so the test suite cannot
answer whether prose ranks above a tool row, where a measure wraps, how a run of calls reads,
whether a docked control clears the thumb floor, or whether a stopped node reads as quieter than
an idle one. A live session is a slow and unrepeatable way to ask, and on the board it can only
show the states its agents happen to be in.

Stories come in two shapes:

| Shape     | Fixture                                                               | Use it for                             |
| --------- | --------------------------------------------------------------------- | -------------------------------------- |
| Component | `src/session/transcript.fixture.ts`, `src/canvas/taskNode.fixture.ts` | one component's states, drawn directly |
| Whole app | `src/test/seedWorld.ts` + `src/test/fakeServer.ts`                    | a surface reached by navigating        |

The whole-app shape mounts the real `App` on the fake server, through the same `deps` injection
`renderApp` uses in the suite. A story therefore runs the production tree rather than a stand-in
that can drift from it. `Documents / Review dock` is the worked example.

A component fixture that stands for a state builds it with the production reader, never by hand:
`taskNode.fixture.ts` reads every node's progress through `progressOf`. A story that hand-rolled
one could draw a state the code cannot produce, which is the one thing a fixture must not do.

The stories carry the states worth checking: prose against tool calls, a long ledger run, the
truncation note, the narrow layout under the 30rem container query, a wide panel, every markdown
element at panel width, the review dock waiting and settled, and every node state side by side.

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
