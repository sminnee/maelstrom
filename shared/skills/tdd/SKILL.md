---
name: tdd
description: Test-driven development. Use when the user wants to build features or fix bugs test-first, mentions "red-green-refactor", wants integration tests, or wants to reshape an existing test suite.
---

# Test-Driven Development

TDD is the red → green loop. This skill is the reference that makes that loop produce tests worth keeping. Every section applies on every cycle — consult them before and during the loop, not after.

When exploring the codebase, read `CONTEXT.md` (if it exists) so test names and interface vocabulary match the project's domain language, and respect ADRs in the area you're touching.

## What a good test is

Tests verify behavior through public interfaces, not implementation details. Code can change entirely; tests shouldn't. A good test reads like a specification — "user can checkout with valid cart" tells you exactly what capability exists — and survives refactors because it doesn't care about internal structure.

See [tests.md](tests.md) for examples and [mocking.md](mocking.md) for mocking guidelines.

## Seams — where tests go

A **seam** is the public boundary you test at: the interface where you observe behavior without reaching inside. Tests live at seams, never against internals.

**Test only at pre-agreed seams.** Before writing any test, write down the seams under test and confirm them with the user. No test is written at an unconfirmed seam. You can't test everything — agreeing the seams up front is how testing effort lands on the critical paths and complex logic instead of every edge case.

Ask: "What's the public interface, and which seams should we test?"

When the shape of that interface is itself in question — how deep the module is, where the seam belongs, what the interface should expose — use the `/codebase-design` skill for the vocabulary. It is the shared source of the module, interface, depth, seam, adapter, leverage and locality terms, and it is a reference to consult, not a session to run.

## Shaping the suite — what the change does to it

The suite is the specification of the system, and every cycle edits it. A test added at the nearest convenient spot is a **hill-climbing** step — locally cheapest, globally worse. The target is a suite optimised a little on every cycle, not one disturbed as little as possible.

**Extend before add.** Name the existing test the new case belongs in — add a row to its parameterisation, widen its input, sharpen its assertion — or say why none reads as the same specification. Write a new test only then.

**A bug is an escape.** The suite should have specified this case and did not. Before writing red, ask why the original suite missed it, and fix that cause — the red test falls out of the fix:

- _Over-mocked_ — the test covering this path mocked an internal collaborator the bug lives behind. Replace the mock with the real thing; the test goes red by itself. This is the common case.
- _Too narrow_ — the test covers a neighbouring input. Broaden it until it goes red.
- _Mis-decomposed_ — the suite's cut of the requirements has no place for this case. Re-cut the affected tests around the requirement as now understood; then the case has a home.

When the diagnosis finds no cause to fix, pin a new test beside the bug.

**A feature moves the specification.** Ask how the spec evolves:

- _Additive_ — existing behaviour stands and the feature sits beside it. Add tests.
- _Reframing_ — the feature changes what existing behaviour means: a new axis, a generalisation, a concept replaced. Re-cut the existing tests around the new frame first — rename, merge, split, delete, re-parameterise — then write red for the new behaviour into the re-cut suite.

**Re-cut green, then go red.** Restructure existing tests against unchanged production code and prove the restructure a no-op — the suite passes before and after. Only then write the failing test.

**A re-cut is a seam decision.** Extending a test within an agreed seam needs no new agreement; merging, splitting or re-homing tests does — settle those where the always-on test-first rule settles seams.

## Anti-patterns

- **Implementation-coupled** — mocks internal collaborators, tests private methods, or verifies through a side channel (querying the database instead of using the interface). The tell: the test breaks when you refactor but behavior hasn't changed.
- **Tautological** — the assertion recomputes the expected value the way the code does (`expect(add(a, b)).toBe(a + b)`, a snapshot derived by hand the same way, a constant asserted equal to itself), so it passes by construction and can never disagree with the code. Expected values must come from an independent source of truth — a known-good literal, a worked example, the spec.
- **Horizontal slicing** — writing all tests first, then all implementation. Bulk tests verify _imagined_ behavior: you test the _shape_ of things rather than user-facing behavior, the tests go insensitive to real changes, and you commit to test structure before understanding the implementation. Work in **vertical slices** instead — one test → one implementation → repeat, each test a **tracer bullet** that responds to what the last cycle taught you.
- **Append-only** — every change adds a test and none reshapes one. The tell: near-duplicate tests differing by one input, and a test count that only rises. The cure is the shaping step above.
- **Query-count** — asserting a ceiling on database queries (`reset_query_count()` … `get_query_count() <= N`). Brittle, and coupled to internals rather than behavior: it breaks on a refactor that changes nothing observable. When a change kills an N+1, ship the batching and assert the *output* through the new path. An existing query-count test in the codebase is not a precedent to copy.
- **Test-driven production change** — weakening app behavior because a test trips over it. Debounces, auto-close on navigation and animations are usually there for real users. Ask whether the behavior serves them; if it does, fix the test to wait for the right signal instead.

## Rules of the loop

- **Shape, then red.** The shaping step above runs first, green, before any failing test.
- **Red before green.** Write the failing test first, then only enough code to pass it. Don't anticipate future tests or add speculative features.
- **One slice at a time.** One seam, one test, one minimal implementation per cycle.
- **Refactoring is not part of the loop.** Production code changes only enough to go green; its refactoring belongs to the review stage (see the `code-review` skill). Tests are re-cut in the shaping step, green, before red.
