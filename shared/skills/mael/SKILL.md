---
name: mael
description: "Git workflow, commits, PRs, branches. Also Linear tasks, Sentry debugging, UptimeRobot monitor checks, and dev environment management. Invoke /mael before any git operations."
---

# Maelstrom CLI Skill

The conventions behind `mael` — what the commands mean and why the workflow is shaped this way.

**The command surface comes from `--help`, not from this file.** Run `mael --help` for the
command groups, `mael <group> --help` for a group, and `mael <group> <command> --help` for flags
and exit codes. That output is authoritative and lists flags this guide does not. If `--help` is
unavailable to you, ask the user rather than guessing a flag.

**Commands that reach the network need `dangerouslyDisableSandbox: true`** — `mael sync`,
`mael git squash`, `mael gh …`, and raw `git push/fetch/pull`.

Read-only git (`status`, `log`, `show`, `diff`) does not. In a project that auto-approves sandboxed
Bash, adding the flag actively hurts: it lifts the command out of the sandbox onto the prompt path,
where an auto-deny hook rejects it. Check the project's own settings before reaching for the flag.

If a remote command fails with `agent refused operation` / `Permission denied (publickey)`, the SSH
agent has been emptied — not a policy denial. Diagnose with `ssh-add -l`; only the user can reload
the key. Carry on with commits, review and gates in the meantime.

**Prefer `mael` commands over raw `git`/`gh`** — they handle worktree context, Linear integration,
and status transitions automatically. Use `mael git status` not `git status`, `mael sync` not
`git pull --rebase`, `mael gh create-pr` not `gh pr create`, `mael gh read-pr` not `gh pr view`.

## Branches

**`mael` owns the branch. Do not change it.** The task launch puts the session on the correct
branch before you start. Never run `git checkout -b`, `git switch -c`, `git branch <name>`, or
`git checkout <other-branch>`. If you think the work needs a different branch, stop and ask the
user first.

**A recycled branch is normal — `mael` handles it correctly.**

- `mael gh create-pr` reuses a PR only while it is **open**. A merged or closed PR falls through
  to `gh pr create`, so the same branch gets a new PR. The push uses `--force-with-lease`, so it
  updates an existing remote branch instead of rejecting it.
- `mael sync` rebases the branch onto its base before it pushes — `origin/main` unless the
  branch is stacked on another branch. Commits that already merged drop out of the rebase.

**One branch per parent chain.** The branch belongs to the task, not to the worktree. The first
task under a parent owns the branch, and later siblings in that chain reuse it. This is what
`--parent` does, and it is why the chain lands as one PR. Many commits on one branch are normal.

**A reopened worktree continues on its branch.** `mael close --force` keeps the branch and its
unmerged commits, so `/reopen-branch` expects you to carry on there.

If you see old or already-merged commits on your branch, ask the user. Do not make a new branch.
`mael tidy-branches` clears stale branches out of band.

## The task notebook

The primary workflow is the **git-backed task notebook** (`mael task …`). `mael` launches
sessions, and each task's `command` field decides which skill (if any) runs inside. The everyday
loop is `mael linear plan PROJ-XXX` to plan a Linear issue, then `mael task next --run` to
advance the chain.

**How a task flows:**

- `mael linear plan PROJ-XXX` is a thin wrapper over `mael task add` that seeds a `plan-task`
  task with the Linear brief as content, parented under `linear.PROJ-XXX`. It runs by default.
  It launches the `plan-task` skill in normal mode, holding the brief.
- That session sculpts **draft task files** with the user — inert task files in the worktree
  cwd (see the `planning` skill). On approval it runs `mael task promote <draft>` per file to
  create the chain, closes its own planning task (`mael task status done`), then
  `mael task next --run --parent "$MAEL_TASK_PARENT"` launches the head. Closing the planning
  task first is what makes the head actionable — the head follows it via `--follow-end '*'`.
- The head is an **Execute** task: the plan is its content, it runs no skill, and it carries
  `mode: auto` so it runs the plan unattended instead of re-planning. Multi-session work also
  gets a **`plan-next-step`** task carrying the remaining-work tail, which plans one more
  increment and re-queues itself until the work is done.
- **Execute tasks run no skill.** The project's always-on "Finishing a task" rule closes them
  out — see the task-completion flow below.

