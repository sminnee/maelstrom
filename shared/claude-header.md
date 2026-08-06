**Always load the `/mael` skill before beginning any work.** It provides essential instructions for
git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for the `/plan-task` and `/plan-next-step` skills.

## Branches — do not change them

Maelstrom assigns the branch for this worktree. Stay on it.

**Never run `git checkout -b`, `git switch -c`, `git branch <name>`, or
`git checkout <other-branch>`.** If you think the work needs a different branch, stop and ask
the user first.

Two things that look like a reason to make a new branch are not:

- **The branch already has a merged PR.** `mael gh create-pr` opens a new PR on the same
  branch. `mael sync` rebases the branch onto `origin/main`, and commits that already merged
  drop out of the rebase. A recycled branch is normal.
- **The branch already has commits.** Many commits on one branch are normal. Tasks in one
  chain share one branch and one PR, so the work merges as a whole.

## The wiki — cross-project patterns

The wiki holds design patterns that apply to more than one project: which linting tool to
use, how to publish a package, how to set up a new service.

**Before you solve a cross-project problem, run `mael wiki list`.** It prints every page and
a one-line description. If a page covers the problem, read it and follow it.

**After you solve one, record it.** Write a new page, or correct the page you consulted if
it is wrong or out of date:

```bash
mael wiki list                                       # table of contents
mael wiki read dev-patterns/python/pypi-publication  # read a page
printf '...' | mael wiki update dev-patterns/python/pypi-publication --content-file -
```

`update` replaces the whole page and commits it. To change one part of a page, read it
first, then write the full body back.

## Language

Write in ASD-STE100 (Simplified Technical English) where possible: short sentences, one
instruction per sentence, active voice, approved words in their approved meaning.

Two allowances on top of plain STE:

- A minimal amount of software development vocabulary is acceptable (commit, branch, rebase,
  fixture, type check).
- Assume the reader knows this project's architecture. Do not explain what a worktree, a task,
  or a port base is.

## Finishing a task — run automatically, do not wait to be asked

When implementation work is complete and gates (tests, lint, typecheck) pass, run this
sequence **without prompting the user**. This overrides the default "only commit when
asked" rule for mael projects:

1. Commit the implementation work.
2. Run `/code-review`.
3. Triage the findings by what the fix costs: apply what is correct and in scope, discard what does not apply. Carry scope changes and potential refactors into the PR description instead of acting on them.
4. Commit the review fixes as `--fixup` commits (one per finding fixed, targeting the originating commit). Do not amend.
5. Push the PR with `mael gh create-pr <ISSUE-ID> --squash` — `--squash` autosquashes the fixup commits into their targets as it rebases onto `origin/main` before pushing.
6. **Close the task.** Run `mael task status done` (defaults to `$MAEL_TASK_ID`). The PR is pushed,
   so the work is handed off — close it now, while you reliably can, rather than after the CI watch.
   A leftover PR is visible and gets chased; a task left in `in-progress/` is invisible and blocks
   its chain. The SessionEnd hook is only a backstop; don't rely on it.
7. Run `/watch-pr` to take CI to green autonomously (fix → fixup/chore → `mael sync` → wait, looping until CI passes or times out).

If there is nothing worth applying, skip steps 3–4 and go straight to step 5.

This whole sequence runs without user confirmation — including the PR push (step 5), closing the
task (step 6), and the CI watch (step 7).

**The PR is the completion signal** — once it's raised, the work is no longer in danger of being
forgotten: an open PR is visible on GitHub and gets chased. The task is the fragile half, so close it
as soon as the PR is pushed, before it can go stray if CI drags on, the session dies, or the PR is
merged before you get back to it. The SessionEnd hook moves the task to `done` when the session ends,
but it can fail silently (if `mael` isn't on PATH, git is unavailable, or the process is killed).
Don't rely on it — run `mael task status done` explicitly at step 6 so the task closes
deterministically.
