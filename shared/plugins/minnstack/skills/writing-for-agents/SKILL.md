---
name: writing-for-agents
description: Write or edit agent instructions, skills, AGENTS.md, and CLAUDE.md.
---

# Writing for agents

For a skill, also read [SKILL-MECHANICS.md](SKILL-MECHANICS.md).

Write a repeatable process, not a lecture. Keep required steps inline and place branch-specific reference behind a precise pointer. A pointer states what it reaches and the distinct conditions that trigger it. Its wording is the invocation mechanism.

Give each step a clear, observable completion condition. State exhaustive demands where they matter. Split only when sequence or invocation makes a real context boundary valuable.

Use progressive disclosure: keep universal steps inline and disclose branch-specific reference. Co-locate a concept’s definition and caveats. A split only helps when it hides later steps across a hand-off or another real context boundary.

Use established leading words for repeated concepts. Prefer positive instructions; retain negative rules only for hard guardrails. Keep each fact in one authoritative home. Do not cache facts that the environment can cheaply reveal.

Prune every line that does not change behaviour, applies only to a disclosed branch, duplicates another rule, or can go stale. Re-read the finished instruction for relevance and missing completion criteria.

Treat environment files and `--help` as the source of truth for cheap lookups. Remove caches unless they preserve an unwritten convention or costly-to-discover gotcha. Delete no-op instructions rather than merely shortening them.

## Compression check

Classify each candidate cut before deleting it:

- Delete repeated examples, rationale that does not alter behaviour, and cheap lookups.
- Keep inline every ordered action, safety guard, state transition, approval gate, and observable completion condition.
- Disclose a detailed, conditional method behind a pointer that names both the material and its trigger.

Do not use word count as the decision rule. Re-read the result against every branch in the original process. A shorter instruction is complete only when each branch still reaches a definite action or outcome.

## Write compactly

Compress structure, not obligations. Give each line one job:

- State the default action first.
- Give each exception its own conditional line: `When <condition>, <action>.`
- End a sequence with its observable result or stopping condition.
- Keep commands, paths, state names, and user-facing output literal.

Do not join independent conditions with commas, semicolons, or parenthetical exceptions. A short list is clearer than one dense sentence. Keep rationale only when it changes a decision in an unwritten case.

Use this edit loop:

1. List the original branches, guards, and outcomes.
2. Mark each as **keep**, **disclose**, or **delete**.
3. Write the default path and each kept exception as separate instructions.
4. Compare the branch list with the draft. Restore every unmatched branch.
5. Remove only duplicate wording, examples, and explanation that did not survive the classification.
