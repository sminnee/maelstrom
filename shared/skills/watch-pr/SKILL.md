---
name: watch-pr
description: Watch or babysit the current PR, fix CI failures, and repeat until CI passes. Use after a PR push or when current-branch CI is red. Invoked as `/watch-pr`.
metadata:
  opencode/autoinvoke: true
  opencode/slash: true
---

# Watch PR

Load `mael` first. An open PR for the current branch is required. Run this loop without waiting for the user:

1. Run `mael gh read-pr --wait; echo "EXIT_CODE=$?"` in the background. Read the printed exit code, not the background completion status.
2. On pass, report green and stop. On timeout, report it and stop.
3. On failure, inspect `mael gh read-pr` and `mael gh check-log <run-id> --failed-only`. Download artifacts when needed. Reproduce where practical and run project gates.
4. Fix every reported failure. Commit a PR-caused failure as `git commit --fixup <sha>` against its story commit. Commit an unrelated failure as a standalone `chore:`.
5. Run `mael sync`. Resolve any rebase conflict, then return to step 1.

Stay on the current branch. Do not run `/present` or close the task here. Stop only on pass, timeout, or a failure you cannot resolve; report the blocker.

Use the Playwright trace skill for a `trace.zip`. Classify a fix by the failing path: PR-caused work is a fixup on its story commit; other work is a separate `chore:`. If `mael sync` reports a successful rebase but a failed push, retry with `mael gh create-pr <ISSUE-ID>`. An ad-hoc parent is not a Linear id; derive an issue id only from a `linear.` parent or branch history.
