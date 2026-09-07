---
name: planning
description: Draft-file mechanics for planning sessions — write draft task files, sculpt them with the user, promote them into the notebook on approval. Load when a planning skill (plan-task, plan-next-step) or any session builds a task chain interactively.
---

# Planning with draft task files

A planning session's deliverable is **tasks**, not a document. This skill is the mechanics:
write **draft task files**, sculpt them with the user, and promote each into the notebook on
approval. The techniques for *what* to plan (slicing, sizing, chain shape) live in the skill
that loaded this one.

## What a draft is

A draft is a task file **outside the notebook** — plain markdown in the task-file format (YAML
frontmatter + Content/Steps/Log sections), sitting in the worktree's `.drafts/` directory. It is
inert: invisible to `mael task list`, `mael task next`, and follow-end resolution. It becomes a
real task only when `mael task promote` loads it into the store. That structural gap *is* the
approval gate — nothing you draft can run until the user approves promotion.

`.drafts/` is gitignored, so an abandoned draft cannot reach a commit.

## Create drafts early

The moment a task's shape emerges, create its draft — don't hold the plan in conversation:

```bash
mael task draft .drafts/<name>.md "<title>" --mode auto --pre-action linear.in-progress
```

One file per future task, in the worktree's `.drafts/` directory. The command takes the same
recipe flags as `mael task add` (`--command`, `--mode`, `--model`, `--priority`,
`--pre-action`, `--post-action`, `--content-file`, …) and writes a valid task file with the
identity fields (`id`, `project`, `created`, `follows`) empty. It refuses to overwrite an
existing file without `--force`. There are no `--follow`/`--follow-end` flags — chain wiring
happens at promote time, when the ids to follow exist.

## Live preview

The moment each draft exists, write a `<doc-file>` tag in your message. It puts the draft in
front of the user as a document in the orchestrator UI, so the user reads it formatted instead
of as a diff:

```
<doc-file kind="tasks" filename=".drafts/iter1.md" title="Iteration 1">
```

`filename` is a path in this worktree. `kind` is `tasks` for a draft task file. `title` is what
the document is called, and defaults to the filename. Write one tag per draft, once per file.

Add `review="true"` only when you want a verdict on the draft. That raises an item on the user's
desk. A tag without it opens the document to read, and blocks nothing.

## Sculpt

Edit the draft files directly as the plan develops with the user. The `## Content` section is
the plan the execute session receives — write it for that session. The frontmatter is the
recipe (`mode`, `command`, `model`, actions); edit it like any other line.

## Promote or discard

On approval, promote each draft **in dependency order**, wiring the chain as you go:

```bash
mael task promote .drafts/first.md --follow-end '*'   # echoes the new id
mael task promote .drafts/second.md --follow <id-from-first>
```

`promote` creates the task (todo), echoes its id, and **deletes the file** — capture each
echoed id to wire the next `--follow`. Flags override the file's fields, same as
`add --from`. On rejection, delete the draft files — nothing was created.

## End the session when the plan is launched

Planning is finished once the tasks exist and the first one is launched. Run `mael session end`
as the last step, after `mael task next --run` reports the launch.

Run it without asking, and without checking first. The launched task runs in its own session, so
ending this one does not touch it, and an ended session is resumable: `claude --resume` opens it
again with the transcript complete.

This holds for planning that started outside a task as well. Such a session has no task to close,
but it still has no work left once the tasks are created and the first is launched.

## Sizing the chain

**Prefer few, large iterations to many small ones.** Bucket remaining work into roughly three
thematically coherent chunks — backend write plus read surface, traversal plus metrics, frontend plus
e2e — rather than one iteration per subsystem touchpoint.

Each session carries fixed overhead: re-planning, a PR, a CI round-trip. Below roughly 750 production
lines that overhead dominates the work, and a chain of nine one-session slices costs far more than
the three it should have been.

## Discipline

This session plans. It writes draft task files and nothing else: no project source edits, no
branches, no implementation. The execute sessions the promoted tasks launch own the work.
