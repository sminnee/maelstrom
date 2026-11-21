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

## One glossary or one for each subdomain

**The root `CONTEXT.md`** holds the terms that no one subdomain defines. A small project has only this file. When no glossary exists, create the root file when the first term is resolved.

**A subdomain glossary** is `docs/specs/<subdomain>/CONTEXT.md`. It has one `##` heading for each subsystem of the subdomain, and holds the terms that those subsystems define.

Where a term goes:

- When `docs/README.md` lists no subdomains, add the term to the root `CONTEXT.md`.
- Otherwise, find the subsystem that defines the term: the one whose commands make or change the thing.
- Add the term below that subsystem's heading in its subdomain's glossary. Create the file when it does not exist.
- A term stays with the subsystem that defines it when other subsystems cite it.
- When no one subsystem defines the term, add it to the root `CONTEXT.md`.
- When you cannot tell which subsystem defines the term, ask.
- A pointer in code or docs to a moved term names the new file.

When you rename a subsystem whose old name is in code identifiers, rename the prose, put the old name under `_Avoid_`, and name in the entry the identifiers that keep it. Propose the code rename in the PR.

### Split at 500 lines

Before you add a term to a root `CONTEXT.md` of more than 500 lines, propose a split to the user. Add the term after the user accepts or declines. In an unattended session, add the term, and write the proposal in the PR description as an open point.

1. Find the subsystems, as the "Subdomains and subsystems" section of [SKILL.md](SKILL.md) says. An existing `##` cluster is a candidate, not a boundary.
2. Propose each subdomain and subsystem with its `docs/README.md` entry, and the terms that move to it.
3. On approval, write the entries, then move the terms of one subdomain at a time to `docs/specs/<subdomain>/CONTEXT.md`. Terms that no one subdomain defines stay in the root.

500 lines is a prompt to split, not a hard limit.

### The map

`docs/README.md` lists the subsystems, one entry each, below a `###` heading for each subdomain:

```md
## Subsystems

### Sales

- **Orders** — receives and tracks customer orders. Code: `src/orders/`, `web/src/orders/`. Specs: `docs/specs/sales/orders/`
- **Billing** — makes invoices and takes payments. Code: `src/billing/`. Specs: none yet
```

The entry form is `- **<Subsystem>** — <one line>. Code: <dirs>. Specs: docs/specs/<subdomain>/<subsystem>/`. Write `Specs: none yet` for a subsystem with no feature file. A project with no subdomain level has no `###` headings.

`Code:` names files where a subsystem does not have a directory of its own. When one layer file serves several subsystems, list it once below the subsystem that owns the frame, and say so above the list. Below the list, the map names the project's Gherkin runner: `Gherkin runner: <name>`, or `Gherkin runner: none`.
