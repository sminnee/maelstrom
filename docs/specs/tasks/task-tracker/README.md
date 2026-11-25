# Task tracker

The Task tracker holds each task of a project: what the work is, how far it is, and which tasks
it waits on. The terms are in [the Tasks glossary](../CONTEXT.md).

A task starts as `todo`. An agent session moves it to `in-progress`, then to `built` when the
work is pushed. It is Done at `merged`, or at `published` when it has Post-merge work. A task
that is dropped is `cancelled`.

Read the feature files in this order:

| File | What it says |
| --- | --- |
| `task.feature` | What a task can hold. Each command keeps these rules |
| `create-task.feature` | How one task is made, and where each value comes from |
| `load-tasks.feature` | How many tasks are made in one step, as a Chain |
| `update-task.feature` | How the fields of a task change |
| `set-task-status.feature` | How a task moves, and what a Status action does |
| `rename-task.feature`, `delete-task.feature` | What happens to the tasks that name the task |
| `template.feature`, `create-template.feature` | The Recipe that tasks are copied from |
| `task-repository.feature` | The laws of the store. Its reader is an engineer |

The queries, the other Template commands and the Registered PR commands have no feature file
yet: see [ADR 0006](../../../adr/0006-feature-files-hold-the-contracts.md).
