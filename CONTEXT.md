# Maelstrom

Maelstrom orchestrates several Claude Code agents working on one repository at the same time.
This file defines the domain terms. Use these words, in these meanings, in code and in prose.

## Project and code isolation

**Project**:
One repository maelstrom manages, held as a bare clone at `~/Projects/<name>/.git` and marked
by a `.mael` file. The project name is load-bearing: worktree folders, port allocations, task
ids and session ids all derive from it.
_Avoid_: Repo, codebase

**Worktree**:
A git worktree under a project. Twenty-six are named from the NATO phonetic alphabet — alpha
to zulu — and sit at `<project>/<project>-<nato>`. The twenty-seventh is `_main`. A worktree is
a durable slot that outlives the branch it currently holds, which is why it is never named
after a branch.
_Avoid_: Checkout, workdir, workspace

**`_main`**:
The one worktree that holds the main branch, so that every NATO worktree stays free for work.
`_main` is the unclosable worktree, and it takes no project prefix in its folder name.
_Avoid_: Reference checkout, main worktree

**Unclosable worktree**:
A worktree that cannot be closed, recycled or removed. `_main` is the only one: it holds the
project's main checkout, and losing it would leave the project with none.

**Recycle**:
Reuse a closed worktree's folder, name and port base for new work. Recycling is why worktree
names and ports stay stable over time.

## Worktree lifecycle

The three verbs differ in what they preserve. Close preserves, remove deletes.

**Open**:
Put a branch into a worktree ready to work in: create the worktree, recycle a closed one, or
reuse the one that already holds the branch, then rebase the branch onto its base before the
session starts. A reused worktree keeps the setup it has, and its rebase does not push.
_Avoid_: Set up, provision

**Base**:
The branch a branch's work is stacked on. Every rebase maelstrom runs targets the base, and
`main` is the base a branch has when it has none of its own. Stored per branch in git config,
so every worktree in the project reads the same value.
_Avoid_: Parent branch, upstream, target branch

**Base tip**:
The SHA `origin/<base>` had at the last successful rebase — the point a branch's own commits
start at. Re-recorded on every successful rebase, because a base amended during review leaves
a stale tip that conflicts.
_Avoid_: Merge base, fork point

**Stack tip**:
One pointer per project naming the branch new worktrees stack on. It is `main` until
`mael stack-tip` moves it, so new work bases on `main`. It falls back to `main` when its
branch is deleted.
_Avoid_: Head, current branch, newest branch

**Collapse**:
What happens to a stacked branch when its base branch is gone — merged or abandoned. The
branch rebases onto `origin/main`, keeping only its own commits, and its stored base is
cleared.
_Avoid_: Flatten, unstack, rebase down

**Close**:
Return a worktree to an empty slot: detach to `origin/main`, free the port allocation, keep the
folder and keep the branch. `close --force` commits outstanding work as `wip: uncommitted
changes` rather than discarding it. `close --discard` removes dirty files but keeps branch
commits and ignored files.

**Remove**:
Delete the worktree folder and free its port allocation. The branch survives. Remove runs the
same teardown as close first — stop the environment, the agents and the sessions — so a removed
worktree leaves nothing of its own running.

**Closed**:
The state that makes a worktree available for recycling: detached HEAD, no dirty files, and no
commits ahead of `origin/main`.

**Step**:
One named unit of a worktree mutation, wrapping a model function and returning its lines. Close,
remove and the server's operations are each a list of steps rather than a function spelled out
per caller, so a step cannot go missing from one of them. See `docs/dev/worktree-steps.md`.
_Avoid_: Stage, phase, action

**Step scope**:
What a step must hold alone while it runs: the repo, or one worktree. The repo scope covers the
project's shared `.git`, which a fetch writes; the worktree scope covers one checkout's index and
`HEAD`. Held with a cross-process lock, because a user running `mael sync` in a terminal is a peer
writer. A step needing both takes repo first, always, so no two steps deadlock. Distinct from the
squash **Scope** below, which is an extent rather than a lock.
_Avoid_: Lock, mutex, critical section

**Dirty file**:
A file `git status` reports as changed in a worktree, staged or unstaged. `.env` is excluded,
because maelstrom generates it — a changed `.env` is not the agent's work.
_Avoid_: Modified file, uncommitted change

**Local commit**:
A commit that exists only on this machine, measured against `origin/<branch>`. A local commit
is work that would be lost with the disk. A branch that is pushed has no local commits, however
far ahead of main it is.
_Avoid_: Unpushed commit, commit ahead

## Task notebook

**Task**:
One unit of agent work, stored as one row in the **State database**. A task carries a plan in
its body and launches exactly one Claude session. The row carries the prose, so a task is read
and written whole.
_Avoid_: Ticket, issue, job

**Status**:
A column on the task row. The six statuses are `todo`, `in-progress`, `blocked`, `done`,
`cancelled` and `template`. Changing one is a single-column update, not a move. The **Task
export** lays a task out under its status, which is a rendering of the column rather than the
authority for it.
_Avoid_: State, folder

**Parent**:
The grouping key that puts a task in a chain sharing one branch and one pull request — "one PR
per parent". A task with an empty parent roots its own chain. A parent is often virtual, such
as `linear.NORT-123`, and is never checked against a real task.
_Avoid_: Epic, group

**Follows**:
The ordering relationship. A task becomes actionable only once every id it follows reaches
`done`. Follows is the system's real blocking mechanism, and it is independent of parent.
_Avoid_: Depends-on, blocked-by

**Dotted id**:
Lineage expressed in a task's name. `PROJ-12.3` is a child of `PROJ-12`, and
`maintenance.2026-07-02` is a scheduled run of the `maintenance` template. Lineage and parent
are separable on purpose: a scheduled run is named under its template yet has an empty parent,
so each firing roots its own chain.

**Qualified id**:
A task id that names its project: `northwind/NORT-7`, against the bare `NORT-7`. A surface
shows the qualified id when it must tell two projects' tasks apart — a panel tab — and the bare
id when something else on screen already names the project, such as a node in its lane.

