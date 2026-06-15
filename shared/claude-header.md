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
4. Commit the review fixes as `--fixup` commits (one per blocking finding, targeting the originating commit). Do not amend; do not run autosquash — the user squashes via `mael sync --squash`.
5. **Stop.** Report back and wait for the user before running `mael gh create-pr`.

If there are no blocking findings, stop after step 2 and report the review summary.
The PR step is the only thing that requires user confirmation.

When the agent session ends, mael automatically moves the task to `done` (the open session is
the "in-progress" signal). You don't need to run `mael task status done` yourself.
