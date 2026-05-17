---
name: code-review
description: Review committed changes on the current branch against project standards, security, simplicity, and architectural fit. Invoked as the `/code-review` slash command. Default range is `origin/main..HEAD`; pass a SHA or range as an argument to scope the review. Runs read-only via a sub-agent; the parent then proposes fixes interactively.
---

# Code Review

Universal code-review skill for maelstrom projects. Reviews a range of commits for project-standards
conformance, security, simplicity, and reuse, and reports findings back to the user. The parent
agent (this skill's top-level section) drives the workflow; a read-only sub-agent does the actual
review against a structured Markdown contract.

**Stateless and one-shot.** Re-invoke `/code-review` after a fix commit lands to re-review — there
is no incremental-review machinery, no resolved-thread tracking, no JSON output.

## Parent-agent section (runs on `/code-review`)

This is what runs when the user types `/code-review`. Follow these steps in order.

### 1. Gate the review and resolve the range

Run:

```bash
mael review-prepare $ARGUMENTS
```

`$ARGUMENTS` is the user's argument string (may be empty). The command handles range resolution
(default `origin/main..HEAD`; a bare SHA expands to `<sha>^..<sha>`; anything else is passed to
git as-is) and the pre-flight gates (aborts if the worktree has uncommitted changes or the range
is empty).

If the command exits non-zero, print its stderr to the user and stop — **do not spawn the
sub-agent**.

On success, capture stdout. It contains a `Range:` header followed by the two `git log` and
`git diff` commands the sub-agent should run itself. The diff stays out of the parent's context.

### 2. Spawn the review sub-agent

Read the reviewer prompt from `reviewer-prompt.md` (alongside this file, at
`~/.claude/skills/code-review/reviewer-prompt.md`).

Use the Task tool with `subagent_type: "Explore"` (read-only — matches the brief: no edits, no
tests, no builds; it can run `git log` / `git diff` via Bash). The sub-agent prompt is the
contents of `reviewer-prompt.md` followed by the captured output of `mael review-prepare` from
step 1.

### 3. Display the sub-agent's response

The sub-agent returns Markdown. Re-display it to the user verbatim, preserving section order:

1. `## Summary`
2. `## Design decisions worth calling out`
3. `## Blocking findings`
4. `## Advisory findings`

### 4. Resolve Blocking findings interactively

For each entry under **Blocking findings**:

- Propose the specific fix to the user (use AskUserQuestion or a plain prompt — your call based on
  fix complexity).
- **Do not auto-apply.** Wait for explicit approval before editing.
- Apply approved fixes via Edit/Write.

Advisory findings: apply only the ones that are clearly correct and low-risk in your judgement.
Skip the rest. State briefly which advisories you addressed and which you skipped.

### 5. Commit the fixes

Once all approved fixes are applied, commit them as **a single new commit**:

```bash
printf 'fix: address code review feedback\n' | git commit -F -
```

Hard rules — these exist so the user can squash manually if they want to:

- **Never `--amend`** the prior commits.
- **Never `--fixup`** — no autosquash machinery.
- **Never squash** in this workflow.
- One review pass = at most one new commit.

### 6. Done

Report what was fixed. The user can re-invoke `/code-review` to re-review if they want — this skill
is stateless.

## Do NOT bake project-specific rules into this skill

This is the universal review skill. Project-specific rules belong in
`docs/review/coding-standards.md` / `docs/review/code-smells.md`, not here — `reviewer-prompt.md`
tells the sub-agent to load them conditionally. The following are examples of things that **must
not** appear in either this file or `reviewer-prompt.md`:

- `Q()` / `%s` SQL placeholders or any other framework-specific API.
- No-Tailwind, project-CSS-utilities, or any other UI-framework rule.
- NZ English / locale-specific copy rules.
- `SystemModel` / `AppModel` / handler-vs-model architectural splits.
- `unittest`-vs-`pytest` test framework preferences.
- File-type→skill mappings beyond the generic "load skills matching diff file types".
- Severity tables enumerating project-specific blocking rules.

Also out of scope:

- Incremental-review mode / resolved-thread tracking.
- GitHub PR comment posting or any CI-gate-specific output (JSON contract, `resolve_thread_ids`,
  inline-anchor rules).
- GraphQL thread IDs.
- Fixup commits, autosquash, or any squash workflow — the parent commits a single fix commit and
  leaves squashing to the user.