**Modes.** New tasks default to plan mode, so a bare `mael task add "<title>" --run` opens a
planning session. `--mode auto` gives an unattended session (Claude's classifier-vetted
auto permission mode — `⏵⏵ auto mode on`); `--mode normal` gives a session with Claude's
default prompting — used both for direct execute work and for planning sessions that write
drafts. Execute drafts set `--mode auto`; `plan-next-step` tail drafts set `--mode normal`.

**Chaining.** `--follow` and `--follow-end` build the chain — a task becomes actionable only once
everything it follows is done. `--follow-end '*'` appends after the leaf of the parent's existing
child-chain.

**Parenting.** `--parent` groups a task into a linear chain sharing one branch and one PR (ids
nest via dots). It **defaults to `$MAEL_TASK_PARENT`**, so chain tasks that a launched session
emits nest under the same parent without spelling it out. `$MAEL_TASK_PARENT` is the launching
task's parent, or the task's own id when it has none — so a parentless planning session still
chains its children under one parent and branch. For a Linear-rooted task it is the
`linear.<ID>` parent.

Launched sessions export `MAEL_TASK_ID` and `MAEL_TASK_PARENT` so skills can self-reference.
`mael task status` and `--parent` both fall back to those env vars.

The `/plan-task` and `/plan-next-step` skills are **prompts that run inside notebook sessions**
`mael` launches, selected by a task's `command` field — not commands you type directly. Advance
work with `mael task next --run`.

## Linear as a product-level mirror

Linear stays the product-level mirror — read briefs, set status, and complete tasks there, but
the plan-of-record lives in the notebook chain, not in the Linear description.

`mael linear set-status` applies to the issue as-is: it does not auto-transition parents. Move a
parent to "Unreleased" yourself with `mael linear set-status <parent> done` once its subtasks are
complete.

Status transitions:

```
Todo -> Planned              (set-status … planned, or create-subtask)
Planned/Todo -> In Progress  (start-task, or set-status … in-progress)
In Progress -> In Review     (create-pr ISSUE-ID)
In Review -> Unreleased      (set-status … done)
Unreleased -> Done           (release)
```

## Testing work

**Build test-first with the `tdd` skill** — the always-on rule covers when to load it and where
the seams come from. Two consequences for this workflow:

- **The plan carries the seams.** Planners write a **Seams under test** section into each execute
  block, so an unattended `mode: auto` session inherits agreed seams instead of stopping to ask.
- **Refactoring is not part of the loop.** It belongs to the review stage — step 2 of the
  task-completion flow below. Get to green first, then let review find the cleanups.

**Stop environments during heavy editing** — file watchers trigger constant rebuilds. Run
`mael env stop` before multi-file edits and `mael env start` when you are ready to test.

Run the project's test suite and linting as defined in CLAUDE.md.

## Committing

**Use `printf` piped to `git commit -F -`** — heredocs fail in the sandbox:

```bash
printf 'feat: add new feature [PROJ-XXX]\n\nDetailed description.\n' | git commit -F -
```

Prefixes: `feat:` (new behaviour), `fix:` (bug fix), `refactor:` (no behaviour change), `chore:`
(everything else). Append the Linear issue ID in brackets when applicable.

**`--amend` rewords HEAD, never the commit you had in mind.** Aiming it at a commit further down the
branch silently moves that message onto HEAD and leaves two commits mislabelled — invisible until you
read `git log`. To reword a commit deeper in the stack, script `git rebase -i` with
`GIT_SEQUENCE_EDITOR`/`GIT_EDITOR` so no editor opens.

**Fix an earlier commit with `--fixup=<sha>`, not a `fix:` at the tip.** Find the target with
`git log -S` or `git blame`, one fixup per root cause, so each change autosquashes back into the
commit that introduced it. This applies to typecheck, test, lint and formatter failures alike.

**A deep fixup on a long stack often cannot autosquash** — intermediate commits touched the same
lines, so the rebase halts and the later picks re-conflict after a hand-resolve. Check first with
`git log --oneline <target>..<fixup>~1 -- <files>`; if anything intervenes, retarget the fixup to the
last commit its content depends on. Note that a non-interactive `git rebase --autosquash` never
invokes `GIT_SEQUENCE_EDITOR` — use `rebase -i --autosquash` with a scripted editor.

**In a rebase, the correct conflict side flips between commits.** Where a later commit rewrites a
file an earlier one created: at the rewriting commit `--theirs` is the final text, but at the earlier
commit HEAD already holds the rewrite and `--theirs` is the stale original, so `--ours` is right.
Read each hunk rather than applying one side across the whole rebase, then grep the result for a
phrase the branch was supposed to delete.