**Failover id**:
The rule that fills an id slot from the next source down. An agent with no task shows its own
agent id where a task would show its id, in the same slot and the same register. The slot is
one field, so a free agent's node and tab name themselves as plainly as a task's do, and
nothing marks a free agent as lacking a task.
_Avoid_: Fallback id, placeholder id

**Actionable**:
A task maelstrom may launch now: not `done`, not `cancelled`, not `blocked`, not a template,
and every followed id is `done`.
_Avoid_: Ready, unblocked

**Template**:
A task parked in `template/` as a recipe to duplicate from. A template never launches directly.
A `schedule` on a template drives the scheduler.

**Chain**:
The sibling tasks that share one parent, ordered by follows, merging as a single pull request.

**Draft**:
A task file outside the notebook, written by a planning session into the worktree's `.drafts/`
directory.
A draft is inert — invisible to listing, `next` and follow-end resolution — until a promote
creates the real task from it and deletes the file. That gap is the approval gate. Two surfaces
open it: `mael task promote` in a session, and approving the task set's document in the
orchestrator UI, which calls the same step.
_Avoid_: Proposal, pending task, plan file

**Task set**:
The drafts of one chain, shown as one document. Its `<doc-file>` tag names every file in chain
order, and that order is the order approval promotes and chains them in.
_Avoid_: Batch, plan bundle

## Sessions

**Session**:
One Claude Code conversation. A session maelstrom launches is tied to exactly one task.

**Live session**:
A session whose `claude` process is currently running, established from the running processes
themselves rather than from any file. Only a live session stops a task from being re-run.

**Stopped session**:
A session the daemon started whose `claude` process has ended, and which `mael agent resume` or
the node card's Resume can bring back. A stopped session keeps its spawn record, which is what a resume reads, and its
transcript, which says what it was doing. A session started by hand has no record and is not a
stopped session. `mael agent list --stopped` lists them.
_Avoid_: Closed session (a closed worktree is a different thing), ended session, dead session

**Task session id**:
The session id derived from the project name and the task id. The task session id exists before
the session is launched and never changes, so it is what links a session back to its task. The
task table keys on it, an agent row reports it, and it rides into the session as
`MAEL_TASK_SESSION_ID`. Use the task session id to answer "which task is this?".
_Avoid_: Session id (for this concept)

**Session id**:
The id of the conversation running now, reported by Claude Code as `CLAUDE_CODE_SESSION_ID`. A
`/clear` starts a new conversation and moves the session id, so it is not stable and cannot key
a task. Use the session id to answer "which conversation am I in now?". A session starts with
its task session id as its session id, so the two agree until the first `/clear`.

**Workspace**:
A cmux workspace named `<project>-<worktree>`, holding three panes: pane 0 the Claude session,
pane 1 the **shell pane**, pane 2 browsers. Pane numbering is 0-based. Every session runs in a workspace
so that no agent runs somewhere you cannot watch it.
_Avoid_: Window, pane group

## Agents

**Model reference**:
A model name in the form `<harness>:<alias>`, such as `claude:opus`,
`codex:terra`, or `opencode:kimi`. A bare value is Claude-compatible. A blank
value resolves to `claude:opus`.

**Execute model**:
The **Model reference** a session switches to when its plan is approved. A session plans on its
model and builds on its execute model. An unset execute model means no switch, which is what
every session did before the field existed. It must name the `claude` harness: the switch is a
`/model` command, and that cannot change which binary is running.
_Avoid_: Child model, next model, build model

**Harness transport**:
The path that starts an agent. `cli` starts the CLI selected by the **Model
reference**. `daemon` starts a driven Claude agent. Daemon agents export
`MAEL_HARNESS_TYPE=daemon` to their children.

**Agent record**:
The canonical row for an agent maelstrom started: its harness, mode, model, and task, stored so
it survives an orchestrator restart. `DaemonRouter` writes one at start, and reads every record
back to restore Codex threads and rebuild `list`. A live subagent has no record of its own; it
rides through on its parent's. An agent live on a daemon with no record is **adopted** by the next
`list`, which writes one for it; `mael agent register` writes one by hand when that has not
happened.
_Avoid_: Agent session, binding.

**Adopted**:
A live top-level agent with no Agent record, given one by the next `list`. `mael add` and
`mael agent start` reach the daemon socket without the router, so their agents arrive with no
record; adoption is what makes them visible to every reader. A subagent is never adopted.
_Avoid_: Claimed, imported, registered.

**Swept**:
Retired because several consecutive `list` calls did not name the agent, rather than because a
`stop` ended it. The record says which, because a `list` revives only a swept one.
_Avoid_: Reaped, culled, timed out.

**Revived**:
An Agent record that read `ended` and is set back to `running`, because its agent turned out to be
alive. Keeps the task and the start the agent really had, which a fresh adoption would lose. A
`list` revives only a **swept** record; a stop is settled until the user resumes the agent.
_Avoid_: Restored, resurrected, reopened.

**Driven agent**:
A `claude` process the agent daemon holds on a stream-json pipe. Every session maelstrom
launches is a driven agent, so a driven agent normally has a workspace whose pane 0 runs
`mael agent attach` as a client of the daemon. The daemon owns the pipe, not the pane: the
agent runs whether a pane watches it or not.

**Subagent**:
A driven agent's child, spawned by its `Agent` tool and held by the agent daemon as a stream of
its own under a dotted id: agent `X` has subagents `X.1` and `X.2`, and `X.1.1` is a subagent of
`X.1`. A subagent is read, never driven: it has no process of its own, so its asks are answered
through the parent. The wait itself is the subagent's — its row says what it waits on, and the
parent reports every ask beneath it. `mael agent list` shows it under its parent, `show` and
`tail` take its id, and the orchestrator UI opens it from the parent's session tab.
_Avoid_: Sidechain, child session, sub-agent

**Delegating**:
The state of a driven agent whose turn ended while at least one of its **subagents** still runs.
No turn is open and nobody waits for the user, but the work goes on. A background `Bash` does not
count.
_Avoid_: Idle, waiting

