---
name: review-project-hygiene
description: Audit a project’s gates, runtimes, dead-code checks, spec-to-test mapping, README, and agent config. Invoked as `/review-project-hygiene`.
---

# Review project hygiene

This audit is read-only. It writes only a follow-up plan after user approval. Its priority is gates that appear green but cannot fail.

1. Enter plan mode. Profile the target directory (`$ARGUMENTS` or cwd): languages, package managers, shape, deployment type, mael use, and rough source/test size.
2. Read [auditor-prompt.md](auditor-prompt.md) and [hygiene-checklist.md](hygiene-checklist.md). Spawn one read-only auditor for each applicable checklist category. Give each the project profile and its checklist section.
3. Merge and deduplicate findings. Show a table grouped by category: Check, State, Recommendation, Effort. Include actionable and considered `n/a` rows; list passing checks in one line.
4. Stop for the user to choose fix, defer, or reject each row. Do not write the plan first.
5. Write a `load-many` plan for confirmed rows only. Use one auto-mode execute block per theme, chained with `follow-end: '*'` then `follow: <previous>`. Keep `branch:` unset. Add `linear.in-progress` only for a Linear-backed audit. Show the file with `<doc-file kind="other" ...>`.
6. After approval, close this task, run `mael task load-many <actual-plan-path> --run`, then end the session.

Do not add project-specific tool settings, code conventions, locales, layouts, or severity tiers to this universal skill or its checklist. Do not implement audit findings here.

Use only the checklist categories that fit the project profile and report considered `n/a` checks. The plan preamble must tell a later reader to close the audit task and use `mael task load-many`; the blocks are not instructions to implement by hand. Exit plan mode after showing the plan, with only the two handoff commands allowed.
