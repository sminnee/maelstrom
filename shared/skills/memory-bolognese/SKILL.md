---
name: memory-bolognese
description: Boil a Claude memory store down into repo docs, skills and context. Invoked as the `/memory-bolognese` slash command.
disable-model-invocation: true
---

# Memory bolognese

A memory store is a stockpot. Facts about the codebase go in and never reduce, until the index
outgrows its read limit and the store carries more stale claims than live ones. **Bolognese** is
the reduction: verify every entry, throw most away, and boil the survivors into the repo — where
they reach every contributor and every session, not one user's private store.

The user runs this by hand, when the index nears its read limit or as a periodic sweep.

The store is not the enemy. It is the wrong home for a fact about *the code*. It is the right home
for a fact about *the user*.

## The four verdicts

Every entry gets exactly one.

- **DELETE** — the repo already states it, or the claim no longer holds. Cite `file:line` for the
  repo statement, or the code that contradicts it.
- **MIGRATE** — durable, undocumented, and about the codebase. Names a specific destination file
  and a one- or two-line boiled-down form.
- **KEEP** — genuinely about this user: their preferences, their working style in this moment,
  their standing corrections. Requires an argument for why it is not a codebase fact.
- **FIX-IN-REPO** — the entry describes a repo defect. A memory that says "command X in the README
  is wrong, use Y" is a bug report wearing a memory's clothes. Fix the README; do not document the
  workaround.

Expect **DELETE to be the majority**. A store of a few hundred entries built over months is mostly
sediment: facts the repo has since documented, migrations that finished, bugs that shipped a fix.

## Steps

### 1. Verify before routing

**Check every entry against current code. Never accept an entry on its own word.**

A memory is a claim made on one day about a codebase that has moved since. A migration target for
a fact that is no longer true is worse than no entry at all — it launders a stale claim into the
repo's documentation, where the next reader trusts it.

For each entry, find the evidence:

- The repo file that already states it → DELETE, cite `file:line`.
- The code that contradicts it → DELETE, cite the contradiction.
- Nothing, and the behaviour still holds → MIGRATE or KEEP.

Fan this out. Verification parallelises cleanly: give each subagent one batch of entries, grouped
by index section so the batch is topically coherent, and have it return one row per entry:

```
<name> | <type> | DELETE|MIGRATE|KEEP|FIX-IN-REPO | <destination or reason> | <evidence file:line>
```

Land the combined table in a scratch file in the repo and **stop for review**. Routing gets agreed
before any destination file is edited.

### 2. Delete first

One commit. Remove every DELETE entry from the store together with its index line.

Deleting first is the cheap half and needs no repo edits, so it lands before any migration work and
the index can be re-measured against the real remainder.

### 3. Migrate, batched by destination

**One commit per destination file.** Migration does not parallelise the way verification does — a
scatter of one-line edits across twenty files is twenty incoherent commits. Batch by destination so
each commit is one coherent documentation change: all the database facts into the database skill,
all the CI facts into the CI section.

**Boil hard on the way in.** Entries run to dozens of lines of narrative — the incident, the
session, the reasoning. A documented line is one or two. Keep the fact and the pitfall; drop the
history.

Each migration commit also carries the store deletion and index line removal for every entry it
absorbed.

### 4. Fix in repo

The FIX-IN-REPO entries, as ordinary repo changes. Each one ends with the store entry deleted —
the defect is gone, so the memory of it is noise.

### 5. Sweep

Delete the scratch verdict table. Re-measure the index and report the number, whether or not it
cleared the threshold.

If it is still over, say so. The remainder is genuinely user-specific, and the threshold is what
should change — trimming true, user-specific entries to hit a number is the failure this whole
exercise exists to prevent.

## The store/index invariant

The index is the only file loaded at session start, so an entry with no index line is invisible and
an index line with no entry is a dangling pointer.

**An entry removed from the store loses its index line in the same commit. An entry kept still has
one.** Check it by listing the store against the index at the end of every commit that touches
either.

## Routing

The general principle: **a fact needed every session belongs in always-loaded context; a fact
needed only when touching a subsystem belongs in that subsystem's skill or README.** Always-loaded
context earns the hardest pruning, because every line costs on every turn.

| Entry kind | Home |
| --- | --- |
| Framework or API facts | the matching skill's `SKILL.md` |
| Architecture invariants | `docs/architecture/patterns/*.md`, or the subsystem README |
| Test-harness facts | the testing skills |
| CI-gate and tooling behaviour | the always-loaded CI section, or the dev-tooling specs |
| Working-style feedback | cross-project skills — see below |
| Cross-project infra patterns | the `mael` wiki |

**Gotchas need a home.** Most `reference` entries are gotchas, and most skills have nowhere to put
one. Add a `## Gotchas` H2 to a skill when it gains its first few; split to a sibling `GOTCHAS.md`
only when the H2 outgrows the file. Look for surfaces already designed to be appended to — a
"things to check" section in an incident playbook, a notes section in a debt register — and use
them before inventing a new home.

**Working-style feedback is not project knowledge.** "Own every failure", "run surgical test
suites", "ask before re-triggering a flake" are how the user wants *any* agent to work, in any
repo. They belong in cross-project skills, grouped by theme — ownership and escalation, refactor
taste, test discipline, git and PR traps, task sizing — not in one project's store.

## Where cross-project destinations live

Getting this wrong loses the work at the next install.

- **Cross-project skills.** `~/.claude/skills/` is an **install target, not a source**. Its
  contents come from `shared/skills/` in the maelstrom repo via `mael install`, so an edit made
  there is overwritten by the next install. Edit them in a worktree:
  `mael add --project maelstrom <branch>`. That is a different repo, so it gets its own PR — hand
  it back to the user rather than pushing it as part of the project's work.
- **The wiki.** Separate again: `~/.maelstrom/tasks/_wiki/`, written whole-page via
  `mael wiki update` and auto-committed. See the `mael` skill. It takes cross-project patterns —
  tool choices, setup steps — not user preferences and not project facts.
- **The store.** Also outside the repo. Its deletions ride along with whichever commit absorbs the
  entry; only the index line has to move in lockstep.

## Prevention

The sustainable end state is that the fact never enters the store. When a session learns something
durable about the codebase, **write it into the repo then** — the skill, the README, the pattern
doc — instead of remembering it privately and boiling it out months later.

The store is for what is true of the **user**, not of the code.
