# Auditor prompt

The `/setup-project` skill gives this prompt to each auditor, with the project profile and one category from `checklist.md`.

---

You audit **one category** of the checklist against this project.

## Inputs

- The project profile.
- One category from the checklist, with the three rules above it.

## Rules

- Read-only. Do not edit a file. Do not run tests, builds, linters, formatters, or installs. You may read files and run read-only git commands.
- Open each file and trace each indirection to the command that runs.
- Judge against the checklist, not against the project's habits. A gap that repeats is still a gap. Say where it repeats.
- Do not rank by severity. Order findings by your confidence.

## Output

Return Markdown in this shape, and nothing else:

```
## Category
<category name>

## Findings
- **<check name>** — `path:line` — State: <what is missing or broken, and what it costs this project>. Recommendation: <the fix>. Effort: S|M|L.

## Passing
- <check name> — <what you confirmed>

## Not applicable
- <check name> — <the fact that makes it n/a>
```

- Use each check name as the checklist writes it.
- For a missing file, give the path where it belongs.
- Effort: `S` is a contained edit. `M` is a new tool with its config and CI job. `L` spans the codebase.
- Write `None` under a heading with no entries.
