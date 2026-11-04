# Code navigation in the Changes tab

The **Changes tab** is a review trail. The reviewer reads a rev's diff and brings extra context
into the same column: a whole file picked from the file list, or the hidden lines between two
hunks. Later work adds the **Symbol card**, which follows a name to its definition and pins the
target after the card it came from.

This page holds the rules that the chain of tasks shares. `docs/dev/orchestrator-ui.md`,
"The Changes tab", describes the code as it is.

## One flow, one card model

The pinned code reads in the same column as the diff that caused it. A second tab strip or a
drawer would split that reading path.

A **Card** is one file from one source, drawn as an ordered list of **Spans**. A diff hunk is one
kind of span. Pinned lines are the other. A diff card, a pinned whole file and a **Fragment**
differ only in their spans and in why they are in the flow. So one render path, one comment path
and one windowing path serve all three.

There is one card per path. A **Pin** on a path that the rev changes merges into its diff card,
clipped so that it does not overlap a hunk.

## The source of every card

Every card reads the rev's new side:

| Rev | Source |
|---|---|
| A commit sha | That commit |
| `branch` | `HEAD` |
| `uncommitted` | The working tree |

A pinned line in a changed file is the same text that its hunks come from. No card mixes the
working tree with a commit.

## The types

Three layers, in one direction:

- **Wire types** are shared. Each is a Python `TypedDict` in `lib/domain/src/mael_domain/protocol.py`
  and its mirror in `orchestrator-ui/src/protocol/entities.ts`.
- **Client state** is what the reviewer chose: change comments and pins.
- **Client view types** are a pure function of the wire data and the client state. Nothing
  derived is stored.

### Wire types

| Type | Shape | Use |
|---|---|---|
| `LineRange` | `{start, end}`, new side, 1-based, inclusive | A range with no lines ends one before it starts: `{1, 0}` |
| `FileRange` | `{path, range}` | A pin's target. Client only until the symbol route needs it |
| `LineSpan` | `{range, lines: DiffLine[]}` | Consecutive lines of one file |
| `DiffHunk` | `LineSpan` plus `header` | `parse_diff` sets `range` from the `+c,d` of the header |
| `FileExcerpt` | `{rev, path, total, truncated, span}` | `GET /excerpt`. Every line is `context` |
| `FileList` | `{rev, paths}` | `GET /tree`. Every path on the rev's new side |

Git's diff stays parsed on the server, and `/diff` keeps its shape. The client turns hunks and
pinned lines into one span shape. The server sends no fragments: pins are client state, and
context drawn from whole-file text would ship both sides of every file.

### Client state

A `Pin` is `{id, target, from}`. `target` is a `FileRange`, or `{path, range: null}` for a whole
file. `from` is the `Origin` of a followed target, `{at: FileRange, name}`, and is `null` for a
pin made in the tab.

Pins live per rev, in `useRetained` under `retainedKey.pins(worktreeId, rev)`. Extra context
relates to the change in one diff. A rev that leaves the branch leaves its key unread.

A pin holds line numbers, not text. The new side of `branch` and `uncommitted` moves as the agent
works, so a range pin there can come to show other lines, or lines that a hunk now covers. A
commit sha does not move. A pin whose lines are all hunk lines, or past the end of the file,
still draws its × after the card's last row, so the reviewer can remove it.

### Client view types

`orchestrator-ui/src/changes/flow.ts` holds them:

- `flow(diff, pins, excerpts)` gives the sections of the flow. The diff cards stay in their
  **Diff group**. A pin on another path makes a pinned card after its origin card, else under
  Pinned. `excerpts` is keyed by pin id: each pin reads its own range.
- `cardRows(card)` gives the rows that `DiffRow` draws, and the marks between them: a hunk's
  header, a gap, or a pin's ×.

Two pins of one file share one card. The card's pinned lines are the union of the pins' ranges,
less the hunks. The lines are cut into spans where the pins that cover them change, so
overlapping pins draw each line once. A pinned line below a hunk takes its old number from the
offset that the hunk leaves, so it reads as a context row of the diff.

## Gaps

`cardRows` keeps the spans in line order. Spans that touch draw with no gap. Between others, a
"⋯ n lines" row shows the hidden lines, and a click pins them. The gap after the last span counts
to the end of the file once an excerpt gives its length. Until then it reads "⋯ to end of file".
A card past the line cap has no gap at its end: the cap holds for pinned lines as for hunks.
An added file has no gaps, because its hunk is the whole file. A deleted file has no new side.

## Windowing

A card's rows split into chunks of 200 (`ui/ChunkedRows.tsx`). A chunk far from the screen draws
as an empty box of its rows' height. Rows never wrap, so the height is exact. The sticky heads and
`scrollIntoView` keep working, which a virtual-list library would break.

A chunk stays mounted out of view while it holds an added comment, the open comment box or the
drag. Tokens are made per run of rows between two marks or chunk edges, while the chunk is in view.

## The language server (planned: task 2)

Nothing in this section runs yet. The project will name its servers in `language_servers:` in
`.maelstrom.yaml`: a name, a command and globs. Maelstrom hardcodes none. Pyright comes first.

The server runs on the working tree, but it must see the rev's tree. Each file whose content
differs between the rev's new side and the working tree is opened in the server at its rev
content, through `didOpen`. For `uncommitted` that list is empty. A rev switch closes and reopens
the set. A target in a file that the rev lacks draws muted, "not in this rev", and pins nothing.

The orchestrator starts a server lazily, per worktree and per server. It stops one after 10
minutes idle, and when the worktree row closes.

A plain click on a name, with no text selected, opens the symbol card. Line selection for change
comments starts only from the gutter button, so the two do not collide.

## The chain

1. The card model, the windowed flow, the full file list and pins of whole files and gaps.
2. The language-server bridge and the symbol card, for Python. A target in a changed file scrolls
   to its line, or pins the lines around it in the diff card. Elsewhere it pins the whole file
   after its origin.
3. Fragments: the enclosing function or class of a target, from `documentSymbol`, in place of
   the whole file.
4. The TypeScript server for `orchestrator-ui`.

Rename, code actions, diagnostics, editing and old-side spans are out of scope.
