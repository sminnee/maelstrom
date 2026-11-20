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
2. **Contract changes.** A contract is the set of rules of one command or query. A rule is one clause: what the command or query requires of its caller, what it ensures, or what always holds for its domain type. `domain-modeling` has the model behind these terms: read it first, if that skill is available. List each rule that the work adds, changes, or withdraws, one line each: `<command, query, or domain type> — adds | changes | withdraws: <rule id, when one exists> <what the caller observes>. Caller: <who>.`
   - The list is also the plan of the tests. Each line gets its evidence at the primary seam of its command or query, as `design-build-test` says, if that skill is available. Write no separate test plan.
   - Before you write the list, find the current rules. When `docs/specs/<subsystem>/` has feature files, read them: the format is in `domain-modeling`'s FEATURE-FORMAT.md, if that skill is available. Cite each rule as `<feature file stem>:<id>`, and give a new rule the next unused id.
   - When the subsystem has no feature file, write the rules from the purpose of the command or query. The lines then have no rule id. Do not add a feature file for them. End each line with its primary seam, the one interface where the rule holds in full: `Primary seam: <interface>.`
   - Then read the suite outline of each module that the work touches: the test names of its owning suite, not the bodies. It shows the tests to change, to delete, or to question. Name those that a line makes wrong, below the line: `Tests to change or delete: <names>.` For a large suite, read the names that concern the behaviour.
   - List changes only. Leave out each rule that the work keeps.
   - A line is a rule only when a caller of the command or query observes it. A field, a key, an argument, a default, a constant, and a call order are facts about the code. Leave them out.
   - List a behaviour once, on the command or query where its caller observes it. Code that only carries a value onward gets no line.
   - Write "None" when the contract does not change. A refactor then gets no new or changed test. For a bug fix, name the regression test and the rule that the fix restores.
   - When one command or query has more than five lines, join the lines that one caller observes as one behaviour, or split the task.
   - When an outline does not read as a specification, write one line under the open points: `Outline finding: <module> — <suite path>: <what the names show>.` Do not reshape the suite in this plan.
3. **Why, and the decisions.** Each decision gets its reason, in one line if you can.
4. **Detail.** Files (with each feature file that a contract change edits), code to reuse, verification (with each ephemeral check for work that no rule covers, such as a layout).
5. **Out of scope and open points.**

Make the plan self-contained:

- Copy each requirement, constraint, and pointer from the task content into the plan. Do not write "see the task".
- Explain a project term the first time you use it, or link its `CONTEXT.md` entry. Write out the task's shorthand: ids, codenames, abbreviations.

Keep it terse. Leave out the alternatives you rejected, unless a reviewer would ask about them. Follow `writing-for-humans`, if the skill is available, for sentence length and vocabulary.

When the work is more than one session can do, name the extra tasks in the plan. [task-chains.md](task-chains.md) says how to splice them in after approval.

When the work touches another project, the plan names one task for each other project and keeps only this project's part. After approval, follow the "Cross-project work" section of [task-chains.md](task-chains.md).

When you draft, promote, or launch tasks, read [task-chains.md](task-chains.md).