**Usage window**:
One rolling budget the Claude account spends against: the five-hour window and the seven-day
window. Each carries a utilisation and the time it rolls over. The reading arrives on a driven
agent's stream, so one account spans every agent on the machine and the freshest reading is the
machine's. It only arrives while an agent takes a turn, so a quiet desk holds an ageing one —
a reader that shows it must say how old it is.
_Avoid_: Rate limit, quota, allowance. **Rate limit** in this codebase means GitHub's, which
refuses a poll and is a different thing entirely.

**Budget quotient**:
How a usage window is doing: the fraction of the window still to run, over the fraction of the
budget still unspent. Below one the window resets before the budget runs out. Above one the
budget goes first, and the figure says by how much. It is what colours the top bar's usage
chips.

The first term is wall clock for the five-hour window, which is shorter than the working day it
sits inside. The seven-day window measures it against the **working week** instead: it spans
nights and weekends, and on the clock those hours give the reading back time nobody could have
spent.
_Avoid_: Burn rate, pace percentage. Not **utilisation**, which is the raw spend the source
reports; the quotient is what that spend means against the time the window actually offers.

**Working week**:
The weighting the seven-day **budget quotient** measures elapsed time against: 8am to 6pm at full
rate on weekdays, the same hours at half rate at the weekend, and nothing overnight. Sixty
weighted hours to the week. The hours are read against a named zone — `Pacific/Auckland` — rather
than the browser's, because a reset is a zone-free instant but "8am" is a wall clock, which exists
only in a zone.
_Avoid_: Business hours, office hours. Both imply a policy about when work is permitted; this is
only a weighting for a reading.

**Context occupancy**:
How full a driven agent's prompt is now, read off the newest `assistant` event on its stream, or
off a **compact boundary**. A level, not a total: each reading replaces the last, so it advances
mid-turn and falls when the agent compacts. It is the number that answers whether to compact, and
the session header and the TUI footer both report it. The **session total** is a different thing:
it sums every turn, re-counting the cached prompt each time, so it runs past any window and says
how much work the session has done.
_Avoid_: Token count, context window usage. Do not call the session total a context size — the
two numbers differ by an order of magnitude on a long session.

**Subagent tokens**:
What a driven agent's subagents consumed, summed, and held on the parent. Disjoint from the
**session total**, which covers the agent's own requests alone: a subagent emits no `result`, so
the host never counted it there. The two sum to the tree's spend, and the parent holds the figure
because an evicted subagent's tokens were still spent.
_Avoid_: Child tokens, nested usage. Not the **session total**, which is the parent's own.

**Compact boundary**:
Where a driven agent's context was compacted, and the only event that says a compact finished. It
carries the occupancy either side of the fall and whether a person asked for it. A compact the
agent refuses — too short a conversation — ends its turn like any other, so nothing else tells a
refusal from a success. The agent may never speak again after compacting, so the boundary is also
where the **context occupancy** falls. The session transcript holds one per compaction, and the
orchestrator UI draws a rule there.
_Avoid_: Compaction event, summary point. A `/clear` is a different thing: it drops the context
and reports nothing.

**Agent daemon**:
The process that holds driven agents and serves the control socket `mael agent` talks to. A
driven agent's live state dies with the daemon, but its spawn record does not, so a later daemon
can start the agent again.

One daemon per daemon root, and one root per machine by default — so normally one daemon holds
every driven agent. An environment can declare its own on its own root, which is how a worktree
tests a change to the agent protocol without driving the agents its `_main` holds.
`mael-agent-daemon status` names the daemon answering: its root, its process id, its start time,
and the worktree its code came from.

**Daemon root**:
The one directory a daemon owns: its socket, its lock, its pid file, its log and its `agents/`
spawn records. `MAEL_AGENT_ROOT` names it, and nothing else does — there is no default. One
daemon per root, enforced by the lock, so a session belongs to exactly one daemon. One owner per
root too: the environment whose `.env` names it. `mael self-env start` runs the everyday daemon
on `~/.maelstrom/daemons/_main`, `mael env start` runs a worktree's, and nothing else starts one.
_Avoid_: Socket directory, spec dir, daemon home

**Wire contract**:
What an agent daemon client sends and reads back: the request payloads, the replies to an ask,
the stream markers, and the shapes of a row and a detail. `agent_wire.py` holds all of it. A
client imports the wire contract and never the daemon's model, so the daemon can change how it
holds agents without changing a client.
_Avoid_: Protocol module, shared types

**Client surface**:
The code a client of the agent daemon imports: the **Wire contract**, the transport that speaks
it, and the harness model. `mael_agent` holds it, so the `mael` CLI and the orchestrator server
reach the daemon without importing `mael_daemon`.
_Avoid_: Client library, SDK

**Stray**:
A driven agent's `claude` process that outlived the daemon that held it. Left by a daemon that
died uncleanly; found by the next daemon start or by `mael-agent-daemon gc` through the pid in its
spawn record, killed with its process group, and its record resumed once. Not an orphan: that
word belongs to the Free agent's `_Avoid_` list and to `mael task reconcile`.
_Avoid_: Orphan, zombie, leftover

**Duplicate**:
A second driven `claude` on a session id a spawn record already owns. Two children on one session
write to one transcript, and each is told the other's turn ended unexpectedly. Killed by the gc;
the record's own child, or the record's resume, is the one that stays.
_Avoid_: Clone, double, second copy

**Permission mode**:
How much a driven agent may do without asking: `plan`, `normal` or `auto`. A task launches under
one mode, and a running agent can be moved between them — by `mael agent set-mode`, by shift+tab
in teleport, by the mode chip in the orchestrator UI, or by the daemon when a plan review is
approved. An approved plan moves the agent to `auto` and clears its context: the plan is settled,
so carrying it out does not need approving edit by edit, and the handover is the whole brief.
The three words are the same ones a task carries, so one word means one thing.
Claude spells `normal` as `default` on the pipe; nothing outside `agent_wire.py` uses that word.
The mode is read off the agent's own event stream, never from what was asked for, so no surface
can show a mode the agent refused.
_Avoid_: Permission level, autonomy, trust level

