# Watch PR Command

Watches the current branch's PR through CI, fixes any failures it finds, resubmits, and **loops
until CI passes** — fully autonomously, without waiting for the user between iterations.

This runs against the PR for the **current worktree's branch**. There must already be an open PR
(create one with `mael gh create-pr <ISSUE-ID>` first if there isn't).

## Usage

```
/watch-pr
```

## Prerequisites

Load the `mael` skill first if it isn't already — all `mael`/`git` commands need
`dangerouslyDisableSandbox: true`.

## Command Logic

Run this loop. Each iteration is one CI cycle.

1. **Wait for CI** — run in the background so you can keep working:

   ```bash
   mael gh read-pr --wait        # run_in_background: true
   ```

   `--wait` blocks until CI completes and exits **0 = pass**, **1 = fail**, **2 = timeout**.

2. **If CI passed (exit 0)** — you're done. Report the green status and **stop the loop**.

3. **If CI timed out (exit 2)** — report the timeout and stop; don't spin. The user can re-run
   `/watch-pr` to resume watching.

4. **If CI failed (exit 1)** — investigate and fix every failure, **regardless of whether this PR
   introduced it**. Don't skip a failure just because it looks pre-existing or flaky.

   - Read the failures: `mael gh read-pr` for the summary, then
     `mael gh check-log <run_id> --failed-only` for the failing steps. Pull artifacts
     (`mael gh download-artifact <run_id> <name>`) when you need test results, screenshots, or
     traces. For Playwright/E2E failures, use the `playwright-trace` skill on any `trace.zip`.
   - Reproduce locally where practical and run the project's gates (tests, lint, typecheck per
     CLAUDE.md) to confirm the fix.

5. **Commit each fix**, classifying it:

   - **Related to this PR** (the PR's own changes caused the failure) → commit as a **fixup**
     targeting the commit that introduced the problem:

     ```bash
     git add <files>
     git commit --fixup <sha>
     ```

     Don't amend. Push the fixup with a plain `mael sync` (step 6) — it stays a
     separate `fixup!` commit on the PR; fold it in later with `mael sync --squash`.

   - **Unrelated to this PR** (the failure is pre-existing or stems from something outside this
     PR's changes — e.g. a flaky test, a broken dependency, a main-branch regression) → commit as a
     standalone **chore**:

     ```bash
     git add <files>
     printf 'chore: <what you fixed> [<ISSUE-ID>]\n\n<why>\n' | git commit -F -
     ```

   When in doubt about classification, look at *what the failure touches* versus *what this PR
   changed*: if the broken code path is part of this PR's diff, it's a fixup; otherwise it's a chore.

6. **Resubmit via `mael sync`** — rebase on `origin/main` and re-push:

   ```bash
   mael sync
   ```

   If the rebase hits conflicts, **resolve them** (inspect both sides, keep the intent of this PR's
   changes alongside upstream), then continue the rebase. `mael sync` force-pushes the branch with
   `--force-with-lease`, putting your fixes up for a fresh CI run.

   > If `mael sync` reports success but its output shows the push step failed (e.g. a transient
   > network error), fall back to `mael gh create-pr <ISSUE-ID>` to re-attempt the push.

7. **Loop back to step 1** — wait for the new CI run. Repeat until CI passes (step 2) or times out
   (step 3).

## Notes

- **Autonomous loop**: do not stop to ask the user between iterations. Fix → commit → sync → wait,
  and keep going until green. The only stopping conditions are CI pass, CI timeout, or a failure you
  genuinely cannot fix (report what's blocking you and stop).
- **Fix everything CI reports**, not just failures attributable to this PR — a red pipeline blocks
  the merge regardless of cause.
- **Fixup vs chore** is the key call each fix: PR-caused → `git commit --fixup <sha>`;
  pre-existing/unrelated → `chore:` commit. Keep them separate commits so the user can squash fixups
  cleanly while chores stand on their own.
- **Run the waits in the background** (`run_in_background: true`) so the session stays responsive
  while CI runs.
- **`<ISSUE-ID>`** is the Linear identifier — it's in `$MAEL_TASK_PARENT` (`linear.<ID>`) when
  launched from a notebook task, and usually in the branch name / recent commits otherwise.
