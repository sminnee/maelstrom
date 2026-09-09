---
name: mael
description: Git, task, PR, environment, Linear, Sentry, and UptimeRobot workflow for maelstrom projects. Load before git work.
---

# Maelstrom workflow

Use `mael --help` for the command surface. Prefer `mael` over raw `git` or `gh`. Network commands need the approved unsandboxed path. Read-only git does not. Mael owns the branch: never create, switch, or check out one.

If network access reports an empty SSH agent, diagnose with `ssh-add -l`; the user must reload it. A recycled branch is normal: `mael sync` rebases onto its real base and `create-pr` can open a new PR after an old one merged. If old or merged commits look wrong, ask rather than making a branch.

## Tasks and planning

`mael task` is the plan of record. A chain shares its parent, branch, and PR. `--follow` and `--follow-end '*'` set order. Planning sessions draft and promote tasks; execute tasks implement their content without a skill. Use `mael task next --run` to advance.

Use `mode: auto` for execute tasks and `mode: normal` for planning. Keep execute tasks' `branch:` unset. For cross-project patterns, run `mael wiki list` before work and update or add the applicable page afterwards.

`$MAEL_TASK_ID` identifies the running task and `$MAEL_TASK_PARENT` identifies the shared chain parent. Linear mirrors status; the notebook holds the plan. Do not write the plan into Linear.

## Build and commit

Build test-first. Stop environments before heavy edits. Run the project gates from its instructions. Working commits may use `wip:`. Use `printf ... | git commit -F -` for commits. Final prefixes are `feat:`, `fix:`, `refactor:`, and `chore:`; include the Linear id when applicable.

Use `--fixup=<sha>` for an earlier decision. Do not amend an earlier commit. Record test-shaping choices in a commit body.

Before a PR, use `mael gh show-code --uncommitted`; use `--committed` for the branch diff. A fixup must target a commit still in the branch range or it will remain an ordinary `fixup!` commit. During a rebase, resolve every hunk by intent because `ours` and `theirs` can reverse across replayed commits.

## Completion

After green gates, run this unattended sequence:

1. Commit the implementation.
2. Run `/present`.
3. Run `/code-review`.
4. Apply correct, in-scope findings as one fixup per finding. Put deferred scope or refactor work in `.drafts/pr.md` under `## Raised by review, not actioned`.
5. Push with `mael gh create-pr <ISSUE-ID> --squash`.
6. Run `mael task status done`.
7. Run `/watch-pr` until CI passes or times out.

Write `.drafts/pr.md` before the push. Use `--progress` for a multi-session PR. Run waits in the background and read their body, not only exit status.

`mael gh read-pr --wait` can report success before substantive CI starts, or hide its exit status through a pipe or background shell. Capture its output and confirm real jobs. `0/0 checks` can mean token permission failure; inspect Actions runs instead.

End a finished session with `mael session end`. Close a task before ending its session.

## Operations

Use `mael gh read-pr` for PR status, `mael gh check-log <run> --failed-only` for failures, and `mael gh download-artifact` for artifacts. Resolve Sentry issues only after confirming the current code fixes them; ask before that write. Run `mael uptimerobot status` for current availability. For scheduled tasks, start with `mael schedule status`.
