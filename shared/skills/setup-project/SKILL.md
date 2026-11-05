---
name: setup-project
description: Set up a project for maelstrom — make its dev env start, then audit its gates, CI, tests, dead code, docs, and agent config. Invoked as `/setup-project`.
---

# Set up a project

This skill makes the project's dev env work, then audits the project. The session starts in normal mode, because phase 2 edits files.

The target is `$ARGUMENTS` when it names a directory. Otherwise it is the cwd.

## Phase 1: profile

1. Profile the target: languages, package managers, shape, deployment type, mael use, and rough source and test size.

## Phase 2: dev env

Read `dev-env.md` in the `mael` skill before you edit. `docs/reference/configuration.md` in the maelstrom repo lists every key.

2. Read `.maelstrom.yaml`. Make it fit the project:
   - `install_cmd` installs the dependencies.
   - `services:` declares each service the project runs, with its `command` and its named `ports:`, as `dev-env.md` describes.
3. Run `mael env reset`. Then run `mael env start <name>` for each service. Confirm that each service with a port answers on it.
4. When a service fails, fix the config or the project's start script, and go back to step 3.
5. When you changed files, commit them with a `chore:` message, and push with `mael gh create-pr`. The dev env is this skill's one deliverable, so it does not wait for the audit. When the file already fit and every service started, record that, and make no commit.

Phase 2 is complete when every declared service starts, and each service with a port answers on it.

## Phase 3: audit

6. Enter plan mode. Read [auditor-prompt.md](auditor-prompt.md) and [checklist.md](checklist.md).
7. Spawn one read-only `Explore` auditor for each checklist category that fits the profile. Give each the auditor prompt, the profile, the checklist's three rules, and its category.
8. Merge and deduplicate the findings. Show one table, grouped by category, with the columns Check, State, Recommendation, Effort. Include the actionable rows and the `n/a` rows. List the passing checks on one line.
9. Stop. The user chooses fix, defer, or reject for each row. Do not write the plan before the user chooses.
   When the user confirms no row, run `mael task status done` and end the session.

## Phase 4: hand-off

10. Write a `load-many` plan for the confirmed rows only:
    - One auto-mode execute block per theme.
    - The first block has `follow-end: '*'`. Each later block has `follow: <previous>`.
    - Leave `branch:` unset.
    - Add `linear.in-progress` only when the project is Linear-backed.
    - The preamble tells a later reader to close this task and run `mael task load-many`. The blocks are not instructions to implement by hand.
11. Show the plan file. Exit plan mode with only the hand-off commands allowed.
12. After approval:
    1. Run `mael task status done`.
    2. Run `mael task load-many <plan-path> --run`.
    3. End the session.

## Guards

- Do not add project-specific tool settings, code conventions, locales, layouts, or severity tiers to this skill or its checklist.
- Do not implement audit findings here. Phase 2 is the only edit this skill makes.
