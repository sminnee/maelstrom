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
mael task promote <draft>…  →  mael task status done  →  mael task next --run
   ▼
Execute session(s) — auto mode, own worktree
```

The planning session writes **draft task files**: one file per future task, in the task-file
format, in the worktree directory. A draft is not in the notebook. It is inert until
`mael task promote` loads it, so approval is structural — nothing you draft can run until you
promote it.

The planning skills open each draft in [`richview`](https://github.com/sminnee/richview) as
soon as they write it, so you read the plan formatted. `richview` live-updates, so later edits
appear without a second command.

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
2. Classifies the work as single-session or multi-session.
3. Creates the draft task files early and opens each in `richview`.
4. Sculpts the drafts with you interactively.
5. Promotes the drafts once you approve them.

The brief is already in the session's opening prompt. You do not paste it.

The planning session **does not implement** — a separate session owns the work. The skill
forbids source edits; the session's only output is the drafts.

## Drafting tasks

`mael task draft` writes a draft file. It takes the same recipe flags as `mael task add`:

```bash
mael task draft draft-iter1.md "Execute: PROJ-123 — add avatar upload" \
    --mode auto --pre-action linear.in-progress
```

The file is a normal task file with the identity fields (`id`, `project`, `created`,
`follows`) empty. Edit it directly — the `## Content` section is the plan the execute session
receives. `draft` refuses to overwrite an existing file unless you pass `--force`, so a
sculpted draft survives a re-run. A hand-written file in the same format works too; the
command is just the path that guarantees a valid one.

Draft files sit untracked in the worktree while you plan. Promotion consumes them; delete any
draft you abandon, so it cannot be swept into a later commit.

## Promoting drafts

`mael task promote` creates the task from a draft, prints the new id, and deletes the file:

```bash
mael task promote draft-iter1.md --follow-end '*'   # prints e.g. linear.PROJ-123.2
mael task promote draft-tail.md --follow linear.PROJ-123.2
```

Chain wiring happens here, not in the draft: `--follow` and `--follow-end` run at promote
time, when the ids they reference exist. Promote in dependency order and feed each printed id
to the next `--follow`. Any recipe flag overrides the file's value, the same way
`add --from` flags override the copied recipe. On an error — missing file, bad frontmatter,
no title — the file is left untouched and no task is created.

## Single-session vs multi-session

**Single-session** — up to about 1500 lines of new code, landing as several ~500-line
commits. One execute draft:

```bash
mael task draft draft-iter.md "Execute: PROJ-123 — add avatar upload" \
    --mode auto --pre-action linear.in-progress
```

Its body carries the full implementation plan: context, steps, seams under test,
verification.

**Multi-session** — larger, or a mechanical transformation that should land first. One
concrete execute draft plus a `plan-next-step` tail carrying the remaining work:

```bash
mael task draft draft-tail.md "Plan next step" \
    --command plan-next-step --mode normal --model opus
```

The tail draft must carry a real remaining-work picture in its body — a `## Remaining work`
list plus a `## What should already be done` summary. An empty placeholder gives the next
planner nothing to work from.

## The rules that make it work

### Leave `branch:` unset

Tasks inherit their parent's branch. That default keeps every iteration on **one branch**,
so `create-pr` appends to the pull request already open on it instead of opening another.
The task then merges **as a whole, once every iteration is complete**.

Setting `branch:` opts a task out into its own branch, worktree and PR. That is for
genuinely unrelated work — not for splitting one task's iterations, which forks a second
worktree and PR mid-task and gives up the accumulating PR.

### Set the mode on every draft

New tasks default to plan mode. An execute draft that omits `--mode auto` would **re-plan**
instead of running its plan.

- `--mode auto` on every execute draft.
- `--mode normal` on a `plan-next-step` tail draft.

### Close the planning task before `task next --run`

```bash
mael linear set-status PROJ-123 planned   # mirror to Linear
mael task promote draft-iter1.md --follow-end '*'
mael task promote draft-tail.md --follow <id from the line above>
mael task status done                     # close this planning task
mael task next --run --parent "$MAEL_TASK_PARENT"   # head now actionable — launches it
mael session end                          # stop this planning session
```

The head promotes with `--follow-end '*'`, so it follows the planning task itself. While the
planning task is `in-progress`, the head is blocked. Closing the planning task satisfies that
dependency, and `mael task next --run` then launches the head. `--parent` scopes the launch
to this chain, so an unrelated actionable task cannot be picked instead. `next --run` prints
the id it launches; when nothing in the chain is actionable it exits non-zero with "No
actionable task."

`mael session end` comes last. The head runs in its own session, so ending the planning session
does not touch it, and an ended session is resumable.

### Chain with `--follow-end` and `--follow`

- `--follow-end '*'` on the **head** promote — append after the current leaf of the parent's
  child-chain, so the plan queues behind work already chained under the issue.
- `--follow <id>` on later promotes — the id printed by the promote before it.

Drafts omit `parent:`; it defaults to `$MAEL_TASK_PARENT` at promote time.

### Set the model on the tail, not on execute drafts

Pin the tail draft to the model the planning session is running, so every planner in the
chain stays on one model. Planning is where the leverage is. Leave `--model` unset on execute
drafts so they inherit your default.

### Lifecycle actions mirror to Linear

- `--pre-action linear.in-progress` on execute drafts — fires when the task launches.
- **Do not set `post-action: linear.done` on execute steps.** The finishing sequence closes
  the task at PR push, before the CI (continuous integration) watch. A post-action would
  therefore flip Linear to Unreleased while CI is still running, overwriting the "In Review"
  that `create-pr` just set. Move
  the issue on deliberately with `mael linear set-status <ID> done` once the work has landed.

## Slice vertically

Each iteration must be a **thin vertical slice**: an end-to-end cut through every layer it
touches, shipping its own tests and delivering behaviour a user can see.

Layer-shaped iterations — "the back-end API", "then the front end", "then the e2e tests" —
are an **antipattern**. A plan whose iterations are named after layers has been sliced the
wrong way. Re-cut it.

Each iteration should:

- Ship its tests with the slice they cover. No test-only iterations.
- Be up to about 1500 lines of new code.
- Be independently testable and pass CI when merged.
- Not break existing behaviour.
- Land on the shared branch.
- Be ordered by dependency.

**Aim for three iterations or fewer.** More than that usually means the slices are too thin
or sliced by layer. If it genuinely needs more, the planner stops and asks whether to split
the issue instead.

The one narrow exception: an enabling refactor that is *genuinely* mechanical and cannot be
folded into the slice that needs it. That does not license splitting feature work by layer.

## Continuing a multi-session chain

```bash
mael task next --run
```

When this reaches a `plan-next-step` task, the `plan-next-step` skill runs. It holds the
running plan-of-record in its prompt — the remaining-work list plus a summary of what should
already be done. It:

1. Reconciles what the summary claims against what the repository shows.
2. Plans **one** concrete next step, biased toward finishing.
3. Re-cuts a layer-shaped tail vertically rather than reproducing it faithfully.
4. Decides whether this step is the last one. If so, it writes no tail draft.
5. Sculpts a fresh execute draft (and tail draft, when work remains) with you, then promotes
   them on approval.

Then the same close-and-advance pair: `mael task status done`, `mael task next --run`.

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
