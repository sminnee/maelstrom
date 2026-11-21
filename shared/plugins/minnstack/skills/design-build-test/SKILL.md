---
name: design-build-test
description: Break work into deep modules, build it, then test its contract changes at their primary seams. Use before you build a feature or fix, when you write a plan's Contract changes, or when you reshape a test suite.
---

# Design, build, test

Read `CONTEXT.md` when present. When `docs/README.md` lists subsystems, also read the glossary of the subdomain you work in. Use `codebase-design` for the vocabulary: module, interface, seam, depth. See [examples.md](examples.md) for good and bad tests, and [mocking.md](mocking.md) before you mock anything.

A contract is the set of rules of one command or query. A rule is one clause: what the command or query requires of its caller, what it ensures, or what always holds for its domain type. `domain-modeling` has the model behind these terms. Test observable behaviour at the boundary of the domain type (Khorikov): the primary seam of a rule is that boundary. A subsystem can hold its rules in feature files, at the `Specs:` path of its `docs/README.md` entry: `domain-modeling`'s FEATURE-FORMAT.md has the format.

1. **Break the problem into modules.** Find deep modules, as `codebase-design` defines them.
2. **Find the contract changes and the primary seam of each.** The contract changes say what to test. The primary seam says where.
   - When there is no plan, agree the contract changes with the user before the build: each rule that the work adds, changes, or withdraws, with its caller. To find them, read the subsystem's feature files, when it has them. Then read the suite outline: the test names of each touched module's owning suite, not the bodies.
   - When a plan has no **Contract changes** section, find them the same way and record them in the PR. Do not stop and wait.
   - The primary seam of a rule is the one interface where its contract holds in full. Take it from the feature file's `Primary seam:` line, or from the plan's line. When neither names it, take it from the project's test-ownership doc, when its `CLAUDE.md` or `AGENTS.md` names one. When that does not settle it, agree it with the user, or in an unattended session choose it and say why in the PR.
   - Test each rule at its primary seam only.
   - An outer seam, such as a handler, an API route, a CLI command or a page, gets a test only for what it adds: that it passes the request on, and that it shows a refusal. Such a test cites no rule id. Name it for the seam.
   - Code below the primary seam gets tests of its own only when it is a query with a contract: a deep module that several commands call, such as a parser or a store.
   - Choose an ephemeral check when a regression test would cost more than it protects: a layout (browser screenshot), a script nothing else calls (run it), a migration that runs once and is then deleted. A migration that ships to users' installs gets a test.
3. **Build the code.** When a contract change edits a feature file, edit the file first: add, change, or withdraw the rule. Run the existing suite as you go. It guards what already works.
4. **Select the tests.** The contract changes select them.
   - Give each contract change a test at its primary seam, an ephemeral check, or a reason for neither.
   - Choose the cases of a rule by equivalence class: a representative case, then a boundary. Fields that the code maps one by one are not one class: give each field a case.
   - Add a test that no contract change asks for only when the build shows a notable risk that no rule covers, such as a race or a path that loses data. First ask if it is a rule that the contract missed: when it is, add the rule. When it is not, test that one risk and say so in the PR. This is the exception, not a second pass over the code.
   - Before you add a test, look for an existing test to extend, change, or delete.
   - When a rule gets a new owning test, delete the old tests of it in the same commit.
   - When the contract changes are "None", add and change no test, except the regression test of a bug fix. When the build must edit a test anyway, say in the PR's test plan which it was: the test was brittle, or the contract changed.
   - When the subsystem has feature files, do this also:
     1. Write no test of a rule that is not yet in the feature file.
     2. Cite one rule id in the name of each new or changed test, in the form that FEATURE-FORMAT.md gives. With no rule, write no test, except the test of an outer seam. When the behaviour matters, add the rule, and say so in the PR.
     3. Give each rule one kind of evidence, as its tag says. A rule with a runnable example gets no unit test too.
     4. Assert nothing that the rule does not state.
     5. Delete each test that you wrote only to drive the build, unless it cites a rule.
5. **Write the tests and run the checks.**
   - Make each new test pass. Then break the behaviour it guards, for example flip a branch or return a wrong value, and see it fail on its assertion. A test that cannot fail proves nothing.
   - Run each ephemeral check.
   - Record each check and its result, and each reason for no test, in the PR's test plan: a `verification` document or `.drafts/pr.md`.
6. **Iterate for large work.** Repeat steps 3–5 for each part. Redo step 1 only when the breakdown was wrong.

In plan mode, do steps 1 and 2. Write the plan's **Contract changes**: each rule that the work adds, changes, or withdraws, with its caller. Name the primary seam of each when no feature file names it, and the existing tests to change or delete. The plan has no other test plan. A build session whose plan has Contract changes starts at step 3.

## Bugs

1. Find why the suite missed the bug: an over-mock, a narrow case, or a bad decomposition. Fix that cause.
2. Write a regression test at the primary seam and see it fail. Skip it only when step 2 would choose an ephemeral check.
3. Fix the bug and see the test pass. Then continue from step 4.

## Reshaping a suite

Keep the production code unchanged and the suite green while you move tests. Move each contract's tests to its primary seam, and delete the copies at other seams. Agree each move as you would agree a primary seam.

To move a suite onto feature files, do these steps in order:

1. Write the feature files first, from the glossary and the purpose of each command, as `domain-modeling`'s FEATURE-FORMAT.md says. Get the user's verdict on the rules.
2. Record the test count and the run time of each suite. Run a mutation tool over the code of the contract and keep the result of each mutant. This is an ephemeral check: commit no config and no result.
3. Classify each test against the rule ids. Give each one verdict: a rule id, a duplicate of a named test, a test of an outer seam to keep, a surplus test of an outer seam, an orphan, or out of scope.
4. Sort the orphans into two lists: behaviour that a rule missed, each with a proposed rule, and implementation detail to delete. Add a third list: each rule that the user intends and the code does not keep. Get the user's verdict on the lists. Delete nothing before it.
5. Add the accepted rules. Rename each test to cite its rule. Split a parametrized test that mixes rules into one test for each rule.
6. Delete the duplicates, the surplus tests, and the implementation-detail tests. Do not thin by field: when a rule covers several fields, keep one parametrized test of the rule with one case for each field. An outer seam keeps one success and one handled refusal for each command, and the facts that only it owns.
7. Run the mutation tool again. Compare the sets, not the scores: for each mutant that the first run killed and the second does not, restore a test or add a rule.
8. Report the counts and the run times before and after, and each rule that has no test.

## Test rules

Avoid internal mocks and private-method tests, assertions that recompute the result, query-count assertions, mirror tests that copy the code's shape, a contract tested twice, E2E tests that re-check component detail, append-only test growth, and weakening user behaviour to satisfy a test.

An expected value comes from a rule. Its sources are the plan's Contract changes, the feature file's example, a specification, a worked example, a recorded real reply, or a result that a caller observes. When the only source is the code under test, write no test of the value: test the behaviour that reads the value, at its primary seam, or test nothing.

Prefer no test: reach first for a type or an assertion that makes the rule impossible to break.

When a test catches real user behaviour such as a debounce or animation, wait for its signal rather than removing that behaviour.

Production refactoring belongs in `/code-review`, not this loop. When a test or a check fails, own the failure as `collaboration` says.

## Milestones

When the system prompt teaches `<compact-session>`, write `<compact-session min-tokens="200k">` and end your turn after a part that closes a build phase. Do not write it after each test.

Write `<milestone>built</milestone>` when the tests are written and the project's gates pass.
