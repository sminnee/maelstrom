---
name: resolve-rebase-conflicts
description: Resolve an in-progress rebase and continue it. Invoked as `/resolve-rebase-conflicts`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Resolve rebase conflicts

Load `mael` first. Confirm a rebase exists with `git status` and the `rebase-merge` or `rebase-apply` paths. If neither exists, report that and stop.

For each replayed commit, list conflicts with `git diff --name-only --diff-filter=U`. Read each complete file, `git show REBASE_HEAD -- <file>`, and the relevant upstream history. Preserve both compatible intents. When they conflict, keep the newer needed intent and report why. Do not invent behaviour. Remove every conflict marker.

Stage resolved files, then continue:

```bash
git add <files>
GIT_EDITOR=true git rebase --continue
```

Repeat until the rebase ends. Run the project's checks. Confirm the original branch is checked out, then report the files, decisions, and uncertainties.

Record the original branch before resolving. If a check exposes a resolution error while replay is active, stage it and amend the replayed commit. Read the PR when it explains intent. Leave an irreconcilable conflict for a human instead of choosing by convenience.

Leave an irreconcilable rebase in place and report the conflict. Do not abort, push, switch branches, or create a branch.
