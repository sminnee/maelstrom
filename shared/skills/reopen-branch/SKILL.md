---
name: reopen-branch
description: Re-orient in a worktree reopened after a force-close. Invoked as `/reopen-branch`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Reopen branch

Load `mael` first. This is an orientation command. Do not resume the task pipeline.

1. Show the branch: `git rev-parse --abbrev-ref HEAD`.
2. Show its PR: `mael gh read-pr`. Report when none exists.
3. Show `mael env status`, `mael git status`, and `mael gh show-code --committed`.
4. Check `git log -1 --format='%s'`. If the tip is `wip: uncommitted changes`, offer `git reset --soft HEAD~1`; run it only with user approval.
5. Summarise the open PR, unmerged commits, and WIP changes. Ask what to finish.

Do not close the reopen task. Resumed work has no agreed test seams; agree them before its first test.

The reopen is the deliverable. Hand control back after orientation; do not infer that an open PR, WIP commit, or unmerged change authorises resuming the pipeline.
