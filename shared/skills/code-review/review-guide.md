# Review Guide

The universal review baseline. The `/code-review` sub-agent loads this file for every review, in
every project. It is symlinked into `~/.claude/skills/` with the rest of the skill, so it travels
wherever the skill is installed.

**This file holds rules that are true in every project.** Project-specific rules belong in that
project's `docs/review/coding-standards.md` (prescriptive rules) and `docs/review/review-guide.md`
(what to look for in that codebase, in the same shape as this file). When a project rule disagrees
with this file, the project rule wins.

**No severity tags.** These entries are not ranked, and reviewers do not label findings blocking
or advisory. Whether a finding must be fixed now depends on the user's context — release
pressure, scope, what they already intend to change — which the review cannot see. The parent
agent decides that with the user. State the consequence of leaving an issue; let that carry the
weight.

---

## Checklist

Scan this first. Read the section below only for the items that hit.

**Correctness** — swallowed errors · data loss · unhandled partial failure · boundary cases ·
silent coercion at boundaries

**Security** — injection · secrets in code · missing authorisation · overbroad permissions ·
input trusted by origin

**Architecture and reuse** — reimplemented helper · wrong layer · leaked abstraction ·
inconsistent with siblings · barrel re-exports · re-exported imported type

**Simplicity** — speculative abstraction · premature compat shim · unnecessary indirection ·
dead paths · pointless try/except

**Naming** — name and behaviour disagree · vague identifiers · inconsistent vocabulary

**Comments** — over-weights the latest change · restates what's inferable · duplicates the
architecture docs · narrates the deliberation · comment/code drift

**Logging** — metadata-only log entries

**Tests** — duplicative · wrong layer · not cross-referenced to spec · assertion-free ·
missing negative case · testing configuration · point-assertions · no converter for complex
state · verbose body · duplicated fixture boilerplate · mocked integration test

**Documentation** — user-visible change with no doc change

**Broken windows** — before withdrawing any finding because "the surrounding code does this
too", read that section. Precedent is not a rationale.

---

## Correctness

- **Swallowed errors.** `except: pass`, `except Exception: pass`, an empty `.catch()` — a caught
  exception that is neither handled, re-raised, nor logged hides failures. If an error is
  genuinely safe to ignore, the exception type must be narrow *and* a comment must explain why.
- **Data loss.** Destructive operations (delete, overwrite, truncate, force-push) that run
  without a guard, a confirmation, or a recoverable path.
- **Unhandled partial failure.** A loop that writes to several places, where a failure halfway
  leaves the system in a state no code path repairs.
- **Off-by-one and boundary cases.** Empty collection, single element, exactly-at-limit. Check
  that new branching logic covers them.
- **Silent type coercion at boundaries.** Values crossing a parse, deserialize, or API edge
  without validation.

## Security

- **Injection.** Any query, shell command, path, or template built by string concatenation from
  input that a user controls. Look for parameterised APIs instead.
- **Secrets in code.** Keys, tokens, passwords, or connection strings as literals — including in
  tests, fixtures, and comments.
- **Missing authorisation.** A new endpoint, command, or handler that reaches privileged data or
  actions without the check its siblings apply.
- **Overbroad permissions.** New file modes, tokens, or scopes wider than the task requires.
- **Input trusted by origin.** Data treated as safe because of where it came from rather than
  because it was validated.

## Architecture and reuse

- **Reimplemented helper.** New code that duplicates something the repo already has. Search
  before accepting a new utility. This is the single most common finding worth making.
- **Wrong layer.** Logic placed where the project's own layering says it does not belong — a
  storage concern in a CLI handler, a presentation concern in a model.
- **Leaked abstraction.** A caller that must know its callee's internals to use it correctly.
- **Inconsistent with siblings.** A new module, command, or handler that ignores the shape its
  peers share. Consistency beats local preference — but consistency with a *good* pattern. Check
  that the shape the peers share is one worth copying before you ask for it (see **Broken
  windows** below).
- **Barrel-pattern re-exports.** A new `index.ts` or `__init__.py` that re-exports purely for
  import ergonomics. Prefer importing from the source file directly.
