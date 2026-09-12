# The data architecture

How the orchestrator server holds state: where a reader gets it, how a reader learns it
changed, and who decides when to refresh it.

> **Status: agreed, not built.** This document is the target. The state database does not
> exist yet, and every subsystem still works as "Why a common architecture" describes below.
> Sections written in the present tense describe the design, not the code. `CONTEXT.md` marks
> the pattern names the same way.

Every subsystem the server shows answers those three questions. Today each answers them its own
way. This document defines five patterns that share one answer set, so adding a subsystem is
choosing a pattern rather than inventing a design.

Read [orchestrator-server.md](orchestrator-server.md) for what the server does with the state,
and [architecture-patterns.md](architecture-patterns.md) for the storage / model / CLI layering
each pattern sits inside.

## Why a common architecture

Four subsystems reach the server, and no two agree on how:

| | Tasks | Worktrees | Pull requests | Agents |
|---|---|---|---|---|
| Where a reader gets it | Parse every file | Re-scan git | A dict on a source object | The world |
| How a reader hears of a change | The notebook's git HEAD moved | It does not | It does not | The host pushes it |
| Who decides to refresh | A 2 s poll | A 60 s poll | The same poll, behind four guards | The agent host |
| What survives a restart | The files | Nothing | Nothing | Nothing |

Four coherent columns and no shared row. Three bug classes follow.

**A cache outlives a rollback.** `store.transaction` rolls the notebook back, but not the
SQLite index beside it, nor a file written next to it. `promote` works around this by passing
`index=None` and deferring its draft deletion until the transaction commits.

**Nothing says what changed.** No source reports which rows moved, so the server diffs whole
tables against whole tables on every poll.

**Every restart is cold.** Only the desk is persisted, so the first paint re-derives everything
the slow way.

The cost is measurable. On a machine with 16 projects, 96 worktrees and 794 tasks, a task read
parses every task file in **2.8 s**, and a worktree read takes **4.1 s** — of which **2.4 s**
is the task notebook being parsed a second time, by a different subsystem, for one column of
the worktree table.

## The five patterns

```
┌──────────────────────────────────────────────────────────────────────────┐
│ CANONICAL        write ──► db ──► revision ──► notice                    │
│  tasks, desk     read: the database. The write is the authoritative act. │
│                  durable: backed up, migrated, never rebuilt from empty  │
├──────────────────────────────────────────────────────────────────────────┤
│ CACHED           read: the database, always, whatever its age            │
│  worktrees            ▲                                                  │
│  pull requests        └── refresher ──► fetch ──► upsert ──► notice      │
│                           owns its cadence, its budget, its stand-off    │
│                  droppable: losing the table costs one slow read         │
├──────────────────────────────────────────────────────────────────────────┤
│ PASS-THROUGH     read: fetch it now. Stored nowhere.                     │
│  Linear issues        One route needs it, not the world.                 │
│  attachment bytes     Cheap to read, or it must be exact.                │
├──────────────────────────────────────────────────────────────────────────┤
│ PUSHED           the owner streams it; the database holds nothing live   │
│  agents               The agent host already works this way.             │
├──────────────────────────────────────────────────────────────────────────┤
│ PROGRESSIVE      a slow write answers at once, then reports itself       │
│  worktree setup  command ──► row at `preparing` ──► reply                │
│  worktree close       then each step ──► upsert ──► notice               │
│  task infer      the entity exists before the work finishes              │
└──────────────────────────────────────────────────────────────────────────┘
```

The first four answer *where a reader gets state*. The fifth answers *how a slow write reports
itself*. It belongs here because it rides the same notice path as a cached table — a row
changing — driven by a local writer instead of a refresher.

### Canonical

Maelstrom authors the data. The write is the authoritative act, so a lost row is lost work.

A canonical table needs a backup story, a versioned migration, and no recovery step that
rebuilds it from nothing. That last rule is what separates it from a cached table, and it must
be hard to break by accident.

Tasks and the desk are canonical. A task's row carries its prose, not a pointer to prose
elsewhere: splitting a row across two stores is what makes a rollback partial.

Tasks also keep a git-committed markdown export at `~/.maelstrom/tasks`. The export is for
audit and backup only. Nothing reads it on any code path, so losing it costs history rather
than data, and the reader that wants a task's prose queries the table.

The export never runs on the write path. A commit enqueues it, and a worker drains the queue,
so a slow `git commit` cannot slow a task write and a broken git repository cannot fail one.
The application otherwise treats the database as an ordinary database-backed service would.

