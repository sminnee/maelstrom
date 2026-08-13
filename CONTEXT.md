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
A git worktree at `<project>/<project>-<nato>`, named from the NATO phonetic alphabet — alpha
to zulu, 26 in all. A worktree is a durable slot that outlives the branch it currently holds,
which is why it is never named after a branch.
_Avoid_: Checkout, workdir, workspace

**Reference checkout**:
The `_main` folder, which holds the main branch so that every NATO worktree stays free for
work. A reference checkout is not a worktree: it has no ports, no `.env`, and never gets
recycled.
_Avoid_: Main worktree

**Recycle**:
Reuse a closed worktree's folder, name and port base for new work. Recycling is why worktree
names and ports stay stable over time.

## Worktree lifecycle

The three verbs differ in what they preserve. Close preserves, remove deletes.

**Close**:
Return a worktree to an empty slot: detach to `origin/main`, free the port allocation, keep the
folder and keep the branch. `close --force` commits outstanding work as `wip: uncommitted
changes` rather than discarding it.

**Remove**:
Delete the worktree folder and free its port allocation. The branch survives.

**Closed**:
The state that makes a worktree available for recycling: detached HEAD, no dirty files, and no
commits ahead of `origin/main`.

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
One Claude Code conversation, tied to exactly one task. Each task maps to a deterministic
session id derived from the project name and task id, so the same task always resolves to the
same session.

**Live session**:
A session whose `claude` process is currently running, established from the running processes
themselves rather than from any file. Only a live session stops a task from being re-run.

**Workspace**:
A cmux workspace named `<project>-<worktree>`, holding three panes: pane 0 the Claude session,
pane 1 a shell, pane 2 browsers. Pane numbering is 0-based. Every session runs in a workspace
so that no agent runs somewhere you cannot watch it.
_Avoid_: Window, pane group

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

**Subscriber**:
A worktree currently using a project's shared services. Shared services stop when the
subscriber list empties.

**Port base**:
The 3-digit number, 300 to 999, that one worktree owns. Each service port is
`port_base * 10 + index`, so two worktrees never collide. The pseudo-worktree `_shared` holds
the project's shared port base.

## Quality checks

**Gate**:
An automated check that blocks work when it fails — the project's tests, lint and type check.
A gate that cannot fail is not a gate. A point where a human approves something is a
*checkpoint*, not a gate.
_Avoid_: Gate (for a human approval step)

## Knowledge stores

**Task notebook**:
The git-backed store of task files at `~/.maelstrom/tasks`. Every change is committed.

**Wiki**:
Curated markdown pages for design patterns that apply to more than one project. The wiki fills
the gap that per-project memory and a repo's own `docs/` both leave open.

## Open questions

These are unresolved. Do not treat either as settled intent.

**Cancelled dependencies**: Follows gating tests for `done` only, so a cancelled task stalls
everything that follows it permanently. Whether cancelling should release or block its
followers is undecided.

**Session identity**: The session registry stores both `session_id` and `session_key` for one
concept. The two hold the same value for a maelstrom-launched session; `session_key` falls back
to `claude-<pid>` for a session maelstrom did not launch. The naming is ambiguous and needs
thought before either name is relied on.
