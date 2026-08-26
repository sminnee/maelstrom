---
name: code-review
description: Review committed changes on the current branch against project standards, security, simplicity, and architectural fit. Invoked as the `/code-review` slash command. Squashes pending fixups onto origin/main first, then reviews up to 8 not-yet-reviewed commits, oldest first, with one read-only sub-agent per commit, skipping commits already tagged as reviewed and deferring the rest to the next run. A further sub-agent reviews the whole branch's prose. The parent then proposes fixes interactively.
---

# Code Review

Universal code-review skill for maelstrom projects. Reviews the branch commit by commit for
project-standards conformance, security, simplicity, and reuse, and reports findings back to the
user. The parent agent (this skill's top-level section) drives the workflow; read-only sub-agents
do the actual review against a structured Markdown contract.

**Two kinds of reviewer run together.** One per commit reviews the code. One more reviews the
whole branch's prose — comments, docstrings and documents.

**Reviewed commits are skipped.** A commit that has been through review carries a `reviewed` git
note, and a later run passes over it. This is the only state the skill keeps: there is no
resolved-thread tracking and no JSON output. Naming an explicit SHA or range bypasses the skip
and forces a fresh review.

**One run reviews at most 8 commits**, oldest first. It reports the rest as deferred, and a later
run picks them up. The cap has no bypass — it applies to an explicit SHA or range too.

## The goal

**Leave the codebase better than you found it, without getting lost in side quests.**

Both halves carry weight.

*Better than you found it* — a review that changes nothing has usually failed. If a finding is
real and the fix belongs in this work, make it. Don't wave things through because the branch is
nearly done, and don't downgrade a genuine problem to a comment to avoid the work.

Most of all, don't let the existing code set the bar. Reading a module and absorbing its habits
is how a review starts certifying decay instead of catching it: the swallowed exception becomes
house style, the untested branch becomes normal. Judge against the standard, not the neighbours.
Broken windows are how a codebase gets worse one reasonable-looking review at a time.

*Without side quests* — the branch has a job, and the review serves that job. A finding is not a
licence to refactor the module it points at, tidy the surrounding code, or chase a problem into
files this branch never touched. When the fix is bigger than the finding, that is a decision for
the user, not a detour to take mid-review.

The triage in step 6 is how you hold both: apply what improves the code within the work already
done, raise what would grow it, discard what doesn't apply. When a finding sits on the line, ask
which side of this goal it serves — that question resolves most cases faster than any rule.

## Parent-agent section (runs on `/code-review`)

This is what runs when the user types `/code-review`. Follow these steps in order.

### 1. Squash pending fixups onto origin/main

Run:

```bash
mael git squash
```

This rebases the branch onto `origin/main` and autosquashes any `fixup!` commits, so the review
sees the commits as they will land rather than a history littered with fixups. It autostashes
uncommitted work safely, so a dirty worktree is fine.

If it exits non-zero, print its output to the user and stop — **do not spawn any sub-agent**. A
non-zero exit means rebase conflicts, and the command has already printed the resolution steps.

Skip this step when `$ARGUMENTS` names an explicit SHA or range: the user asked to review
specific history, and rebasing would move it underneath them.

### 2. Resolve the range

`$ARGUMENTS` is the user's argument string (may be empty).

- Empty → `origin/main..HEAD`
- A bare SHA (7–40 hex chars) → `<sha>^..<sha>`
- Anything else → use verbatim as a git range

### 3. List the commits to review

```bash
git log --reverse --format='%h %s' <range>
```

Each line is one commit to review, oldest first: short SHA, then subject. This is both the
work list and the branch context you pass to every sub-agent, so run it once and reuse it.

If the output is empty, tell the user there are no commits to review and stop — **do not spawn
any sub-agent**. If the command fails, the range is invalid: print the git error and stop.

### 3b. Drop the commits that were already reviewed

For each commit from step 3:

```bash
git notes show <sha> 2>/dev/null | grep -qx 'reviewed'
```

Skip the commit when that matches. Review it otherwise — a non-zero exit means no note, which is
the normal case. Match the whole line (`grep -qx`) so an unrelated note on `refs/notes/commits`
cannot be read as a pass.

Report every commit you skip:

```
Already reviewed: <sha> <subject>
```

Always report this. A note outlives a change to the commit it sits on (see step 7b), so a skipped
commit may have changed since it was reviewed, and this report is the only place that shows.

If every commit is skipped, say so and **spawn no commit sub-agent**. Give the user the command
that reviews them again: `/code-review origin/main..HEAD`. Then go to step 4 for the prose agent
and carry on: prose carries no `reviewed` note, so a branch whose commits are all tagged has
still never had its prose swept. Skip to step 5 once it returns.

Skip this whole step when `$ARGUMENTS` names an explicit SHA or range, exactly as step 1 does: a
user who names a commit is asking for it to be reviewed. This is also the manual override when a
commit needs reviewing again.

Keep the full commit list from step 3 for the sub-agent prompts. The skip decides what to review,
not what a reviewer is told about the branch.

### 3c. Cap the run at 8 commits

Take the **oldest 8** of the commits that remain after step 3b. The list from step 3 is already
`--reverse`, so this is its first 8 entries. A larger fan-out spawns too many concurrent
sub-agents and returns a report too big to triage well.

Report every commit you defer, one line each, and close the list with the re-run instruction:

```
Deferred to the next run: <sha> <subject>
Run /code-review again once these fixups are squashed to review them.
```

Report it now, and repeat it in the final report (step 8).

This needs no new state. The commits reviewed in this run get `reviewed` notes in step 7 (on their
fixups) and step 7b (on the commits that reviewed clean), so the next run's step 3b skips them and
the cap lands on the next 8.

**The cap applies on every run, including an explicit SHA or range.** Steps 1 and 3b are both
bypassed when `$ARGUMENTS` names a SHA or range. This step is not — there is no bypass. A user who
wants commits 9 and later of a named range runs the command again, or names a narrower range.

### 4. Spawn the review sub-agents

Read both prompts alongside this file: `reviewer-prompt.md` for the commit reviewers, and
`prose-reviewer-prompt.md` for the prose reviewer.

Spawn **one sub-agent per commit, plus one prose sub-agent**, all in a single message so they run
concurrently. Use the Agent tool with `subagent_type: "Explore"` for every one (read-only —
matches the brief: no edits, no tests, no builds; it can run `git log` / `git diff` via Bash).

Each sub-agent's prompt is the contents of `reviewer-prompt.md`, followed by that commit's
assignment:

```
Review commit: <sha>
Branch range: <range>
Commits in this branch, oldest first:
  <sha1> <subject1>
  <sha2> <subject2>
  ...
```

Include the full branch commit list in **every** sub-agent's prompt. A reviewer looking at one
commit needs to know what comes after it — see the note on later commits in `reviewer-prompt.md`.

The prose sub-agent's prompt is the contents of `prose-reviewer-prompt.md`, followed by the same
branch context with **no commit assignment** — its unit of work is the branch:

```
Branch range: <range>
Commits in this branch, oldest first:
  <sha1> <subject1>
  <sha2> <subject2>
  ...
```

Give it the range from step 2, not the capped list from step 3c. The 8-commit cap exists to bound
the fan-out and the report; the prose agent is one agent reading one diff, and a range trimmed to
8 commits would hide prose the branch actually ships.

**Skip the prose agent only when the branch changes no Markdown.** Check first:

```bash
git diff --name-only <range> -- '*.md'
```

Spawn the agent when that prints anything. Skip it when the output is empty, and say so in the
final report — a branch with no prose findings must not read the same as a branch never checked.

Markdown alone decides this, for two reasons. A comment change hides inside a source diff that
`--name-only` cannot see, and reading the diff to find one would pull it into the parent's
context. And any list of source extensions would be a guess about which languages a project
uses, which this skill does not make. A source-only branch therefore still spawns the agent when
it touches any Markdown, and a branch that only reworded comments costs one agent that finds
little. Both are cheaper than a guard the parent cannot follow.

Keep the diff out of the parent's context: sub-agents run their own `git show` and `git diff`,
and the parent never runs the review diff itself. The `--name-only` guard above is the one
exception, and it prints no content.

### 5. Merge and display the findings

Each sub-agent returns Markdown for its own commit. Combine them into one report, in commit order
(oldest first):

```
## Summary
<one paragraph covering the branch as a whole: what it does and overall verdict>

## <sha-short> — <commit subject>

### Design decisions worth calling out
### Findings

## Prose — whole branch

### Design decisions worth calling out
### Findings
```

The prose section goes last, after every commit section. Its findings belong to the branch, not
to any one commit.

Write the top-level `## Summary` yourself from the sub-agents' summaries — do not paste each one
separately. Preserve every finding verbatim.

Drop a commit's section entirely if it has no findings and nothing to call out; list those
commits as "Reviewed with no findings: `<sha>`, `<sha>`" at the end. Drop the prose section the
same way, and say whether it reviewed clean or was skipped by step 4's guard.

If a sub-agent reports the same issue against more than one commit, keep the earliest commit's
copy and drop the rest. Where a commit reviewer and the prose reviewer report the same line,
**keep the prose reviewer's copy** — it has the cross-file view, so its version of the finding is
the one that names every site.

**Documentation coverage is the exception: keep the commit reviewer's copy.** There the
branch-wide view is the weaker one. A flag added in one commit and documented in another reads
as covered from the branch, while the commit reviewer correctly reports it against the commit
that added it. The prose reviewer's coverage findings only ever add to the report.

### 6. Triage the findings

The sub-agents do not rank findings — they report what they found and what it costs to leave.
Sorting them is your job, and it turns on **what the fix would cost**, not on how serious the
issue sounds. This is where *better than you found it, without side quests* gets applied.

Triage is **one pass over the whole merged report**. Bucket every finding, for every commit,
before you make any fix. Judgement that spans commits depends on it: the duplicate-finding rule in
step 5, and the "a later commit already handles it" discard ground below. The output of this step
is a per-commit list of fixes to apply — the work list step 7 walks.

Put each finding in one of three buckets:

**Apply it.** The finding is correct and the fix sits inside the work already done — a missed
guard, a wrong name, a duplicated helper, a missing test case. Fix these without asking. That is
what the review is for.

**Raise it with the user.** The fix would materially change scope: a different approach, a new
abstraction, work in code the branch does not touch, or a trade-off with no clearly right answer.
Do not start these, and do not raise them here — collect them, finish the fix loop in step 7, then
put them all to the user together in step 7c. That is one interruption for the run instead of one
per commit.

**A prose cut in a file the branch never touched belongs in this bucket.** The prose reviewer
sweeps the whole repo for duplication, so it will name copies in files this branch had no
business opening. Cutting one is the right call and often the whole point — but it is the user's
call, not a silent edit made under cover of a review. Carry it to step 7c with the survivor named
and the word count the cut would save, and let the user take it now or file it.

**Potential refactors belong in this bucket — never in the discard bucket.** A review often
surfaces that a larger piece of work would pay off: a seam in the wrong place, a pattern several
commits are working around, an abstraction the codebase has outgrown. The fix is out of scope for
this branch, which makes it tempting to drop. Don't. That judgement is the user's, and these are
the findings a review is uniquely placed to catch — the pattern is only visible from across the
whole change.

Record each one with what it would cost and what it would buy. Step 7c puts them to the user, who
decides whether to do it now, file it as a follow-up task, or decline.

**Discard it.** The finding does not apply — the reviewer missed context, the pattern is
deliberate, a later commit already handles it, or it is listed as an anti-smell. Drop it. Do not
raise a finding with the user just because a sub-agent produced it.

**"The surrounding code already does this" is not grounds for discarding.** It is the most
common way a review quietly lowers the bar: the finding is real, the module is full of the same
problem, and matching it looks like consistency. Existing code shows what the project has done,
not what it should do. If the finding is right, either fix it (when the fix is in scope) or
raise it as a potential refactor (when the decay is wider than this commit). Discard it only if
it is *wrong*, not merely inconvenient — and if the pattern is deliberate, say so explicitly
rather than assuming it from its frequency.

Report the split briefly: what you applied, what you are asking about, what you discarded and
why. If a discard was a close call, say so — it may belong in the project's *Anti-smells*
section so the next review does not repeat it.

### 7. Fix one commit at a time

Walk the work list from step 6 **one commit at a time, oldest first**. Complete each commit fully
before you start the next. Two nested loops:

For each commit that has fixes, oldest first:

- For each of that commit's findings, one at a time:
  1. Make the code edits for that finding only. Where the finding carries `Replace with:`, apply
     that text as written — the reviewer wrote it holding context you no longer have. Rewrite it
     only when applying it verbatim would be wrong, and say so in the report.
  2. Stage them: `git add -- <paths relevant to this finding>`
  3. Commit them: `git commit --fixup=<sha>`
  4. Tag the fixup: `git notes add -f -m "reviewed" <fixup-sha>` (see step 7b for what that note
     does).

The inner loop keeps the rule of **one fixup per finding**. The outer loop keeps each fixup on the
commit it targets.

Do not let another commit's fixes sit unstaged in the worktree while you commit this one. That is
how a fixup picks up the wrong hunks: `git add -- <paths>` stages whatever is currently in those
paths, not only the change you meant. End each commit's turn with a clean staging area, and every
fixup stays aimed at the right target.

If a fix can't be attributed to a single commit in the range (e.g. it spans multiple commits),
fall back to `git commit --fixup=HEAD` and call that out in the report. Do **not** tag that
commit — see step 7b.

Prose fixes attribute the same way, by which commits the fix touches:

| The fix touches | How to commit it |
|---|---|
| Prose in files one commit touched | `git commit --fixup=<sha>`, as above |
| One cut spanning several commits | `git commit --fixup=HEAD`, left untagged |

A duplication fix usually lands in the second row: the survivor sits in one file and the cuts in
others, and no single commit owns the change. A cut in a file the branch never touched reaches
neither row — step 6 routed it to step 7c, and it stays unfixed until the user says otherwise.

Hard rules:

- **Never `--amend`** existing commits.
- **Don't run the autosquash rebase yourself** — leave that to the user (`mael sync --squash` or
  `mael git squash`). Step 1 of the *next* review will pick them up.

