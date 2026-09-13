---
name: present
description: Re-cut finished build commits into story commits, one per design decision. Invoked as `/present` after `/code-review` and before the PR push.
---

# Present

Re-cut the reviewed tree into readable **story commits**. Preserve the final tree exactly. Do this once per task, after `/code-review`; never do it during Land.

1. Use `mael git status` to find the real base. If `git log --format='%s' "origin/<base>..HEAD"` finds `fixup!`, `squash!`, or `amend!`, stop: the branch is in Land, and present must not re-cut pushed story commits. A clean tree does not prove review ran — it only proves nothing is uncommitted. If `/code-review` was skipped, stop and run it first.
2. Run `mael git uncommit-branch`. Record its history ref. Stop on refusal or conflict. It refuses three ways: a dirty tree, which here means review's fixes were never committed; a rebase or merge in progress; and no commits ahead of the base.
3. Read the full diff. Choose one to eight decisions, ordered so each builds on the last. A decision is one reason, not one file. Put mechanical moves first. The history ref holds both the build commits and the review fixes.
4. Stage and commit one decision at a time. Split a file only by complete hunks. Check `git diff --cached --stat` before every commit.
5. Write a truthful subject and body for each decision.
6. Keep unchanged prior decisions verbatim. Merge a revised decision into one updated story commit.
7. Confirm both `git diff --stat <history-ref>` and `git status --porcelain` are empty. If not, stop and find the missing or dropped decision.
8. Update `.drafts/pr.md`, which review already wrote: keep its decisions and its `## Raised by review, not actioned` section, and bring the overview, diagrams and test seams into line with the story commits. Report the decisions and history ref.

The history ref is the undo and chronological record.

For a new file split by decisions, run `git add -N` before producing a patch. Stage complete hunks only; if a hunk needs another hunk's context, they are one decision. A message explains the decision. In a later chain task, preserve still-valid prior decision messages verbatim and fold revisions into one current decision.
