---
name: code-review
description: Review committed changes on the current branch against project standards, security, simplicity, and architectural fit. Invoked as the `/code-review` slash command. Squashes pending fixups onto origin/main first, then reviews each commit with its own read-only sub-agent. The parent then proposes fixes interactively.
---

# Code Review

Universal code-review skill for maelstrom projects. Reviews the branch commit by commit for
project-standards conformance, security, simplicity, and reuse, and reports findings back to the
user. The parent agent (this skill's top-level section) drives the workflow; one read-only
sub-agent per commit does the actual review against a structured Markdown contract.

**Stateless and one-shot.** Re-invoke `/code-review` after a fix commit lands to re-review — there
is no incremental-review machinery, no resolved-thread tracking, no JSON output.

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

### 4. Spawn one review sub-agent per commit

Read the reviewer prompt from `reviewer-prompt.md` (alongside this file, at
`~/.claude/skills/code-review/reviewer-prompt.md`).

Spawn **one sub-agent per commit**, all in a single message so they run concurrently. Use the
Task tool with `subagent_type: "Explore"` (read-only — matches the brief: no edits, no tests, no
builds; it can run `git log` / `git diff` via Bash).

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

Keep the diff out of the parent's context: sub-agents run their own `git show`, and the parent
never runs `git diff` for the review.

### 5. Merge and display the findings

Each sub-agent returns Markdown for its own commit. Combine them into one report, in commit order
(oldest first):

```
## Summary
<one paragraph covering the branch as a whole: what it does and overall verdict>

## <sha-short> — <commit subject>

### Design decisions worth calling out
### Findings
```

Write the top-level `## Summary` yourself from the per-commit summaries — do not paste each
sub-agent's summary paragraph separately. Preserve every finding verbatim.

Drop a commit's section entirely if it has no findings and nothing to call out; list those
commits as "Reviewed with no findings: `<sha>`, `<sha>`" at the end.

If a sub-agent reports the same issue against more than one commit, keep the earliest commit's
copy and drop the rest.

### 6. Triage the findings

The sub-agents do not rank findings — they report what they found and what it costs to leave.
Sorting them is your job, and it turns on **what the fix would cost**, not on how serious the
issue sounds. This is where *better than you found it, without side quests* gets applied. Put
each finding in one of three buckets:

**Apply it.** The finding is correct and the fix sits inside the work already done — a missed
guard, a wrong name, a duplicated helper, a missing test case. Fix these without asking. That is
what the review is for.

**Raise it with the user.** The fix would materially change scope: a different approach, a new
abstraction, work in code the branch does not touch, or a trade-off with no clearly right answer.
Do not start these. Put the choice to the user (AskUserQuestion or a plain prompt) with the fix
you would make and what it would cost, and wait.

**Potential refactors belong in this bucket — never in the discard bucket.** A review often
surfaces that a larger piece of work would pay off: a seam in the wrong place, a pattern several
commits are working around, an abstraction the codebase has outgrown. The fix is out of scope for
this branch, which makes it tempting to drop. Don't. That judgement is the user's, and these are
the findings a review is uniquely placed to catch — the pattern is only visible from across the
whole change.

Raise each one with what it would cost and what it would buy, and let the user decide whether to
do it now, file it as a follow-up task, or decline. Say plainly that it is out of scope for the
current branch, so the choice is clear.

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

### 7. Commit the fixes

For each finding that was fixed, create one fixup commit targeting the commit the finding was
reported against. Per-commit review means that SHA is always known:

```bash
git add -- <paths relevant to this finding>
git commit --fixup=<sha>
```

Stage only the files relevant to that finding before each fixup so the fixups stay aligned with
their target commits.

If a fix can't be attributed to a single commit in the range (e.g. it spans multiple commits),
fall back to `git commit --fixup=HEAD` and call that out in the report.

Hard rules:

- **Never `--amend`** existing commits.
- **Don't run the autosquash rebase yourself** — leave that to the user (`mael sync --squash` or
  `mael git squash`). Step 1 of the *next* review will pick them up.

### 8. Done

Report what was fixed. The user can re-invoke `/code-review` to re-review if they want — this skill
is stateless.

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

- Incremental-review mode / resolved-thread tracking.
- GitHub PR comment posting or any CI-gate-specific output (JSON contract, `resolve_thread_ids`,
  inline-anchor rules).
- GraphQL thread IDs.
- The autosquash rebase after fixes — the parent creates fixup commits but leaves the rebase to
  the user or to the next review's step 1.