**Handover**:
The user turn a driven agent gets after its plan is approved and its context is cleared. It names
the plan file rather than carrying the plan, and it says the conversation is fresh, so the agent
re-reads instead of assuming it remembers. The plan file is the canonical copy and has no size
limit; a plan review that names no file is denied, because there is no handover to make.
_Avoid_: Handoff, brief, kickoff

**Wait kind**:
Which of three things a driven agent is blocked on: `awaiting-question`, `awaiting-plan-review`,
or `awaiting-permission`. All three arrive as the same `can_use_tool` event, so the wait kind
comes from the tool name. An agent can hold several waits at once, its own and its subagents',
and each is answered on its own; its state names the oldest. The wait kind is what makes an answer possible — it says which of
`answer`, `approve` or `deny` applies.
_Avoid_: Blocked, stuck

**Stale prompt**:
A prompt whose wait ended, and which nobody answered through the orchestrator. The tool was
approved in the cmux pane, by `mael agent approve`, or by auto-accept; the host resolves the
request and sends no `control_response`. The user can also interrupt the wait, or the agent can
stop, before any answer arrives. A wait also ends when the agent host stops reporting it. The
normaliser marks the item stale when the wait ends. A stale prompt shows what was asked. It
never offers a decision. A stale plan review takes its plan document to the `stale` status, so
the document's review bar stops offering one too. Stale means
the outcome is unknown, not that the answer was no: a tool approved in the cmux pane went ahead,
and the orchestrator only knows it never saw the answer.
_Avoid_: Abandoned, orphaned, expired

**Interrupt**:
Abandoning the turn an agent is running, and leaving the agent alive to take the next message.
Three surfaces offer it to the user: `mael agent interrupt`, Esc in teleport, and the session
tab's **Stop** button. An interrupt of a waiting agent denies the open ask first, with the
reason `Interrupted by user`; answer or deny the ask instead. This is not **stop**, which ends
the agent's process group and is terminal — the node card labels that button **Terminate**. The
wire says what the daemon says; the UI says what the user means.
_Avoid_: Cancel, abort, kill

**Silent agent**:
A working agent that has said nothing for ten minutes. The node card colours the age of its last
message, because past that point the age is the signal and the message is not. An idle agent is
never silent in this sense — an idle agent has nothing to say, and its age is not alarming.
_Avoid_: Stale (a stale prompt is a different thing), stalled, hung

**Drift**:
A task status that disagrees with the agent observed on the task. Three kinds, the same three
`mael task reconcile` names: *finished*, *never-ran*, *orphan-session*. The agent wins when the
two disagree, because an agent state is observed from events and a task status is a file that
goes out of date. A closed task with an agent still on it is not drift — that is
**Finalising**. Drift is a bookkeeping note, never something waiting on the user: it is not
counted in attention.
_Avoid_: Stale (a stale prompt is a different thing), out of date, orphaned

**Finalising**:
A task that is `done` while an agent still runs on it. That is the ordinary tail of the
task-completion flow: the PR is pushed, the task is closed, and `watch-pr` carries CI to green.
The node draws its own state and sits in the running zone, because the work is not settled until
CI is. It carries no drift mark. When the agent stops, the node reads Done.
_Avoid_: Orphan session, lingering session, post-done

**Reconcile**:
Reading every in-progress task against the sessions running now, and reporting each drift with
the status that would correct it. `mael task reconcile` lists the findings; `--fix` applies
them. Reconcile never guesses at a terminal task: a done task with a session still up is
reported, not moved.
_Avoid_: Repair, sync, heal

**Agent message**:
One thing a driven agent said, in its own words. Text blocks only — a `thinking` block is
reasoning the agent did not choose to say, and a `tool_use` block is an action. The daemon keeps
only the last message, so `mael agent list` and `mael agent show` answer without reading a file.

**Loaded skill**:
The whole skill file, injected as a user turn when an agent loads a skill. It is not something
the user said, so the transcript folds it under the skill's name rather than showing the file.
The turn opens with `Base directory for this skill:`, which is the only mark the daemon stream
carries.
_Avoid_: Skill message, skill prompt

