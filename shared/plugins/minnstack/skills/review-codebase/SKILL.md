---
name: review-codebase
description: Sweep an existing codebase section by section with the code-review rules, rank the hotspots, and make tasks for the chosen rows. Invoked as `/review-codebase`.
---

# Review codebase

This sweep is read-only. It writes only its state files and, after the user chooses rows, task drafts. It applies the `code-review` rules to code that already exists, not to a branch.

Keep all state under `.drafts/codebase-review/`, so a compaction or a new session loses nothing:

- `inventory.json` — the output of `inventory.py`.
- `ledger.md` — one line per section: checkbox, section name, paths, line count, churn.
- `findings/<section>.md` — one reviewer report per section.
- `report.md` — the collated, ranked report.

1. Resume or start. Plan mode blocks the state-file writes.
   - When the session is in plan mode, stop and tell the user to relaunch the task with `--mode normal`.
   - When the ledger's target differs from `$ARGUMENTS`, ask the user before you start again.
   - When `report.md` exists, show it as in step 5 and go to step 6.
   - When every section in `ledger.md` is ticked, go to step 5.
   - When `ledger.md` exists, go to step 3 at the first unticked section.
   - Otherwise continue at step 2.
2. Make the inventory with [inventory.py](inventory.py), from the project root:
   ```bash
   mkdir -p .drafts/codebase-review
   python3 <this skill's directory>/inventory.py $ARGUMENTS > .drafts/codebase-review/inventory.json
   ```
   It leaves out lockfiles, docs, data and config, test data, and vendored or built code, and counts them in `excluded_files`.
   - When the project has generated or vendored code that the script counts, run it again with `--exclude <glob>` for each path. Use the same flags in each later run.
   - Write `ledger.md`. Its header gives the target and the inventory's `counter`, `total_lines`, `units`, and `excluded_files`. Then write one line per inventory section, in the inventory's order: checkbox, section name, each path in backticks, line count, churn. Paths are relative to the repository root.
   - You can rename a section. You can merge small sections of the same unit. Do not drop a section, and do not drop a unit.
   - For each section with `tests_only: true`, find the code that its test files import: by import, not by file name. Move each test file to the line of that code's section. When one test file covers code in two sections, give it to one section and name it in both lines. Keep a test-only line only for tests that import no section's code, such as E2E tests.
   - Run `python3 <this skill's directory>/inventory.py $ARGUMENTS --check .drafts/codebase-review/ledger.md`. When it lists `uncovered` files, add each to a ledger line and run the check again. The ledger is complete when the check exits 0.
   - Write a `<note>` with the unit, section, and batch counts, and start step 3.
3. Sweep in batches of three. Read [section-reviewer-prompt.md](section-reviewer-prompt.md). Spawn three read-only `Explore` reviewers in parallel, one for each of the next three unticked sections. Give each the prompt, its section name and paths, and the paths of `code-review/review-guide.md` and `code-review/reviewer-prompt.md` in the skills directory. When all three return, write each report to `findings/<section>.md`, then tick the three sections in `ledger.md`.
4. Compact only at a batch boundary. When the system prompt teaches `<compact-session>` and the batch is on disk, write `<compact-session min-tokens="200k">Running /review-codebase. Ledger: .drafts/codebase-review/ledger.md; continue at the next unticked section</compact-session>` and end the turn. Do not compact while a reviewer runs: its report is not on disk yet. Repeat step 3 until every section is ticked.
5. Collate. Read every findings file. Run the step 2 check again, and confirm that each ledger section has a findings file. Merge the same smell from several sections into one systemic finding with a list of locations: the "Outside this section" field shows candidates. Rank by consequence × breadth, then by fix cost. Write `report.md` as a table: Rank, Hotspot, Locations, Guide rule, Consequence, Suggested fix, Effort (S/M/L). Above the table, give the coverage: units, sections, and lines reviewed, and the `excluded_files` counts. Show it with `<doc-file kind="review" review="true" filename=".drafts/codebase-review/report.md" title="Codebase review">`.
6. Stop for the user to choose fix, defer, or reject each row. Do not draft tasks first. A bare document approval is not a choice: ask which rows to fix.
7. Load `planning`. Draft one plan-mode task per chosen row, or per theme when the rows exceed its cap. Each draft's content names the hotspot, its locations, and the guide rule it breaks. Present the drafts as `planning` says.
8. Promote, close this task, launch the head, and end the session, as `planning` says.

The rank is a sort order, not a severity tier. A sweep finds more than anyone will fix, so the report must rank. State consequence and breadth on each row, so the user can check the order.

Do not fix findings here. Do not add project-specific rules to this skill: they belong in the project's review documents, which the reviewers load.
