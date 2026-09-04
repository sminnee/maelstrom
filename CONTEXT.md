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
Put a branch into a worktree ready to work in: create the worktree or recycle a closed one,
then rebase the branch onto its base before the session starts. Reusing a worktree that
already holds the branch is not opening it — nothing is set up and no rebase runs.
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
One pointer per project naming the branch new worktrees stack on. It advances to each new
branch, falls back to `main` when its branch is deleted, and is moved by `mael stack-tip`.
_Avoid_: Head, current branch, newest branch

**Collapse**:
What happens to a stacked branch when its base branch is gone — merged or abandoned. The
branch rebases onto `origin/main`, keeping only its own commits, and its stored base is
cleared.
_Avoid_: Flatten, unstack, rebase down

**Close**:
Return a worktree to an empty slot: detach to `origin/main`, free the port allocation, keep the
folder and keep the branch. `close --force` commits outstanding work as `wip: uncommitted
changes` rather than discarding it.

**Remove**:
Delete the worktree folder and free its port allocation. The branch survives.

**Closed**:
The state that makes a worktree available for recycling: detached HEAD, no dirty files, and no
commits ahead of `origin/main`.

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
One unit of agent work, stored as a markdown file at `<project>/<status>/<id>.md`. A task
carries a plan in its body and launches exactly one Claude session.
_Avoid_: Ticket, issue, job

**Status**:
The folder a task sits in. Status is never written into the file — moving the file is the only
way to change status. The six statuses are `todo`, `in-progress`, `blocked`, `done`,
`cancelled` and `template`.
_Avoid_: State

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
A task file outside the notebook, written by a planning session into the worktree directory.
A draft is inert — invisible to listing, `next` and follow-end resolution — until
`mael task promote` creates the real task from it and deletes the file.
_Avoid_: Proposal, pending task, plan file

## Sessions

**Session**:
One Claude Code conversation. A session maelstrom launches is tied to exactly one task.

**Live session**:
A session whose `claude` process is currently running, established from the running processes
themselves rather than from any file. Only a live session stops a task from being re-run.

**Stopped session**:
A session the daemon started whose `claude` process has ended, and which `mael agent resume` can
bring back. A stopped session keeps its spawn record, which is what a resume reads, and its
transcript, which says what it was doing. A session started by hand has no record and is not a
stopped session. `mael agent list --stopped` lists them.
_Avoid_: Closed session (a closed worktree is a different thing), ended session, dead session

**Task session id**:
The session id derived from the project name and the task id. The task session id exists before
the session is launched and never changes, so it is what links a session back to its task. It
keys the session's file in the registry, and rides into the session as `MAEL_TASK_SESSION_ID`.
Use the task session id to answer "which task is this?".
_Avoid_: Session id (for this concept)

**Session id**:
The id of the conversation running now, reported by Claude Code as `CLAUDE_CODE_SESSION_ID`. A
`/clear` starts a new conversation and moves the session id, so it is not stable and cannot key
a task. Use the session id to answer "which conversation am I in now?". A session starts with
its task session id as its session id, so the two agree until the first `/clear`.

`session_key` is neither term: it is the registry filename only. `session_key` holds the task
session id where maelstrom launched the session, and `claude-<pid>` where it did not.

**Workspace**:
A cmux workspace named `<project>-<worktree>`, holding three panes: pane 0 the Claude session,
pane 1 a shell, pane 2 browsers. Pane numbering is 0-based. Every session runs in a workspace
so that no agent runs somewhere you cannot watch it.
_Avoid_: Window, pane group

## Agents

**Driven agent**:
A `claude` process the agent daemon holds on a stream-json pipe. A driven agent has no cmux
workspace and no TTY, so nothing observes or answers it except the daemon. Contrast a session,
which runs in a workspace with its hooks.

**Agent daemon**:
The one process per machine that holds every driven agent and serves the control socket
`mael agent` talks to. A driven agent's live state dies with the daemon, but its spawn record
does not, so a later daemon can start the agent again.

**Permission mode**:
How much a driven agent may do without asking: `plan`, `normal` or `auto`. A task launches under
one mode, and a running agent can be moved between them — by `mael agent set-mode`, by shift+tab
in teleport, by the mode chip in the orchestrator UI, or by the agent itself when a plan review
is approved. The three words are the same ones a task carries, so one word means one thing.
Claude spells `normal` as `default` on the pipe; nothing outside `agent_model.py` uses that word.
The mode is read off the agent's own event stream, never from what was asked for, so no surface
can show a mode the agent refused.
_Avoid_: Permission level, autonomy, trust level

