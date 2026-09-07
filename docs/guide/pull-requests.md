# Pull requests

How work leaves an agent session and lands on GitHub.

## The finishing sequence

When an execute session's implementation is done and the project's gates pass, it runs this
sequence **without asking**. The gates are the project's automated checks — tests, lint and
type check, as CLAUDE.md defines them:

1. Commit the implementation.
2. Run `/present` to re-cut the branch into story commits.
3. Run `/code-review`.
4. Triage the findings: apply what is correct and in scope, discard what does not apply, and
   write scope changes and potential refactors into `.drafts/pr.md` under
   `## Raised by review, not actioned`.
5. Commit each fix as a `--fixup` commit targeting the story commit it revises. Do not amend.
6. Push: `mael gh create-pr <ISSUE-ID> --squash`.
7. **Close the task:** `mael task status done`.
8. Run `/watch-pr` to take CI (continuous integration) to green.

With nothing worth applying, steps 4 and 5 are skipped.

This overrides the usual "only commit when asked" rule. In a maelstrom project it is
always on, because an agent that stops to ask at each step cannot run unattended.

## Commits

**The commits you make while you build are working history.** Commit as often as you like, and
`wip:` is a fine subject. `/present` squashes them and re-cuts the final diff into story commits,
so the order you worked in is never the order a reviewer reads.

The message rules below apply from `/present` onward; the mechanics apply throughout. Use a prefix
and append the Linear issue in brackets:

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

A story commit's body states the decision, and its `Review:` trailer says how deep to read it:

```
feat: store the base tip per branch [PROJ-12]

Why this decision, what it replaces, what was rejected. Mermaid allowed.

Review: read
```

