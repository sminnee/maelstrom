---
name: code-review
description: Review committed current-branch changes for standards, security, simplicity, and architecture. Invoked as `/code-review`.
---

# Code review

Review up to eight commits, oldest first, plus branch prose when Markdown changed. Judge against the standard, not local habits. Fix correct work in scope. Preserve scope decisions for the user.

1. Unless `$ARGUMENTS` names a SHA or range, run `mael sync --squash --no-push`. If it fails, show the output and stop without reviewers. Use `origin/main..HEAD` for no argument, `<sha>^..<sha>` for a bare SHA, and any other argument as the range. Print an invalid-range error and stop.
2. List the complete range with `git log --reverse --format='%h %s' <range>`. If it is empty, report that and stop. For a non-explicit range, skip a commit only when `git notes show <sha> | grep -qx reviewed` succeeds. Report every skipped commit. An explicit range always re-reviews its commits.
3. Select the oldest eight unskipped commits. Report every deferred commit and tell the user to run `/code-review` again after fixups squash. The eight-commit cap also applies to explicit ranges. If every commit was skipped, run only the prose review.
4. Read [reviewer-prompt.md](reviewer-prompt.md), [prose-reviewer-prompt.md](prose-reviewer-prompt.md), and [review-guide.md](review-guide.md). Spawn one read-only reviewer for each selected commit in parallel. Give every reviewer the full commit list, range, assignment, and its `Review:` depth; only `scan` means scan. Spawn one prose reviewer for the full range when `git diff --name-only <range> -- '*.md'` prints a path. Otherwise report that prose was skipped.
5. Merge reports in commit order under `Summary`, each commit's `Design decisions` and `Findings`, then a branch-wide prose section. Write the summary; retain findings. Deduplicate by keeping the earliest commit finding, except keep the prose version for general duplicates and the commit version for documentation coverage. List clean commits rather than empty sections.
6. Triage the merged report before editing. Apply correct, in-scope findings. Discard invalid findings with a reason. Collect scope changes, potential refactors, and prose cuts in untouched files for one user decision after in-scope fixes. Put deferred scope work in `.drafts/pr.md` under `## Raised by review, not actioned`; never silently drop it.
7. Fix one finding at a time, by target commit oldest first. Stage only its paths and commit one `git commit --fixup=<sha>` per finding. Add a `reviewed` note to each fixup. For a fix spanning commits, use `git commit --fixup=HEAD` and leave it untagged. Add a `reviewed` note to each clean commit. Do not amend or autosquash. On an explicit re-review that finds a tagged commit faulty, remove its note.
8. Ask the user once about collected scope work. Report applied fixes, skips, deferrals, prose status, and the available explicit-range re-review command.

This is a universal skill. Keep project rules in project review documents. Do not add resolved-thread tracking, PR comment posting, severity tiers, or project-specific conventions here.
