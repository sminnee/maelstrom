---
name: plan-task
description: Turn a task brief into approved draft tasks, promote them, and launch the head. Runs in a mael planning session. Invoked as `/plan-task`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Plan task

Load `planning` first. Read the brief from the initial prompt. This session plans only; do not implement.

1. **Research.** Read the relevant code and patterns. Use one to three read-only explorers when they help.
2. **Choose the bites.** Split the work into 1–5 plan-mode tasks, each a piece a single session can plan and build. Do not split work only to make the PR small: `/present` re-cuts the commits into one story commit per decision. Keep every task on the inherited branch. Confirm a material split with the user.
3. **Shared design.** When the tasks share design context, write the ADR or design doc that `planning` describes, or make writing it the first task's first bullet.
4. **Draft.** One draft per task, in the shape `planning` gives. Do not set `post-action: linear.done`.
5. **Tail, only when needed.** When it is genuinely unclear what remains after the named tasks, add a normal-mode `plan-next-step` tail draft using this session's model. Its content states what remains and what the named tasks will have done.
6. **Sculpt.** Edit the drafts with the user until they approve.
7. **Promote, close, launch, end.** Set Linear to planned when the brief is Linear-backed. Then:

```bash
mael task promote .drafts/1.md .drafts/2.md   # every draft, in chain order
mael task status done
mael task next --run --parent "$MAEL_TASK_PARENT"
mael session end
```

UI approval already promotes the drafts; use its ids and skip only the promote command. On rejection, delete the drafts.

Do not write a plan body to Linear.
