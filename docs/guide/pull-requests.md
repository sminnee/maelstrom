# Pull requests

How work leaves an agent session and lands on GitHub.

## The finishing sequence

When an execute session's implementation is done and the project's gates pass, it runs this
sequence **without asking**:

1. Commit the implementation.
2. Run `/code-review`.
3. Address **Blocking** findings. Advisory findings are a judgement call.
4. Commit each fix as a `--fixup` commit targeting the commit that introduced the issue. Do
   not amend.
5. Push: `mael gh create-pr <ISSUE-ID> --squash`.
6. **Close the task:** `mael task status done`.
7. Run `/watch-pr` to take CI to green.

With no blocking findings, steps 3 and 4 are skipped.

This overrides the usual "only commit when asked" rule. In a maelstrom project it is
always on, because an agent that stops to ask at each step cannot run unattended.

## Commits

Use a prefix and append the Linear issue in brackets:

| Prefix | For |
|---|---|
| `feat:` | New behaviour. |
| `fix:` | A bug fix. |
| `refactor:` | No behaviour change. |
| `chore:` | Everything else. |

```bash
mael gh show-code --uncommitted        # review before committing
git add src/maelstrom/ports.py
printf 'feat: widen the port range [PROJ-123]\n\nDetail.\n' | git commit -F -
```

Check where you are before pushing:

```bash
mael git status                # branch, diff stats, recent commits
mael gh show-code --committed  # everything since branching from main
```

## Code review

```bash
/code-review              # origin/main..HEAD
/code-review <sha>        # one commit
/code-review <range>      # any git range
```

Review is **stateless and one-shot**. Re-invoke it after a fix lands to re-review; there is
no incremental machinery and no resolved-thread tracking.

It gates first with `mael review-prepare`, which refuses to run when the worktree has
uncommitted changes or the range is empty. Then a **read-only sub-agent** does the review,
so the diff never enters the parent's context. Findings come back as:

1. Summary
2. Design decisions worth calling out
3. **Blocking findings**
4. Advisory findings

If the project supplies `docs/review/coding-standards.md` or `docs/review/code-smells.md`,
the sub-agent loads them automatically.

### Fixups, not amends

Commit each blocking fix as a `fixup!` commit aimed at the commit that introduced the
problem:

```bash
git commit --fixup <sha>
```

One fixup per finding. Do not amend — amending rewrites commits the review already covered
and makes the fix impossible to trace.

`--squash` folds them into their targets at push time, so the PR still lands with clean
history.

## Pushing

```bash
mael gh create-pr PROJ-123 --squash
```

- **New PR** — the first commit becomes the title.
- **Existing PR** — this just pushes. It does not open a second one.
- **With an issue id** — appends `(Fixes PROJ-123)` for Linear auto-linking and sets the
  issue to "In Review".
- **`--squash`** — autosquashes `fixup!` commits into their targets while rebasing onto
  `origin/main`, then force-pushes with `--force-with-lease`.

Other flags:

```bash
mael gh create-pr PROJ-123 --draft       # draft PR
mael gh create-pr PROJ-123 --progress    # "(Progresses …)"; leaves status alone
mael gh create-pr PROJ-123 --wait        # block until CI finishes
```

Use `--progress` for a multi-session task with iterations still to come. It avoids marking
the issue "In Review" before the work is actually complete.

## Why the task closes before the CI watch

Step 6 comes before step 7 deliberately.

**The pull request is the completion signal.** Once it is raised the work cannot be
forgotten: an open PR is visible on GitHub and gets chased.

The task is the fragile half. A task left in `in-progress/` is **invisible, and it blocks
the rest of its chain** — every task that follows it stays unactionable. So close it while
you reliably can, rather than after a CI watch that might drag on, time out, or lose its
session.

The SessionEnd hook moves the task to `done` when the session ends, but it can fail
silently — if `mael` is not on `PATH`, if git is unavailable, or if the process is killed.
Run `mael task status done` explicitly.

## Taking CI to green

```bash
/watch-pr
```

This loops autonomously until CI passes:

1. `mael gh read-pr --wait` — run in the background. Exit 0 = pass, 1 = fail, 2 = timeout.
2. **Pass** — report and stop.
3. **Timeout** — report and stop. Do not spin; re-run `/watch-pr` to resume.
4. **Fail** — fix *every* failure, whether or not this PR caused it. Do not skip one because
   it looks pre-existing or flaky.

Investigating a failure:

```bash
mael gh read-pr                            # summary and comments
mael gh check-log <run_id> --failed-only   # the failing steps
mael gh download-artifact <run_id> <name>  # test results, screenshots, traces
```

For Playwright or E2E failures, use the `playwright-trace` skill on any `trace.zip`.

Fix, then re-push. Use a `fixup!` commit when the PR caused the failure and a `chore:`
commit when it did not. Then `mael sync` and loop.

## Reading a PR

```bash
mael gh read-pr                  # status, comments, CI results
mael gh read-pr --all-comments   # include comments older than the last push
mael gh read-pr --wait           # block until CI finishes
mael gh read-pr --wait-for-review # block until a reviewer responds
```

`read-pr` shows top-level comments, review summaries and unresolved inline threads. Comments
older than the most recent push collapse into a count line unless you pass `--all-comments`.

Run the `--wait` variants in the background so you can keep working.

## Merging

Normally you merge on GitHub. To merge locally:

```bash
mael git merge            # rebase onto main, fast-forward main, push
mael git merge --close    # ...then close the worktree and delete the branch
mael git squash           # autosquash fixups without pushing
```

Then release the Linear issue when it actually ships:

```bash
mael linear set-status PROJ-123 done   # → "Unreleased"
mael linear release                    # promote every "Unreleased" to "Done"
```

## See also

- [The multi-agent workflow](multi-agent-workflow.md) — where this sits in the loop.
- [Integrations](integrations.md) — Linear status transitions.