### 7b. Tag the remaining commits

Step 7 already tagged each fixup as it was made. Two kinds of tagging are left.

Tag every commit that reviewed clean, as it stands:

```bash
git notes add -f -m "reviewed" <sha>
```

`-f` is mandatory. Without it git concatenates, and the notes pile up.

A commit that had findings is **not** tagged itself — its fixup carries the tag, and the note
rides onto the squashed result, so the fixed commit is not reviewed again next run. If the fixup
is never made, the commit stays untagged and comes back for review next run, which is what you
want.

Do **not** tag a `--fixup=HEAD` fallback commit (the spanning-multiple-commits case in step 7). It
is not attributable to one commit, so let the commits it touches be reviewed again.

**The prose review tags nothing, and that is deliberate.** The `reviewed` note marks a commit, and
prose duplication is a property of the tree — a phrase copied into a file the branch never opened
belongs to no commit. So the prose agent runs on **every** invocation, over the branch's current
state, and step 3b never skips it. Do not look for a prose note; none is ever written.

On an explicit-SHA or range run, where step 3b was bypassed and a tagged commit was reviewed
anyway, remove the note if that commit is found wanting. A stale tag must not linger on a commit
now known to have findings:

```bash
git notes remove --ignore-missing <sha>
```

Notes are local: sibling worktrees share them through the common git dir, and nothing pushes them
to origin. They need `notes.rewriteRef`, which `mael doctor` sets — without it a rebase drops
every note and no commit is ever skipped. `refs/notes/commits` is git's default display ref, so
`reviewed` appears in `git log` and `git show` with no flag.

