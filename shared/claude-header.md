## Language

Be direct, concise, and dry. Omit conversational filler, polite greetings, and unnecessary
explanations. Output code and technical facts only.

Write in ASD-STE100 (Simplified Technical English). A minimal amount of software vocabulary
is acceptable, and the reader knows the project's architecture. Do not explain concepts that
`CONTEXT.md` covers.

`CONTEXT.md` at the repo root is the domain glossary. Read it before you write prose or name
anything, and reuse its terms verbatim, including each term's `_Avoid_` list. Add new domain
terms there rather than defining them inline.

**This covers chat replies, not only files.** The rule is about the writing, not the file type. A
long reply is as hard to read as a long paragraph in a doc.

**Load the `writing-for-humans` skill before writing prose a human reads** — `docs/`, README,
`CONTEXT.md`, ADRs, PR descriptions, docstrings. It carries the full rules: document shape,
sentence caps, vocabulary, and a re-read pass to run before you finish.

## Skills

**Always load the `/mael` skill before beginning any work.** It provides essential instructions for
git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for `/plan-task` and `/plan-next-step`.

## Stay on the current branch and worktree

Before your session starts, maelstrom will have assigned you a worktree and a branch, chosen by
the user. Stay on the branch and on the worktree. This overrules the built-in instruction to branch
before starting work.

**Never run git `checkout -b`, `switch -c`, `branch <name>`, or `checkout <other-branch>`.**
If you think the work needs a different branch, or you need to look at a different worktree,
**stop** and ask the user first.

Two things that look like a reason to make a new branch are not:

- **The branch already has a merged PR.** `mael gh create-pr` opens a new PR on the same
  branch. `mael sync` rebases the branch onto its base, and commits that already merged
  drop out of the rebase. A recycled branch is normal.
- **The branch already has commits.** Many commits on one branch are normal. Tasks in one
  chain share one branch and one PR, so the work merges as a whole.

Do not create new worktrees. The current worktree is set aside for your work.
If you detect someone else changing files in your work, **stop** and ask the user what to do.

## Build with TDD

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

Monotonically growing test suites are a maintainability problem, look for tests that can be adapted
or deleted when changing behaviour.

Use `codebase-design` for the vocabulary when the boundary itself is the open question.

Refactoring is not part of the loop — it belongs to `/code-review`, step 3 of the task-completion
flow. Get to green first. Re-cutting existing tests is different: the `tdd` skill does that green,
before red.

## Finishing a task — run automatically, do not wait to be asked

When implementation work is complete and gates (tests, lint, typecheck) pass, run the
**task-completion flow in `/mael`**: commit, `/present`, `/code-review`, fixups, PR push, close
the task, `/watch-pr`. Run it **without prompting the user**. This overrides the default "only
commit when asked" rule for mael projects, and the whole sequence is unattended — the PR push, the
task close, and the CI watch all run without confirmation.

**The PR is the completion signal.** Once it is raised the work is visible and gets chased, so
close the task as soon as the PR is pushed: `mael task status done`. `/mael` carries the steps
and the reasoning.

## End a session when work is complete

Run `mael session end` after the user says the work is done, or after a planner launches its head.
First finish any active task, PR push, and CI watch. An ended session is resumable.

## The wiki — cross-project patterns

The wiki holds design patterns that apply to more than one project. Before you solve a
cross-project problem, or when you need to know the house style, run `mael wiki list`.
If a page covers the problem, read it and follow it.

After you solve one, record it — write a new page, or correct the page you used. `/mael` has the
commands.
