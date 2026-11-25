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
| Glossary | What the words mean | `CONTEXT.md`, and `docs/specs/<subdomain>/CONTEXT.md` |
| Feature file | The contract of one command or query, the invariants of one domain type, or the laws of its repository | `docs/specs/<subdomain>/<subsystem>/<name>.feature` |
| ADR | Why: a decision, the options rejected, and how far its rollout is | `docs/adr/NNNN-slug.md` |
| Map | Which subdomains and subsystems exist, and where the code of each is | `docs/README.md` |

Model with DDD (Evans). State each command's contract as Assertions (Evans ch. 10) using requires / ensures / invariant (Meyer's terms only). Specify with rules and key examples (Adzic, Specification by Example; Wynne, Example Mapping). Prefer types to checks (King, "Parse, don't validate").

The model has one hierarchy:

- A project has subdomains. A small project has none.
- A subdomain is a part of what the project does, with one language. It has one glossary.
- A subdomain has subsystems. A subsystem is a module of its subdomain (Evans ch. 5): it has the feature files.
- A subsystem has domain types: each entity, value object, or aggregate that the glossary names.
- An aggregate is a domain type with parts that one write keeps consistent. Most domain types are not aggregates: say "domain type" unless you mean that.
- A domain type has invariants, commands, and queries.
- A command or a query has a contract: the set of its contract rules.
- A contract rule is one clause: what the command or query requires of its caller, what it ensures, or what always holds for the domain type.

The glossary gives the words, and the feature file states the contract in those words.

Content that fits none of these is a candidate for deletion. When it is mechanism, it goes in the project's developer docs.

Create a file or a directory only when the first term, rule, or ADR needs it. A project whose `docs/README.md` lists no subsystems has one glossary, the root `CONTEXT.md`. A project whose subsystems share one language has no subdomain level: its feature files are in `docs/specs/<subsystem>/`.

During a discussion:

1. Challenge terms that conflict with the glossary.
2. Replace vague or overloaded terms with one canonical term.
3. Test relationships with concrete edge cases.
4. Check stated behaviour against code and surface contradictions. When the code does not keep a rule that the user intends, write no rule yet: list it as a rule the code does not keep, get the user's verdict, and name it in the PR.
5. Add each resolved term immediately, below the heading of the subsystem that defines it, as CONTEXT-FORMAT.md says. Keep a glossary implementation-free.

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

## Subdomains and subsystems

A subdomain is where one language holds. A subsystem is a slice of behaviour inside it, with its own feature files. A subsystem can span code directories, for example a frontend part and a backend part. A layer, such as "the CLI" or "the API", is not a subsystem.

- Split subdomains where the language changes. When one word means two things to two groups, that is a subdomain boundary.
- Whatever must be consistent at once is one aggregate, and one aggregate is in one subsystem.
- Keep the commands and queries that other subsystems call few.
- The map covers the whole codebase. Code that serves every subsystem, such as the frame of a layer, a shared store, or the client of an outside service, is an infrastructure subsystem. Group the infrastructure subsystems in one subdomain.
- A use case that changes the domain types of several subsystems belongs to the subsystem of its verb. It calls the commands of the others.
- An agent-facing skill is a door of the subsystem that it drives, as a CLI command is.

To find the subsystems of an existing codebase:

1. List the domain types that the glossary and the stored data name. Mark each aggregate.
2. Group the domain types that one write keeps consistent, or that share most of their commands. Each group is a candidate subsystem. Give it a name and one line of purpose.
3. Give each glossary term one owner: the subsystem that defines it. A term that others only cite stays with its owner.
4. List each call that crosses a candidate boundary. Many calls between two candidates is a reason to merge them.
5. Count the terms and the domain types of each candidate. Split one that is far larger than the others where a part has its own words.
6. Group the subsystems into subdomains by language, and put each infrastructure subsystem in the infrastructure subdomain.
7. Get the user's verdict on the names, the boundaries and each term owner, one small set of questions at a time.

Expect the first split to be wrong. Look at the boundary again when one of these signals shows:

| Signal | Likely cause |
| --- | --- |
| A rule cannot be stated in glossary terms | The boundary is in the wrong place, or a term is missing |
| An invariant spans two subsystems | They are one subsystem, or the invariant is eventual: name the event |
| Many rules for little hidden behaviour | The module is shallow, as `codebase-design` defines it |
| Rules in different features change together | Those features are one command |
| The same rule text is in two feature files | It is a rule of the type: state it once, in `<type>.feature` |
| A field has a fixed set of legal values | It is a value object: `<type>.feature` lists the legal values once, as one row of a table |
| One subsystem owns the reference to each neighbour, and most of the terms | It is a hub: split out the part that has its own words |