### 7c. Raise the scope questions

Put the findings you collected in step 6's **"Raise it with the user"** bucket to the user now, all
together (AskUserQuestion or a plain prompt). Give each one the fix you would make and what it
would cost. The fixes are already committed, so the user answers once, with the in-scope work done.

Potential refactors belong here. Say plainly that each is out of scope for the current branch, so
the choice — do it now, file it as a follow-up task, or decline — is clear. **Never drop one.**

Prose cuts in untouched files belong here too. Give each one the survivor, the sites to cut, and
the words it would save, so the user is choosing between concrete options rather than approving
an unbounded edit.

### 8. Done

Report:

- what was fixed;
- which commits you skipped as already reviewed (step 3b);
- which commits you deferred to the next run (step 3c);
- whether the prose reviewer ran, and if it was skipped, that the branch changed no prose
  (step 4).

The user can re-invoke `/code-review` with an explicit SHA or range to force a fresh review of a
commit. A plain `/code-review` picks up the deferred commits once the fixups are squashed.

## Do NOT bake project-specific rules into this skill

This is the universal review skill. Cross-project rules that hold everywhere go in
`review-guide.md` (alongside this file). Project-specific rules belong in that project's
`docs/review/coding-standards.md` / `docs/review/review-guide.md` — `reviewer-prompt.md` tells the
sub-agent to load all three. The following are examples of things that **must not** appear in this
file, `reviewer-prompt.md`, or `review-guide.md`:

- `Q()` / `%s` SQL placeholders or any other framework-specific API.
- No-Tailwind, project-CSS-utilities, or any other UI-framework rule.
- NZ English / locale-specific copy rules.
- `SystemModel` / `AppModel` / handler-vs-model architectural splits.
- `unittest`-vs-`pytest` test framework preferences.
- File-type→skill mappings beyond the generic "load skills matching diff file types".
- Severity tiers of any kind. Findings are not ranked blocking/advisory — step 6 triages them by
  what the fix costs, with the user.

Also out of scope:

- Resolved-thread tracking.
- GitHub PR comment posting or any CI-gate-specific output (JSON contract, `resolve_thread_ids`,
  inline-anchor rules).
- GraphQL thread IDs.
- The autosquash rebase after fixes — the parent creates fixup commits but leaves the rebase to
  the user or to the next review's step 1.
