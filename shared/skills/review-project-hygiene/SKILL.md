---
name: review-project-hygiene
description: Audit an existing project against a hygiene checklist — CI gates that can actually fail, pinned runtimes, dead-code detection, spec-to-test mapping, a truthful README, and agent config. Invoked as the `/review-project-hygiene` slash command. Runs read-only sub-agents per category, presents a table of state and recommendations for you to confirm, then writes a load-many plan that fixes the agreed rows. It never edits the project itself.
---

# Review Project Hygiene

Universal project-hygiene audit for maelstrom projects. It reads an existing project — usually
after its first MVP — and reports where the project misses what a maintained project needs. The
parent agent (this file) drives the workflow; one read-only sub-agent per category does the audit
against a shared checklist.

**The audit never edits the project under audit.** The one file it writes is the plan file in
step 6. Every agreed fix becomes a task in that plan. The audit reads and reports; the chain it
emits does the work.

## The goal

**Find the gates that report success but cannot fail.**

A missing gate is visible. Somebody notices there is no lint step. A *broken* gate is invisible:
the CI badge is green, the step name says "Lint", and the command is `npm run format`, which
writes files and always exits 0. The project believes it is covered. This class of failure is the
most valuable thing the audit finds, and it is the reason the audit opens files instead of
listing them.

Two related failures round it out:

- **Silent decay.** A README that names a different project, a config file for a tool the project
  dropped two migrations ago, a runtime pinned only inside a workflow file and now years past end
  of support. Nothing fails, so nothing tells you.
- **A test suite nobody can evaluate.** Tests exist, they pass, and no one can say what they
  cover or what was never built, because nothing maps them to a written spec.

The audit exists to make these visible, then to turn the agreed ones into work.

## What this skill does not do

**It checks that code conventions are *documented*, not whether the code follows them.** This is
the meta layer: does the project have a `docs/review/coding-standards.md`? Is there a
`.claude/review-guides/<language>.md`? Reviewing code *against* those standards is
`/code-review`'s job. Do not audit code quality here.

## Parent-agent section (runs on `/review-project-hygiene`)

This is what runs when the user types `/review-project-hygiene`. Follow these steps in order.

### 1. Ensure plan mode

Look for `Plan mode is active` in the system-reminder tags. If it is absent, call
`EnterPlanMode` and continue. Do not fail; just enter the mode.

Plan mode is what holds the read-only contract. The audit writes one plan file at the end and
nothing else.

### 2. Detect the project

Build a short **project profile**. Read only what you need to answer these:

- **Languages and package managers.** Look for `pyproject.toml`, `package.json`,
  `Cargo.toml`, `go.mod`, and the matching lockfiles.
- **Repository shape.** Single package, workspace, or monorepo. Count the packages or crates.
- **Kind of project.** A deployed web service, a library, a CLI tool, a desktop app, or a static
  site. Look for `Dockerfile`, `Procfile`, `k8s/`, or a deploy workflow.
- **Whether it is a maelstrom project.** Look for `.maelstrom.yaml`.
- **Size.** Roughly how many source files, and how many test files.

The profile selects which checklist categories apply. A desktop game needs no deploy gate. A
library needs no `Procfile`. A two-crate workspace is below the architecture-fence threshold.
Record what you decided and why — step 5 shows the declined checks to the user.

`$ARGUMENTS` may name a project directory. If it is empty, audit the current working directory.

### 3. Spawn one auditor sub-agent per applicable category

Read the auditor prompt from `auditor-prompt.md` (alongside this file, at
`~/.claude/skills/review-project-hygiene/auditor-prompt.md`).

Read the checklist from `hygiene-checklist.md` (at
`~/.claude/skills/review-project-hygiene/hygiene-checklist.md`).

Spawn **one sub-agent per applicable category**, all in a single message so they run
concurrently. Use the Task tool with `subagent_type: "Explore"` — read-only, which matches the
brief: no edits, no test runs, no builds.

The checklist's `## Category:` headings are the canonical category list. Use those names
throughout — in the sub-agent prompts, in the merged report, and in the table.

Each sub-agent's prompt is:

1. The contents of `auditor-prompt.md`.
2. That category's section of `hygiene-checklist.md`, verbatim.
3. The project profile from step 2.

Keep file contents out of your own context. The sub-agents read the project's files themselves
and report what they found.

### 4. Merge the findings

Combine the sub-agent reports into one set, grouped by category. Where a category returned
nothing, collapse it to a single line: `No findings: <category>, <category>`.

If two sub-agents report the same underlying problem, keep one copy.

### 5. Present the hygiene table, and stop

