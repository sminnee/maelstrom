---
name: code-review
description: Review the working tree's branch changes for standards, security, simplicity, and architecture. Invoked as `/code-review`.
---

# Code review

Review the whole branch as a working tree, split by concern. Judge against the standard, not local habits. Fix correct work in scope. Preserve scope decisions for the user.

1. Run `mael git uncommit-branch`. Record the history ref it prints. This rebase is the sync — `uncommit_branch` fetches origin, fast-forwards main, resolves the base and rebases, so no separate `mael sync` is needed. It refuses three ways: a dirty tree, a rebase or merge in progress, and no commits ahead of the base. On a dirty-tree refusal, check `git for-each-ref refs/mael/history/<branch>/`: a history ref from an earlier run means a previous review already uncommitted this branch, so commit the tree and carry on from step 2. Otherwise stop and show the output.
2. Read [reviewer-prompt.md](reviewer-prompt.md), [prose-reviewer-prompt.md](prose-reviewer-prompt.md), and [review-guide.md](review-guide.md).
3. Spawn one read-only reviewer per concern, in parallel: **design/architecture** (layers 1–2 and 5), **tests** (layer 3), **security & correctness** (layer 4), and **prose**. Layer 5's documentation coverage goes to prose; the rest of it, including the project's `docs/review/coding-standards.md`, goes to design. Give each its concern, its layers, the history ref, and `.drafts/pr.md`. Reviewers read the working tree themselves; do not dump a diff into their prompts.
4. Merge the reports by concern under `Summary`, then each concern's `Design decisions` and `Findings`. Write the summary; retain findings. Deduplicate across concerns, keeping the prose version for general duplicates and the code version for documentation coverage. List clean concerns rather than empty sections.
5. Triage the merged report before editing. Apply correct, in-scope findings as plain edits to the working tree. Discard invalid findings with a reason. Collect scope changes, potential refactors, and prose cuts in untouched files for one user decision after in-scope fixes. `.drafts/pr.md` already exists — the build wrote it. Append deferred scope work under `## Raised by review, not actioned`, and leave the build's own sections intact; never silently drop it.
6. Commit the tree in one `wip: review fixes` commit. Do this even when no finding was applied: the branch is uncommitted from step 1, and until it is committed the whole change exists only as unstaged edits that a stray `git checkout` or `reset --hard` destroys. No history ref covers this — step 1's ref holds the build commits, never review's edits. `/present` re-cuts the commit away, so it costs nothing.
7. Ask the user once about collected scope work. Report applied fixes, discards, deferrals, and the history ref.

`/present` runs next and partitions the reviewed tree into story commits.

This is a universal skill. Keep project rules in project review documents. Do not add resolved-thread tracking, PR comment posting, severity tiers, or project-specific conventions here.
