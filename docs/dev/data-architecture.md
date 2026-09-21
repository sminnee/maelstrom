# The data architecture

How the orchestrator server holds state: where a reader gets it, how a reader learns it
changed, and who decides when to refresh it.

> **Status: the machinery is built; two subsystems are not on it.** The state database, the
> revision counter, the notice path and the refresher contract exist, in
> [`state_db/`](../../src/maelstrom/state_db/) and
> [`refresh.py`](../../src/maelstrom/refresh.py). The desk and the tasks are canonical and on
> the database. Worktrees and pull requests still work as "Why a common architecture" describes
> below, and moving each one is its own task.

Every subsystem the server shows answers those three questions. Today each answers them its own
way. This document defines five patterns that share one answer set, so adding a subsystem is
choosing a pattern rather than inventing a design.

Read [orchestrator-server.md](orchestrator-server.md) for what the server does with the state,
and [architecture-patterns.md](architecture-patterns.md) for the storage / model / CLI layering
each pattern sits inside.

## Why a common architecture

Four subsystems reach the server, and no two agreed on how. The desk and the tasks are now on
the database; the two below are what is left:

| | Worktrees | Pull requests | Agents |
|---|---|---|---|
| Where a reader gets it | Re-scan git | A dict on a source object | The world |
| How a reader hears of a change | It does not | It does not | The host pushes it |
| Who decides to refresh | A 60 s poll | The same poll, behind four guards | The agent host |
| What survives a restart | Nothing | Nothing | Nothing |

Three bug classes followed from the columns not sharing a row. Moving the tasks closed each one
for them, and the two subsystems above still carry all three.

**A cache outlives a rollback.** A store transaction rolled the notebook back, but not a SQLite
index beside it nor a file written next to it, so a failed multi-task write left a partial
state. A task now carries its prose in its own row: one write, one transaction, nothing beside
it to disagree.

**Nothing says what changed.** A source that reports no rows makes the server diff whole tables
against whole tables on every poll. Tasks report what moved through `changed_since`, so a poll
costs the rows that changed.

**Every restart is cold.** State nobody persists is re-derived the slow way at first paint.

The cost was measurable. On a machine with 16 projects, 96 worktrees and 794 tasks, a task read
parsed every task file in **2.8 s**, and a worktree read took **4.1 s** — of which **2.4 s**
was the task notebook being parsed a second time, by a different subsystem, for one column of
the worktree table. A task read is now a query.

## The five patterns

