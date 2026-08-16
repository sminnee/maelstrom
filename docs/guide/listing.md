# Reading `mael list`

`mael list` answers one question: what is every agent doing right now? One row per open
worktree, showing the branch, the uncommitted work, the unpushed work, the pull request, the
app URL and the live sessions. It is the command to run when you come back to the machine and
need to know where things stand.

```bash
mael list                # the current project
mael list askastro       # a named project
mael list-all            # every project, one table
mael --json list-all     # every project as JSON
```

`mael --json list-all` is the only machine-readable form. `mael --json list` is accepted and
then ignored: it prints the same table, so a script that pipes it into a JSON parser fails at
the parser rather than at the command.

The JSON is not the table. It also carries every closed worktree, flagged by `is_closed`. A
closed worktree's counts are placeholders rather than measurements — `dirty_files`,
`local_commits` and `pr_number` are filled in as `0`, `0` and `null` without anything being
measured. Skip the `is_closed` entries unless you want them.

## A worked example

This is real output from a project with seven open worktrees:

```
WORKTREE  BRANCH                                 DIRTY FILES  LOCAL COMMITS  PR (COMMITS)  APP                    SESSION
---------------------------------------------------------------------------------------------------------------------------
_main     main                                                                                                             
alpha     feat/896-docx-custom-template                                      #1635 (2)     *3000                  — stopped
bravo     task/2026-06-15.1                                                  #1594 (6)     *3020                  3
charlie   feat/unified-relationship-declaration               76             #1766 (6)     *3030                  — stopped
lima      fix/maint-fetcher-mcp-oom-2026-08-14                                             *3170                  — stopped
mike      feat/pgsql-users                                                   #1543 (2)     *3120
november  refactor/document-derivatives                                      #1837 (5)     http://localhost:3160  1

Closed environments: main-check, delta, echo, foxtrot, golf, hotel, india, juliet, kilo, oscar, papa, quebec
```

Read it row by row:

- **`_main`** is the reference checkout. It appears as a row, with an empty APP column,
  because it has no port allocation.
- **`alpha`** has everything pushed. Its work is entirely in PR #1635, which holds 2 commits.
  Nothing is on this machine alone.
- **`bravo`** has 3 live sessions in one worktree.
- **`charlie`** has 76 commits that exist only on this machine. Its PR #1766 still shows the
  6 commits that were pushed before that work started.
- **`lima`** has no pull request and nothing pushed, so `PR (COMMITS)` is blank.
- **`mike`** has never run a session, so `SESSION` is blank rather than `— stopped`.
- **`november`** is the only worktree whose app is running: the APP column gives the full URL
  instead of `*3160`.

Closed worktrees are not rows. `mael list` names them on one line under the table.

## The columns

| Column | What it shows | Blank means |
|---|---|---|
| `WORKTREE` | The NATO name — `bravo`, not `myproject-bravo` | Never blank |
| `BRANCH` | The checked-out branch, or `(detached)`. A stacked branch reads `feat/child ← feat/parent` | Never blank |
| `DIRTY FILES` | How many files `git status` reports as changed | No uncommitted changes |
| `LOCAL COMMITS` | Commits that exist only on this machine | Nothing unpushed |
| `PR (COMMITS)` | `#1766 (6)` — the open pull request and its commit count | No pull request and nothing pushed |
| `APP` | The app URL when the app runs, `*3030` when it does not | The worktree has no port allocation, or the project has no `APP`/`FRONTEND` service |
| `SESSION` | The number of live sessions, or `— stopped` | The worktree has never run a session |

Every count renders blank at zero. So "clean", "nothing unpushed" and "not pushed at all" all
look the same — an empty cell. Read a blank as "nothing to tell you here", not as a zero you
can act on.

A `(detached)` row goes further. `LOCAL COMMITS` and `PR (COMMITS)` are both keyed on the
branch name, so a detached worktree reports them blank without checking anything. Nothing was
measured and nothing was zero.

`mael list-all` prints the same columns, with a `PROJECT` column in front. Its `WORKTREE`
column carries the folder name (`askastro-charlie`) rather than the NATO name (`charlie`). The
JSON form gives you both, as `name` and `folder`. It also breaks the closed worktrees out per
project, one `- <project>: <names>` line each, rather than the single flat line `mael list`
prints.

### `DIRTY FILES`

The count is the number of paths in `git status --porcelain` — staged, unstaged and untracked.
A new file nobody has added counts as dirty, so build output or a scratch file that `.gitignore`
misses shows up here. One path counts once, however many ways it changed, and a rename counts
once.

`.env` never counts. Maelstrom generates that file, so a changed `.env` is not your work. The
exclusion matches the path exactly, so a root `.env` is skipped but a nested `apps/web/.env`
still counts as dirty.

