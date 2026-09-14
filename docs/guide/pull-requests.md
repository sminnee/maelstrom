# Pull requests

How work leaves an agent session and lands on GitHub.

## The finishing sequence

When an execute session's implementation is done and the project's gates pass, it runs this
sequence **without asking**. The gates are the project's automated checks — tests, lint and
type check, as CLAUDE.md defines them:

1. Commit the implementation.
2. Write `.drafts/pr.md` — the decisions, the rationale, diagrams and test seams. It is what
   review reads first, and it becomes the PR body.
3. Run `/code-review`. It uncommits the branch, reviews the working tree, and commits its fixes.
4. Run `/present` to re-cut the reviewed tree into story commits.
5. Push: `mael gh create-pr <ISSUE-ID> --squash`.
6. **Close the task:** `mael task status done`.
7. Run `/watch-pr` to take CI (continuous integration) to green.

Review triages its own findings: it applies what is correct and in scope, discards what does not
apply, and writes scope changes and potential refactors into `.drafts/pr.md` under
`## Raised by review, not actioned`.

**Review comes before present.** Review's findings become plain edits to the working tree, so
present partitions code whose review points are already addressed — and the story a reviewer
reads on the PR is the final one, not the one told before the findings landed.

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

A story commit's body states the decision:

```
feat: store the base tip per branch [PROJ-12]

Why this decision, what it replaces, what was rejected. Mermaid allowed.
```

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

It squashes the branch, then partitions the final diff into one to eight story commits — one per
design decision, ordered so each reads on top of the last. The reviewer then reads them in order on
the PR's Commits tab.

**The invariant is the tree, not the story.** The final tree equals the tree the branch had before
the pass, and the working history is the undo.

**Present runs once per task**, after `/code-review`, so it partitions reviewed code. After the PR
is pushed, every change is a `fixup!` on the story commit it revises, or a `chore:` when it revises
none. A later task in a chain re-presents the whole branch from scratch. Present refuses a branch
already carrying fixups, which means it is in Land, and it stops when `mael git uncommit-branch`
refuses — on a dirty tree that means review's fixes were never committed.

An additive review passes `--local` to both commands, so present re-cuts only the new work —
see "Re-reviewing an open PR" below.

See the journey afterwards:

```bash
git log refs/mael/history/<branch>/<stamp>
```

### Collapsing a branch

`/code-review` and `/present` each collapse the branch for you — review to read its final state,
present to re-cut it afterwards. Run these yourself only to re-cut a branch's commits by hand.

```bash
mael git squash-branch     # collapse into one commit, still committed
mael git uncommit-branch   # collapse into the working tree, unstaged
```

Both save the commits as a working history and rebase the branch onto its base. They differ in
where the collapsed work ends up. `squash-branch` leaves one commit on the branch, so a stray
`git reset --hard` cannot destroy it. `uncommit-branch` then resets that commit into the working
tree, ready to be committed again in whatever order reads best.

Both refuse, and change nothing, when the working tree is dirty, when a rebase or merge is in
progress, or when the branch has no commits ahead of its base. A rebase conflict aborts and
restores the tree — run `mael sync`, resolve the conflict, then try again.

### Re-reviewing an open PR

By default a collapse takes the whole branch. When the PR is already open, only the new commits
need review, and re-reading the merged part is waste:

```bash
mael git squash-branch --local
```

`--local` collapses only `origin/<branch>..HEAD` — the commits that were never pushed. The
already-reviewed commits keep their own subjects. A branch that was never pushed has nothing
pushed to keep, so `--local` takes the whole branch.

`--local` rewrites the pushed commits during the rebase, so the next push must force.
`mael gh create-pr` already does.

One case is refused: an unpushed `fixup!` aimed at an already-pushed commit, which the collapse
would silently discard. Run `mael sync --squash --no-push` first, or collapse the whole branch.

The working history is your record and your undo:

