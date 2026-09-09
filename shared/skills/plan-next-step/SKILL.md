---
name: plan-next-step
description: Plan one concrete step of a multi-session chain and re-queue the remaining work. Runs in a mael planning session. Invoked as `/plan-next-step`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Plan next step

Load `planning` first. The initial prompt contains remaining work and the expected completed state. Verify both against the branch, task status, and relevant files. This session plans only; do not implement or write to Linear.

Use the prompt as the plan of record, then reconcile it with commits, status, diffs, and source. Plan the top item, not a reconstruction of the whole task.

Plan the top remaining item as a substantial, tested vertical slice. Finish all remaining work when it fits one execute session. Re-cut layer-shaped work into vertical slices. The execute draft must describe implementation, public **Seams under test**, verification, and any required test re-cut.

Create `.drafts/step.md` in auto mode with `linear.in-progress`. If work remains, create a normal-mode `plan-next-step` tail on the inherited branch and update it with the reduced remaining list and expanded completed-state summary. Do not set `post-action: linear.done`.

After approval, promote the step, then the tail if present; close this planner; launch the step scoped to `$MAEL_TASK_PARENT`; and end the session:

```bash
mael task promote .drafts/step.md --follow-end '*'
mael task promote .drafts/tail.md --follow <step-id> # if needed
mael task status done
mael task next --run --parent "$MAEL_TASK_PARENT"
mael session end
```

UI approval already promoted drafts. On rejection, delete them. If no work remains after this step, create no tail.

Set the tail model to this planner's model. Its `## Remaining work` removes the planned step and its completed-state summary adds that step. Keep `branch:` unset. Confirm the launched id is the step just promoted; a blocked chain requires `mael task list`, not a different task launch.