### `LOCAL COMMITS`

`LOCAL COMMITS` counts the commits that exist only on your machine — the work you would lose
if the disk died. It goes blank the moment you push, even when the branch is far ahead of main.
That is the number worth watching: a pushed branch is safe, wherever main has got to.

In the example above, `alpha` is 2 commits ahead of main and still shows blank. Those 2 commits
are on the remote, so nothing is at risk.

The count compares `HEAD` against `origin/<branch>`. When that remote branch does not exist —
a branch you have never pushed — the count falls back to comparing against `origin/main`
instead. A never-pushed branch therefore reports its whole history since main, which is also
what you would lose. Both branches of that rule read as "work only you have".

The command never reads the branch upstream. It builds the `origin/<branch>` and `origin/main`
ref names itself. A branch tracking something else reports against those names regardless.

### `PR (COMMITS)`

`#1766 (6)` names an open pull request and the number of commits GitHub says it holds. The
count comes from GitHub, over the network — it is not derived from your local refs.

`PR (COMMITS)` and `LOCAL COMMITS` count different things, and they do not add up to a total.
In the example, `charlie` shows `#1766 (6)` beside 76 local commits: the pull request holds the
6 commits that were pushed, and 76 more sit on the machine unpushed.

A branch with no open pull request falls back to a bare `(1)` — the number of commits on
`origin/<branch>` that are ahead of `origin/main`. That fallback reads refs only. It does not
look at `HEAD`, so it tells you what is on the remote, not what you have.

The same fallback runs whenever `gh` cannot answer: `gh` missing, `gh` not authenticated, a
network timeout, a rate limit. None of those is distinguishable from "this branch has no pull
request". So on a machine without a working `gh`, every row shows the bare remote count or a
blank, and every open pull request disappears from the table. Run `gh auth status` when the
column looks emptier than you expect.

The lookup asks for every open pull request in the repository at once. When that one call
fails, each branch is retried on its own, so a single failure costs you one blank row rather
than a blank column.

### `SESSION`

The column resolves in three steps:

1. A live session count wins. `3` means 3 `claude` processes run in that worktree now.
2. Otherwise `— stopped`, when a task on that branch left a transcript on disk. The worktree
   ran and stopped.
3. Otherwise blank. The worktree has never run a session.

The live count comes from the running processes, not from any file. So the column stays
correct after a session dies unexpectedly.

## Where each fact comes from

The command builds each row from nine sources. Most run once for the whole project. Only the
per-worktree ones grow with the number of open worktrees.

```
git worktree list ──────────────────► WORKTREE, BRANCH             once per project
one git config --get-regexp ────────► BRANCH (the ← base)          once per project
one rev-list ───────────────────────► which worktrees are closed   once per project
gh api graphql ─────────────────────► PR (COMMITS)                 once per project, over the network
one pgrep + lsof sweep ─────────────► SESSION (live count)         once per project
git status ─────────────────────────► DIRTY FILES                  once per worktree
git rev-list ───────────────────────► LOCAL COMMITS                once per open worktree
port allocation + port probe ───────► APP                          once per open worktree
transcript file checks ─────────────► SESSION (— stopped)          once per task on the branch
```

Five of those run once per project, however many worktrees it holds: the worktree list, the
base lookup, the closed check, the pull request lookup and the session sweep. A closed
worktree still costs one
`git status`, because the closed check must know whether the worktree is clean. It costs nothing
else.

The `— stopped` marker is not part of the session sweep. It checks for a transcript file per
task on the branch, so a branch with many tasks costs several file checks.

The pull request lookup is one GraphQL query for every open pull request in the repository,
rather than one `gh` call per branch. It is still the slowest single source, because it is the
only one that leaves the machine.

## Open and closed worktrees

Only open worktrees get a row. A **closed** worktree is one in the state that makes it
available for recycling: detached HEAD, no dirty files, and no commits ahead of `origin/main`.
See the **Closed** entry in [CONTEXT.md](../../CONTEXT.md) for the definition of record. Closed
worktrees appear as names on the `Closed environments:` line under the table.

`_main` is the reference checkout, and it has `main` checked out rather than a detached HEAD.
It is therefore never closed, and always gets a row. It is never recycled either — see
[worktrees.md](worktrees.md).

## What it does not tell you

`mael list` never fetches. Every count against `origin/<branch>` or `origin/main` reads the
refs your machine already has. Those refs are as old as your last fetch.

So a branch someone else pushed to still shows your stale picture, and `LOCAL COMMITS` can
report work as unpushed that is in fact on the remote. Run `mael sync` on the worktree, or
`git fetch`, before you trust a number you are about to act on.

The command also says nothing about what a session is working on. Use `mael task list` for
that.