See [Presenting the change](#presenting-the-change) for what each depth means.

Check where you are before pushing:

```bash
mael git status                # branch, diff stats, recent commits
mael gh show-code --committed  # everything since branching from main
```

## Presenting the change

Serious review is the bottleneck, and the chronological commits of a build are not the story a
reviewer needs. `/present` re-cuts them:

```bash
/present
```

It squashes the branch, then partitions the final diff into three to eight story commits — one per
design decision, ordered so each reads on top of the last. The reviewer then reads them in order on
the PR's Commits tab.

**The invariant is the tree, not the story.** The final tree equals the tree the branch had before
the pass, and the working history is the undo.

**Present runs once per task**, at the end of the build, before `/code-review`. After it, every
change is a `fixup!` on the story commit it revises, or a `chore:` when it revises none. A later
task in a chain re-presents the whole branch from scratch; what present refuses is a branch that
already carries fixups.

See the journey afterwards:

```bash
git log refs/mael/history/<branch>/<stamp>
```

### Uncommitting a branch

`/present` runs this command for you. Run it yourself only before review has started, to re-cut a
branch's commits by hand.

```bash
mael git uncommit-branch
```

The command rebases the branch onto its base, saves the commits as a working history, then resets
the branch to its base tip. Every change is then unstaged in the working tree, ready to be
committed again in whatever order reads best.

The command refuses, and changes nothing, when the working tree is dirty, when a rebase or merge is
in progress, or when the branch has no commits ahead of its base. A rebase conflict aborts and
restores the tree — run `mael sync`, resolve the conflict, then try again.

The working history is your record and your undo:

```bash
git log refs/mael/history/<branch>/<stamp>   # the journey, in the order it happened
git reset --hard refs/mael/history/<branch>/<stamp>   # undo the uncommit
```

The command prints the ref it wrote. Each run writes a new one, so an earlier run's chronology
survives. The refs are deleted with the branch.

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

A presented branch is reviewed fresh: its story commits are new objects, so they carry no
`reviewed` note.

**One run reviews at most 8 commits** — the oldest 8 that are not yet reviewed. A presented branch
fits in one run by design, because present caps decisions at eight. A `chore:` or a fixup landing
before the review can push it over, and the run then defers the overflow. It reports the
rest as deferred. Run `/code-review` again to review them: the first run tags its commits
`reviewed`, so the second run skips them and takes the next 8. The cap holds even when you name
an explicit SHA or range. Run the same command again to take the next 8, or name a narrower
range if you want different commits.

It runs `mael sync --squash --no-push` first, so the review sees the commits as they will
land instead of a history littered with fixups. Rebase conflicts stop the review; a dirty
worktree does not, because the rebase autostashes. This step is skipped when you name an
explicit SHA or range.

Then it spawns **read-only sub-agents**, all running concurrently, so the diff never enters the
parent's context. Two kinds run:

- **One per story commit**, reviewing that decision. The reviewer is told the commit's review
  depth, and it judges the decision the body states against the diff the commit makes. Each
  reviewer may read *later* commits in the branch, so work finished by a follow-up commit is not
  reported as a problem.
- **One for the whole branch**, reviewing prose: comments, docstrings, and documents. It also
  reads the story whole — whether the partition is honest, and whether `.drafts/pr.md` describes
  the branch the commits actually make.

The prose reviewer exists because the commit reviewers cannot do its job. They weigh
architecture above language, and each one sees a single commit — so a paragraph copied into
four files is invisible to all of them. Its own agent gives prose its own budget and the
whole-branch view. It is skipped on a branch that changes no prose.

Findings are merged into one report:

1. Summary (the branch as a whole)
2. Per commit: design decisions, then findings
3. Prose: design decisions, then findings

Reviewing per commit means every code finding is already attributed to the commit that
introduced it, which is what the fixup below targets. A prose finding often spans commits, so
it lands as a fixup on `HEAD`, and those commits come back for review next run.

The prose reviewer also sweeps the repo for duplicated explanations, which can name a file the
branch never touched. Review never edits such a file on its own. It asks you first, with the
copy it would keep and the words the cut would save.

**Findings are not ranked blocking vs advisory.** A sub-agent reviewing one commit cannot know
your release pressure, or what you already plan to change. It therefore reports what it found
and what it costs to leave. The parent then sorts by what each fix would cost: apply the correct,
in-scope ones; discard the ones that do not apply; raise anything that materially changes scope
with you. Potential refactors always go in that last bucket — a review is the best place to
notice them, and dropping them silently is how they get lost.

Every commit reviewer loads `review-guide.md` from the skill directory — the cross-project
baseline, worked layer by layer: specifications & subsystems, architecture, test design,
security & correctness, coding standards. The prose reviewer loads the `writing-for-humans` and
`writing-for-agents` skills instead, plus `CONTEXT.md` as the glossary. If the project also
supplies `docs/review/coding-standards.md` or its own `docs/review/review-guide.md`, those load
too and take precedence.

### Fixups, not amends

Commit each fix as a `fixup!` commit aimed at the story commit whose decision it revises:

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

**The PR body comes from `.drafts/pr.md`.** Write the overview, the diagrams and the test notes
there, and `create-pr` puts them on the PR. A new PR gets the draft as its body; an open PR has its
body replaced. The command deletes the draft once the PR has it, so a failed push keeps the draft
for the next attempt. With no draft file, a new PR gets an empty body and an open PR's body is left
alone.

The title is never touched on an open PR. Set it with `gh pr edit <n> --title` if it is wrong.

Other flags:

```bash
mael gh create-pr PROJ-123 --draft       # draft PR
mael gh create-pr PROJ-123 --progress    # "(Progresses …)"; leaves status alone
mael gh create-pr PROJ-123 --wait        # block until CI finishes
```

Use `--progress` for a multi-session task with iterations still to come. It avoids marking
the issue "In Review" before the work is actually complete.

## Why the task closes before the CI watch

Step 7 comes before step 8 deliberately.

**The pull request is the completion signal.** Once it is raised the work cannot be
forgotten: an open PR is visible on GitHub and gets chased.

The task is the fragile half. A task left in `in-progress/` is **invisible, and it blocks
the rest of its chain** — every task that follows it stays unactionable. So close it while
you reliably can, rather than after a CI watch that might drag on, time out, or lose its
session.

A task does not close itself. Run `mael task status done`. If a session dies before it gets
there, `mael task reconcile --fix` finds the task and closes it.

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

Normally you merge on GitHub. **Use rebase merge, not squash merge.** A squash merge collapses the
branch into one commit, which throws away the story the commits tell. `mael git merge` rebases, so
it keeps them.

To merge locally:

```bash
mael git merge            # rebase onto main, fast-forward main, push
mael git merge --close    # ...then close the worktree and delete the branch
mael sync --squash --no-push   # autosquash fixups without pushing
```

Then release the Linear issue when it actually ships:

```bash
mael linear set-status PROJ-123 done   # → "Unreleased"
mael linear release                    # promote every "Unreleased" to "Done"
```

## See also

- [The multi-agent workflow](multi-agent-workflow.md) — where this sits in the loop.
- [Integrations](integrations.md) — Linear status transitions.
