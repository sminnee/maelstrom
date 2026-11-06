---
name: plan-next-step
description: Plan the next tasks of a multi-session chain when what remains was unclear. Runs in a mael planning session. Invoked as `/plan-next-step`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Plan next step

Load `planning` first. The initial prompt says what remains. Verify it against the branch, task status, and relevant files. This session plans only; do not implement or write to Linear.

1. Reconcile the prompt with commits, status, diffs, and source.
2. Draft 1–3 plan-mode tasks for what remains, as `planning`'s task-chains.md says.
3. When it is still genuinely unclear what remains after them, add a normal-mode `plan-next-step` tail draft using this planner's model. Its content states what remains.
4. After approval, promote, close, launch, and end as `planning`'s task-chains.md says.

When no work remains, draft nothing and say so.

Confirm the launched id is the first task just promoted. A blocked chain requires `mael task list`, not a different task launch.
