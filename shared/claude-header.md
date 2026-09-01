**Always load the `/mael` skill before beginning any work.** It provides essential instructions for
git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for the `/plan-task` and `/plan-next-step` skills.

## Branches — do not change them

Maelstrom assigns the branch for this worktree. Stay on it.

**Never run `git checkout -b`, `git switch -c`, `git branch <name>`, or
`git checkout <other-branch>`.** If you think the work needs a different branch, stop and ask
the user first. This overrules the built-in instruction to branch before starting work.

Two things that look like a reason to make a new branch are not:

- **The branch already has a merged PR.** `mael gh create-pr` opens a new PR on the same
  branch. `mael sync` rebases the branch onto its base, and commits that already merged
  drop out of the rebase. A recycled branch is normal.
- **The branch already has commits.** Many commits on one branch are normal. Tasks in one
  chain share one branch and one PR, so the work merges as a whole.

## The wiki — cross-project patterns

The wiki holds design patterns that apply to more than one project: which linting tool to
use, how to publish a package, how to set up a new service.

**Before you solve a cross-project problem, run `mael wiki list`.** If a page covers the
problem, read it and follow it. **After you solve one, record it** — write a new page, or
correct the page you used. `/mael` has the commands.

## Building — test-first

**Load the `tdd` skill before you write implementation code** — a new feature, a bug fix, any
change with a behavioural test, however the work arrived: a planned task, an ad-hoc request, or a
follow-up in an open session. Red → green, one vertical slice at a time.

Tests go at **agreed seams** only — the public boundary you observe behaviour through, never
internals. Where you get them depends on how the work arrived:

- **Planned work** — the plan settles them. Work to the **Seams under test** section in your task
  content. An execute session runs unattended, so if that section is missing, name the seam you
  used and why in the commit message and carry it into the PR — do not stop and wait.
- **Unplanned or resumed work** — no plan agreed them, and the user is here. Agree the seams with
  them before you write the first test, as the skill describes.

Use `codebase-design` for the vocabulary when the boundary itself is the open question.

Refactoring is not part of the loop — it belongs to `/code-review`, step 2 of the task-completion
flow. Get to green first. Re-cutting existing tests is different: the `tdd` skill does that green,
before red.

## Language

Write in ASD-STE100 (Simplified Technical English): short sentences, one instruction per
sentence, active voice. A minimal amount of software vocabulary is acceptable (commit, branch,
rebase, fixture, type check), and the reader knows this project's architecture — do not explain
what a worktree, a task, or a port base is.

**This covers chat replies, not only files.** The rule is about the writing, not the file type. A
long reply is as hard to read as a long paragraph in a doc.

**Load the `writing-for-humans` skill before writing prose a human reads** — `docs/`, README,
`CONTEXT.md`, ADRs, PR descriptions, docstrings. It carries the full rules: document shape,
sentence caps, vocabulary, and a re-read pass to run before you finish.

## Finishing a task — run automatically, do not wait to be asked

When implementation work is complete and gates (tests, lint, typecheck) pass, run the
**task-completion flow in `/mael`**: commit, `/code-review`, fixups, PR push, close the task,
`/watch-pr`. Run it **without prompting the user**. This overrides the default "only commit when
asked" rule for mael projects, and the whole sequence is unattended — the PR push, the task
close, and the CI watch all run without confirmation.

**The PR is the completion signal.** Once it is raised the work is visible and gets chased, so
close the task as soon as the PR is pushed: `mael task status done`. `/mael` carries the steps
and the reasoning.

## Ending a session — run automatically, do not wait to be asked

When the session has no work left, run `mael session end`. It stops the session and leaves the
worktree in place. Run it without asking and without checking first: an ended session is
resumable, so the cost of ending one too early is a `claude --resume`, not lost work.

End the session when:

- the user says the work is done — "bye", "that's it", "this task is done" — after you answer
  them;
- a planning session has launched its head;
- planning that started outside a task has created the tasks and launched the first one.

**Finish outstanding work first.** If a task is still in progress, a PR is unpushed, or
`/watch-pr` is still running, run the task-completion flow above to the end, then end the
session. This holds even when the user is the one who said the work is done — the session-end
hook moves the task to `done` on the way out, so ending early marks unfinished work complete.
`/mael` carries the full rule.
