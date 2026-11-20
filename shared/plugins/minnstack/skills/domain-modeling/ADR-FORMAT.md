# ADR Format

ADRs live in `docs/adr/` and use sequential numbering: `0001-slug.md`, `0002-slug.md`, etc.

Create the `docs/adr/` directory lazily — only when the first ADR is needed.

## Template

```md
# {Short title of the decision}

{1-3 sentences: what's the context, what did we decide, and why.}
```

That's it. An ADR can be a single paragraph. The value is in recording *that* a decision was made and *why* — not in filling out sections.

## Optional sections

Only include these when they add genuine value. Most ADRs won't need them.

- **Status** — a `Status:` line below the title (`proposed | accepted | deprecated | superseded by ADR-NNNN`) — useful when decisions are revisited
- **Considered Options** — only when the rejected alternatives are worth remembering
- **Consequences** — only when non-obvious downstream effects need to be called out
- **Rollout** — required when the decision is not in effect at once. See "Rollout" below.

## Numbering

Scan `docs/adr/` for the highest existing number and increment by one.

## When to offer an ADR

All three of these must be true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If a decision is easy to reverse, skip it — you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing."

### What qualifies

- **Architectural shape.** "We're using a monorepo." "The write model is event-sourced, the read model is projected into Postgres."
- **Integration patterns between contexts.** "Ordering and Billing communicate via domain events, not synchronous HTTP."
- **Technology choices that carry lock-in.** Database, message bus, auth provider, deployment target. Not every library — just the ones that would take a quarter to swap out.
- **Boundary and scope decisions.** "Customer data is owned by the Customer context; other contexts reference it by ID only." The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path.** "We're using manual SQL instead of an ORM because X." Anything where a reasonable reader would assume the opposite. These stop the next engineer from "fixing" something that was deliberate.
- **Constraints not visible in the code.** "We can't use AWS because of compliance requirements." "Response times must be under 200ms because of the partner API contract."
- **Rejected alternatives when the rejection is non-obvious.** If you considered GraphQL and picked REST for subtle reasons, record it — otherwise someone will suggest GraphQL again in six months.
- **A system-wide refactor that more than one session rolls out.** "Each money value is a `Money` value object, not a float." Without the ADR, a reader sees two forms and cannot tell which is the target.

## Rollout

A decision that is in effect at once has no rollout. A decision that the code reaches component by component records its own progress: a `Status:` line, a `Rollout:` line below it, and a `## Rollout` checklist.

```md
Status: accepted, 2026-11-20
Rollout: partial, 2 of 5

## Rollout

Done for a component means: {one or two checks an agent can run}.

- [x] orders — #212
- [x] billing — #215
- [ ] warehouse
- [ ] frontend/admin
- [x] voice — n/a: it has no unit tests of business rules
```

`Rollout:` is `none` when no line is ticked, then `partial, <ticked> of <total>`, then `complete`. An `n/a` line counts as ticked.

When you write the ADR:

- Write one line for each component. Each component is small enough for one agent session.
- Take the components from the subsystem list in `docs/README.md`. When a subsystem is too large for one session, split it by code directory. When the project has no map, use its top-level packages.
- The lines cover the whole repo. Tick a component that the decision does not apply to as `n/a`, with the reason.
- State once what "done" means, as a check that an agent can run.

When you continue a rollout:

1. Take one open line.
2. Do the work, then run the "done" check.
3. Tick the line with the PR number, and update the `Rollout:` count, in the PR that finishes the component. Write `complete` when no line is open.
4. When you tried a component and left it, write the reason on its line and leave it open.
