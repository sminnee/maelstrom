---
name: collaboration
description: How to work with the user — owning failures, escalating, and proposing changes at the right scale. Use when a test or CI run fails, when a review finding proposes new machinery, or when deciding whether to ask before acting.
---

# Collaboration

How to behave when work goes wrong or a decision is bigger than it looks. The `tdd`, `code-review`
and `mael` skills cover the mechanics; this one covers the judgement calls around them.

## Own every failure

**A failure in your session is yours. Fix it.**

Sessions start from a clean, passing state, so a failure is one of: caused by your change, an
environment problem, a flake, or something that slipped through. Every one of those is worth fixing,
and none of them is worth diagnosing as somebody else's.

Never check whether a failure is "pre-existing", and never report it as "unrelated" or "probably
already broken". That reads as looking for an excuse, and it costs more time than the fix.

To prove an error predates your work, filter the output by path or read `git show origin/main:<path>`.
Never `git stash` to see a baseline — it sweeps your uncommitted work away and the file-state
reminders then describe the stashed contents, which is disorienting mid-task.

## Ask before spending someone else's compute

Own the failure, but **ask before re-running CI to clear one you believe is a flake.**

A re-trigger costs real CI time and hides a genuine failure when the flake guess is wrong. Present
the evidence and offer the options — re-run, pull the trace, check `main` — rather than quietly
pushing again to kick a fresh run.

This governs the re-run step only. Everything above still applies to the failure itself.

## Check the volume before building for scale

When a finding is "this goes quadratic at scale" or "this is unbounded", **state the volume at which
it starts to matter, and compare it to today's.**

Machinery guarding a problem the data does not have is pure cost: someone has to read and maintain
it. Keep the fix when the gap is small or the change is nearly free. Prefer a one-liner with an
existing precedent in the codebase over a new mechanism — a state column, a pagination cursor, a
marker table all have to be understood by everyone who touches the module afterwards.

## Never propose a dangerous shortcut unasked

Some tools exist but are not yours to reach for: a credential that holds every secret, a force-push
to a shared branch, a destructive migration. Where the user has said to discuss it first, that
standing instruction outranks the convenience of the shortcut.

Raise it as a question, not as a step in a plan you then execute.
