# Review Guide

The universal review baseline, loaded for every review in every project.

**This file holds rules that are true in every project.** Project-specific rules belong in that
project's `docs/review/coding-standards.md` (prescriptive rules) and `docs/review/review-guide.md`
(what to look for in that codebase, in the same shape as this file). When a project rule disagrees
with this file, the project rule wins.

**Review one layer at a time, top down.** The layers are ordered by altitude: specifications and
subsystems first, coding standards last. Work them in that order and give the earlier layers the
most attention — the most likely feedback on a review is not code correctness but an architectural
decision. We structure code so that the code, and changes to it, are easy for a reviewer (human or
machine) to understand; layers 1–2 are where that is won or lost.

**No severity tags.** These entries are not ranked, and reviewers do not label findings blocking
or advisory. Whether a finding must be fixed now depends on the user's context — release
pressure, scope, what they already intend to change — which the review cannot see. The parent
agent decides that with the user. State the consequence of leaving an issue; let that carry the
weight.

---

## Checklist

Scan this first, layer by layer, top down. Read the layer's section below only for the items that
hit.

**Layer 1 — Specifications & subsystems** — essence captured? · responsibilities in the right
subsystems? · implementation exposing mistakes in the plan?

**Layer 2 — Architecture** — accidental complexity · a supporting tool the code is tolerating
instead of redesigning · reimplemented helper · wrong layer · leaked abstraction · inconsistent
with siblings · barrel re-exports · re-exported imported type · speculative abstraction ·
premature compat shim · unnecessary indirection · dead paths · pointless try/except

**Layer 3 — Test design** — tests focused on the business domain at component/subsystem level? ·
E2E limited to critical interaction patterns? · unit tests limited to critical or complex
subsystems? · duplicated fixture boilerplate · verbose body · non-obvious assertions ·
point-assertions · no converter for complex state · duplicative · wrong layer · not
cross-referenced to spec · assertion-free · missing negative case · testing configuration · mocked
integration test

**Layer 4 — Security & correctness** — injection · secrets in code · missing authorisation ·
overbroad permissions · input trusted by origin · swallowed errors · data loss · unhandled
partial failure · boundary cases · silent coercion at boundaries

**Layer 5 — Coding standards** — project's `docs/review/coding-standards.md` · name and behaviour
disagree · vague identifiers · inconsistent vocabulary · metadata-only log entries ·
user-visible change with no doc change

**Broken windows** — before withdrawing any finding because "the surrounding code does this
too", read that section. Precedent is not a rationale.

---

## Layer 1 — Specifications & subsystems

Judge the change against what it set out to build, before judging the code.

- **Essence captured?** Does the implementation capture the essence of what was being built, or
  has it drifted into a shape that satisfies the letter of the plan and misses the point? Where
  the plan is available (task content, spec files, PR description), read it.
- **Responsibilities in the right subsystems?** Check the assignment of duties across subsystems.
  A responsibility given to the wrong subsystem shows up as Layer 2 findings — but the fix is
  often a re-planning decision, not a refactor.
- **Plan mistakes surfaced by the implementation.** The finished implementation often exposes
  mistakes in the original plan — a wrong assumption, a missing requirement, a subsystem boundary
  that does not hold. Report these. The plan is not certified by having been executed; the
  implementation is the evidence against it.

## Layer 2 — Architecture

Is each modified subsystem focused on its own concerns? Are the supporting subsystems doing
their job?

### Accidental complexity

A subsystem absorbing complexity unrelated to its domain — business logic assembling a form that
spends most of its lines on statement management, a model that spends its lines on persistence
mechanics. Sometimes this is inevitable. Often it is the signal that an underlying abstraction is
not helping, and the fix is to refactor that abstraction, not to keep schlep-ing around it.

When you find it:

- Name the schlep. Say what fraction of the module's effort goes to work outside its domain.
- Ask what abstraction would absorb it. A helper, a different seam, a redesigned interface.
- Report it even when the fix is a refactor out of scope for the branch — this is exactly what
  the "potential refactor" bucket is for.

### Redesign the tool, don't tolerate it

When every consumer of a supporting subsystem works around the same inadequacy, the finding is
against the subsystem, not the consumers. Favour redesigning our own tools over putting up with
them — the status quo of an internal tool carries no weight. Report the workaround pattern and
name the tool that should change.

### Architecture and reuse

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

### Simplicity

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

## Layer 3 — Test design

Are the tests mostly focused on the important aspects of the business domain, with minimal E2E and
unit tests? Are the test supports keeping the tests descriptive? Are the input fixtures and output
assertions easily understood by a reviewer?

### Test shape

- **The bulk of tests target the business domain, at component/subsystem level.** These are the
  tests that describe the behaviour the work exists to deliver.
