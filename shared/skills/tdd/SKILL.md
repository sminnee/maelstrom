---
name: tdd
description: Test-driven development. Use for test-first features and fixes, red-green-refactor work, integration tests, or reshaping a test suite.
---

# Test-driven development

Read `CONTEXT.md` when present. Test behaviour through agreed public **seams**, never internals. Before the first test, agree the seams with the user. Use `codebase-design` if the interface or seam is unsettled. See [tests.md](tests.md) and [mocking.md](mocking.md) for examples.

Work one vertical slice at a time:

1. Shape the affected suite while production code stays green. Extend an existing specification where possible; otherwise state why a new test belongs.
2. For a bug, find why the suite missed it: an over-mock, narrow case, or bad decomposition. Fix that cause before adding the red case.
3. Write one failing behavioural test at the agreed seam.
4. Make the smallest production change that turns it green.
5. Repeat.

When the system prompt teaches `<compact-session>`, write `<compact-session min-tokens="200k">` and end your turn after a group of slices that closes a build phase. Do not write it after each slice.

Write `<milestone>built</milestone>` when the last slice is written and the project's
gates pass.

For a feature, add tests for additive behaviour. Re-cut the suite first if the feature reframes existing behaviour. Production refactoring belongs in review, not the loop.

Avoid internal mocks and private-method tests, assertions that recompute the result, query-count assertions, mirror tests that copy the code's shape, a contract tested twice, E2E tests that re-check component detail, append-only test growth, bulk horizontal test writing, and weakening user behaviour to satisfy a test.

Expected values need an independent source: a literal, worked example, or specification. When a test catches real user behaviour such as a debounce or animation, wait for its signal rather than removing that behaviour. Re-cutting tests across seams needs the same agreement as choosing a new seam.