```bash
git log refs/mael/history/<branch>/<stamp>   # the journey, in the order it happened
git reset --hard refs/mael/history/<branch>/<stamp>   # undo the uncommit
```

**`--hard` is safe only while the tree is still clean.** The ref holds the commits as they stood
at the reset, so it restores those and discards everything unstaged. Run it straight after an
uncommit and you lose nothing; run it after review has edited the tree and you lose those edits,
because no ref holds them. Commit first if in doubt.

The command prints the ref it wrote. Each run writes a new one, so an earlier run's chronology
survives. The refs are deleted with the branch.

## Code review

```bash
/code-review
```

Review's subject is the **working tree**. It starts by running `mael git squash-branch`, which
collapses every commit on the branch into one. The reviewers then read the branch's final state
directly, and a finding becomes a plain edit rather than a commit to target. The work stays
committed throughout, so nothing depends on an unstaged tree surviving.

Review chooses its scope first. A branch whose PR is already open gets an **additive** pass —
`--local`, reading only the commits that were never pushed. Any other branch, or a request for
substantial rework, gets a **fresh** pass over the whole branch.

That squash is also the sync: it fetches origin, fast-forwards local main, resolves the base
and rebases, which is everything `mael sync --no-push` does. It refuses a dirty working tree, so
the build must commit its work before review runs.

Then it spawns **read-only sub-agents**, all running concurrently, so the diff never enters the
parent's context. One runs per concern:

- **Design and architecture** — the review guide's layers 1 and 2, plus naming and vocabulary. It
  judges the decisions `.drafts/pr.md` states against what the tree actually does.
- **Tests** — layer 3: what is tested, at which seam, and whether the assertions read.
- **Security and correctness** — layer 4.
- **Prose** — comments, docstrings, and documents.

Splitting by concern rather than by commit gives each reviewer the whole change to judge, so a
question that spans files is answerable. Each reads the worktree itself; no diff is dumped into
its prompt.

The prose reviewer exists because the code reviewers cannot do its job. They weigh architecture
above language, and a paragraph copied into four files is invisible to them. Its own agent gives
prose its own budget.

Findings are merged into one report:

1. Summary (the branch as a whole)
2. Per concern: design decisions, then findings
3. Prose: design decisions, then findings

The prose reviewer also sweeps the repo for duplicated explanations, which can name a file the
branch never touched. Review never edits such a file on its own. It asks you first, with the
copy it would keep and the words the cut would save.

**Findings are not ranked blocking vs advisory.** A sub-agent reviewing one concern cannot know
your release pressure, or what you already plan to change. It therefore reports what it found
and what it costs to leave. The parent then sorts by what each fix would cost: apply the correct,
in-scope ones; discard the ones that do not apply; raise anything that materially changes scope
with you. Potential refactors always go in that last bucket — a review is the best place to
notice them, and dropping them silently is how they get lost.

Every code reviewer loads `review-guide.md` from the skill directory — the cross-project
baseline, worked layer by layer: specifications & subsystems, architecture, test design,
security & correctness, coding standards. The prose reviewer loads the `writing-for-humans` and
`writing-for-agents` skills instead, plus `CONTEXT.md` as the glossary. If the project also
supplies `docs/review/coding-standards.md` or its own `docs/review/review-guide.md`, those load
too and take precedence.

Review leaves its fixes uncommitted. Commit them, then run `/present`.

### Fixups, not amends

Review needs no fixups — its edits sit in the working tree. Fixups belong to **Land**: a change
made after the PR is pushed, when the branch already carries story commits.

```bash
git commit --fixup <sha>
```

Aim it at the story commit whose decision it revises, one fixup per change. Do not amend —
amending rewrites commits the review already covered and makes the fix impossible to trace.

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

**The PR body comes from `.drafts/pr.md`**, written at step 2. A new PR gets the draft as its
body; an open PR has its body replaced. The command deletes the draft once the PR has it, so a failed push keeps the draft
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

Step 6 comes before step 7 deliberately.

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