- **E2E tests are for the most critical interaction patterns only.** They are the most expensive
  to run and maintain; spending them on anything less crowds out the domain tests.
- **Unit tests are limited to very critical or very complex subsystems supporting the business
  domain.** In most cases the business-domain tests smoke-test the constituents for free. A unit
  test that re-covers what a domain test already exercises is duplicate coverage.

### Is the test worth having?

**Duplicate coverage** — a new test asserting what an existing test already covers — adds
maintenance cost and no assurance. Likewise a test of incidental implementation detail rather
than **business functionality** that matters.

**One spec point, one layer.** Test at the layer that would actually catch the regression, not
at unit *and* integration *and* end-to-end. Re-testing the same point at every layer multiplies
the cost of every future change and still catches the bug only once.

**Cross-reference to the spec.** Where the project keeps spec files, a test should name the spec
point it covers, so a reader can trace test to requirement.

**Assertion-free tests.** A test that exercises code but asserts nothing — or only that no
exception was raised — cannot fail, so it provides no assurance.

**Negative cases.** New branching logic tested only on its success path leaves the branch that
actually breaks untested.

### Testing configuration

A test that restates a config, constant table, or literal structure asserts nothing about
behaviour. It requires the same value to be updated in two places, and it cannot catch a bug —
it can only report that someone edited the config.

Delete it, or replace it with a test of what *consumes* the config.

### Readable assertions

A reviewer reading the test should understand the input data, the sequence of events, and the
output data without detective work.

**Prefer whole-object assertions.** A run of `assert result.a == 1`, `assert result.b == 2`
hides the shape of the answer. Assert the whole object or a meaningful sub-object in one
comparison: a reader sees the expected state at a glance, and a failure diff shows everything
that moved rather than only the first field that tripped.

**Use converters for complex or non-deterministic state.** When the object is large, or carries
timestamps, ids, or ordering that vary per run, do not fall back to point-assertions. Write a
converter that projects the state into a simplified but *complete* structure — stable fields
only — and assert against that in one comparison. Completeness matters: a converter that drops
fields silently stops testing them.

### Fixtures and test supports

**Test bodies should read as the scenario.** Push complex fixture coordination into helpers so
the body states intent, not setup mechanics.

**Reuse fixture helpers.** Repeated setup across tests signals a missing shared helper. Reuse
the project's existing coordination rather than re-establishing it inline.

**A library of domain-specific test-support helpers is worth building.** Where fixtures are hard
to construct, add factory functions to the test-support module — `make_<thing>()` builders with
sensible defaults, overridable per test — so each test states only what makes its scenario
distinct. Inadequate test support is accidental complexity in the tests.

**Prefer seams with easily instantiated domain objects.** When choosing where a test drives the
system, a seam whose domain objects are cheap to construct keeps the tests on the behaviour and
out of the mechanics. A seam that forces elaborate orchestration is a Layer 2 finding waiting to
happen.

**Avoid shared mutable fixtures.** State carried between tests makes them order-dependent.

**Do not assert the implementation.** A test coupled to internal call order or private state
breaks on a valid refactor.

**Integration tests must hit the real dependency.** A mocked database, queue, or filesystem in a
test labelled *integration* removes the only thing that test was for. Mocks have masked broken
migrations before.

## Layer 4 — Security & correctness

### Security

- **Injection.** Any query, shell command, path, or template built by string concatenation from
  input that a user controls. Look for parameterised APIs instead.
- **Secrets in code.** Keys, tokens, passwords, or connection strings as literals — including in
  tests, fixtures, and comments.
- **Missing authorisation.** A new endpoint, command, or handler that reaches privileged data or
  actions without the check its siblings apply.
- **Overbroad permissions.** New file modes, tokens, or scopes wider than the task requires.
- **Input trusted by origin.** Data treated as safe because of where it came from rather than
  because it was validated.

### Correctness

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

## Layer 5 — Coding standards

The project's `docs/review/coding-standards.md` is the source of truth for project-specific
conventions — load it where it exists and scan the diff against it. The universal floor:

- **Name and behaviour disagree.** `get_*` that writes, `is_*` that returns non-boolean,
  `validate_*` that mutates. Judge the name against what the code does now.
- **Vague identifiers.** `data`, `info`, `handle`, `process`, `manager` where a specific term
  exists.
- **Inconsistent vocabulary.** The same concept under two names in one codebase.
- **Metadata-only log entries.** A log that exists only to carry telemetry — no message, only
  structured fields. Piggyback on an existing log line, or add one with meaningful content.
- **User-visible change, no doc change.** New or changed flags, commands, config keys, or
  environment variables that the project's reference docs do not mention. Judge it against your
  own commit's diff.

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

## Out of scope

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
