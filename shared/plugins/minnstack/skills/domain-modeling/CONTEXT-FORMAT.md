# CONTEXT.md Format

## Structure

```md
# {Context Name}

{One or two sentence description of what this context is and why it exists.}

## Language

**Order**:
{A one or two sentence description of the term}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request

**Customer**:
A person or organization that places orders.
_Avoid_: Client, buyer, account
```

## Rules

- **Be opinionated.** When multiple words exist for the same concept, pick the best one and list the others under `_Avoid_`.
- **Keep definitions tight.** One or two sentences max. Define what it IS, not what it does.
- **Only include terms specific to this project's context.** General programming concepts (timeouts, error types, utility patterns) don't belong even if the project uses them extensively. Before adding a term, ask: is this a concept unique to this context, or a general programming concept? Only the former belongs.
- **Group terms under subheadings** when natural clusters emerge. If all terms belong to a single cohesive area, a flat list is fine.

## One glossary or one for each subsystem

**The root `CONTEXT.md`** holds the terms that two or more subsystems use. A small project has only this file. When no glossary exists, create the root file when the first term is resolved.

**A subsystem glossary** is `docs/specs/<subsystem>/CONTEXT.md`, beside the subsystem's feature files. It holds the terms that only that subsystem uses.

Where a term goes:

- When `docs/README.md` lists no subsystems, add the term to the root `CONTEXT.md`.
- Otherwise, find the subsystems that use the term.
- One subsystem uses it: add it to that subsystem's glossary. Create the file when it does not exist.
- Two or more use it: add it to the root `CONTEXT.md`.
- When a second subsystem starts to use a subsystem term, move the entry to the root.
- When you cannot tell which subsystem the topic belongs to, ask.

### Split at 500 lines

Before you add a term to a root `CONTEXT.md` of more than 500 lines, propose a split to the user. Add the term after the user accepts or declines. In an unattended session, add the term, and write the proposal in the PR description as an open point.

1. Find the subsystems, as the "Subsystems" section of [SKILL.md](SKILL.md) says. An existing `##` cluster is a candidate, not a boundary.
2. Propose each subsystem with its `docs/README.md` entry, and the terms that move to it.
3. On approval, write the entries, then move each term that only one subsystem uses to `docs/specs/<subsystem>/CONTEXT.md`. Terms that two or more subsystems use stay in the root.

500 lines is a prompt to split, not a hard limit.

### The map

`docs/README.md` lists the subsystems, one entry each:

```md
## Subsystems

- **Orders** — receives and tracks customer orders. Code: `src/orders/`, `web/src/orders/`. Specs: `docs/specs/orders/`
- **Billing** — makes invoices and takes payments. Code: `src/billing/`. Specs: `docs/specs/billing/`
```

The entry form is `- **<Subsystem>** — <one line>. Code: <dirs>. Specs: docs/specs/<subsystem>/`. Below the list, the map names the project's Gherkin runner: `Gherkin runner: <name>`, or `Gherkin runner: none`.
