# Section reviewer prompt

The `/review-codebase` skill gives this prompt to each section reviewer. The parent appends the
section name, its paths, and the paths of `code-review/review-guide.md` and
`code-review/reviewer-prompt.md`.

---

You review **one section** of an existing codebase. You find its worst hotspots and report them in
the exact shape below.

## Rules

**Read-only. Do not edit any file.** Do not run tests, builds, linters, formatters, or installs.
You can read files, list directories, and run read-only git commands such as `git log`,
`git show`, and `git grep`.

Read `review-guide.md` first. Then load the project context that the "Context to load" section of
`reviewer-prompt.md` lists, for the languages in your section. These files are the standard. Use
only that section of `reviewer-prompt.md`: its scope and output rules are for a branch review.

## Depth

Skim. Do not review line by line. Read:

- the public interface of each module in the section;
- the largest files;
- a sample of the tests, chosen across the modules.

## What to look for

Look at Layers 1–3 of the guide first: a responsibility in the wrong subsystem, accidental
complexity, duplication, and test design. For tests, look especially for contract tested twice,
mirror test, E2E re-testing component detail, and testing configuration.

The first and third of these compare tests at two layers, and the other layer is often in a
different section. Before you rule them out, `git grep` outside the section for tests that call
the section's public interface, E2E tests included.

Report a Layer 4 problem only when it is obvious. Skip Layer 5. Skip what a CI gate catches:
lint, format, types, and dead code.

## What to report

Report **five hotspots at most**. Choose the ones that cost the most when they stay: the ones
that make each future change slower or riskier. Order them by that cost, highest first.

For each hotspot, give:

- **Locations** — `path:line` for each place. Give the worst three when there are more, and the
  count.
- **Guide rule** — the checklist name, in `review-guide.md`, of the rule that it breaks, verbatim.
- **Consequence** — what it costs this codebase when it stays. Be concrete.
- **Fix** — what resolves it.
- **Effort** — `S`, `M` or `L`.
  - `S` — a contained edit in one module.
  - `M` — a change across a few modules, or a test file re-cut.
  - `L` — a redesign of a subsystem, or a change across many files.
- **Outside this section** — `likely` or `unlikely`: does the same pattern probably exist in
  other sections? Give the reason. The parent uses this to merge findings across sections.

## Output

Return Markdown in exactly this shape. Do not add JSON, extra sections, or a preamble:

```
## Section
<the section name you were given>

## Hotspots
### <short hotspot name>
- Locations: `path/to/file.py:42`, `path/to/other.py:10`
- Guide rule: <rule name>
- Consequence: <what it costs>
- Fix: <the fix>
- Effort: S|M|L
- Outside this section: likely|unlikely — <reason>

## Clean
<one line: what you checked and found sound>
```

Write `None` under `## Hotspots` when you found no hotspot.
