# Worktree steps

Every worktree mutation is the same small set of steps in a different subset and order. Close
stops the environment, the agents and the sessions, rescues `.env`, closes in git and closes the
cmux workspace. Remove is that list with a different git step, a dirty-file guard and no rescue.
The open path is not a sequence — see "What stays whole" — but it takes the same scopes.

`src/maelstrom/worktree_steps.py` holds that vocabulary once, so a sequence is a list rather than
a function each caller writes out. `worktree_close.py` builds close and remove over it, and
`worktree_ops.py` builds sync and the environment — every operation the orchestrator offers.

## Why it is shared

Spelling a sequence by hand loses a step. `mael remove` stopped the environment and deleted the
checkout, and nothing else. The pid sweep that followed signalled the `claude` process of any
driven agent, which the daemon reads as an unexpected exit and records as a crash. Every remove
over a driven agent left a phantom `exited` agent in `mael agent list --all` and on the canvas.

Close got this right. Remove never did, because nothing made the step shared.

## A step and a sequence

A step is a named unit with a call that returns its lines:

```python
@dataclass
class StepOutcome:
    messages: list[str] = field(default_factory=list)
    blocked: str | None = None
```

`run_sequence` runs the steps in order, collects their lines, and stops at the first `blocked`.
That message is what the user reads — on a button, or on stderr.

The vocabulary, each step wrapping a model function that already exists. The steps themselves are
built in `worktree_close.py`; `worktree_steps.py` holds `Step`, `StepOutcome` and `run_sequence`:

| Step | Wraps | Scope |
|---|---|---|
| `stop_env` | `env.get_env_status` + `env.stop_env` | worktree |
| `stop_agents` | `agent_stop.stop_agents_in_worktree` | worktree |
| `stop_sessions` | `session_discovery.LiveSessionSet.all_for` + `env.stop_sessions` | worktree |
| `rescue_env_vars` | `worktree.copy_back_new_env_vars` | worktree |
| `check_dirty` | `worktree.get_worktree_dirty_files` | worktree |
| `git_close` | `worktree.close_worktree` | worktree |
| `git_remove` | `worktree.remove_worktree_by_path` | repo + worktree |
| `close_workspace` | `cmux.mael_layout.close_workspace` | none |
| `rebase` | `worktree.sync_worktree` / `sync_worktree_with_autorepair` | worktree |
| `start_env` | `env.start_env` | worktree |
| `stop_env` (as an operation) | `env.stop_env` | worktree |

`close_workspace` takes no scope: cmux is not git.

A restart is `stop_env` then `start_env`, not a third code path. A stop that fails blocks the
sequence, so a half-restart never happens — starting over a process that would not die hides the
fault behind a running service.

## Two ordering rules

Both are encoded in `_teardown_steps` rather than remembered at each caller.

**`stop_agents` runs before `stop_sessions`.** Signalling the pids first makes the daemon record a
deliberate stop as a crash. This is the rule `mael remove` broke.

**`close_workspace` runs only after the git step succeeded.** A refused close leaves the user's
cmux workspace where it was. A refusal stops the sequence, so this holds by construction.

## Scopes

Worktree operations are concurrent. Two syncs on two worktrees share nothing, so serialising them
would make a fleet of worktrees as slow as a queue.

**Each step declares the scope it needs, and holds it only while it runs.** The lock belongs to the
step, not to the sequence: locking a whole worktree for a whole run would serialise more than the
work requires, and could not express a step needing two scopes.

| Scope | Held by | Why |
|---|---|---|
| repo | a fetch, and anything writing shared refs | The worktrees share one bare `.git`. A fetch writes the object store and the remote refs every other worktree rebases against |
| worktree | a teardown step, a close, a remove | The index and `HEAD` belong to one checkout. A rebase and a detach at once is corruption, not slowness |

A step needing both takes **repo then worktree, always in that order**, so no two steps can
deadlock against each other.

