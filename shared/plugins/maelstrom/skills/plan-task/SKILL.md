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
2. **Choose the bites.** Split the work into plan-mode tasks within the cap in `planning`'s task-chains.md, each a piece a single session can plan and build. Do not split work only to make the PR small: `/present`, where available, re-cuts the commits into one story commit per decision. Confirm a material split with the user.
3. **Shared design.** When the tasks share design context, handle it as `planning`'s task-chains.md says.
4. **Draft.** One draft per task, as `planning`'s task-chains.md says.
5. **Tail, only when needed.** When it is genuinely unclear what remains after the named tasks, add a normal-mode `plan-next-step` tail draft using this session's model. Its content states what remains and what the named tasks will have done.
6. **Sculpt.** Edit the drafts with the user until they approve.
7. **Promote, close, launch, end.** Set Linear to planned when the brief is Linear-backed. Then promote, close, launch, and end as `planning`'s task-chains.md says.

Do not write a plan body to Linear.
