# Task chains

A draft is an inert task file in `.drafts/`. It becomes a task only through `mael task promote`.

## The shape of a chain

A chain is 1–5 plan-mode tasks. Each task is one piece of the problem, and its own plan mode writes the detailed plan.

- 5 is the cap. When the work is large, make each task broader. A task's plan mode can add more tasks later.
- Make each task a vertical slice: it ships its own tests and behaviour a user can see. Re-cut a layer-shaped or test-only split.
- When the tasks share design context, put it in an ADR (`docs/adr/`, format in `domain-modeling/ADR-FORMAT.md` if that skill is available) or a design doc. Write it in this session, or make writing it the first bullet of the first task. Later tasks link it.

## Drafts

Create each draft as soon as its shape is clear:

```bash
mael task draft .drafts/<n>.md "<title>" --mode plan --pre-action linear.in-progress
```

Keep `branch:` unset: every task inherits its parent's branch. Do not set `post-action: linear.done`: the finishing sequence closes the task.

To build on a different model than the one that plans, give the draft `--execute-model <alias>`. Confirm the alias with the user. Use Claude aliases only.

`## Content` is brief bullets:

- Goal, first: what will be different when the task is done.
- In scope.
- Out of scope, and which later task holds it.
- Pointers: files, the ADR or design doc.

Leave out implementation steps and the test plan. The task's plan mode writes those.

Present the complete chain as soon as drafts exist: list every draft in one `<doc-file kind="tasks">` tag, in chain order. Present the same tag again after each edit. Add `review="true"` once the chain is ready for approval.

Edit drafts with the user. Planning changes drafts only; build sessions own source changes.

## Promote

On chat approval, promote the whole set with one call, in chain order, with no follow flags:

```bash
mael task promote .drafts/1.md .drafts/2.md .drafts/3.md
```

The set splices in after the current task.

On UI approval, the UI already promoted the set the same way and deleted the drafts. Use the reported ids and skip the promote command. On rejection, delete the drafts.

## Close, launch, end

When this is a planning session, after promotion close the planning task, launch the head, then end the session:

```bash
mael task status done
mael task next --run --parent "$MAEL_TASK_PARENT"
```

When the system prompt teaches `<end-session>`, write it and end your turn.

The head follows the planning task, so close the planner before launching it. A planning session ends only after the first task launches. Do not end it merely because the drafts exist.

## Splice from a plan-mode task

When a plan-mode task's plan finds more than one session's work, the plan names the extra tasks. Plan approval approves them. After it, draft them as plan-mode tasks and promote them in one call, in chain order, with no follow flags. Do not present them for a second approval. Then build this session's part. They splice in after this task. Skip "Close, launch, end".

## Cross-project work

When the work touches another project, do not diagnose, plan, or build that project's part. Create one plan-mode task in each other project and launch it. The agent in that project diagnoses and writes the detailed plan. This project's own part stays a normal chain, or stays in this plan.

Approval:

- In a planning session, list the cross-project tasks in chat, after the `<doc-file kind="tasks">` tag. Chat or UI approval of the chain approves them. On rejection, create none. Create them after the promote and before "Close, launch, end".
- In a plan-mode task, the plan names them. Plan approval approves them. Do not present them for a second approval.

Content is the same brief bullets as a draft's `## Content`, plus the contract between the projects: the interface, the data shape, the order. Write this project's task as a qualified id (`<project>/<id>`).

Create and launch each task with one command. `mael project list` gives the project names.

```bash
mael task add -p <project> "<title>" --mode plan --content-file - --run <<'EOF'
- Goal: ...
EOF
```

When the other project has its own Linear issue, add `-P linear.<ID> --pre-action linear.in-progress`. Without a Linear parent, the pre-action does nothing.

Do not draft, promote, or follow across projects. When one project needs the other's change first (an API, a schema, a release), say so in the content of both tasks.
