---
name: resolve-rebase-conflicts
description: Resolve the conflicts of a rebase that is already in progress, then continue the rebase to its end. Usually started headlessly by `mael sync --autorepair`. Invoked as the `/resolve-rebase-conflicts` slash command.
disable-model-invocation: true
metadata:
  opencode/autoinvoke: false
  opencode/slash: true
---

# Resolve Rebase Conflicts Command

Resolves the conflicts of a rebase that is already in progress, then continues the rebase to its
end. It runs **inside** the conflicted worktree, usually as a headless session that `mael sync
--autorepair` (or the sync that runs when a worktree is opened) started for you.

Adapted from Matt Pocock's MIT-licensed `resolving-merge-conflicts` skill
(https://github.com/mattpocock/skills).

## Usage

```
/resolve-rebase-conflicts
```

## Prerequisites

Load the `mael` skill first if it isn't already — all `mael`/`git` commands need
`dangerouslyDisableSandbox: true`.

## Command Logic

1. **Confirm a rebase is in progress.** Read the state:

   ```bash
   git status
   git rev-parse --git-path rebase-merge
   git rev-parse --git-path rebase-apply
   ```

   If neither path exists, no rebase is in progress. Say so and stop. Do not start one.

2. **Learn what the rebase is doing.** Find the branch, the commit being replayed, and how many
   commits remain:

   ```bash
   git rev-parse --abbrev-ref HEAD
   cat "$(git rev-parse --git-path rebase-merge)/head-name" 2>/dev/null
   git log --oneline -1 REBASE_HEAD
   ```

3. **List the conflicted files:**

   ```bash
   git diff --name-only --diff-filter=U
   ```

4. **Resolve each conflict by intent, not by text.** For every conflicted file:

   - Read the whole file, not only the conflict markers.
   - Find out what **each side wanted**. Read the commit message of the commit being replayed
     (`git log --oneline -1 REBASE_HEAD`, `git show REBASE_HEAD -- <file>`) and the upstream
     commits that touched the same lines (`git log --oneline HEAD -- <file>`). Read the PR if one
     explains the change.
   - **Resolve the two intents.** When both changes are compatible, keep both. When they are not,
     keep the one the newer intent needs, and say why in your final report.
   - **Never invent behaviour.** Do not add code that neither side wrote. Do not delete a side's
     change because it is easier to.
   - Remove every conflict marker (`<<<<<<<`, `=======`, `>>>>>>>`).

5. **Stage and continue** through every remaining commit:

   ```bash
   git add <resolved files>
   GIT_EDITOR=true git rebase --continue
   ```

   A rebase replays commits one at a time, so a later commit can conflict too. Repeat steps 3–5
   until `git status` reports no rebase in progress.

6. **Run the project's checks** on the result — the test and lint commands in `CLAUDE.md`. A
   resolution that breaks the build is not a resolution. Fix what you broke, `git add` the fix, and
   amend it into the commit being replayed (`git commit --amend --no-edit`) if the rebase is still
   running.

7. **Finish on the original branch.** Confirm the branch from step 2 is checked out:

   ```bash
   git rev-parse --abbrev-ref HEAD
   ```

8. **Report** what conflicted, how you resolved each one, and any resolution you were unsure about.

## Notes

- **Never run `git rebase --abort`.** Aborting throws away the work this session exists to do. The
  caller aborts if you fail; that is its decision, not yours.
- **Do not push.** The caller completes the sync and pushes the branch.
- **Do not switch branches** and do not create one.
- If you cannot resolve a conflict — the intents genuinely contradict and no reading of the
  history settles it — stop, leave the rebase as it is, and report why. A blocked launch that a
  human fixes beats a wrong merge that lands.
- All `mael`/`git` commands need `dangerouslyDisableSandbox: true`.
