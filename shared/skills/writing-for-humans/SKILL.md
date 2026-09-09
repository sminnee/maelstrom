---
name: writing-for-humans
description: Write readable developer documentation, READMEs, ADRs, PR descriptions, docstrings, and comments. For skills, AGENTS.md, and CLAUDE.md, use writing-for-agents.
---

# Writing for humans

Write for a capable developer new to the codebase. Read `CONTEXT.md` first when it exists. Reuse its terms and avoid-list.

Lead with purpose. Order summary, detail, then edge cases. Use one topic per section and one idea per paragraph. Explain non-obvious rules by their reason.

Use short active present-tense sentences. Name the subject instead of using ambiguous pronouns. Use one name per concept. State concrete values, preconditions, and failure modes. Expand an acronym on first use.

Target fewer than 20 words per sentence and never exceed 30. Describe current behaviour; put migration history in a migration note or PR description. Where no `CONTEXT.md` exists, use the vocabulary already present in the code and docs.

Prefer an example for usage and a table for three or more parallel facts. Comments earn their place only for a non-obvious constraint or decision; describe current code, not the change history.

Before finishing, remove filler, check terms and sentence length, verify every command and claim against the source, and show substantial documents with a `<doc-file>` tag.

Use the project’s existing section shape for the same document type. A comment should state a non-obvious constraint, rejected plausible alternative, or invisible consequence; never narrate the diff.
