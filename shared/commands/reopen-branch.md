# Reopen Branch Command

Re-orients you in a worktree that was just **reopened** after a `mael close --force`. The branch
(and any unmerged commits) survived the force-close; this command surfaces the PR and env so you can
decide what's left to finish. It does **not** auto-resume any task pipeline — you drive the next
steps.

This runs **inside** the freshly reopened worktree — the task launch already placed you on the
branch.

## Usage

```
/reopen-branch
```

## Prerequisites

Load the `mael` skill first if it isn't already — all `mael`/`git` commands need
`dangerouslyDisableSandbox: true`.

## Command Logic

1. **Confirm where you are** — show the current branch:

   ```bash
   git rev-parse --abbrev-ref HEAD
   ```

2. **Surface the existing PR**, if any:

   ```bash
   mael gh read-pr
   ```

   If there's no PR for this branch, say so — the work may never have been pushed.

3. **Show env status** so the user knows which services are wired up:

   ```bash
   mael env status
   ```

4. **Summarise outstanding work** — what's unmerged/unpushed:

   ```bash
   mael git status
   mael gh show-code --committed       # everything since branching from main
   ```

5. **Check for a wip commit.** If the branch tip is a `wip: uncommitted changes` commit (it will be
   if `--force` saved a dirty tree), point it out and **offer to soft-reset it** so those changes
   become working-tree edits again:

   ```bash
   git log -1 --format='%s'            # is the tip "wip: uncommitted changes"?
   git reset --soft HEAD~1             # only if the user wants the wip changes back as edits
   ```

6. **Invite the user to decide what to finish.** Lay out what remains (open PR, unmerged commits,
   wip changes) and ask how they want to proceed. Do **not** auto-resume the task pipeline — this is
   an orientation step.

## Notes

- The reopen itself was the deliverable, so the reopen task is effectively complete the moment the
  worktree is back. Closing it (`mael task status done`) is the user's call — don't close it
  automatically.
- This is an **orientation** command: gather context and hand control back. The user drives the
  actual finishing work.
- All `mael`/`git` commands need `dangerouslyDisableSandbox: true`.
