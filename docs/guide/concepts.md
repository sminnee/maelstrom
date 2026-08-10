# Concepts

## What maelstrom is for

Maelstrom is an orchestration layer for multi-agent development. It uses cmux to manage
workspaces, git worktrees to isolate code, and Claude Code as its agent. Integrations with
Linear, Sentry and GitHub keep the workflow streamlined. It adds two things of its own: a
task notebook that tracks what each agent is doing and in what order, and a dev environment
manager that gives each worktree isolated services and ports. It is a highly opinionated
Swiss-army knife.

## The problem

One agent working on one branch is easy. You open a terminal, start Claude Code, and watch.

Several agents at once is not. Each agent needs its own checkout, or they overwrite each
other. Each needs its own database and its own ports, or their dev servers collide. You
need to know which agent is doing what, and in what order, because some work must wait for
other work. You need somewhere to watch them all. And when an agent finishes, someone has
to review the code, open the pull request and take CI to green.

Doing that by hand does not scale past about two agents. Maelstrom automates it.

## The pieces

Each component earns its place by the role it plays in the loop.

| Component | Role |
|---|---|
| [cmux](cmux-workspaces.md) | Manages workspaces — where sessions run and where you watch them |
| [git worktrees](worktrees.md) | Isolate code — a branch and a checkout per unit of work |
| Claude Code | The agent that does the work |
| [Task notebook](tasks.md) | Detailed task management — what each agent is doing, in what order |
| [Dev environments](dev-environments.md) | Isolated services and ports per worktree |
| [Linear / Sentry / GitHub](integrations.md) | Streamline the workflow |

### cmux — where sessions run

cmux is a workspace manager. Maelstrom gives each worktree a workspace named
`<project>-<worktree>`, with three panes:

- **Pane 0** — the Claude session.
- **Pane 1** — a shell, which runs the install command on creation.
- **Pane 2** — browsers: the running app and the pull request.

Sessions run in cmux workspaces. Maelstrom starts a session by driving the cmux socket. If
cmux is down, maelstrom starts it. If cmux cannot be reached, maelstrom fails rather than
running the agent somewhere you cannot see it. The one exception is `--here`, which runs
the agent in your current shell — a deliberate escape hatch, not a fallback.

This matters because you cannot supervise what you cannot find. With one workspace per
worktree, every agent is always in a known place.

### git worktrees — isolating code

A worktree is a second checkout of the same repository on its own branch. Maelstrom names
them from the NATO phonetic alphabet — alpha, bravo, charlie — rather than after branches,
because a worktree outlives the branch it currently holds.

Names are stable and reusable. When you finish work, `mael close` resets the worktree to
main but keeps the folder, the name and the port allocation. The next `mael add` recycles
it. So `myproject-bravo` is a durable slot, and its ports never change.

