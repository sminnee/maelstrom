# Pull requests

How work leaves an agent session and lands on GitHub.

## The finishing sequence

When an execute session's implementation is done and the project's gates pass, it runs this
sequence **without asking**. The gates are the project's automated checks — tests, lint and
type check, as CLAUDE.md defines them:

1. Commit the implementation.
2. Run `/code-review`.
3. Triage the findings: apply what is correct and in scope, discard what does not apply, and
   carry scope changes and potential refactors into the PR description.
4. Commit each fix as a `--fixup` commit targeting the commit that introduced the issue. Do
   not amend.
5. Push: `mael gh create-pr <ISSUE-ID> --squash`.
6. **Close the task:** `mael task status done`.
7. Run `/watch-pr` to take CI (continuous integration) to green.

With nothing worth applying, steps 3 and 4 are skipped.

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

Review **skips commits it has already reviewed**. A reviewed commit carries a `reviewed` git
note, which `git log` shows with no flag. Name an explicit SHA or range to review a commit
again. There is no resolved-thread tracking.

A note is local to your machine — sibling worktrees share it, but it is never pushed to
origin. `mael doctor` sets `notes.rewriteRef`, which keeps a note on its commit through a
rebase. A note also survives a change to the commit it sits on, so a commit that is modified
after review is not reviewed again. The run reports each commit it skips, so you can see when
that happens.

**One run reviews at most 8 commits** — the oldest 8 that are not yet reviewed. It reports the
rest as deferred. Run `/code-review` again to review them: the first run tags its commits
`reviewed`, so the second run skips them and takes the next 8. The cap holds even when you name
an explicit SHA or range. Run the same command again to take the next 8, or name a narrower
range if you want different commits.

It runs `mael git squash` first, so the review sees the commits as they will land instead of
a history littered with fixups. Rebase conflicts stop the review; a dirty worktree does not,
because the squash autostashes. This step is skipped when you name an explicit SHA or range.

Then it reviews the branch **one commit at a time**: one **read-only sub-agent** per commit,
all running concurrently, so the diff never enters the parent's context. Each reviewer may
read *later* commits in the branch, so work finished by a follow-up commit is not reported as
a problem. Findings are merged into one report, in commit order:

1. Summary (the branch as a whole)
2. Per commit: design decisions, then findings

Reviewing per commit means every finding is already attributed to the commit that introduced
it, which is what the fixup below targets.

**Findings are not ranked blocking vs advisory.** A sub-agent reviewing one commit cannot know
your release pressure, or what you already plan to change. It therefore reports what it found
and what it costs to leave. The parent then sorts by what each fix would cost: apply the correct,
in-scope ones; discard the ones that do not apply; raise anything that materially changes scope
with you. Potential refactors always go in that last bucket — a review is the best place to
notice them, and dropping them silently is how they get lost.

Every reviewer loads `review-guide.md` from the skill directory — the cross-project baseline,
worked layer by layer: specifications & subsystems, architecture, test design, security &
correctness, coding standards, language. If the project also
supplies `docs/review/coding-standards.md` or its own `docs/review/review-guide.md`, those load
too and take precedence.

### Fixups, not amends

Commit each fix as a `fixup!` commit aimed at the commit that introduced the problem:

```bash
git commit --fixup <sha>
```

One fixup per finding. Do not amend — amending rewrites commits the review already covered
and makes the fix impossible to trace.

Fixes are applied and committed **one commit at a time, oldest first**. Each commit's fixups are
made and committed before the next commit's fixes are written, so a fixup carries only the changes
for the commit it targets.

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

The SessionEnd hook moves the task to `done` when the session ends. That hook can fail
silently: `mael` may not be on `PATH`, git may be unavailable, or the process may be killed.
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
