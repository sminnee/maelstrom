---
name: plan-task
description: Turn a task brief into approved draft tasks, promote them, and launch the head. Runs in a mael planning session. Invoked as `/plan-task`.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Plan task

Load `planning` first. Read the brief from the initial prompt. Research the relevant code and patterns before drafting. This session plans only; do not implement.

Classify work as one execute session (about 1,500 new lines or less) or a short chain. Confirm a material classification choice with the user. Make each execute step a tested, independently mergeable vertical slice. Keep all steps on the inherited branch. Do not set `post-action: linear.done`.

Research before drafting. Use one to three read-only explorers when they help. A slice includes its tests and public behaviour; never make layer-only or test-only iterations. Name the public seams and any test re-cut in each execute draft. Ask before a chain longer than three slices.

Create an execute draft early:

```bash
mael task draft .drafts/iter1.md "Execute: <ID> — <description>" --mode auto --pre-action linear.in-progress
```

For remaining work, add a normal-mode `plan-next-step` tail draft using this session's model. Each execute draft must name context, implementation steps, files, public **Seams under test**, and verification. A tail states what remains and what should already be done.

Sculpt the drafts with the user. On approval, set Linear to planned when applicable, promote drafts in order, close this planning task, launch the head scoped to `$MAEL_TASK_PARENT`, then end the session:

```bash
mael task promote .drafts/iter1.md --follow-end '*'
mael task promote .drafts/tail.md --follow <iter1-id> # if needed
mael task status done
mael task next --run --parent "$MAEL_TASK_PARENT"
mael session end
```

UI approval already promotes drafts; use its ids and skip only the promotion commands. On rejection, delete drafts. Aim for three or fewer slices; ask before creating a longer chain.

The `## Content` body is the execute session's plan. A multi-session head also states the overall goal and architecture. Its tail must state remaining work and the completed-state summary; it is not a placeholder. Set Linear to planned before promotion when the brief is Linear-backed. Do not write a plan body to Linear.