**Wait kind**:
Which of three things a driven agent is blocked on: `awaiting-question`, `awaiting-plan-review`,
or `awaiting-permission`. All three arrive as the same `can_use_tool` event, so the wait kind
comes from the tool name. The wait kind is what makes an answer possible — it says which of
`answer`, `approve` or `deny` applies.
_Avoid_: Blocked, stuck

**Stale prompt**:
A prompt whose wait ended, and which nobody answered through the orchestrator. The tool was
approved in the cmux pane, by `mael agent approve`, or by auto-accept; the host resolves the
request and sends no `control_response`. The user can also interrupt the wait, or the agent can
stop, before any answer arrives. The normaliser marks the item stale when the wait ends. A stale
prompt shows what was asked. It never offers a decision. A stale plan review takes its plan
document to the `stale` status, so the document's review bar stops offering one too. Stale means
the outcome is unknown, not that the answer was no: a tool approved in the cmux pane went ahead,
and the orchestrator only knows it never saw the answer.
_Avoid_: Abandoned, orphaned, expired

**Agent message**:
One thing a driven agent said, in its own words. Text blocks only — a `thinking` block is
reasoning the agent did not choose to say, and a `tool_use` block is an action. The daemon keeps
only the last message, so `mael agent list` and `mael agent show` answer without reading a file.

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
per open agent transcript. `mael orchestrator serve` runs it.
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
to them, and they stop when the last subscriber leaves.

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

## Orchestrator UI

**Phase**:
Which of four stages a task's work is in: shape, plan, build, land. A phase is named as the
imperative of the work, which is what keeps it apart from the agent's state — a task is in build
whether or not an agent runs on it now. Read from the task's `command`, and never stored: an
agent shows the phase of its task. A command nobody recognises has no phase, and neither does an
agent with no task.
_Avoid_: Stage, step, shaping, planning, executing, finalising

**Shape**:
Exploring a brief until a set of tasks is agreed and created. Ends at a user checkpoint. May be
skipped.

**Plan**:
Producing an agreed plan for one task. Ends at the plan-review checkpoint.

**Build**:
Building the code, running its own review, opening the PR. Ends when the PR first ships.

**Land**:
Answering CI failures and review feedback on an open PR. Ends when it merges.

**Document**:
A versioned markdown artefact an agent produces for a checkpoint: a plan, a task set, a PR
description, a review.
_Avoid_: Artefact, output, file

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
task stays on the desk whatever its status. There is one desk today, and one per user later.
_Avoid_: Workspace, board, pinned

**Free agent**:
An agent with no task. A launch pins a task session id on the agent, so an agent started by hand
in a worktree matches no task. A free agent takes its name, branch and lane from the worktree it
runs in; an agent whose worktree the world has not read yet falls back to its own project and a
generic name. A free agent is dismissed from its own node.
_Avoid_: Orphan agent, loose agent, unlinked agent

**Canvas**:
The view that draws swimlanes of nodes: one per task, one per free agent. A node is drawn when
it is on the desk, or it has a live agent, so running work is always visible.
_Avoid_: Graph view, board

**Task list**:
The full-width view that lists every task the server knows, with filters for status, project,
branch and text. The task list is where a task joins the desk or leaves it. It lists tasks only:
a free agent has no row, and is dismissed from its node on the canvas.
_Avoid_: Table view, index

**Task editor**:
The form that edits one task's fields: title, content and branch, with command, mode, priority
and model folded away. It opens from the task list and writes through `task.update`. A status
moves through the status picker instead, because status is folder-derived.
_Avoid_: Task modal, edit form, task detail

**Expanded node**:
A node grown in place into a card that shows its status, the decision it waits on, and links
into the panel. One node is expanded at a time.
_Avoid_: Popup, detail panel, summary tab, dialog

**Decision**:
The block an expanded node or a document shows when an agent waits on the user: the last
messages before the wait, then the prompt (question, permission or plan review).
_Avoid_: Checkpoint UI, prompt card

**Panel link**:
A link that opens a session or a document as a tab in the right panel. It carries the
open-in-panel icon.
_Avoid_: Open button

## Knowledge stores

**Task notebook**:
The git-backed store of task files at `~/.maelstrom/tasks`. Every change is committed.

**Wiki**:
Curated markdown pages for design patterns that apply to more than one project. The wiki fills
the gap that per-project memory and a repo's own `docs/` both leave open.

## Open questions

This is unresolved. Do not treat it as settled intent.

**Cancelled dependencies**: Follows gating tests for `done` only, so a cancelled task stalls
everything that follows it permanently. Whether cancelling should release or block its
followers is undecided.
