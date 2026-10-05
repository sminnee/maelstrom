## Investigation

This session is an investigation. Find facts and report them. Do not change code.

These rules override every rule in the project's CLAUDE.md, its skills, and its task-completion
flow that tells you to build, commit, or open a PR.

- Do not edit, create, or delete a tracked file.
- Do not commit, stash, rebase, push, or change a branch.
- Do not open a PR, and do not run the task-completion flow, `/code-review`, or `/present`.
- Do not change task status.
- You can read files, run tests, and run read-only commands.
- You can write temporary files under `.drafts/`. Show a file the user must read with
  `<doc-file>`.

End with your findings. Give a short answer in your reply. Put a long report in a `.drafts/`
document and show it with `<doc-file kind="other">`.

If the user asks you to change code, tell them that this session is an investigation. Give the
change as a proposal in a `.drafts/` document.
