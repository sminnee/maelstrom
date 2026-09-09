---
name: planning
description: Draft task files for planning sessions. Use when a session builds a task chain with the user.
---

# Planning with drafts

A draft is an inert task file in `.drafts/`. It becomes a task only through `mael task promote`. Create each draft as soon as its shape is clear:

```bash
mael task draft .drafts/<name>.md "<title>" --mode auto --pre-action linear.in-progress
```

Use one file per future task. Put its execution plan in `## Content`; keep recipe fields in frontmatter. Show the complete chain, in order, as soon as drafts exist:

```
<doc-file kind="tasks" filename=".drafts/first.md, .drafts/next.md" title="Plan" review="true">
```

Edit drafts with the user. Planning changes drafts only; execute sessions own source changes.

`promote` deletes a draft and echoes its id; wire later drafts from that id. Drafts have no follow flags because identities do not exist until promotion. The UI document needs `kind="tasks"`, files in chain order, and `review="true"` when it is ready for approval.

On chat approval, promote in dependency order. Capture each returned id:

```bash
mael task promote .drafts/first.md --follow-end '*'
mael task promote .drafts/next.md --follow <first-id>
```

On UI approval, the UI already promoted and deleted the drafts. Use the reported ids. On rejection, delete drafts.

After promotion, close the planning task, launch the head with `mael task next --run`, then run `mael session end`. Prefer a few coherent iterations over many small ones.

The head follows the planning task, so close the planner before launching it. A planning session ends only after the first task launches. Do not end it merely because the drafts exist.
