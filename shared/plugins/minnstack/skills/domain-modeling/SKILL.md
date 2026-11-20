---
name: domain-modeling
description: Build and place a project's domain model - glossary terms, decision records, the contracts of commands and queries as feature files, and the subsystem breakdown. Use when defining a term or a durable decision, writing a feature file, splitting a glossary, or finding where a document goes.
metadata:
  opencode/slash: true
---

# Domain modeling

A domain model has four documents. Each has one job:

| Document | Job | Path |
| --- | --- | --- |
| Glossary | What the words mean | `CONTEXT.md`, and `docs/specs/<subsystem>/CONTEXT.md` |
| Feature file | The contract of one command or query, the invariants of one domain type, or the laws of its repository | `docs/specs/<subsystem>/<name>.feature` |
| ADR | Why: a decision, the options rejected, and how far its rollout is | `docs/adr/NNNN-slug.md` |
| Map | Which subsystems exist, and where each part is | `docs/README.md` |

Model with DDD (Evans). State each command's contract as Assertions (Evans ch. 10) using requires / ensures / invariant (Meyer's terms only). Specify with rules and key examples (Adzic, Specification by Example; Wynne, Example Mapping). Prefer types to checks (King, "Parse, don't validate").

The model has one hierarchy:

- A project has subsystems. A small project has none.
- A subsystem has domain types: each entity, value object, or aggregate that its glossary names.
- A domain type has invariants, commands, and queries.
- A command or a query has a contract: the set of its contract rules.
- A contract rule is one clause: what the command or query requires of its caller, what it ensures, or what always holds for the domain type.

The glossary gives the words, and the feature file states the contract in those words.

Content that fits none of these is a candidate for deletion. When it is mechanism, it goes in the project's developer docs.

Create a file or a directory only when the first term, rule, or ADR needs it. A project whose `docs/README.md` lists no subsystems has one glossary, the root `CONTEXT.md`.

During a discussion:

1. Challenge terms that conflict with the glossary.
2. Replace vague or overloaded terms with one canonical term.
3. Test relationships with concrete edge cases.
4. Check stated behaviour against code and surface contradictions.
5. Add each resolved term immediately, to the narrowest glossary that covers its users. Keep a glossary implementation-free.

This is an active design discipline. Reading a glossary for existing vocabulary does not invoke it. Keep a glossary a glossary, never an implementation specification or scratchpad.

## Format files

- Read [CONTEXT-FORMAT.md](CONTEXT-FORMAT.md) before you add a term, choose the glossary a term goes in, split a glossary, or add a subsystem to `docs/README.md`.
- Read [FEATURE-FORMAT.md](FEATURE-FORMAT.md) before you write or edit a feature file, write a contract rule, specify a repository, or cite a rule id in a test.
- Read [ADR-FORMAT.md](ADR-FORMAT.md) before you offer or write an ADR, or continue or tick a rollout.

Offer an ADR only for a choice that is costly to reverse, surprising without context, and made after a real trade-off.

## Related skills

- `codebase-design` has the vocabulary of the code: module, interface, seam, depth. Use it when a subsystem boundary or a command is hard to place in the code.
- `design-build-test` selects the tests that prove a contract rule, and the layer that owns each.
- `writing-for-humans` has the prose rules for a glossary entry and an ADR.
- `grilling` stress-tests a term, a boundary, or a decision before you record it.

## Subsystems

A subsystem is a slice of behaviour with its own glossary and feature files. It can span code directories, for example a frontend part and a backend part.

- Split where the language changes. When one word means two things to two groups, that is a subsystem boundary.
- Whatever must be consistent at once is one aggregate, and one aggregate is in one subsystem.
- Keep the commands and queries that other subsystems call few.

Expect the first split to be wrong. Look at the boundary again when one of these signals shows:

| Signal | Likely cause |
| --- | --- |
| A rule cannot be stated in glossary terms | The boundary is in the wrong place, or a term is missing |
| An invariant spans two subsystems | They are one subsystem, or the invariant is eventual: name the event |
| Many rules for little hidden behaviour | The module is shallow, as `codebase-design` defines it |
| Rules in different features change together | Those features are one command |
