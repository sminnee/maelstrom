---
name: present
description: Re-cut finished build commits into story commits, one per design decision. Invoked as `/present` after `/code-review` and before the PR push.
---

# Present

Re-cut the reviewed tree into readable **story commits**. Preserve the final tree exactly. Do this once per task, after `/code-review`; never do it during Land.

Story commits do not each need to pass the gates. Only the final tree must pass, and it is the reviewed tree. Do not merge two decisions to make an intermediate commit green.

1. Use `mael git status` to find the real base. If `git log --format='%s' "origin/<base>..HEAD"` finds `fixup!`, `squash!`, or `amend!`, stop: the branch is in Land, and present must not re-cut pushed story commits. A clean tree does not prove review ran — it only proves nothing is uncommitted. If `/code-review` was skipped, stop and run it first.
2. Run `mael git uncommit-branch`, with the same scope `/code-review` used — `--local` after an additive pass, so present re-cuts only the new work and leaves the pushed story commits alone. Record its history ref. Stop on refusal or conflict. It refuses three ways: a dirty tree, which here means review's fixes were never committed; a rebase or merge in progress; and no commits ahead of the base.
3. Read the reasons before you choose: the task content (`mael task show "$MAEL_TASK_ID"`), the build commit messages, and the review decisions in `.drafts/pr.md`. The build commits are under the history ref `/code-review` printed, which is the second-newest ref under `refs/mael/history/<branch>/`: run `git log "origin/<base>..<review-history-ref>"`. Then read the full diff to see where each reason landed.
   Choose three to ten decisions, ordered so each builds on the last. A decision is one reason, not one file. Put mechanical moves first. Use fewer only when the branch really has fewer reasons, and say so in the report.
4. Stage and commit one decision at a time. Stage complete hunks: write them from `git diff -- <path>` to a patch and run `git apply --cached`. For a new file, run `git add -N` first. Check `git diff --cached --stat` before every commit.
   When one file holds two decisions and its hunks do not separate, write the earlier decision's version of the file, stage and commit it, then restore the final content with `git restore --source=<history-ref> --worktree -- <path>`. When the final tree deletes the file, `git rm` it in the later decision instead.
   When a staged commit changes more than about 250 lines, check whether it holds more than one reason. Split it, or state in the body why it is one decision. A widespread mechanical change, such as a rename, can be large and still be one decision.
5. Write a truthful subject and body for each decision.
6. Keep unchanged prior decisions verbatim. Merge a revised decision into one updated story commit.
7. Confirm both `git diff --stat <history-ref>` and `git status --porcelain` are empty. If not, stop and find the missing or dropped decision.
8. Update `.drafts/pr.md`, which review already wrote: keep its decisions and its `## Raised by review, not actioned` section, and bring the overview, diagrams and test seams into line with the story commits. Report the decisions and history ref.

Write `<milestone>presented</milestone>` when the story commits are cut.

The history ref is the undo and chronological record.