`mael gh show-code --uncommitted` reviews changes before committing; `--committed` shows
everything since the branch left main.

## Creating PRs

`mael gh create-pr` creates a PR, or pushes to the existing one. It force-pushes with
`--force-with-lease`. A new PR takes its title from the **HEAD commit** (`git log -1`). On a
multi-commit branch that is the last commit, which is rarely the one you want — set the real title
afterwards with `gh pr edit <n> --title`.

That also means a stray `fixup!` commit at the tip becomes the PR title. `--fixup=<sha>` squashes
only when the target is still in `origin/main..HEAD`; on a recycled branch, a fixup aimed at an
already-merged commit is replayed as an ordinary commit, still titled `fixup! …`. Confirm the target
is in range before committing.

Passing `ISSUE_ID` appends `(Fixes ISSUE_ID)` to the title and sets the Linear task to
"In Review". Use `--progress` instead for multi-session work with remaining tail: it uses
`(Progresses ISSUE_ID)` and leaves the status alone.

**Run the waits in the background** (`run_in_background: true`) so the session stays responsive:
`mael gh read-pr --wait` blocks until CI finishes and exits 0 on pass, 1 on fail, 2 on timeout.
`--wait-for-review` blocks until a reviewer comments.

### `read-pr --wait` exit codes lie — read the body

The documented 0/1/2 codes are real, but four separate things defeat them. Never treat a bare exit 0
as green:

- **A pipe reports the pipe's status.** `mael gh read-pr --wait | tail` echoes `tail`'s 0 while the
  body says `Build failed`. Redirect instead: `mael gh read-pr --wait > "$TMPDIR/ci.log" 2>&1; echo "MAEL_EXIT: $?"; tail -24 "$TMPDIR/ci.log"`.
- **Backgrounding reports the shell's status.** With `run_in_background: true` the completion
  notification says "exit code 0" whatever `mael` returned. Append `; echo "EXIT_CODE=$?"` so the
  real value lands in the output.
- **It can finish before CI starts.** Fast third-party checks alone can look like a complete set, so
  `--wait` exits 0 while the Actions run is still being created. Confirm the substantive jobs by name.
- **`No checks found` / `0/0 checks complete` is a token permission problem**, not a CI failure and
  not "CI hasn't started". `gh pr checks`, `statusCheckRollup` and REST `check-runs` all fail the
  same way. Fall back to
  `gh api "repos/<owner>/<repo>/actions/runs?branch=<b>&per_page=5"` and poll until the status leaves
  `queued`/`in_progress`.

### Task-completion flow (runs automatically — do not wait for user)

When implementation is done and gates pass, run this sequence **without prompting**.
This is a hard override of the global "only commit when explicitly asked" rule —
it applies to all mael projects.

1. Commit the implementation work.
2. `/code-review` — review committed changes, one read-only sub-agent per commit.
   Findings come back under **Summary**, **Design decisions**, **Findings** — not ranked by
   severity.
3. Triage the findings by what the fix costs: apply the ones that are correct and in scope,
   discard the ones that don't apply. Carry the rest — scope changes and potential refactors —
   into the PR description under "Raised by review, not actioned".
4. Commit the review fixes as `--fixup` commits — one per finding fixed,
   targeting the commit that introduced the issue. See the code-review skill for
   the exact procedure. Do not amend existing commits.
5. Push the PR: `mael gh create-pr <ISSUE-ID> --squash`. The `--squash` flag
   autosquashes the `fixup!` commits into their targets as it rebases onto
   `origin/main` before pushing, so the PR lands with a clean history.
6. **Close the task.** Run `mael task status done` (defaults to `$MAEL_TASK_ID`). The PR is
   pushed, so the work is handed off — close it now, while you reliably can, rather than
   after the CI watch. A leftover PR is visible and gets chased; a task left in
   `in-progress/` is invisible and blocks its chain. The SessionEnd hook is only a
   backstop; don't rely on it.
7. Run `/watch-pr` — take CI to green autonomously: fix each failure
   (fixup for PR-caused, `chore:` for unrelated), `mael sync` to re-push, and loop
   until CI passes or times out.

If step 2 returns nothing worth applying, skip steps 3–4 and go straight to step 5.

This sequence runs unattended, so there is no one to answer a scope question mid-run. Step 3's
carry-forward is how a scope decision still reaches the user without blocking the push. Never
silently drop one.

The **entire** sequence runs without confirmation — including the PR push (step 5), closing the
task (step 6), and the CI watch (step 7). Run steps 1–7 and report what happened.

