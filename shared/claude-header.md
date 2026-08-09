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
  branch. `mael sync` rebases the branch onto `origin/main`, and commits that already merged
  drop out of the rebase. A recycled branch is normal.
- **The branch already has commits.** Many commits on one branch are normal. Tasks in one
  chain share one branch and one PR, so the work merges as a whole.

## The wiki — cross-project patterns

The wiki holds design patterns that apply to more than one project: which linting tool to
use, how to publish a package, how to set up a new service.

**Before you solve a cross-project problem, run `mael wiki list`.** If a page covers the
problem, read it and follow it. **After you solve one, record it** — write a new page, or
correct the page you used. `/mael` has the commands.

## Language

Write in ASD-STE100 (Simplified Technical English): short sentences, one instruction per
sentence, active voice. A minimal amount of software vocabulary is acceptable (commit, branch,
rebase, fixture, type check), and the reader knows this project's architecture — do not explain
what a worktree, a task, or a port base is.

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