**Shell command**:
A command the agent host runs on the user's behalf, asked for with a `!` line in teleport or in
the orchestrator UI. The host runs it in the agent's working directory and injects the command
and its output as two user turns. Maelstrom asks the agent for nothing: a shell command is
context, never a request.
_Avoid_: Bash command (that is the agent's own tool call), local command, bang command

**Spawn record**:
What one driven agent takes to start again: its working directory, its session id, its permission
mode, its model, and the environment it was given. Claude keeps the conversation itself, so the
spawn record holds only the things Claude does not.
_Avoid_: Agent state, checkpoint, snapshot

**Teleport**:
`mael agent attach <id>` — driving one agent from a terminal UI: a transcript of what it does, a
console to answer it in, a prompt for each wait, and a key that interrupts the running turn.
Teleport is a client of the control socket, not a pane attach: a driven agent has no pane to
attach to. Contrast a tail, which renders the same stream but sends nothing back.

**Tail**:
`mael agent tail <id>` — rendering one driven agent's event stream without driving it. A tail
is read-only by construction: it has no channel back to the agent at all.

**Agent host**:
The agent daemon as the orchestrator server sees it: the thing agents run in and are answered
through, reached only over its control socket. The name says the server has a client's view of
it, and that it may later run on another machine.
_Avoid_: Daemon (in UI-facing prose)

**Orchestrator server**:
The process that builds the world from the notebook, `list-all` and the agent host, and serves
it to the orchestrator UI over HTTP: resources by REST, change notices on one stream, one socket
per open agent transcript. `mael-orchestrator serve` runs it.
_Avoid_: Backend, API server

**Change notice**:
One message on the orchestrator server's event stream saying which entities of one kind changed,
by id, and nothing else. The UI refetches what it shows and finds each id present or gone.
_Avoid_: Event, update, push, delta

**Epoch**:
A name for one life of the thing that mints it, so a cursor from before it is refused. The
agent daemon mints one per `start` and per `resume`, and carries it on the backlog marker. The
orchestrator server mints one at start, and carries it on the change stream's `reset`. The two
are unrelated names on unrelated streams.
_Avoid_: Generation, run id, session

## Dev environments

**Environment**:
The running services for one worktree. An environment exists only while its services run — at
most one per worktree, and none once every process is dead. This is the only meaning of "env"
as a noun; a set of shell variables is a *service environment*.
_Avoid_: Env (as a standalone noun for the running services)

**Service**:
One process maelstrom spawns for a worktree, declared in `.maelstrom.yaml`. A service that
declares an `engine` is a container service; every other service is a command service.

**Shared service**:
A service marked `shared: true`, started once for the whole project rather than once per
worktree. A database is the usual case. The project owns shared services; worktrees subscribe
to them, and they stop when the last subscriber leaves. A shared service is a project-level
singleton, so it counts as running when it runs for any worktree. A start brings up the
shared services that are not running, whenever they were declared.

**Optional service**:
A service marked `optional: true`. `mael env start` skips an optional service; `mael env start
<name>` starts that one service and leaves the rest of the environment alone. An optional
service still owns its declared ports, so marking a service optional never renumbers the
services after it. A service cannot be both optional and shared.

**Subscriber**:
A worktree currently using a project's shared services. Shared services stop when the
subscriber list empties.

**Port base**:
The number a worktree owns. Each service port is `port_base * 10 + index`, so two worktrees
never collide. The pseudo-worktree `_shared` holds the project's shared port base.

**Floating base**:
A port base the allocator picks, from the pool of 3-digit numbers 300 to 999. Every NATO
worktree has one, and a recycled worktree keeps the base it had.

**Reserved base**:
The port base `_main` owns, declared by the project and outside the floating pool. A reserved
base returns the same ports every time, and no NATO worktree can be given it.

**Fixed environment**:
The environment of `_main`, on a reserved base — the one instance that is always at the same
address, whatever a NATO worktree happens to be running. A project opts in by declaring the
reserved base; a project that declares none gives `_main` no ports and no environment.

## Quality checks

**Gate**:
An automated check that blocks work when it fails — the project's tests, lint and type check.
A gate that cannot fail is not a gate. A point where a human approves something is a
*checkpoint*, not a gate.
_Avoid_: Gate (for a human approval step)

**Checkpoint**:
A point where the user approves, answers or decides something before the work continues: a plan
review, a question, a permission, a document review. A checkpoint is where a human steps in; a
gate is automated.
_Avoid_: Gate, approval step

## Pull requests

**PR draft**:
The file `.drafts/pr.md`, holding the body a PR will get. `mael gh create-pr` reads it, writes it
to the PR, then deletes it. A missing draft leaves an existing PR's body alone.
_Avoid_: PR description file, body file, draft PR (which is GitHub's own unready-for-review state)

**Working history**:
The ref `refs/mael/history/<branch>/<stamp>`, holding the chronological commits that
`mael git squash-branch` or `mael git uncommit-branch` collapsed. The working history is the order
the work happened in, kept so `git log` can still show the journey and so the collapse can be
undone. One ref per run, stamped in UTC, so a second run keeps the first run's chronology. Review
and present each write one.
_Avoid_: Backup branch, history branch, archive ref

**Squash**:
To collapse a branch's commits into one, leaving it committed. `mael git squash-branch` saves the
working history, collapses the commits, then rebases onto the base. Review runs it to put the
branch's final state in one commit a reviewer reads whole.
_Avoid_: Fold, flatten, uncommit (which resets the commit into the working tree)

**Scope**:
How much of a branch a squash or an uncommit takes in. Distinct from a **Step scope**, which is a
lock rather than an extent. The whole branch (`--remote`, the default),
or only the commits that were never pushed (`--local`). A re-review of a branch whose PR is open
uses `--local`, so it reads the new work alone and the already-reviewed commits keep their own
subjects.
_Avoid_: Range, extent, depth

**Uncommit**:
To return a branch to unstaged changes at its base tip. `mael git uncommit-branch` squashes the
branch, then resets that commit into the working tree. Nothing is discarded — every change is in
the working tree, ready to be committed again. Present runs it to re-cut a reviewed tree.
_Avoid_: Reset, unwind

**Story commit**:
One commit per design decision, holding the decision's rationale in its body. A reviewer reads
story commits in order on the PR's Commits tab. They replace the chronological commits of the
build, so a story commit says *why*, never *when*.
_Avoid_: Logical commit, atomic commit, curated commit

**Present**:
To squash a branch's commits into a working history, then re-cut the same final diff into story
commits. Present runs once per task, after the branch's own review, and never during land. The
tree is the invariant: a wrong partition gives a wrong story, never a wrong tree.
_Avoid_: Curate, reorganise, tidy

## Orchestrator UI

**Phase**:
Which of four stages a task's work is in: shape, plan, build, land. A phase is named as the
imperative of the work, which is what keeps it apart from the agent's state — a task is in build
whether or not an agent runs on it now. Read from the task's `command`, and never stored: an
agent shows the phase of its task. A command nobody recognises has no phase, and neither does an
agent with no task.
_Avoid_: Stage, step, executing, finalising. **Shape** and **plan** are the phases' own names, so
they are used for those phases and not as loose synonyms: shaping creates the tasks that planning
then plans, and `plan` belongs to the `plan-task` skill and to an agent's plan mode.

**Shape**:
Exploring a brief until a set of tasks is agreed and created. Ends at a user checkpoint. May be
skipped.

**Plan**:
Producing an agreed plan for one task. Ends at the plan-review checkpoint.

**Build**:
Building the code, running its own review, presenting it, opening the PR. Ends when the PR first
ships.

**Land**:
Answering CI failures and review feedback on an open PR. Ends when it merges.

**Held text**:
What the user typed into a surface of the orchestrator UI and has not submitted, kept in the
browser so that closing a dialog, switching a tab or reloading the page does not lose it. Held
text is browser-local and per surface. It is cleared when the work is submitted, and where a
surface offers a control to clear it — never when the surface closes. View state — the open tabs,
the filters, the expanded node — is not held.
_Avoid_: Draft (a task file in `.drafts/`), autosave, cache, unsaved changes

**Document**:
A versioned markdown artefact an agent puts in front of the user: a plan, a task set, a PR
description, a review, a document bound for the repo. A document that stands at a checkpoint
awaits review. A document the user was only asked to read is a **draft**, and blocks nothing.
_Avoid_: Artefact, output, file

**Document tag**:
The marker an agent writes in the text of an ordinary message to put a document in front of the
user. `<doc-content>` carries the markdown inline; `<doc-file>` names files in the agent's
worktree, comma-separated, resolved against that directory and nothing outside it. The tag names
the document's `kind` and `title`, and is cut out of the message the transcript shows. A tag
opens its document at `draft`; `review="true"` opens it awaiting review instead, which is what
raises an attention item. One of five markers, with the **Image tag**, the **Note** and the
**Milestone**. The names
carry nothing maelstrom-specific, so another frontend may render them its own way.
_Avoid_: Directive, macro, shortcode

**Note**:
What an agent says it is doing now, written as `<note>Rebasing onto main</note>` in the text of an
ordinary message. A field rather than a document: it is cut from the message, and the latest one
replaces the one before it. The expanded card shows it in place of the agent's last message, which
is whatever prose happened to end a turn. A message carrying no note leaves the standing one
alone, and a subagent writes none. The card still dates its block from the last message, because
silence means the agent said nothing, and a note is not speech. One of five markers, with the
**Document tag**, the **Image tag** and the **Milestone**.
_Avoid_: Status, Progress (that is a node's state), Activity

**Milestone**:
A stage of the work an agent marks as reached, written as `<milestone>built</milestone>` in the
text of an ordinary message. The vocabulary is the task-completion flow: `planned`, `built`,
`reviewed` and `presented`. A name outside it is recorded as the agent wrote it
and flagged, never dropped. The marker is cut from the message and writes a **milestone
snapshot**; the latest one in a message wins, as a note does. `planned` is Maelstrom's own: it is
written when the user approves a plan, because the approval clears the agent before it could
write one. One of five markers, with the
**Document tag**, the **Image tag** and the **Note**. A subagent writes none.
_Avoid_: Checkpoint, phase, stage marker. A **phase** is a task's own, and a checkpoint is where
a document awaits review.

**Milestone snapshot**:
What a driven agent had spent when it reached a milestone: its own tokens, its subagents' tokens,
and the host's dollar figure, with the delta since the milestone before it. The deltas sum to the
total, which is what makes the record answer which stage the spend went to. Written to the
`agent_milestones` table, so it outlives the agent. Read by `mael agent cost` and by the
orchestrator UI, which draws it in the session panel and on the expanded node card.
_Avoid_: Checkpoint, usage record. Not a **usage window**, which is the account's budget rather
than one agent's spend.

**Closing row**:
The milestone snapshot Maelstrom writes as an agent exits, named `<final>`, holding what the
agent spent after its last stage — `/present`, the PR push, the CI watch. Without it that spend
is unaccounted and the deltas no longer sum to the total. Maelstrom's own row, so it is never
flagged as an unknown name, and the brackets are syntax no marker can carry, so an agent cannot
write one. An agent that spent nothing since its last stage gets none.
_Avoid_: Final milestone, shipped. A **milestone** is a stage the agent declares; this one closes
the ledger instead.

**Image tag**:
The marker an agent writes in the text of an ordinary message to show the user a picture:
`<image src="docs/shot.png" alt="The failing dialog">`. The tag becomes a picture where it was
written, so an image is read in the flow of the message and is not a document. `src` names a file
in the agent's worktree; `alt` describes the picture and defaults to the filename. A file the agent
may not show, or one that is not there, leaves prose saying so, never a broken picture. One of five
markers, with the **Document tag**, the **Note** and the **Milestone**.
_Avoid_: Screenshot tag, figure, embed

**User attention**:
The rank an agent sets with `<user-attention high>` or `<user-attention low>` in an ordinary
message. High prose is for the user to read. Low prose is working detail. The renderer draws low
prose at the low rank. The tag stays in the message until the renderer reads it, so it is not
one of the five markers. Low prose holds markdown, including a literal or a link. Nothing on the
server parses it, except that node summaries remove the tag.
_Avoid_: Callout, Highlight, Summary, Important, Attention tag

**Task notification**:
What the harness injects to say background work finished. A subagent's arrives as a `system` event
and ends the subagent, drawing no transcript line of its own. A background command's arrives as a
user turn, which is that command's only trace, so the transcript folds it to one line carrying its
status and summary and drops the ids and the output path it also carries — addresses of files on
the agent's host, which the reader cannot open.
_Avoid_: Task update, background result, completion message

**File registry**:
The ids that stand for the files agents named, and the only route to a file's bytes. A `<doc-file>`
or an `<image>` registers its file, and the URL carries the id and never a path, so a file nobody
registered cannot be reached. An id is not a secret — it is guessable, and what it cannot do is
name a file nobody registered. Not persisted, as a document is not.
_Avoid_: File table, allow-list, handle

**Comment**:
Feedback anchored to a span of one document version. Requesting changes sends the unresolved
comments back to the agent.

**Attention item**:
One thing waiting on the user: a wait kind, a document awaiting review, an exited agent. Raised
and cleared by the backend, never inferred by the UI.

**Brief**:
The free-text starting point for shaping.

**Desk**:
The sticky record of what the canvas draws: tasks, and free agents. The server adds an entry for
every agent it sees start, and the entry outlives the agent, so stopped work stays on the
canvas until the user dismisses it. A restart rebuilds the agents, so an entry naming an agent
that is gone is dropped as the desk loads. Each entry names its kind — `task:<project>/<notebook id>`
or `agent:<agent id>`. The desk is tracked apart from the notebook and is not a status, so a
task stays on the desk whatever its status. A node leaves the desk by a **Dismiss**. There is
one desk today, and one per user later.
_Avoid_: Workspace, board, pinned

**Dismiss**:
Taking a node off the desk. A dismiss never stops an agent; **stop** does, and the node card
offers both in one control — see `docs/dev/orchestrator-ui.md`. The task list row and the
card's follows rows label the same act "Remove from desk".
_Avoid_: Hide, archive

**Active branch**:
A branch with a desk entry at it — a task through the notebook, an agent through its worktree.
The worktree poll asks GitHub about active branches only, because GraphQL is charged by node
count against a budget that refills hourly. A branch off the desk keeps the pull request the last
read saw, rather than reading as having none.
_Avoid_: Watched branch, live branch

**Free agent**:
An agent with no task. A launch pins a task session id on the agent, so an agent that carries
none matches no task. A free agent is started by hand in a worktree, or from the orchestrator
UI's new-work form. A free agent takes its name, branch and lane from the worktree it runs in;
an agent whose worktree the world has not read yet falls back to its own project and a generic
name. A free agent has no task list row, so its node is the only place to **Dismiss** it.
_Avoid_: Orphan agent, loose agent, unlinked agent

**Canvas**:
The view that draws swimlanes of nodes: one per task, one per free agent. A node is drawn when
it is on the desk, or it has a live agent, so running work is always visible.
_Avoid_: Graph view, board

**Zone**:
One of the three stages of progress the canvas lays out left to right: done, running, not
started. A node's zone comes from its state, not from its task status: the running zone means an
agent has been launched and the task is not finished, so an agent that ran and stopped is still
running work rather than history. Zone boundaries line up across every lane, so the board reads
as three vertical stripes. A zone is a stage of progress; a lane is one project's strip.
_Avoid_: Band, column group, stage, phase

**Deck list**:
The narrow layout's main view: the desk as one row per node, tabbed by zone and opening on
running. It draws what the canvas draws — a node per task and per free agent — laid out down the
screen instead of across it, because a phone has no room for a board. The deck list replaces the
canvas below 840px, and never appears at or above that width.
_Avoid_: Mobile canvas, card list, feed

**Unallocated**:
The canvas lane that holds work which resolves to no open worktree, drawn only when grouping by
worktree. A task whose branch has none lands there, and so does a free agent whose worktree is
closed or unread. Unallocated stands for no worktree, so its lane offers no close.
_Avoid_: Ungrouped, orphaned, no worktree

**Task list**:
The view that lists every task the server knows, with filters for status, project, branch and
text. The task list is where a task joins the desk, and one of the places it leaves it. A task
node's expanded card offers the same toggle for every task its task follows or is followed by.
The task list lists tasks only: a free agent has no row, and is dismissed from its node on the canvas.
_Avoid_: Table view, index

**Task editor**:
The form that edits one task's fields: title, status, content and branch, with the planning level
shown above Advanced, and command, mode, base, priority, model and follows folded away there. It
opens from the task list or a task's node on the desk, and writes through `task.update`, except
status, which writes through its own route since it is folder-derived. New work composes the same
field components, so both surfaces read and order fields the same way — except status, which new
work has no control for.
_Avoid_: Task modal, edit form, task detail

**Planning level**:
How much planning a new task gets before it is built: high, regular or none. The level is a
reading over the task's `command` and `mode`, never stored as a field of its own — high is
`plan-task` under `normal`, regular is no command under `plan`, none is no command under `auto`.
High and none mirror `mode_for_command`; regular has no equivalent in Python, where an empty
command means `auto`. New work and the task editor both offer the three as radios and read them
back off the two fields, so editing either field re-derives the level and a pair no level names
reads as N/A.
_Avoid_: Planning depth, plan mode (that is a permission mode), autonomy

**Expanded node**:
A node grown in place into a card that shows its status, the decision it waits on, and links
into the panel. One node is expanded at a time. The narrow layout has no canvas to grow on, so
the same reading fills the screen as a pushed screen instead.
_Avoid_: Popup, detail panel, summary tab, dialog

**Pushed screen**:
What the narrow layout puts over the deck list: a node's detail, a session, or a document. One
screen shows at a time and back pops one level, because a phone has no room for the panel's
tabs. The wide layout pushes nothing — it expands a node and opens tabs.
_Avoid_: Route, page, modal, drill-down

**Decision**:
The block an expanded node or a document shows when an agent waits on the user: the last
messages before the wait, then the prompt (question, permission or plan review).
_Avoid_: Checkpoint UI, prompt card

**Review dock**:
The band under a document holding whatever waits on the user there: an agent's own wait, or the
document's own review route. One dock, so a reader answers in one place whoever is asking. See
`orchestrator-ui/DESIGN.md`, "Review Dock".
_Avoid_: Action bar, footer

**Panel**:
The right-hand column of session and document tabs, beside the canvas and the task list. The
worktree table and the narrow layout have no panel. The Panel toggle in the top bar collapses it,
and a panel link opens it again.
_Avoid_: Sidebar, drawer, detail pane

**Panel link**:
A link that opens a session or a document as a tab in the panel. It carries the open-in-panel
icon.
_Avoid_: Open button

**Offer**:
The list a combobox shows under its field: the options that match what is typed, which the user
may take or ignore. It narrows as the user types and closes when nothing matches, because the
field keeps free text either way.
_Avoid_: Dropdown, autocomplete list, suggestions, menu

**External link**:
A link that leaves the app in a new browser tab — a worktree's pull request on GitHub, or its
dev environment. It carries the external-link icon, so a reader tells it from a panel link
before clicking.
_Avoid_: Outbound link, web link

**PR state**:
How close a worktree's pull request is to merging, as one of seven values. The server decides it
from the merge, the head commit's check rollup and GitHub's mergeability, so every reader shows
one reading.

| Value | Means |
|---|---|
| `merged` | The pull request merged |
| `ci-failed` | A check failed |
| `ci-running` | A check is pending or running |
| `checks-unreadable` | Nothing may read this commit's checks; waiting will not help |
| `conflict` | Checks pass, and the branch does not merge cleanly |
| `unknown` | GitHub has not answered mergeability yet |
| `ready` | Checks pass, and the branch merges cleanly |

The order above is the order the rule reads them, and it is the order a user asks in. A red
build is the thing to fix before a conflict, and `unknown` settles within seconds of a push —
never a quiet `ready`. A draft pull request reads as a draft instead of its state.

`checks-unreadable` and `unknown` part on whether waiting helps, so the first reads quiet rather
than as a spinner nothing will stop. See **Check rollup**.
_Avoid_: PR status, merge state, CI state

**Check rollup**:
GitHub's one-word verdict on a commit's checks, read through GraphQL. The rollup needs the
`checks=read` token permission, which GitHub no longer offers, so some repositories refuse it and
answer `null` — the same answer a repository running no CI gives. The refusal arrives beside the
data as a per-field error, so only the payload says it happened.

A refused rollup falls back to the **Actions run** for the same commit. A repository that refuses
both reads `checks-unreadable`.
_Avoid_: Check status, CI rollup, status check

**Actions run**:
One workflow run, read from the Actions REST API. It carries a status, a conclusion and the
commit it ran against, and it is how a pull request gets a state when the **check rollup** is
refused. Runs are read one page per repository rather than one read per branch, and matched on
the pull request's head commit: a run against an earlier push says nothing about this one.
_Avoid_: Workflow, job, build

**Tone**:
The reading a colour stands for, as one of six: `good`, `bad`, `busy`, `neutral`, `quiet`,
`special`. A tone names how a thing reads, never what it is — `bad`, never `ci-failed` — so one
chip serves a pull request and anything else that reports a state, and each domain keeps its own
map from its values onto the six. Two states may share a tone when they make the same demand on
the reader; the icon parts them.
_Avoid_: Colour, status colour, variant

## Data patterns

How a piece of state reaches the orchestrator. The state database and its machinery exist, and
the desk is on them. Tasks, worktrees and pull requests are not yet: the terms below say where
each is going. See [`docs/dev/data-architecture.md`](docs/dev/data-architecture.md).

The entries above describe the system as it stands today. Where the two disagree — a task is a
markdown file above and a canonical row here — the disagreement is the work outstanding.

**Canonical**:
State maelstrom itself authors, held in the state database. The write is the authoritative act,
so the table is backed up, migrated, and never rebuilt from empty. A row carries its own prose
rather than pointing at a file. The desk is canonical today; tasks are to follow.
_Avoid_: Source of truth, primary, master

**Cached**:
State another system authors, held in the state database as the last answer seen. A reader
takes what is there whatever its age, and a refresher keeps it current. Losing the table costs
one slow read, never data. Worktrees and pull requests are cached.
_Avoid_: Mirror, copy, snapshot

**Refresher**:
What keeps one cached table current. It owns its cadence, its budget and what it does when the
upstream refuses. A reader never triggers one.
_Avoid_: Poller, syncer, updater

**Pass-through**:
State read from its source on each request and stored nowhere, because one route needs it
rather than the whole world. The Linear issues route and attachment bytes are pass-through.
_Avoid_: Uncached, direct, live

**Pushed**:
State whose owner streams it, so the state database holds nothing live. Agents are pushed: the
agent host owns them.
_Avoid_: Streamed, realtime, subscribed

**Progressive**:
A write too slow to answer on completion. It replies at once with an entity in a preparing
state, then reports each step as a change. Worktree setup, worktree close and task inference
are progressive.
_Avoid_: Async, background, deferred

**State database**:
The SQLite database at `~/.maelstrom/state.db` holding every canonical and cached table. One
file, so one transaction and one revision counter cover them all. It holds the desk and the tasks.
`mael admin migrate` creates and upgrades it; every other open refuses a schema it cannot read.
_Avoid_: Cache, store, db

**Revision**:
One monotonic counter in the state database, bumped once per write transaction, and stamped on
every row that transaction wrote. A reader asks what changed since a revision it holds, rather
than re-reading a table. One counter across every table, because that is what lets one number
name an atomic write of several. A transaction that moves no row consumes none, so an idle poll
leaves it where it was. It is not an **Epoch**: an epoch names one life of a stream, and a
revision orders writes within one database.
_Avoid_: Version, sequence, generation, epoch

**Task export**:
The git-committed markdown tree at `~/.maelstrom/tasks`, written from the task table by a queued
worker. It exists for audit and for reading a task in an editor: nothing reads it on any code
path, and it never runs on a write path. A task write queues its export in the same transaction,
and the orchestrator drains that queue — so a write that rolls back exports nothing, and a CLI
process never writes the tree.
_Avoid_: Notebook, mirror, backup

## Knowledge stores

**Task notebook**:
Every task maelstrom knows, as rows in the **State database**. The **Task export** renders it to
markdown at `~/.maelstrom/tasks`, where the **Wiki** and task attachments also live.

**Wiki**:
Curated markdown pages for design patterns that apply to more than one project. The wiki fills
the gap that per-project memory and a repo's own `docs/` both leave open.

**Attachment**:
An image stored in the task notebook under `<project>/images/<bucket>/`, referenced from task
content by a `{{MAEL_TASK_DIR}}` token that expands to an absolute path at launch. A brief from
Linear and a screenshot pasted into the orchestrator UI both land here.
_Avoid_: Upload, file, asset

**Bucket**:
The directory that groups one piece of work's attachments. A task uses its notebook id, a
Linear issue its identifier, an agent tied to no task `agent-<id>`, and a task that does not
exist yet `draft-<random>`.
_Avoid_: Folder, group, album

## Code layout

**Workspace member**:
One package in the uv workspace, with its own `pyproject.toml`, `src/` and `tests/`. The members
are `cli/`, `lib/common/`, `lib/agent/`, `lib/domain/`, `agent-daemon/` and `orchestrator-api/`.
The repository root is not a member: it builds nothing, and `uv sync` reads it to install every
member into one environment.
_Avoid_: Subproject, module, crate

## Open questions

This is unresolved. Do not treat it as settled intent.

**Cancelled dependencies**: Follows gating tests for `done` only, so a cancelled task stalls
everything that follows it permanently. Whether cancelling should release or block its
followers is undecided.