The **project name is load-bearing** too: the worktree folders, task directories, port
allocations and each task's Claude session id all derive from it. `mael mv-project` is the
only safe way to change it — see [troubleshooting](troubleshooting.md#renaming-a-project).

### Claude Code — the agent

Maelstrom launches `claude` with the right working directory, the right permission mode,
a deterministic session id, and the task's content piped in as the opening prompt. A task's
`mode` decides how the session behaves:

- `plan` — the session plans and asks before acting. This is the default for a new task.
- `auto` — an unattended execute session that runs its plan without prompting.
- `normal` — an execute session that prompts on each action.

A task's `command` selects which skill runs, if any. An execute task runs no skill: its
plan *is* its content, and it implements it directly.

### The task notebook — what each agent is doing

The notebook is a git-backed set of markdown files. Each task is one file, and **its status
is the folder it sits in** — `todo/`, `in-progress/`, `blocked/`, `done/`, `cancelled/` or
`template/`.

Two of the six statuses park a task rather than track its progress. A `template/` task is a
recipe to duplicate from. A `blocked/` task is one you parked by hand. Maelstrom never launches
either, whatever their `follows` say.

Three ideas describe how tasks relate, and they are separable. This is the single most
important idea in the system:

- **`parent` groups a chain that shares one branch and one pull request.** One PR per
  parent. Siblings under a parent run in order and merge together.
- **`follows` orders execution.** A task becomes actionable only once everything it follows
  is done.
- **Dots in an id express lineage.** `PROJ-12.3` is a child of `PROJ-12`;
  `maintenance.2026-07-02` is a scheduled run of the `maintenance` template.

They are separable on purpose. A scheduled run is named as a child of its template through
its dot-id, yet has an empty `parent`, so each firing roots its own chain instead of piling
onto the template's.

Read [tasks.md](tasks.md) for the detail.

### The wiki — cross-project patterns

The wiki is a curated set of markdown pages for design patterns that apply to more than one
project: which linting tool to use, how to publish a package, how to set up a new service.

It fills a gap that the other two knowledge stores leave open. Claude's memory is
per-project — maelstrom symlinks each worktree's memory directory to a shared one at the
project level, so knowledge is unified across the worktrees of one project but not across
projects. A repo's own `docs/` is scoped to that repo. Neither can hold a pattern that
belongs to no single project.

Pages live in the same git-backed store as the task notebook, under a reserved prefix, so
every change is committed and can be rolled back. The store is local: there is no remote
sync in this version.

A page is markdown with a one-line `description:` in its frontmatter. `mael wiki list`
prints the path and the description of every page, which is how an agent finds the right
page without reading all of them. `mael wiki read` prints a page, and `mael wiki update`
replaces one and commits it.

Page paths are free-form. The convention is `dev-patterns/<language-or-area>/<topic>`, for
example `dev-patterns/python/pypi-publication`. Maelstrom does not enforce it.

Agents are told to consult the wiki before they solve a cross-project problem, and to
correct the page they used if it turns out to be wrong. See
[cli.md](../reference/cli.md) for the commands.

### Dev environments — isolated services

Each worktree gets a `PORT_BASE` in the range 300-999. Service ports are
`PORT_BASE * 10 + index`, so bravo's frontend and charlie's frontend never collide. Declare
services in `.maelstrom.yaml` and maelstrom starts them, tracks their PIDs, collects their
logs, and stops them again. Containers are supported through Docker or Apple `container`.

Some services should not be duplicated — a database is the usual case. Mark them
`shared: true` and the project starts one copy that every worktree subscribes to.

### Linear, Sentry, GitHub — streamlining

These are not peers of the components above. They remove manual steps:

- **Linear** is the product-level mirror. Read briefs, mirror status. The plan of record
  lives in the notebook, not in a Linear description.
- **GitHub** is where pull requests land and CI runs.
- **Sentry** turns production errors into work.

## How a piece of work flows

```
Linear issue
   │  mael linear plan PROJ-123
   ▼
Planning session (plan mode, in a cmux workspace)
   │  writes a plan file whose blocks ARE the chain
   ▼
mael task status done  →  mael task load-many <plan> --run
   │
   ▼
Execute session (auto mode, own worktree, own ports)
   │  implement → commit → /code-review → fixups
   ▼
mael gh create-pr PROJ-123 --squash   →   mael task status done   →   /watch-pr
   │
   ▼
One pull request per parent
```

Several of these run at once, in different worktrees, in different workspaces. That is the
point. [multi-agent-workflow.md](multi-agent-workflow.md) walks the loop with several
agents in flight.

## Ideas worth internalising

- **One PR per parent.** `parent` groups; `follows` orders; dot-ids express lineage.
- **Leave `branch:` unset in plan blocks.** Tasks inherit their parent's branch, so every
  iteration accumulates into one pull request.
- **Plans become tasks.** The plan file *is* the chain.
- **Sessions run in cmux workspaces.** `--here` is the local escape hatch.
- **Close preserves, remove deletes.** `mael close --force` discards nothing.
- **Thin vertical slices.** Never layer-shaped iterations.
- **The pull request is the completion signal.** Close the task as soon as it is pushed.

## Next

- [Getting started](getting-started.md) — from nothing to a running agent session.
- [The multi-agent workflow](multi-agent-workflow.md) — the core loop.