**A step must not declare a scope the algorithm it wraps takes for itself.** `flock` is per open
file description, so a second acquire blocks against the first even inside one thread. `git_close`
therefore takes the worktree scope only: the `close_worktree` it wraps syncs, and that sync's fetch
takes the repo scope at the fetch — which is the right granularity anyway, because the rest of the
close touches one checkout. `git_remove` takes both, because `remove_worktree_by_path` takes
nothing of its own.

The locks are `flock`, not `asyncio.Lock`. The orchestrator server is not the only writer: a user
running `mael sync` in a terminal is a peer, and so is another worktree's tooling. `task_store.py`
and `agent_server.py` lock the same way. The lock file lives under the project's shared
`.git/mael-locks/`, which every worktree of the project reaches.

A project with no `.git` locks nothing. The lock protects real git work from real git work, and a
lock that cannot be taken must never be the reason an operation fails.

### The repo scope is easy to miss

A fetch looks worktree-local. `rebase_worktree` runs `git fetch origin` with `cwd=worktree_path`,
then calls `update_local_main(worktree_path.parent)` — writing the project's shared `main`. It is a
repo-scoped write issued from inside a worktree. The sharp edge is `fetch --prune`, which a stacked
branch adds and which deletes remote refs another worktree may be mid-rebase against.

Only the rebase funnel takes the scope. The other `fetch origin` sites in `worktree.py` and
`cli.py` do not, and `tidy_branches` prunes unconditionally — locking those is follow-up work.

One check-then-act needs the repo scope for more than a single git call. `setup_worktree_for_branch`
reads `find_closed_worktree` and then writes with `recycle_worktree`. Two opens in one project could
otherwise claim the same closed worktree.

Nothing else needs a lock. `ports.py` does its load-modify-save through `util.locked_file`, a
cross-process `flock`, so a port allocation is already atomic across every process.

## What stays whole

`worktree.close_worktree` is **not** decomposed. It looks like the same kind of sequence — sync,
verify dirty, verify unmerged, detach, free ports. But its `force` and `discard` flags branch
*inside* its steps, and those steps share mutable local state. Threading that through a step
context is more machinery than the duplication costs. It is one `git_close` step, entire.

`sync_worktree` and `create_worktree`/`recycle_worktree` are each one step for the same reason.

`setup_worktree_for_branch` is not a sequence either. Its conditionals live *between* the steps it
would have: an early return when a worktree already holds the branch, a recycle that falls back to
create on any exception, and a three-way base recording. A flat list cannot express that. The open
path takes the scopes, not the shape.

## Where a step runs

A blocking step runs on the executor the sequence was given. `None` uses the loop's default, which
is right for the CLI: one operation, one process.

The server passes its bounded worktree pool, which is a second pool beside the notebook's. The
notebook's SQLite index is bound to the thread that opened it, so it must stay one worker — and a
fetch holding that thread would stall every task read for as long as it ran.

**The pool is handed to the sequence, not applied at the call site.** An operation is a coroutine
that awaits its steps, so offloading the operation would do nothing; it is each blocking *step*
that needs a thread. `asyncio.to_thread` would use the loop's own default executor instead, which
is unbounded, so the pool's width would bound nothing.

That width is for overlap, not for correctness. The scopes are what enforce correctness, so do not
reduce it to one.

## Designed for streaming

Per-step progress is a planned next iteration, so the runner takes that seam now.

A sequence is driven through one `announce: Callable[[str], None]` — the argument
`sync_worktree_with_autorepair` and `setup_worktree_for_branch` already take. The CLI passes
`click.echo`; the server passes a collector. When streaming lands, the server passes a callback that
publishes a notice, and no step changes.

Two properties make that swap cheap. A step is **named**, so a later notice can say "stopping env"
rather than relaying a raw line — `SequenceResult.blocked_step` carries which step refused. And a
step **announces as it goes and returns its lines too**: the returned `messages` stay the record of
the whole run, and `announce` is the live edge.

The notice kind, the per-row client state, and the channel that carries them are not built.
