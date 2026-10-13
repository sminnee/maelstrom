# Planning

How a brief becomes a chain of tasks that agents execute.

## The shape

```
Linear issue (or a bare idea)
   │  mael linear plan PROJ-123
   ▼
Planning session — normal mode, in a cmux workspace
   │  research → discuss → sculpt draft task files
   ▼
mael task promote <drafts>  →  mael task status done  →  mael task next --run
   ▼
Task session(s) — plan mode, then build, own worktree
```

The planning session writes **draft task files**: one file per future task, in the task-file
format, in the worktree's `.drafts/` directory. A draft is not in the notebook. It is inert until
`mael task promote` loads it, so approval is structural — nothing you draft can run until you
promote it.

The planning skills show the drafts in the orchestrator UI as soon as they write them, so you
read the plan formatted. The agent does this by writing a marker in its message. Each draft opens
as its own document, and the task's card lists the set under one title. A later edit is the next
version of the same document.

Those documents are also where you approve the plan — see [Approving in the orchestrator
UI](#approving-in-the-orchestrator-ui).

## Starting a plan

From a Linear issue:

```bash
mael linear plan PROJ-123           # creates the task and launches it
mael linear plan PROJ-123 --no-run  # create only
```

`mael linear plan` is a thin wrapper over `mael task add`. It fetches the issue brief, makes
a `plan-task` task holding the brief as content, parents it under `linear.PROJ-123`, and
launches the session. The planning defaults — `plan-task`, normal mode, the `opus` model, the
`linear.<ID>` parent, `linear.planned` post-action — are all overridable with the matching
flag.

Without a Linear issue, the same thing directly:

```bash
mael task add "Rework the port allocator" --run
```

New tasks default to plan mode, so a bare `--run` opens a planning session. Its chain
parents under the planning task's own id.

## Inside the planning session

The `plan-task` skill runs there. It:

1. Researches the codebase with Explore sub-agents.
2. Splits the work into 1–5 plan-mode tasks.
3. Writes a design doc when the tasks share design context.
4. Creates the draft task files early and shows each as a document.
5. Sculpts the drafts with you interactively.
6. Promotes the drafts once you approve them.

The brief is already in the session's opening prompt. You do not paste it.

The planning session **does not implement** — a separate session owns the work. The skill
forbids source edits; the session's output is the drafts and any design doc.

## The shape of a plan

Shaping produces 1–5 **plan-mode tasks**. Each task names one piece of the problem in a few bullets.
The detailed plan is made later, in that task's own plan mode, by the session that builds it.
Most work is clear enough to name up front, but the detail is best decided next to the code.

- **5 is the cap.** When the work is large, make each task broader. A task's plan mode can add
  more tasks later — see [Expanding a chain mid-way](#expanding-a-chain-mid-way).
- **Shared design goes in one place.** When the tasks share design context, the planner writes
  an ADR (architecture decision record) in `docs/adr/` or a design doc. The planner can also
  make writing it the first bullet of the first task. Later tasks link it.
- **A `plan-next-step` tail is the exception.** Add one only when it is genuinely unclear what
  remains after the named tasks.

## Drafting tasks

`mael task draft` writes a draft file. It takes the same recipe flags as `mael task add`:

```bash
mael task draft .drafts/1.md "PROJ-123: add avatar upload" \
    --mode plan --pre-action linear.in-progress
```

The file is a normal task file with the identity fields (`id`, `project`, `created`,
`follows`) empty. Edit it directly. The `## Content` section is the prompt the task's session
receives. For a plan-mode task it is brief bullets:

- the goal;
- what is in scope;
- what is out of scope, and which later task holds it;
- pointers to files and to the design doc.

It holds no implementation steps or test seams. The task's plan mode writes those.

`draft` refuses to overwrite an existing file unless you pass `--force`, so a sculpted draft
survives a re-run. A hand-written file in the same format works too; the command is just the
path that guarantees a valid one.

Draft files sit in `.drafts/` while you plan. `mael` creates the directory and gitignores it, so
a draft never reaches a commit. Promotion consumes each file; delete any draft you abandon.

A planning session edits a draft many times. `mael` adds `Write(.drafts/**)` and
`Edit(.drafts/**)` to `.claude/settings.json`, so those edits raise no permission prompt. It
adds the rules and changes nothing else in the file. A new project carries them from its first
commit; an existing worktree gets them the next time it opens. A rule reaches the next session,
not one already running.

## Promoting drafts

`mael task promote` creates one task per draft, in the order given, and deletes the files. It
prints the new ids, one per line:

```bash
mael task promote .drafts/1.md .drafts/2.md .drafts/3.md
```

Each task follows the one before it. With no follow flag, the set **splices** in after the
current task (`$MAEL_TASK_ID`) — see [Expanding a chain mid-way](#expanding-a-chain-mid-way).
Outside a task session, the first task follows nothing.

`--follow` and `--follow-end` wire the first task instead, and move no other task. Any recipe
flag overrides every file's value, the same way `add --from` flags override the copied recipe.
The set is one notebook write. On an error — missing file, bad frontmatter, no title — every
file is left untouched and no task is created.

### Approving in the orchestrator UI

A cmux session promotes because you told it to in the chat. The orchestrator UI has no chat, so
the drafts' tab does it. Its button reads **Approve and create 3 tasks**, one per draft. Pressing
it on any draft promotes every draft the tag named, in the order it named them. The set splices
in after the planning task, as `mael task promote` does.

The whole set is one notebook write. A draft that will not parse creates nothing, deletes
nothing, and leaves the drafts awaiting review with a message naming the file to fix. On success
the tab lists the ids it created and every draft moves to approved.

The tasks are created, not started. Approving a plan and starting work are two decisions, so
launch the head yourself from its node card or the task list.

To send the plan back instead, type a summary in the field. The button turns into **Decline**,
and the summary reaches the agent as a message. It revises the drafts and tags them again, and
each draft comes back as version 2. A draft the agent leaves out drops off the card.

## The rules that make it work

### Leave `branch:` unset

Tasks inherit their parent's branch. That default keeps every task in the chain on **one
branch**, so `create-pr` appends to the pull request already open on it instead of opening
another. The chain then merges **as a whole, once every task is complete**.

Setting `branch:` opts a task out into its own branch, worktree and PR. That is for
genuinely unrelated work — not for splitting one chain, which forks a second worktree and PR
mid-chain and gives up the accumulating PR.

### Set the mode on every draft

- `--mode plan` on each task. The session opens in plan mode and writes the detailed plan for
  you to approve.
- `--mode normal` on a `plan-next-step` tail draft.

### Close the planning task before `task next --run`

```bash
mael linear set-status PROJ-123 planned   # mirror to Linear
mael task promote .drafts/1.md .drafts/2.md
mael task status done                     # close this planning task
mael task next --run --parent "$MAEL_TASK_PARENT"   # head now actionable — launches it
mael session end                          # stop this planning session
```

The head follows the planning task itself. While the planning task is `in-progress`, the head
is blocked. Closing the planning task satisfies that dependency, and `mael task next --run`
then launches the head. `--parent` scopes the launch to this chain, so an unrelated actionable
task cannot be picked instead. `next --run` prints the id it launches; when nothing in the
chain is actionable it exits non-zero with "No actionable task."

`mael session end` comes last. The head runs in its own session, so ending the planning session
does not touch it, and an ended session is resumable.

Drafts omit `parent:`; it defaults to `$MAEL_TASK_PARENT` at promote time.

### Set the model on the tail, and the execute model on tasks

Pin a tail draft to the model the planning session is running, so every planner in the chain
stays on one model. Leave `--model` unset on task drafts: an unset model launches on `opus`.
Set `--execute-model` on a task draft to plan on `opus` and build on the model named there.

### Lifecycle actions mirror to Linear

- `--pre-action linear.in-progress` on task drafts — fires when the task launches.
- **Do not set `post-action: linear.done` on tasks.** The finishing sequence closes the task at
  PR push, before the CI (continuous integration) watch. A post-action would therefore flip
  Linear to Unreleased while CI is still running, overwriting the "In Review" that `create-pr`
  just set. Move the issue on deliberately with `mael linear set-status <ID> done` once the work
  has landed.

## Slice vertically

Each task must be a **vertical slice**: an end-to-end cut through every layer it touches,
shipping its own tests and delivering behaviour a user can see.

A slice can be a substantial piece of work. Do not split work only to make the PR small.
`/present` re-cuts the build commits into one story commit per design decision, so a reviewer
can read a large PR.

Layer-shaped tasks — "the back-end API", "then the front end", "then the e2e tests" — are an
**antipattern**. A plan whose tasks are named after layers has been sliced the wrong way.
Re-cut it.

Each task should:

- Ship its tests with the slice they cover. No test-only tasks.
- Fit one session.
- Be independently testable and pass CI when merged.
- Not break existing behaviour.
- Land on the shared branch.
- Be ordered by dependency.

The one narrow exception: an enabling refactor that is *genuinely* mechanical and cannot be
folded into the slice that needs it. That does not license splitting feature work by layer.

## Continuing a chain

```bash
mael task next --run
```

Each task in the chain opens in plan mode. Its session reads the bullets and the design doc,
writes the detailed plan, and builds it once you approve.

When this reaches a `plan-next-step` tail, the `plan-next-step` skill runs. It:

1. Reconciles what the tail says remains against what the repository shows.
2. Drafts 1–3 plan-mode tasks for the remaining work.
3. Adds another tail only when it is still unclear what remains.
4. Sculpts the drafts with you, then promotes them on approval.

Then the same close-and-advance pair: `mael task status done`, `mael task next --run`.

## Expanding a chain mid-way

A task's plan mode can find more work than one session holds. The session then plans its own
part, drafts the rest as plan-mode tasks, and promotes them with no follow flag. The new tasks
**splice** in after it, ahead of the tasks that already followed it:

```
before:  A → P → C
after:   A → P → N1 → N2 → C
```

`C` now waits for `N2`, so the chain keeps its order and no task forks off its end.

## Auditing an existing project

`/review-project-hygiene` is a second way into a chain. It does not start from a brief. It
reads a project that already exists, usually after its first minimum viable product (MVP).
The skill turns the gaps it finds into a batch of tasks, written as a
[`load-many`](tasks.md) plan file — the bulk form suited to many similar blocks.

```bash
mael task add "Hygiene audit: forecastel" --run
```

Then run `/review-project-hygiene` in the planning session.

The skill audits tooling, CI/CD, tests and specs, docs, agent config, security and
dependencies, dead code, and architecture fences. It looks
hardest for **gates that report success but cannot fail**. A CI step named "Lint" that runs
the write variant of a formatter. A deploy that never checks whether the build passed. A
dead-code config no job invokes. An absent gate is visible. A broken one is not.

Two things it deliberately does not do:

- **It never edits the project.** Every agreed fix becomes a task. The audit reads and
  reports; the chain it emits does the work.
- **It checks that code conventions are *documented*, not whether the code follows them.**
  Reviewing code against a standard is [`/code-review`](pull-requests.md)'s job.

### The table checkpoint

The audit stops in the middle and shows you a table — one row per check, with its state, a
recommendation, and an S/M/L effort:

| Check | State | Recommendation | Effort |
|---|---|---|---|
| Lint gate | ✗ runs `npm run format` (writes, always exits 0) | switch to `format:check` | S |
| Dead code | ✗ none configured | add knip + CI gate | M |
| Arch fence | n/a — 2 crates, below threshold | none | — |

**The plan file is written after you confirm this table, from the confirmed rows only.** A row
you decline is dropped. Nothing that was not in the table reaches the plan.

The `n/a` rows are there on purpose. Showing that a check was considered and declined is what
makes the table reviewable, and it surfaces a wrong threshold before it becomes a task. If the
audit declines a check you wanted, or recommends one that makes no sense for the project, say
so — that is a checklist bug worth fixing.

There are two review points, not one: the table checkpoint agrees the scope, and ExitPlanMode
then approves the chain that implements it.

The confirmed rows become one execute block per theme, chained with `follow`, with `branch:`
unset — so the whole audit lands as one pull request.

## Linear stays a mirror

The plan of record lives in the notebook chain, not in a Linear description. The planning
skill mirrors *status* with `set-status … planned`, but never writes the plan body back to
Linear.

`mael linear write-plan` / `read-plan` / `edit-plan` are there for when you do want a plan
in the issue description.

## See also

- [Tasks](tasks.md) — parent, follows, drafts, load-many.
- [Pull requests](pull-requests.md) — how an execute session finishes.
