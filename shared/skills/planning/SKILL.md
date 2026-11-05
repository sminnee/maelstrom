---
name: planning
description: Write a plan in plan mode, or draft and promote a task chain. Use when in plan mode, or when a session drafts tasks.
---

# Planning

A plan has two readers: a reviewer who did not write the task, and the build session. Approving the plan clears that session's context, so the build session sees only the plan. These rules override plan mode's built-in template.

In a planning session (`/plan-task`, `/plan-next-step`), skip the plan rules below and read [task-chains.md](task-chains.md).

When the arguments carry a task, that task is what you plan.

Write the plan as an inverted pyramid. A reader who stops after any paragraph has the most important part:

1. **The change.** The title and the first one or two paragraphs say how it works now, how it will work when the work is done, and the approach in one sentence. Write them as prose for a user or a new developer, with no heading before or inside them.
2. **Why, and the decisions.** Each decision gets its reason, in one line if you can.
3. **Detail.** Files, code to reuse, **Test plan** (the module breakdown, the owning layer for each test, what gets only an ephemeral check, and the risks to cover), verification.
4. **Out of scope and open points.**

Make the plan self-contained:

- Copy each requirement, constraint, and pointer from the task content into the plan. Do not write "see the task".
- Explain a project term the first time you use it, or link its `CONTEXT.md` entry. Write out the task's shorthand: ids, codenames, abbreviations.

Keep it terse. Leave out the alternatives you rejected, unless a reviewer would ask about them. Follow `writing-for-humans` for sentence length and vocabulary.

When the work is more than one session can do, name the extra tasks in the plan. [task-chains.md](task-chains.md) says how to splice them in after approval.

When you draft, promote, or launch tasks, read [task-chains.md](task-chains.md).
