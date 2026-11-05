---
name: design-build-test
description: Break work into deep modules, build it, then choose what to test and at which layer. Use before you build a feature or fix, when you write a plan's Test plan, or when you reshape a test suite.
---

# Design, build, test

Read `CONTEXT.md` when present. Use `codebase-design` for the vocabulary: module, interface, seam, depth. See [examples.md](examples.md) for good and bad tests, and [mocking.md](mocking.md) before you mock anything.

1. **Break the problem into modules.** Find deep modules, as `codebase-design` defines them.
2. **Choose the layer that owns each test.**
   - Read the project's test-ownership doc first, when its `CLAUDE.md` or `AGENTS.md` names one.
   - Test a lower layer when it is a deep module with callers of its own: a parser, a store, a reducer.
   - Let a higher layer cover the layers below it when they have no contract of their own.
   - Test each contract at one layer only.
   - Choose an ephemeral check when a regression test would cost more than it protects: a layout (browser screenshot), a script nothing else calls (run it), a migration that runs once and is then deleted. A migration that ships to users' installs gets a test.
   - When there is no Test plan and the test-ownership doc does not settle a layer, agree it with the user.
3. **Build the code.** Run the existing suite as you go. It guards what already works.
4. **Select the tests.** Start from the Test plan's risks, when there is one. Add the riskiest parts the build shows: branching, concurrency, parsing, state transitions, error paths, data loss. Add the most critical behaviour a user depends on. Give each item a test at its owning layer, an ephemeral check, or a reason for neither. Before you add a test, look for an existing test to extend, change, or delete.
5. **Write the tests and run the checks.**
   - Make each new test pass. Then break the behaviour it guards, for example flip a branch or return a wrong value, and see it fail on its assertion. A test that cannot fail proves nothing.
   - Run each ephemeral check.
   - Record each check and its result, and each reason for no test, in the PR's test plan: a `verification` document or `.drafts/pr.md`.
6. **Iterate for large work.** Repeat steps 3–5 for each part. Redo step 1 only when the breakdown was wrong.

In plan mode, do steps 1 and 2. Then write the plan's **Test plan**: the modules, the owning layer for each, what gets only an ephemeral check, and the risks step 4 must cover. A build session with a Test plan starts at step 3.

## Bugs

1. Find why the suite missed the bug: an over-mock, a narrow case, or a bad decomposition. Fix that cause.
2. Write a regression test at the owning layer and see it fail. Skip it only when step 2 would choose an ephemeral check.
3. Fix the bug and see the test pass. Then continue from step 4.

## Reshaping a suite

Keep the production code unchanged and the suite green while you move tests. Move each contract's tests to the layer that owns it, and delete the copies at other layers. Agree each move between layers as you would agree an owning layer.

## Test rules

Avoid internal mocks and private-method tests, assertions that recompute the result, query-count assertions, mirror tests that copy the code's shape, a contract tested twice, E2E tests that re-check component detail, append-only test growth, and weakening user behaviour to satisfy a test.

Expected values need an independent source: a literal, worked example, or specification. When a test catches real user behaviour such as a debounce or animation, wait for its signal rather than removing that behaviour.

Production refactoring belongs in review, not this loop.

## Milestones

When the system prompt teaches `<compact-session>`, write `<compact-session min-tokens="200k">` and end your turn after a part that closes a build phase. Do not write it after each test.

Write `<milestone>built</milestone>` when the tests are written and the project's gates pass.