Two rules keep an asynchronous export honest. The queue **drains on shutdown**, so a clean stop
loses nothing. And falling behind is **visible** — a queue that stops draining is a backup
nobody has, and silence is the failure mode to design against.

### Cached

Someone else authors the data. The database holds the last answer seen.

A reader queries the table and takes what is there, whatever its age. It never asks whether the
row is fresh, and it never triggers a fetch. A refresher keeps the table current on its own
cadence and raises a notice when a row moves.

Each row carries `fetched_at`, so "as of four minutes ago" is answerable without asking the
refresher. Losing the whole table costs one slow read, never data.

Worktrees and pull requests are cached. Git and GitHub own them.

### Pass-through

Read it now, from its source, and store nothing.

This suits data one route needs rather than the whole world, and that is either cheap to read
or must be exact at the moment of reading. `GET /api/linear/issues` is the example: it asks
Linear for the project's current cycle on each request, and no issue ever enters the world.
Attachment and registered-file bytes are served the same way.

Pass-through is a real answer, not a compromise. Naming it stops a reviewer proposing a cache
for data that does not want one.

It is not a place to put data that is merely expensive to read today. A task's `content` looks
like a candidate while tasks are markdown files, because the whole notebook must be parsed to
answer for one task. Once tasks are rows, one task's prose is a single-row query, and it
belongs in the canonical table with the rest of the task.

### Pushed

The owner holds the state and streams it. The database holds nothing live.

The agent host works this way already, and it is the fastest and most correct subsystem here.
Caching live agent state would add a second place for that state to be wrong.

A poll may still run as a reconciliation net — the server reconciles the host's `list` against
the world every 2 s — but it corrects the stream rather than replacing it.

### Progressive

A command that takes tens of seconds answers at once with an entity in a preparing state, then
reports each step over the notice path.

The entity exists before the work finishes. That is the point: a `preparing` worktree can be
drawn, named and failed. A command that answers only on completion leaves a failure nowhere to
land except a toast on the button that started it.

Three commands qualify, all of them already running on the server's executor:

| Command | Why it is slow |
|---|---|
| `agent.launch`, `agent.start` | `setup_worktree_for_branch` runs git, rebases, and allocates ports |
| `worktree.close` | Stops the environment, the agents and the sessions, then runs git |
| `task.infer` | Two 20-second `claude -p` attempts |

## What the patterns share

The patterns are only worth naming because canonical and cached converge on one read path and
one notice path. A reader queries a table and gets an answer. It never branches on whether a
writer or a refresher put the row there, nor on how old the row is.

Four pieces of machinery carry that convergence.

**One read.** A caller queries a table. No caller asks whether the data is fresh enough, and no
read triggers a fetch as a side effect.

**One notice.** A changed row bumps a revision and raises a notice naming what moved. The
server's `diff_kind` is then handed rows rather than whole tables. A task write and a pull
request backfill travel this path identically.

**A refresher contract.** A refresher says what to fetch, how often, and what it does when
refused. The GitHub rate-limit stand-off, the worktree poll's `only_when_watched` guard and the
desk-narrowed branch set are three writings of one idea: a refresher decides its own budget.

**Freshness as data.** A cached row carries `fetched_at`. Staleness is a property a reader can
see, not a question only the refresher can answer.

## One database

All of it lives in one SQLite database at `~/.maelstrom/state.db`.

One file gives one transaction, so a canonical write and its derived rows commit or roll back
together. That alone removes the bug class `promote` works around today. It also gives one
revision counter, which is what lets a notice name rows instead of tables.

Writers run in several processes — the CLI, the server and the agents all write — so the
database runs in WAL mode. WAL replaces the `fcntl` lock the task store holds today, and its
60-second timeout, and extends the same guarantee to writers that have none.

The difference between a canonical table and a cached one is **one property on the table**, not
a separate database, a separate read path, or a separate design. Only a canonical table is
backed up. Only a canonical table refuses to be rebuilt from empty.

## Choosing a pattern

Ask who authors the data, then how expensive it is to read.

```
  Does maelstrom author it?
    yes ──► CANONICAL
    no  ──► Does the owner stream it?
              yes ──► PUSHED
              no  ──► Is a read cheap, and needed by one route?
                        yes ──► PASS-THROUGH
                        no  ──► CACHED

  Separately: does a write take tens of seconds?
    yes ──► it is also PROGRESSIVE
```

Progressive is not a fifth alternative to the other four. It describes a write, where the
others describe a read, so a subsystem can be canonical and progressive at once.

If a reader of this document cannot tell which pattern a new subsystem wants, the architecture
is not yet common, and this document has failed.
