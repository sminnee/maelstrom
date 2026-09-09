---
name: domain-modeling
description: Build a project's domain model. Use when defining domain terms, a ubiquitous language, or a durable architectural decision.
metadata:
  opencode/slash: true
---

# Domain modeling

Use `CONTEXT.md` as the glossary. If `CONTEXT-MAP.md` exists, use the context it selects. Create a context file or `docs/adr/` only when the first term or ADR needs it.

During a discussion:

1. Challenge terms that conflict with the glossary.
2. Replace vague or overloaded terms with one canonical term.
3. Test relationships with concrete edge cases.
4. Check stated behaviour against code and surface contradictions.
5. Add each resolved term immediately with [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md). Keep this file implementation-free.

Offer an ADR only for a choice that is costly to reverse, surprising without context, and made after a real trade-off. Use [ADR-FORMAT.md](ADR-FORMAT.md).

This is an active design discipline. Reading a glossary for existing vocabulary does not invoke it. Keep `CONTEXT.md` a glossary, never an implementation specification or scratchpad.