**The PR is the completion signal** — once it's raised, the work is no longer in danger of being
forgotten: an open PR is visible on GitHub and gets chased. The task is the fragile half, so close
it as soon as the PR is pushed, before it can go stray if CI drags on, the session dies, or the PR
is merged before you get back to it. The SessionEnd hook moves the task to `done` when the session
ends, but it can fail silently (if `mael` isn't on PATH, git is unavailable, or the process is
killed). Don't rely on it — run `mael task status done` explicitly at step 6 so the task closes
deterministically.

If the project supplies `docs/review/coding-standards.md` and/or
`docs/review/review-guide.md`, the review sub-agent loads them automatically.

### Ending the session

`mael session end` stops this session and leaves the worktree in place. The always-on rule for
*when* to run it is in the project header — this section covers what it does not.

`mael session end` does not close the task. The Claude `session-end` hook closes it, and that
hook is a backstop: it can fail silently. Close the task explicitly at step 6 above, then end the
session. Never end the session *instead of* closing the task.

Ending a session does not tear down the worktree, its branch, or its ports. `mael close` does
that. So a session ended in error costs a `claude --resume`, and nothing else.

A session with a task still in progress is not finished, whoever says otherwise. Run the
task-completion flow to the end first — the hook would move that task to `done` on the way out,
which marks unfinished work complete.

## Working with PR failures

`mael gh read-pr` shows merge status, comments, review summaries, and unresolved inline review
threads. Comments older than the most recent push collapse into a count line; `--all-comments`
expands them.

For a failing run, `mael gh check-log <run_id> --failed-only` gives the failing steps, and
`mael gh download-artifact <run_id> <name>` pulls test results, screenshots, and traces.

## Monitoring production

**Sentry.** Prioritise by escalating trend > recency > frequency, then investigate the stacktrace
and fix. Use `mael sentry resolve-issue` only when the issue is confirmed fixed in current code —
for example the reported release pre-dates the fix commit and call-sites now handle the case.
Treat it as a write action and confirm with the user first.

**UptimeRobot.** `mael uptimerobot status` answers "is anything down right now?"; `outages`
investigates recent incidents. Run `monitors` once to discover IDs, then list them under
`uptimerobot.monitors` in `.maelstrom.yaml`. With no monitors configured, commands fall back to
all monitors on the account.

## Development patterns — the wiki

The wiki is a curated set of markdown pages for patterns that apply across projects — tool
choices, publication steps, project setup. It is separate from per-project docs (scoped to
one repo) and from Claude memory (per-project and auto-curated).

**Consult it before you solve a cross-project problem.** Run `mael wiki list` and read any
page whose description matches. **Correct it after.** If the page you used was wrong or
incomplete, update it in the same session. If no page existed, add one.

`mael wiki update` writes the whole page — there is no partial edit. Read the page, change the
text, then write the full body back.

Page paths are free-form, but keep to the convention
`dev-patterns/<language-or-area>/<topic>`, for example
`dev-patterns/python/pypi-publication` or `dev-patterns/ci/github-actions`. Give every page
a one-line `description:` in YAML frontmatter — that line is what `mael wiki list` prints,
so it is how the next agent finds the page:

```markdown
---
description: How to publish a Python package to PyPI
---

# PyPI publication
...
```

Pages live in the same git-backed store as the task notebook (`~/.maelstrom/tasks`), so
every change is committed. The store is local — there is no remote sync.

## Scheduled (template) tasks

The hourly launchd agent that runs scheduled templates is **opt-in per machine**
(`mael schedule install`). It fires hourly and once on load (`RunAtLoad`), with one coalesced
catch-up on wake and **no backfill**.

Nothing wakes a sleeping Mac — launchd starts the missed job on the next wake by itself. `install`
needs no sudo. `uninstall` clears a wake left by the removed `--wake-at`, and prompts for sudo only
on a machine that has one.

Run `mael schedule status` first when a scheduled task didn't fire. See
`docs/dev/scheduled-tasks.md`.

## Prerequisites

- **GitHub CLI:** `brew install gh && gh auth login`
- **Env vars** in `.env`: `LINEAR_API_KEY`, `SENTRY_API_KEY`, `UPTIMEROBOT_API_KEY` (or set under
  `uptimerobot.api_key` in `~/.maelstrom/config.yaml`)
- **Config** in `.maelstrom.yaml`: `linear.team_id`, `sentry.org`, `sentry.project_id`,
  `uptimerobot.monitors`
