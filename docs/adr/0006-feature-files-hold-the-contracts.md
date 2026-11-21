# Feature files hold the contracts of commands and queries

Status: accepted, 2026-11-20
Rollout: partial, 1 of 22

## Context

The contract of a task command was stated in three places: the model tests, the refusal tests of
the orchestrator server, and copies at the CLI and the route layers. No document said what
`mael task update` promises. A test that proved a rule a second time could not be told from a
test that proved it first, so the suites only grew.

## Decision

A **Feature file** under `docs/specs/<subdomain>/<subsystem>/` holds the contract of each command
and each query, as **Contract rules** with stable ids. `docs/README.md` maps the subdomains and
the subsystems. A subdomain has one glossary, `docs/specs/<subdomain>/CONTEXT.md`.

- A feature file describes the model we want. Its `Primary seam:` line names the target. A
  `Held today at:` line names the code that holds each kind of clause now, until the code
  reaches the target.
- A plain test cites one rule id in its name, in the form `test_<feature file stem>_<id>_…`.
  A test of an outer seam cites no rule and is named for its door.
- No Gherkin runner runs the files. Each `R` and `E` rule is `@unit`.

## Considered options

- **Runnable scenarios with a step layer.** The step layer is a second codebase: it costs about
  one line of step code for each line of feature file. Rejected.
- **Contracts in test names only.** The rules stay spread over the suites, and nothing shows a
  rule that has no test or a test that has no rule. Rejected.

## Consequences

- A plan states its work as the rules it adds, changes or withdraws in a feature file.
- A test with no rule is an outer seam test, or it is deleted.
- The check that joins each rule id to a test is not built yet.

The trial on the Task tracker gave these results:

- Feature files that are written before the tests are read find rules that the code does not
  keep. The trial found six.
- A suite is thinned by rule, not by field. A mutation run before and after the deletes shows
  each test that was the only guard of a fault: the trial restored tests for 36 of 39 such faults.
- The test count falls little: 1,527 to 1,493. The gain is that each test says which rule it
  proves, and a plan states its work as a diff of a feature file.

## Rollout

Done for a subsystem means: each of its commands and queries has a feature file, and each test
in its owning suites cites a rule id or is the test of an outer seam. Check it with a `grep` of
each `@id:` of the subsystem's feature files, in its `_` form, over the test directories. An `I` rule of a
domain type has no test of its own while an `R` rule of each command illustrates it.

Tasks:

- [ ] Task tracker — the 7 write commands are done (task t33y.5). The queries, the other
  Template commands and the Registered PR commands are open
- [ ] Task runs
- [ ] Task documents
- [ ] Comms

Code:

- [ ] Projects and worktrees
- [ ] Dev environments
- [ ] Changes review
- [ ] Live feedback

Delivery:

- [ ] Pull requests and landing
- [ ] Quality checks

Agents:

- [ ] Agents
- [ ] Remote hosts

Desk:

- [ ] Desk

Method:

- [x] minnstack — n/a: it is skill text, with no command or query
- [ ] Wiki

Platform:

- [ ] State database
- [ ] Orchestrator server
- [ ] Operations and console
- [ ] CLI
- [ ] UI shell
- [ ] Integrations
- [ ] Install
