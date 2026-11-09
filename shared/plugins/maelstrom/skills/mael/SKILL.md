---
name: mael
description: Git, task, PR, dev environment, Linear, Sentry, and UptimeRobot workflow for maelstrom projects. Load before git work or dev environment work.
---

# Maelstrom workflow

Use `mael --help` for the command surface. Prefer `mael` over raw `git` or `gh`. Network commands need the approved unsandboxed path. Read-only git does not. Mael owns the branch: never create, switch, or check out one.

If network access reports an empty SSH agent, diagnose with `ssh-add -l`; the user must reload it. A recycled branch is normal: `mael sync` rebases onto its real base and `create-pr` can open a new PR after an old one merged. If old or merged commits look wrong, ask rather than making a branch.

## Reference files

- Read [dev-env.md](dev-env.md) before you add or change a service, a port, a `.env` value, or a dev URL.
- Read [jig.md](jig.md) before you add the jig to a project, or before you turn a confirmed Monkeypatch into source.
- [verification.md](verification.md) is read from step 2 of "Completion".

## Tasks and planning

`mael task` is the plan of record. A chain shares its parent, branch, and PR. `--follow` and `--follow-end '*'` set order. Planning sessions draft and promote tasks; each task plans its own content in plan mode, then builds it. Use `mael task next --run` to advance.

When a plan-mode task's plan finds more than one session's work, splice the extra tasks in as `planning`'s task-chains.md says.

When the work touches another project, follow the "Cross-project work" section of `planning`'s task-chains.md.

Use `mode: plan` for tasks and `mode: normal` for a `plan-next-step` tail. For cross-project patterns, run `mael wiki list` before work and update or add the applicable page afterwards.

`$MAEL_TASK_ID` identifies the running task and `$MAEL_TASK_PARENT` identifies the shared chain parent. Linear mirrors status; the notebook holds the plan. Do not write the plan into Linear.

## Build and commit

Build as `design-build-test` says, if the skill is available. Stop environments before heavy edits. Run the project gates from its instructions. Commit in small steps. Give each build commit a short body that says why the change was made: `/present` reads these bodies to find the design decisions. Working commits may use `wip:`. Do not use `--fixup` before review; commit the correction as its own step. Use `printf ... | git commit -F -` for commits. Final prefixes are `feat:`, `fix:`, `refactor:`, and `chore:`; include the Linear id when applicable.

During Land, use `--fixup=<sha>` for an earlier decision. Do not amend an earlier commit. Record test-shaping choices in a commit body.

Before a PR, use `mael gh show-code --uncommitted`; use `--committed` for the branch diff. A fixup must target a commit still in the branch range or it will remain an ordinary `fixup!` commit. During a rebase, resolve every hunk by intent because `ours` and `theirs` can reverse across replayed commits.

## Completion

When the implementation is written, run this unattended sequence:

1. Run `mael sync --no-push`, so the gates run on the current base.
2. Commit the implementation, then run the project gates on the clean tree. Continue when they pass. When the system prompt teaches `<compact-session>`, write `<compact-session min-tokens="200k">` and end your turn.
3. Write `.drafts/pr.md`: the decisions, the rationale, diagrams, and the test plan. Review reads it.
4. Run `/code-review`, if the skill is available. It squashes the branch, applies its fixes to the working tree, and commits them. A branch whose PR is already open gets an additive pass over the unpushed commits alone.
5. Run `/present`, if the skill is available. It writes `<milestone>presented</milestone>`.
6. Push with `mael gh create-pr --squash`, in the background. It rebases, runs the project's `pre_push_cmd`, then pushes. When the check fails, nothing is pushed: fix the failure and run step 6 again.
7. Run `mael task status done`. When the system prompt teaches `<compact-session>`, write `<compact-session min-tokens="200k">` and end your turn.
8. Run `/watch-pr` until CI passes or times out.

When more work starts after step 6, run `mael gh withdraw-pr` first, then run the flow again from step 1. A CI fix inside `/watch-pr` does not withdraw.

When the work has a result the user can see, write a verification after step 2. Read [verification.md](verification.md).

Use `--skip-pre-push` only when the user asks for an emergency push.

Run waits in the background and read their body, not only exit status.

Outside the daemon, `mael gh read-pr --wait` can report success before real CI starts, or hide its exit status through a pipe. Capture its output and confirm real jobs. `0/0 checks` can mean token permission failure; inspect Actions runs instead.

When the system prompt teaches `<end-session>`, end a finished session with it.

## Operations

Use `mael gh read-pr` for PR status, `mael gh check-log <run> --failed-only` for failures, and `mael gh download-artifact` for artifacts. Resolve Sentry issues only after confirming the current code fixes them; ask before that write. Run `mael uptimerobot status` for current availability. For scheduled tasks and the host services, start with `mael self status`.
