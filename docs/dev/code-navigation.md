# Code navigation in the Changes tab

The **Changes tab** is a review trail. The reviewer reads a rev's diff and brings extra context
into the same column: a whole file picked from the file list, or the hidden lines between two
hunks. A click on a name follows it: a use pins its definition's **Fragment**, and the
definition opens a **Reference menu** of its uses. Later work adds the **Symbol card**, which
shows a name's type.

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
| `CodeTarget` | `{path, range, inRev, anchor?, symbol?}` | A definition or a use. `range` is the lines to pin; `anchor` is the name's line, absent when it is `range.start`; `symbol` is the dotted name of the enclosing function or class |
| `Definition` | `{rev, targets, references, moreReferences?, reason}` | `GET /definition`. `references` is `null` for a click on a use. `reason` is `ok`, `no_server`, `no_definition` or `no_references` |

Git's diff stays parsed on the server, and `/diff` keeps its shape. The client turns hunks and
pinned lines into one span shape. The server sends line ranges, never pinned text: pins are client state, and
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

## The language server

A click on a name in a Python card follows it. The flow is: the click, the `/definition` route,
the worktree's **Language server**, then a scroll, a **Pin** or a **Reference menu**.

### The list

Maelstrom hardcodes its servers in `mael_domain.language_servers.LANGUAGE_SERVERS`. Each entry is
a name, a command and globs. The one entry is `pyright`, with the glob `**/*.py`. A project does
not configure them.

`server_for(worktree_path, file)` picks the first entry whose globs match the file. It resolves the
command in the worktree's `.venv/bin` first, then on `PATH`. With neither, the file is not
navigable. The `.venv` binary runs the project's own interpreter; `uv run` would sync on each
start.

The server decides what a card can navigate. `FileDiff` and `FileExcerpt` carry `navigable`, set
from `server_for`. The client needs no glob matcher and no language names.

### The rev overlay

The server runs on the working tree, but the reviewer reads the rev's new side. The **Rev
overlay** closes that gap. Before each request, `LanguageServers` reads `git diff --name-only <commit>`
and keeps the files the server's globs match. It opens each one at its rev text, through
`didOpen`, and it always opens the file of the click.

| Case                                  | What `LanguageServers` does                         |
| ------------------------------------- | --------------------------------------------------- |
| A file no longer differs              | `didClose`; the server reads the disk again         |
| A file starts to differ               | `didOpen` at the rev text                           |
| The resolved commit changes           | Closes every open file, then opens the new set      |
| `uncommitted`                         | Opens only the file of the click, at its disk text, on each request |

A target outside the worktree, such as typeshed, or in a file the rev does not track, such as one
in `.venv`, comes back with `inRev: false`. The tab says "Definition not in this rev" and pins
nothing.

### Fragments and references

The server picks the lines to pin for every target, so the client has no line rule of its own.
The symbol tree stays on the server, as `navigable` does. One `/definition` request sends up to
three LSP requests, under one lock hold, so all of them read the same **Rev overlay**:

1. `textDocument/definition`, for the targets.
2. `textDocument/references`, with `includeDeclaration: false`, only when the click is on the
   definition. It waits up to 30 s, because pyright reads the whole project for it.
3. `textDocument/documentSymbol`, once for each distinct path the rev holds among the targets
   and references.

The click is on the definition when a target in the clicked file has a name range that holds
the click's line and character. The name range is `targetSelectionRange`, else `range`. Only the
server has the character ranges, so the server decides.

A target's `range` is its **Fragment**: the innermost Class, Method, Constructor or Function
symbol (kinds 5, 6, 9 and 12) that holds the name's line. A use in a method pins the method, not
its class. When no such symbol holds the line, such as for a module-level constant, `range` is
5 lines above the name to 25 lines below it, clipped at line 1. The excerpt read and the flow
clip the end. A target outside the rev keeps the name's own lines.

| Rule | Why |
|---|---|
| Both symbol reply shapes are read: `DocumentSymbol[]` with `children`, and flat `SymbolInformation[]` with `containerName` | `initialize` asks for the tree, but a server may send the flat list |
| A failed `documentSymbol` request gives the window | The targets are already known. The error is logged |
| A failed `references` request fails the route | As a failed `definition` request does |
| References are the ones the rev holds, sorted by path then line, at most 100 | The cap bounds the `documentSymbol` requests. `moreReferences` counts the rest |

### Life

The orchestrator runs one server per worktree and server name, over `LspClient`, its own stdio
client. A server starts on its first request. It stops after 10 minutes idle, when its worktree
row closes or goes, and when the orchestrator stops. A server that crashed starts again on the
next request.

### The click

The click handler is on the row text, never on the gutter. Line selection for change comments
starts only from the gutter button, so the two do not collide. A click while text is selected
sends nothing. A token in a keyword, comment or string colour is not a name.

The client sends the 1-based new-side line and the UTF-16 offset of the token.

A click on a use follows the first target the rev holds. A click on the definition opens the
**Reference menu** beside the name. The menu lists each use as `{symbol} — {path}:{line}`, and a
last row says how many uses it leaves out. A pick goes to that use. Escape or a click outside
closes the menu. With no uses, the card says "No uses of {name} in this rev".

To go to a target, the tab uses its `anchor`, else `range.start`:

1. The flow draws the anchor line: the tab scrolls to it and flashes it.
2. Else, unless the card already has a whole-file pin, the tab pins `range`. A pin in an
   unchanged file goes after the card of the click. Then the tab scrolls to the anchor.

## The chain

1. The card model, the windowed flow, the full file list and pins of whole files and gaps.
2. The language-server bridge, for Python, and a click that goes to a definition.
3. Fragments: the enclosing function or class of a target, from `documentSymbol`, and the
   **Reference menu**, from `references`. Hover type stays with the **Symbol card**.
4. The TypeScript server for `orchestrator-ui`.

Rename, code actions, diagnostics, editing and old-side spans are out of scope.
