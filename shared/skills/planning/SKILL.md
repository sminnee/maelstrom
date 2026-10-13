---
name: planning
description: Draft task files for planning sessions. Use when a session builds a task chain with the user.
---

# Planning with drafts

A draft is an inert task file in `.drafts/`. It becomes a task only through `mael task promote`.

## The shape of a plan

A plan is 1–5 plan-mode tasks. Each task is one piece of the problem, and its own plan mode writes the detailed plan.

- 5 is the cap. When the work is large, make each task broader. A task's plan mode can add more tasks later.
- Make each task a vertical slice: it ships its own tests and behaviour a user can see. Re-cut a layer-shaped or test-only split.
- When the tasks share design context, put it in an ADR (`docs/adr/`, format in `domain-modeling/ADR-FORMAT.md`) or a design doc. Write it in this session, or make writing it the first bullet of the first task. Later tasks link it.

## Drafts

Create each draft as soon as its shape is clear:

```bash
mael task draft .drafts/<n>.md "<title>" --mode plan --pre-action linear.in-progress
```

To build on a different model than the one that plans, give the draft `--execute-model <alias>`. Confirm the alias with the user. Use Claude aliases only.

`## Content` is brief bullets:

- Goal.
- In scope.
- Out of scope, and which later task holds it.
- Pointers: files, the ADR or design doc.

Leave out implementation steps and seams. The task's plan mode writes those.

Present the complete chain as soon as drafts exist: list every draft in one `<doc-file kind="tasks">` tag, in chain order. Present the same tag again after each edit. Add `review="true"` once the chain is ready for approval.

Edit drafts with the user. Planning changes drafts only; build sessions own source changes.

## Promote

On chat approval, promote the whole set with one call, in chain order, with no follow flags:

```bash
mael task promote .drafts/1.md .drafts/2.md .drafts/3.md
```

The set splices in after the current task.

On UI approval, the UI already promoted the set the same way and deleted the drafts. Use the reported ids. On rejection, delete the drafts.

When this is a planning session, after promotion close the planning task, launch the head with `mael task next --run`, then run `mael session end`. The head follows the planning task, so close the planner before launching it. A planning session ends only after the first task launches. Do not end it merely because the drafts exist.