**This is a hard gate. Do not write the plan file until the user has confirmed the table.**

Emit one row per check that produced a finding, a recommendation, or a considered decline.
Group the rows by category:

| Check | State | Recommendation | Effort |
|---|---|---|---|
| Lint gate | ✗ runs `npm run format` (writes, always exits 0) | switch to `format:check` | S |
| Deploy gating | ✗ no `conclusion == 'success'` guard | add the guard | S |
| Dead code | ✗ none configured | add knip + CI gate | M |
| Spec↔test map | — no specs; retrofit is real work | discuss | L |
| Arch fence | n/a — 2 crates, below threshold | none | — |

**States** are `✓` present and working, `✗` missing or broken, and `n/a` with the reason.

**Include the `n/a` rows.** Showing that a check was considered and declined is what makes the
table reviewable. It also surfaces a wrong threshold before it becomes a task — if the checklist
declined a check the user wanted, or recommended one that makes no sense for this project, the
table is where that shows up.

**Effort** is S, M or L, taken from the sub-agent that read the files. This is what the user
triages on. It is the same "what does the fix cost" axis that `/code-review` sorts by.

**Passing checks do not get a row.** They would fill the table with work that is already done.
List them below the table as one line — `Passing: <check>, <check>, …` — in the same way step 4
collapses a clean category. A check that passes but is worth a remark still gets a `✓` row.

Then use `AskUserQuestion` to agree which rows become work. Each row is fix now, defer, or
does-not-apply.

**A row the user declines is dropped.** If a row was declined because the checklist is wrong for
this class of project, say so plainly. That is a checklist bug, and it is worth knowing about.

### 6. Write the load-many plan file

Write the plan from the **confirmed rows only**. Nothing that was not in the confirmed table may
appear in the plan.

One execute block per theme, chained. The head block takes `follow-end: "*"`; each later block
takes `follow: <previous block name>`. Set `mode: auto` on every block. Leave `branch:` unset so
the chain accumulates into one pull request. Blocks omit `parent:`.

Each block needs a `title:`. The marker must sit on its own line, with the frontmatter keys on
the lines below it:

```markdown
---CREATE TASK gates---
title: "Hygiene: make the CI gates able to fail"
mode: auto
follow-end: "*"
---
# Gates

## Context
<the confirmed rows for this theme, with what each costs>

## Implementation steps
...

## Verification
...

---CREATE TASK security---
title: "Hygiene: rotate the committed credentials"
mode: auto
follow: gates
---
# Security
...
```

Add `pre-action: linear.in-progress` only if a Linear issue is in play. An ad-hoc audit launched
with `mael task add` self-parents and has no Linear issue, so it gets no pre-action.

The block format and the rules behind it are documented in `docs/guide/planning.md`. Read it
rather than guessing the syntax.

### 7. Exit plan mode

Present the plan with `ExitPlanMode`, with
`allowedPrompts: [{"tool": "Bash", "prompt": "mael task load-many"}, {"tool": "Bash", "prompt": "mael task status done"}]`.

The plan file you wrote *is* the chain. Approving it runs the hand-off commands in step 8.

### 8. Hand off

After approval, run these two commands **in this order**:

```bash
mael task status done
mael task load-many <plan-file> --run
```

Close this task first. The head block's `follow-end: "*"` makes it follow this task, so while
this task is `in-progress` the head is blocked and `--run` launches nothing — silently, exiting
0.

**Do not implement the fixes.** The chain does that.

## Do NOT bake project-specific rules into this skill

This is the universal hygiene skill. The checklist is cross-project. A rule that is true of only
one project belongs in that project — in its `CLAUDE.md`, its `docs/review/coding-standards.md`,
or its own `.claude/skills/`.

The following are examples of things that **must not** appear in this file,
`hygiene-checklist.md`, or `auditor-prompt.md`:

- Exact tool settings: a ruff `select` list, a line length, a formatter's quote style, a pyright
  `report*` escalation list. A project may reasonably differ, and a checklist that fails them
  produces noise on every audit.
- Code conventions: file naming, path aliases, barrel exports, component style, docstring style.
  That is `docs/review/coding-standards.md`'s job, and `/code-review` enforces it. The audit
  checks such a document *exists*; it does not duplicate its contents.
- NZ English or any other locale rule applied to the project under audit.
- A named project's directory layout, service names, or deployment target.
- Severity tiers. Rows are not ranked blocking/advisory — step 5 triages them with the user by
  what the fix costs.

Also out of scope:

- Editing the project under audit. The audit only ever writes its own plan file.
- Scaffolding a new project. This skill audits what exists.
- Reviewing code against standards. That is `/code-review`.
