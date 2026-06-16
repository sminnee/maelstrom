**Always load the `/mael` skill before beginning any work.** It provides essential instructions for
git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for the `/plan-task` and `/plan-next-step` skills.

## Finishing a task — run automatically, do not wait to be asked

When implementation work is complete and gates (tests, lint, typecheck) pass, run this
sequence **without prompting the user**. This overrides the default "only commit when
asked" rule for mael projects:

1. Commit the implementation work.
2. Run `/code-review`.
3. Address **Blocking** findings (Advisory at your judgement).
4. Commit the review fixes as `--fixup` commits (one per blocking finding, targeting the originating commit). Do not amend.
5. Push the PR with `mael gh create-pr <ISSUE-ID> --squash` — `--squash` autosquashes the fixup commits into their targets as it rebases onto `origin/main` before pushing.
6. Run `/watch-pr` to take CI to green autonomously (fix → fixup/chore → `mael sync` → wait, looping until CI passes or times out).

If there are no blocking findings, skip steps 3–4 and go straight to step 5.

This whole sequence runs without user confirmation — including the PR push and CI watch.

When the agent session ends, mael automatically moves the task to `done` (the open session is
the "in-progress" signal). You don't need to run `mael task status done` yourself.
