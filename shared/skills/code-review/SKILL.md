---
name: code-review
description: Review the working tree's branch changes for standards, security, simplicity, and architecture. Invoked as `/code-review`.
---

# Code review

Review the whole branch as a working tree, split by concern. Judge against the standard, not local habits. Fix correct work in scope. Preserve scope decisions for the user.

1. Choose the scope. Run `mael gh has-pr --open`. Exit 0 and no request for substantial rework means an **additive** pass: review only the commits that were never pushed. Any other exit code, or a request for substantial rework, means a **fresh** pass over the whole branch. Exit 2 means the check could not run — treat it as fresh, never as "no PR". Do this before step 2: afterwards the branch no longer resembles what the PR holds.
2. Run `mael git squash-branch`, adding `--local` for an additive pass. Record the history ref it prints. This rebase is the sync — it fetches origin, fast-forwards main, resolves the base and rebases, so no separate `mael sync` is needed. It refuses on a dirty tree, a rebase or merge in progress, and no commits ahead of the base; `--local` also refuses when every commit is already pushed, or when the unpushed commits hold a `fixup!` aimed at a pushed one. On a dirty-tree refusal, check whether the uncommitted changes are your own: if they are, commit them and retry; if they are not, stop and ask the user. Otherwise stop and show the output.
3. Read [reviewer-prompt.md](reviewer-prompt.md), [prose-reviewer-prompt.md](prose-reviewer-prompt.md), and [review-guide.md](review-guide.md).
4. Spawn one read-only reviewer per concern, in parallel: **code** (layers 1, 2, 4, and 5), **tests** (layer 3), and **prose**. Layer 5's documentation coverage goes to prose; the rest of it, including the project's `docs/review/coding-standards.md`, goes to code. Give each its concern, its layers, the history ref, and `.drafts/pr.md`. On an additive pass, tell each reviewer to review the squashed commit alone, not the whole branch. Reviewers read the working tree themselves; do not dump a diff into their prompts.
5. Merge the reports by concern under `Summary`, then each concern's `Design decisions` and `Findings`. Write the summary; retain findings. Deduplicate across concerns, keeping the prose version for general duplicates and the code version for documentation coverage. List clean concerns rather than empty sections.
6. Triage the merged report before editing. Apply correct, in-scope findings as plain edits to the working tree. Discard invalid findings with a reason. Collect scope changes, potential refactors, and prose cuts in untouched files for one user decision after in-scope fixes. `.drafts/pr.md` already exists — the build wrote it. Append deferred scope work under `## Raised by review, not actioned`, and leave the build's own sections intact; never silently drop it.
7. Commit any fixes you applied as `fixup!` commits on the squashed commit, or as one `wip: review fixes` commit. Skip this when no finding was applied: the branch is already committed from step 2, so there is nothing at risk. `/present` re-cuts these away.
8. Ask the user once about collected scope work. Report applied fixes, discards, deferrals, and the history ref.

`/present` runs next and partitions the reviewed tree into story commits.

This is a universal skill. Keep project rules in project review documents. Do not add resolved-thread tracking, PR comment posting, severity tiers, or project-specific conventions here.