```
┌──────────────────────────────────────────────────────────────────────────┐
│ CANONICAL        write ──► db ──► revision ──► notice                    │
│  tasks, desk,    read: the database. The write is the authoritative act. │
│  agents                                                                  │
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
│  live sessions        The agent host already works this way.             │
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

Tasks, the desk and agents are canonical. A task's row carries its prose, not a pointer to prose
elsewhere: splitting a row across two stores is what makes a rollback partial.

An **Agent record** (see `CONTEXT.md`) is canonical for the same reason a task is: it is the
only copy of the harness, mode, model and task an agent started with. Losing it loses that
history. It differs from a task in what it is not: it does not describe the agent's live state,
only what started it. The live session that state belongs to is PUSHED, below.

The record outlives the agent: `stop` writes `status: ended` rather than deleting the row, so the
spend recorded against it survives. `DaemonRouter` restores only live records, because an ended
one would come back as an `exited` row on every poll and the reconcile loop retires an agent by
its id dropping out of `list`.

Store-as-truth cuts both ways, so `list` repairs the store in both directions:

- A live top-level row with no record is **adopted**. `mael add` and `mael agent start` reach the
  daemon socket directly, and without this their agents are invisible to every reader.
- A record the daemon does not name is retired only after `UNCONFIRMED_LISTS_BEFORE_END`
  consecutive lists miss it, and never inside `START_GRACE_SECONDS` of its start. One missing row
  is not evidence of an exit — `daemon_bridge.py` says what else looks the same.
- A reply that carries an `error` counts as neither a sighting nor a miss.
- A **swept** record whose agent turns out to be alive is **revived** onto its own row, keeping the
  task and the start it really had. A record a `stop` ended is settled and never comes back.
- An unconfirmed row reports the state the agent was last seen in. The server ends every wait a row
  does not report as `awaiting-`, so a placeholder would cancel a live agent's open ask.

The `agent_milestones` table is canonical too: a **milestone snapshot** is the only record of what
an agent had spent at each stage, and no transcript can rebuild it. It carries no `fetched_at` and
nothing draws it, so a write to it is not news for a client.

Tasks also keep a git-committed markdown export at `~/.maelstrom/tasks`. Nothing reads it on
any code path, so losing it costs history rather than data, and the reader that wants a task's
prose queries the table.

The export never runs on the write path: a task write enqueues, and the orchestrator drains.
See [`task_export.py`](../../src/maelstrom/task_export.py) for why the queue is a table in the
same database.

Two rules keep it honest. The queue **drains on shutdown**, so a clean stop loses nothing. And
falling behind is **visible** — `mael admin export-queue` reports the depth and the oldest
entry, because silence is the failure mode to design against.

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

It is not a place to put data that is merely expensive to read today. A task's `content` looked
like a candidate while tasks were markdown files, because the whole notebook had to be parsed to
answer for one task. Now that tasks are rows, one task's prose is a single-row query, and it sits
in the canonical table with the rest of the task.

### Pushed

The owner holds the state and streams it. The database holds nothing live.

A live session works this way already, and it is the fastest and most correct subsystem
here. Caching live agent state would add a second place for that state to be wrong. This is the
agent's running state only — what started it is the canonical **Agent record**, above.

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

### Async surface, sync engine

Every method on `StateDb` is `async def`, and the engine underneath is stdlib `sqlite3` called
inline. Those are two decisions, not one. The async surface is bought for reversibility: it is
every call site in four subsystems' stores, so it is the expensive thing to change later. The
sync engine is kept because SQLite answers here in microseconds. One private helper, `_call`, is
the seam between them, and moving its body to `aiosqlite` changes no caller.

An `await` on a method that runs inline does not yield the loop, so the loop occupancy is the
sync cost. Measured on a file-backed database:

| Operation | Median |
|---|---|
| 96-row `write_all` | 0.67 ms |
| 96-row `read_all` | 0.05 ms |
| Single-row `upsert` | 0.01 ms |

The write costs more than the rows alone because each one reads its stored row first, to decide
whether anything a client draws moved. That is what buys the silent poll below, and it is the
first figure to re-check when a table grows.

### Writing several rows

| Shape | Use it when |
|---|---|
| `upsert` / `delete` | One row. Each is its own transaction. |
| `write_all(writes)` | Several rows as one cut. The default. |
| `async with transact()` | A later write depends on an earlier read in the same transaction. |

A store exposes the same choice. `DeskStore` carries `add` and `remove` for one entry, beside
the `save` that replaces the whole table: the store underneath is a database, so changing one
entry should cost one row rather than a rewrite.

`write_all` takes the whole batch at once, so the engine runs it start to finish with no
suspension point inside. Nothing can interleave, and nothing can await back into the database
mid-transaction. A `transact()` block holds a write lock across caller code, which is what makes
it the exception rather than the default; awaiting back into the same database from inside one
raises `TransactionOpenError` rather than hanging.

A multi-statement transaction blocks every other writer, in this process and at the file lock in
every other one. If that contention ever becomes real rather than theoretical, it is the signal
to reconsider the datastore — not to add machinery around SQLite.

### Freshness

A cached row carries `fetched_at`, stamped on every successful fetch even when nothing changed:
"we asked and it said the same" is a different fact from "we have not asked". A write that moves
only that stamp still commits, but **bumps no revision and raises no notice**, because nothing a
reader draws has moved. A write that gives no stamp leaves the stored one alone, so a local edit
to a cached row cannot erase a refresher's answer. That is what keeps a 60-second poll over 96 worktrees silent rather than a notice storm.

A canonical table has no `fetched_at`, and passing one raises rather than being dropped quietly.
Nobody else authors a canonical table, so there is nothing to be fresh with respect to.

### Schema versions

A `schema_version` table holds one row per subsystem, so one subsystem's schema moves without
dragging the others. The spine — `meta`, `schema_version`, `removals`, `refresher_health` — is
the one table set every subsystem depends on, so it carries its own version in `PRAGMA
user_version` and is checked first. A refusal naming "the spine" means that check failed.

Each subsystem declares an append-only ladder of migrations, and a migration's version is its
index plus one, so the number is derived rather than maintained.

| Found | What happens |
|---|---|
| Equal | Opens. |
| Lower | Refuses, naming `mael admin migrate`. |
| Higher | Refuses, naming both versions. |

A rung usually runs SQL. It may instead run Python, for a step SQL cannot take — reading a
file into rows is the case, and the desk's second rung imports `desk.json`. Such a rung is
handed the raw connection rather than a transaction object, because **a migration must not bump
the revision counter**: its rows name `revision = 0` themselves, so a client polling
`changed_since` reads them as the state it started from rather than as a change. The rung runs
inside the migration's own transaction, so a rung that raises rolls the whole run back with it.

Lower refuses rather than upgrading because several processes share one `~/.maelstrom`, and a
background process that rewrote the schema under a running server is worse than a stop with a
one-line fix. Higher is the real hazard: every worktree shares that directory, so running an
older branch after a newer one is ordinary, and writing rows that miss the newer migration's
columns is unrecoverable. Migrations are forward-only, and the whole run is one transaction —
SQLite's DDL is transactional, so a migration that fails halfway leaves the tables where they
were.

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
