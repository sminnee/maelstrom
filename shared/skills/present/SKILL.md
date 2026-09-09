---
name: present
description: Re-cut finished build commits into story commits, one per design decision. Invoked as `/present` after green build and before `/code-review`.
---

# Present

Re-cut chronological build history into reviewable **story commits**. Preserve the final tree exactly. Do this once per task; never do it during Land.

1. Use `mael git status` to find the real base. If `git log --format='%s' "origin/<base>..HEAD"` finds `fixup!`, `squash!`, or `amend!`, stop: review has started.
2. Run `mael git uncommit-branch`. Record its history ref. Stop on refusal or conflict.
3. Read the full diff. Choose one to eight decisions, ordered so each builds on the last. A decision is one reason, not one file. Put mechanical moves first.
4. Stage and commit one decision at a time. Split a file only by complete hunks. Check `git diff --cached --stat` before every commit.
5. Write a truthful subject and body for each decision. Add `Review: read` for logic and `Review: scan` only for mechanical diffs.
6. Keep unchanged prior decisions verbatim. Merge a revised decision into one updated story commit.
7. Confirm both `git diff --stat <history-ref>` and `git status --porcelain` are empty. If not, stop and find the missing or dropped decision.
8. Write `.drafts/pr.md` with the overview, useful diagrams, test seams, and unplanned test work. Report the decisions and history ref.

The history ref is the undo and chronological record.

For a new file split by decisions, run `git add -N` before producing a patch. Stage complete hunks only; if a hunk needs another hunk's context, they are one decision. A message explains the decision and ends with `Review: read`; use `Review: scan` only when a reviewer can verify a mechanical diff without reading its logic. In a later chain task, preserve still-valid prior decision messages verbatim and fold revisions into one current decision.