- **Re-exporting an imported type.** A module that imports a type or value from another module
  and exports it again (`export type { Foo } from "./bar.js"`). This creates a misleading second
  source of truth: consumers cannot tell where the type is really defined, and the owning module
  is obscured. Distinct from the barrel smell — here one feature module launders another's
  symbol rather than aggregating for ergonomics.

## Simplicity

- **Speculative abstraction.** A new helper used in only one place, a class wrapping a single
  function, or parameters and config no current caller uses. Three similar lines beat a premature
  abstraction.
- **Premature backwards-compat shim.** For unreleased code — supporting both an old and a new API
  surface when the old one never shipped — replace the old surface outright. Carrying both during
  a refactor doubles the maintenance load for no one's benefit.
- **Unnecessary indirection.** A wrapper, alias, or layer that only forwards.
- **Dead paths.** Branches that no input can reach; flags that are never set.
- **Pointless try/except.** A handler that only re-raises, or only logs without adding context.
  If it does not change control flow or enrich the error, delete it.

## Naming

- **Name and behaviour disagree.** `get_*` that writes, `is_*` that returns non-boolean,
  `validate_*` that mutates. Judge the name against what the code does now.
- **Vague identifiers.** `data`, `info`, `handle`, `process`, `manager` where a specific term
  exists.
- **Inconsistent vocabulary.** The same concept under two names in one codebase.

## Comments

The default is **no comment**. Most code carries its own meaning, and a comment earns its place
only by holding something the reader cannot infer locally. When one is warranted it should be
terse — a sentence or two, not a paragraph arguing its case.

Flag a comment or docstring that:

- **Over-weights the latest change.** It describes *the diff that produced the code* rather than
  the code as it now stands — "this used to be X, but…", or multi-line rationale bolted onto a
  small edit. That story belongs in the commit message. Test: would this comment still earn its
  place if the change had always been there?
- **Restates what's inferable locally.** Narrating the mechanics on the next line, spelling out a
  type the annotation already gives, or repeating a rationale a sibling docstring already carries.
- **Duplicates the architecture docs.** Subsystem READMEs and spec directories are the home for
  design rationale; a comment may *point* at them, but should not re-argue them in place.
- **Narrates the deliberation.** The reader needs the conclusion and the constraint forcing it,
  not the alternatives weighed or why the first attempt was wrong.
- **Drifts from the code.** A comment describing behaviour the code no longer has is worse than
  no comment.

A layering constraint, a surprising type, or a deliberate broad `except` is worth a line — none
needs a paragraph. Prefer trimming to deleting: the fact is usually worth keeping, the essay
around it is not.

## Logging

- **Metadata-only log entries.** A log that exists only to carry telemetry — no message, only
  structured fields. Piggyback on an existing log line, or add one with meaningful content.

## Tests

### Is the test worth having?

**Duplicate coverage** — a new test asserting what an existing test already covers — adds
maintenance cost and no assurance. Likewise a test of incidental implementation detail rather
than **business functionality** that matters.

**One spec point, one layer.** Test at the layer that would actually catch the regression, not
at unit *and* integration *and* end-to-end. Re-testing the same point at every layer multiplies
the cost of every future change and still catches the bug only once.

**Cross-reference to the spec.** Where the project keeps spec files, a test should name the spec
point it covers, so a reader can trace test to requirement.

**Assertion-free tests.** A test that exercises code but asserts nothing — or only
that no exception was raised — cannot fail, so it provides no assurance.

**Negative cases.** New branching logic tested only on its success path leaves the branch that
actually breaks untested.

### Testing configuration

A test that restates a config, constant table, or literal structure asserts nothing about
behaviour. It requires the same value to be updated in two places, and it cannot catch a bug —
it can only report that someone edited the config.

Delete it, or replace it with a test of what *consumes* the config.

### Readable assertions

**Prefer whole-object assertions.** A run of `assert result.a == 1`, `assert result.b == 2`
hides the shape of the answer. Assert the whole object or a meaningful sub-object in one
comparison: a reader sees the expected state at a glance, and a failure diff shows everything
that moved rather than only the first field that tripped.

