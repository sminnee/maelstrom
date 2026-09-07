---
name: present
description: Re-cut a finished branch's chronological commits into story commits, one per design decision. Invoked as the `/present` slash command. Runs once per task, after the build is green and before `/code-review`, never during Land.
---

# Present

The build's commits are the order the work happened in. The reviewer needs the order the work
_reads_ in. This pass squashes the journey and re-cuts the same final diff into **story commits**:
one per design decision, ordered so each reads on top of the last. **The invariant is the tree, not
the story.** A wrong partition gives a wrong story, never a wrong tree — the final tree must equal
the tree you started from, and the working history is the undo.

Run this once, at the end of Build, before `/code-review`. Land never presents.

## 1. Guard

Look for a fixup in the branch's own commits. `mael git status` names the base — the branch is
stacked, so `main` is not always it:

```bash
mael git status                      # "N ahead of <base>"
git log --format='%s' "origin/<base>..HEAD" | grep -E '^(fixup|squash|amend)!'
```

Any hit means review or Land has already started on this branch. Stop and say so.

Read the range against the real base. On a stacked branch `origin/main..HEAD` spans the parent's
commits too, so a fixup belonging to the parent stops a branch that is clean.

The clean-tree and commits-ahead checks belong to `mael git uncommit-branch` in the next step.

## 2. Uncommit

```bash
mael git uncommit-branch
```

It rebases onto the base, keeps the chronological commits under
`refs/mael/history/<branch>/<stamp>`, and leaves every change unstaged at the fork point.

**Record the history ref it prints.** It is the undo (`git reset --hard <ref>`) and step 8's
reference.

If it refuses or reports a conflict, stop and say why. It refuses on a dirty tree, a rebase or
merge in progress, or a branch with no commits ahead of its base, and it changes nothing when it
does. Fix the cause it names — a conflict wants `mael sync` — and run it again. Raw git is not a
way around it: the base it rebases onto is stack-aware, and hand-rolling that is how a stacked
branch loses its base.

## 3. Choose the decisions

Read the whole diff and pick **three to eight** decisions. A decision is one _why_, not one file:
the change to the port allocator and the test that pins it are one decision, and two unrelated
edits in one file are two.

Order them so each reads on top of the previous. Mechanical work — renames, moves, generated code
— is its own commit and usually goes first, so the decisions that follow read against the new
names.

One decision is one commit. A branch with one real decision gets one story commit; padding it to
three tells the reviewer a lie about where the thinking was. More than eight is a sign the PR is
too big, not a reason to raise the review cap.

## 4. Stage per decision

Stage one decision, commit it, then stage the next. File-level is the common case:

```bash
git add -- src/maelstrom/ports.py tests/test_ports.py
```

Hunk-level, where one file carries two decisions:

```bash
git add -N -- <path>                                  # a new file only
git diff HEAD -- <path> > "$TMPDIR/decision.patch"
# delete the unwanted whole hunks from that file
git apply --cached "$TMPDIR/decision.patch"
```

`git diff HEAD` does not see an untracked file at all, so a **new** file splitting across two
decisions needs `git add -N` first. Without it the patch comes back empty.

Cut **whole hunks** — from the `@@` line to the line before the next `@@` — out of git's own
output. Git wrote the line counts and the context, so what is left applies. Write the patch body
yourself and the counts stop matching the body, which is how a staged hunk silently loses a line.

A hunk that will not apply on its own, because a hunk already staged changed its context, means the
two belong to one decision. Fold them into one commit.

Check what you staged before each commit: `git diff --cached --stat`.

## 5. Message shape and depth

```
feat: store the base tip per branch [PROJ-12]

Why this decision, what it replaces, what was rejected. Mermaid allowed.

Review: read
```

Prefix and Linear id follow the project's usual rule. The body states the **decision** — the
reviewer judges it against the diff, so a body that oversells or misdescribes its own diff is worse
than no body.

The trailer says how deep to read:

| Trailer | Means |
| --- | --- |
| `Review: read` | Read every line. The logic is the risk. |
| `Review: scan` | Check the diff is what the subject says and nothing else hides in it. |

`scan` is for mechanical work — 800 lines of rename, a move, generated output. Choose it only when
a reviewer can confirm the diff matches the subject without reading the logic. A commit with no
trailer reads as `read`.

## 6. A later task in the chain

A chain shares one branch, so a later task's present covers every decision on it, not only the new
ones.

Keep a prior decision's message **verbatim** when the decision still stands — the same subject,
body and trailer, so the reviewer sees an unchanged commit. Where the new task revised a decision,
the result is **one** commit stating the decision as it now is. Never "decision" followed by
"revise decision": the journey is what present exists to remove.

## 7. Final check

```bash
git diff --stat <history-ref>
git status --porcelain
```

Both must print nothing. That is the invariant: same tree, different story.

If either prints, name the differing paths and stop. Do not commit the remainder to make it empty —
a leftover hunk means a decision is missing or a patch dropped a line, and the answer is to find
which.

## 8. Write the PR draft

Write `.drafts/pr.md`:

- a short overview of what the branch does;
- a diagram for a `read` decision where a picture beats a paragraph;
- test notes: the seams, and anything the plan did not name.

`mael gh create-pr` reads this file, puts it on the PR and deletes it. `/code-review` appends a
`## Raised by review, not actioned` section later when it has one, so leave that heading out.

## 9. Report

Give the user:

- each decision, with its subject and `git show --stat` line;
- the history ref, as the undo and the journey (`git log <ref>`).
