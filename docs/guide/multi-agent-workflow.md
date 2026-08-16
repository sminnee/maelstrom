# The multi-agent workflow

This is the core loop: **plan → chain → parallel sessions → pull request**. Read
[concepts.md](concepts.md) first if you have not.

## The shape of it

You do not type commands into an agent's terminal. You create tasks and launch them.
Maelstrom gives each one a worktree, a workspace, ports and a session. Several run at once.
You supervise; you do not drive.

## 1. Plan

Start from a Linear issue:

```bash
mael linear plan PROJ-123
```

This creates a planning task holding the issue brief, then launches a planning session in
its own cmux workspace. Without a Linear issue, do the same thing directly:

```bash
mael task add "Fix flaky port test" --run
```

New tasks default to plan mode, so a bare `--run` opens a planning session.

Inside that session, the agent researches the codebase, discusses the approach with you,
and sculpts **draft task files** — one file per future task, previewed live with `richview`.
A draft is inert: it becomes a real task only when it is promoted into the notebook.

### Drafts become tasks

Each draft is a task file with its recipe in the frontmatter and the execute session's plan
in the body (abridged — `mael task draft` emits every frontmatter key and all three body
sections):

```markdown
---
title: "Execute: PROJ-123 — add the upload endpoint"
mode: auto
pre-action: linear.in-progress
---

## Content

# PROJ-123: Avatar upload — Iteration 1

Overall goal, iteration scope, seams under test, verification…
```

Two rules do most of the work here:

- **Set the mode on every draft.** New tasks default to plan mode, so an execute draft that
  omits `mode: auto` re-plans instead of running its plan.
- **Leave `branch:` unset on every draft.** Tasks inherit their parent's branch. That default
  keeps every iteration on one branch, accumulating into a **single pull request** that merges
  as a whole. Setting `branch:` opts a task out into its own worktree and PR — right for
  genuinely unrelated work, wrong for splitting one task's iterations.

### Approval promotes the drafts

When you approve the plan, the session promotes each draft in dependency order, closes its
own planning task, and launches the head:

```bash
mael task promote draft-iter1.md --follow-end '*'   # create the head; prints its id
mael task promote draft-tail.md --follow <that id>  # multi-session only
mael task status done                               # close the planning task
mael task next --run --parent "$MAEL_TASK_PARENT"   # head now actionable — launches it
mael session end                                    # stop the planning session
```

Promotion consumes each draft file and wires the chain (`--follow` / `--follow-end`) at the
moment ids exist. The head follows the planning task, so closing the planning task is what
makes the head actionable. `--parent` scopes the launch to this chain, and `next --run`
prints the id it launches.

The head runs in its own session, so the planning session has no work left. It ends itself. The
worktree stays in place — the head usually reuses it, because it inherits the same branch.

## 2. Chain

The promoted head runs in a **separate** session. That session owns the implementation.

Advance the chain with:

```bash
mael task next --run
```

Each `plan-next-step` task plans one more increment and re-queues itself, until the work is
done.

### Slice vertically

Each iteration should be a **thin vertical slice**: an end-to-end cut through every layer it
touches, shipping its own tests and delivering behaviour a user can see. Aim for up to
~1500 lines, landing as several ~500-line commits.

Layer-shaped iterations — "the back-end API", "then the front end", "then the e2e tests" —
are an antipattern. A plan whose iterations are named after layers has been sliced the wrong
way. Re-cut it. Tests ship with the slice they cover; there are no test-only iterations.

Aim for three iterations or fewer per task. More than that usually means the slices are too
thin or sliced by layer.

### Build test-first, at agreed seams

The execute session builds test-first: one failing test, then enough code to pass it, then the
next. A **seam** is the public boundary a test observes behaviour through. Tests go at seams,
never against internals — that is what lets the code be rewritten without rewriting the tests.

Seams are agreed, not chosen mid-implementation. The plan names them in a **Seams under test**
section, and approving the plan agrees them. This matters because an execute session runs
unattended: there is nobody to ask once it starts. Deciding the seams at plan time is what lets
the session write tests without stopping, and it puts the decision where you can still change it.