**Use converters for complex or non-deterministic state.** When the object is large, or carries
timestamps, ids, or ordering that vary per run, do not fall back to point-assertions. Write a
converter that projects the state into a simplified but *complete* structure — stable fields
only — and assert against that in one comparison. Completeness matters: a converter that drops
fields silently stops testing them.

### Fixtures and doubles

**Test bodies should read as the scenario.** Push complex fixture coordination into helpers so
the body states intent, not setup mechanics.

**Reuse fixture helpers.** Repeated setup across tests signals a missing shared helper. Reuse
the project's existing coordination rather than re-establishing it inline.

**Avoid shared mutable fixtures.** State carried between tests makes them order-dependent.

**Do not assert the implementation.** A test coupled to internal call order or private state
breaks on a valid refactor.

**Integration tests must hit the real dependency.** A mocked database, queue, or filesystem in a
test labelled *integration* removes the only thing that test was for. Mocks have masked broken
migrations before.

## Documentation

- **User-visible change, no doc change.** New or changed flags, commands, config keys, or
  environment variables that the project's reference docs do not mention.

## Broken windows

**Existing code is not automatically the standard.** The surrounding code shows you what the
project *has done*, which is not the same as what it *should do*. Where the two differ, review
against the standard.

This is the easiest failure to fall into, because it looks like good judgement. You read the
module, absorb its conventions, and calibrate to them — so a swallowed exception reads as house
style, an assertion-free test reads as normal, and a fourth copy of a helper reads as the
pattern. The review then certifies the decay instead of catching it. Each pass lowers the bar,
and the bar never comes back up on its own.

Guard against it:

- **Do not withdraw a finding because the surrounding code does the same thing.** That the
  problem already appears three times makes it more worth reporting, not less. Say that you
  found it repeated — the repetition is evidence, not a defence.
- **Precedent is not a rationale.** "Matches the existing pattern" only answers a finding if the
  existing pattern is defensible on its own terms. Ask whether you would accept the code with no
  precedent in sight. If not, report it.
- **Judge against the guides, not the neighbours.** This file and the project's own guides are
  the standard. A module that predates them, or drifted from them, does not amend them.
- **Widespread decay is a refactor finding.** When a whole module or subsystem has drifted, the
  fix is usually too big for the commit in front of you. Report it as a potential refactor rather
  than dropping it — that is the bucket built for exactly this, and it is how the bar gets raised
  instead of quietly lowered.

The counterweight is real, though: do not report every historical wart in a file the commit
barely touches. The commit's own code is the target. Existing decay is worth raising when the
commit adds to it, follows it, or sits close enough that a reader would take it as endorsed.

---

## What NOT to report

**Defer to CI gates.** These run as their own jobs. Duplicating them wastes the review:

- Type errors, unresolved imports — pyright, tsc.
- Formatting, unused imports, lint nits — ruff, eslint, prettier.
- Unused exports, dead code — knip, vulture. Obvious cases are still worth a note at review
  time: they are easier to remove before merge than after.

**Also skip:**

- Style preferences the project has not written down.
- Rewrites that trade one valid approach for an equally valid one.
- Praise. The Summary carries the verdict; findings are for problems.
- Anything you have not confirmed by reading the code. A finding you are unsure of costs the
  user more than it saves. Verify, or leave it out.

## Anti-smells

Patterns that look wrong at a glance but are correct. Reviewers — LLM reviewers especially —
have flagged these before. Do not report them.

- **`except TypeError, ValueError:` is valid in Python 3.14+.** The parser accepts a
  parenthesis-free tuple of exception types. It is *not* Python 2's `except Type as name:` —
  that form bound the exception to a name; this form catches either type. Ruff may rewrite it to
  the parenthesised form depending on the module's target version, but that is a formatter
  preference, not a correctness issue.

When a review produces a false positive that a reader had to argue down, add it here if it
generalises, or to the project's own guide if it does not. This section is how the review stops
repeating its mistakes.
