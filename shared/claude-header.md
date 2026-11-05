## Language

Be direct, concise, and dry. Omit conversational filler, polite greetings, and unnecessary
explanations. Output code and technical facts only. Working detail is welcome at the low rank;
the rank changes its presentation and does not delete it.

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

In plan mode, load `planning` before you write the plan.

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

## Build, then test what matters

**Load the `design-build-test` skill before you write implementation code** — a new feature, a
bug fix, any change with behaviour to test, however the work arrived: a planned task, an ad-hoc
request, or a follow-up in an open session. Break the work into modules, build it, then select
tests by risk and write them at the layer that owns each contract.

Where the modules and owning layers come from depends on how the work arrived:

- **Planned work** — the **Test plan** section in your plan or task content settles them. When it
  is missing, name the layer you chose and why in the commit body and the PR. Do not stop and
  wait.
- **Unplanned or resumed work** — no plan agreed them, and the user is here. When the owning
  layer is not clear from the project's test-ownership doc, agree it with them.

Monotonically growing test suites are a maintainability problem, look for tests that can be adapted
or deleted when changing behaviour.

Use `codebase-design` for the vocabulary when the boundary itself is the open question.

Refactoring is not part of the loop — it belongs to `/code-review`, step 4 of the task-completion
flow.

## Finishing a task — run automatically, do not wait to be asked

When implementation work is written, run the **task-completion flow in `/mael`**: it syncs, runs
the gates, reviews, pushes the PR, closes the task, and watches CI. Run it **without prompting
the user**.
This overrides the default "only commit when asked" rule for mael projects, and the whole sequence
is unattended — the PR push, the task close, and the CI watch all run without confirmation.

**The PR is the completion signal.** Once it is raised the work is visible and gets chased, so
close the task as soon as the PR is pushed: `mael task status done`. `/mael` carries the steps
and the reasoning.

## End a session when work is complete

When the system prompt teaches `<end-session>`, end a finished session with it. It says when.

## The wiki — cross-project patterns

The wiki holds design patterns that apply to more than one project. Before you solve a
cross-project problem, or when you need to know the house style, run `mael wiki list`.
If a page covers the problem, read it and follow it.

After you solve one, record it — write a new page, or correct the page you used. `/mael` has the
commands.