If you are working outside a plan, agree the seams with the agent before it writes the first
test.

## 3. Run several agents at once

Nothing above is limited to one piece of work. Plan three Linear issues and you get three
chains, each with its own parent, branch, worktree, ports and workspace:

```bash
mael linear plan PROJ-123     # → the next free slot, e.g. charlie
mael linear plan PROJ-124     # → e.g. delta
mael linear plan PROJ-125     # → e.g. echo
```

Each takes the next free worktree slot and the next free port base. The names and numbers
depend on what is already in use, not on the issue id.

Their **workspaces** cannot collide: different worktrees, different branches, different ports.
Their **code** still can. Chains that touch the same files conflict on rebase, and the last one
to merge does the work of reconciling.

Stacking is the answer to that. Base one chain's branch on another's, and the auto-rebase
cascades the parent's changes into the child as they land, instead of saving the whole
reconciliation for merge time:

```bash
mael stack-tip feat/first-chain    # new worktrees stack on this branch
mael stack-tip main                # back to independent chains
```

See [stacking.md](../dev/stacking.md).

Watch them:

```bash
mael list             # what every worktree is doing — see listing.md
mael session list     # live Claude sessions
mael task list        # what is actionable now
```

`mael list` reads live `claude` processes, so its session column is accurate even when a
session died unexpectedly. When a task and its session disagree:

```bash
mael task reconcile         # show the mismatches
mael task reconcile --fix   # apply the corrections
```

### How many at once?

The limit is your attention, not the machine. Each agent needs review when it finishes.
Three or four in flight is comfortable; ten means the pull requests queue up behind you.

## 4. Finish

When an execute session's gates pass, it runs the finishing sequence **without asking**. The
gates are the project's automated checks — tests, lint and type check, as CLAUDE.md defines
them:

1. Commit the implementation.
2. Run `/code-review`.
3. Address blocking findings.
4. Commit each fix as a `--fixup` commit targeting the commit that introduced it.
5. Push: `mael gh create-pr PROJ-123 --squash`. The `--squash` autosquashes the fixups into
   their targets while rebasing onto the branch's base, so the PR lands with clean history.
6. Close the task: `mael task status done`.
7. Run `/watch-pr` to take CI (continuous integration) to green.

### Why the task closes at step 6, not step 7

**The pull request is the completion signal.** Once it is raised, the work cannot be
forgotten — an open PR is visible and gets chased. The task is the fragile half: a task
left in `in-progress/` is invisible and **blocks the rest of its chain**. So close it as
soon as the PR is pushed, while you reliably can, rather than after a CI watch that might
drag on, time out, or lose its session.

See [pull-requests.md](pull-requests.md) for the detail.

## 5. Close the worktree

```bash
mael close              # after the PR merges
mael close --wait       # wait for the merge, then close
```

Close syncs, checks the worktree is clean, and resets to main. It keeps the folder, the
name and the ports, so the next `mael add` recycles the slot. See
[worktrees.md](worktrees.md).

## A full day

```bash
# Morning: plan three issues. Three planning sessions open in three workspaces.
mael linear plan PROJ-123
mael linear plan PROJ-124
mael linear plan PROJ-125

# Approve each plan. Each planning session closes itself and launches its head
# execute task into a new session.

# Check on them.
mael list
mael task list

# PROJ-123 was multi-session; its first iteration is done and its PR is open.
# Advance the chain.
mael task next --run --parent linear.PROJ-123

# Something unrelated came up. It does not need planning.
mael task add "Bump the pinned pyright" --mode auto --run

# End of the day: PROJ-124 merged.
mael close myproject.delta
```

## Next

- [Tasks](tasks.md) — parent, follows, chains, load-many.
- [Planning](planning.md) — the plan-task and plan-next-step skills.
- [Pull requests](pull-requests.md) — the finishing sequence in full.
- [Troubleshooting](troubleshooting.md) — when a session or task goes wrong.
