# Project Skeleton Command

**PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

Scaffolds a new project with all configuration, tooling, and minimal working entry points. Guides the
user through architecture choices via interactive Q&A, writes a detailed file-by-file plan, then
creates all files after approval.

## Usage

```
/project-skeleton
```

## What This Command Does

1. **Validates environment** - Fails if not in plan mode
2. **Loads project-conventions skill** - Reads conventions for reference, if available
3. **Asks interactive questions** - Project basics, component conventions, Maelstrom setup
4. **Writes a file-by-file scaffold plan** - Every file listed with exact content
5. **Exits plan mode** - With allowed prompt to scaffold the project
6. **After approval**: Creates all files, runs install commands, confirms completion

## Command Logic

### 1. Plan Mode Check (MANDATORY)

Check for `Plan mode is active` in system-reminder tags. If not present:

```
Error: Project-skeleton command requires plan mode. Please enter plan mode first.
```

Stop immediately - do not proceed with any other logic.

### 2. Load Project Conventions Skill

Read `~/.claude/skills/project-conventions/SKILL.md` for convention reference. This skill contains
standard conventions for TypeScript, Python, and other languages that should be applied to the
scaffold.

If the skill file doesn't exist, continue with sensible defaults (documented in this command).

### 3. Interactive Q&A

Use AskUserQuestion tool for three rounds of questions. Each round gathers information needed for the
scaffold plan.

#### Round 1: Project Basics

Ask these questions in a single AskUserQuestion call:

- **Project name**: Used for package.json `name` fields, CLAUDE.md title, `.maelstrom.yaml`
  `product_label`. Should be kebab-case.
- **Brief description**: One-line description for CLAUDE.md and package.json.
- **Components needed**: Present options as a checklist:
  - Web frontend (React)
  - Server/API (Hono, Express, FastAPI, etc.)
  - Desktop app (Tauri)
  - iOS app (Swift)
  - Python backend/CLI
  - Other (user specifies)
- **Monorepo?**: Default yes for multi-component projects, no for single-component.

#### Round 2: Confirm Conventions (per component)

For each component chosen in Round 1, present smart defaults from the project-conventions skill and
ask the user to confirm or override:

**TypeScript component defaults:**
- Language: TypeScript
- Package manager: pnpm v9
- Linter/formatter: Biome (2-space indent, 100 char width, double quotes, semicolons)
- Type checking: TypeScript strict mode (`strict: true`, `noUnusedLocals`, `noUnusedParameters`,
  `noFallthroughCasesInSwitch`, `noEmit: true`)
- Test framework: Vitest (globals, jsdom for frontend, @testing-library/react for components)
- Dead code detection: Knip
- Path alias: `@/*` -> `./src/*`
- File naming: kebab-case

**Python component defaults:**
- Language: Python
- Package manager: uv
- Build backend: hatchling
- Source layout: `src/<package>/`
- Linter/formatter: Ruff (line-length 120, standard rule set)
- Type checking: Pyright basic mode, strict inference
- Test framework: pytest with `pythonpath = ["src"]`
- Dead code detection: Vulture with whitelist

Only ask where there's genuine ambiguity (e.g., which server framework: Hono vs Express vs FastAPI).
Present defaults concisely - don't ask about things that are clearly standard.

#### Round 3: Maelstrom & CI Setup

- **Linear team ID**: Team ID for Linear integration (e.g., `ME`), or skip to use a placeholder.
- **Additional port names**: Auto-derive port names from components (e.g., `FRONTEND`, `FRONTEND_HMR`, `SERVER`).

### 4. Write Plan to File

Write the detailed scaffold plan to the plan file (path provided in system context). The plan lists
every file to be created, grouped by category, with exact content for each file.

#### Plan Structure

```markdown
# Project Scaffold: <project-name>

<brief description>

## Components

- <list of chosen components with confirmed conventions>

## Files to Create

### Project Root

| File | Purpose |
|------|---------|
| .maelstrom.yaml | Maelstrom config with ports, install_cmd, linear |
| Procfile | Service definitions using $PORT vars |
| .editorconfig | Indent rules per language |
| .gitignore | Combined ignores for all languages |
| CLAUDE.md | Dev instructions for Claude Code |
| .claude/settings.json | Claude Code permissions for dev tools |
| pnpm-workspace.yaml | Workspace config (if pnpm monorepo) |
| package.json | Root package.json with workspace scripts |

### <component-name>/ (per component)

| File | Purpose |
|------|---------|
| package.json / pyproject.toml | Dependencies and scripts |
| tsconfig.json | Type checking config (TypeScript) |
| biome.json | Linting & formatting (TypeScript) |
| knip.json | Dead code detection (TypeScript) |
| vitest.config.ts / pytest config | Test configuration |
| src/ skeleton | Minimal working entry point |

### Testing

| File | Purpose |
|------|---------|
| e2e/playwright.config.ts | Playwright config |
| e2e/example.spec.ts | Example e2e test |

### CI/CD

| File | Purpose |
|------|---------|
| .github/workflows/run-tests.yml | PR test workflow |

### Dev Scripts (bin/)

| File | Purpose |
|------|---------|
| bin/dev | Start dev environment |
| bin/test | Run all unit tests |
| bin/lint | Run all linting (lint + typecheck + deadcode) |
| bin/test-e2e | Run e2e tests |
| bin/ci-check | Run everything CI runs |

---

## File Contents

### .maelstrom.yaml

<exact YAML content>

### Procfile

<exact Procfile content>

... (every file with exact content)
```

Each file entry must include the **exact content** to be written, referencing conventions from the
project-conventions skill.

#### .maelstrom.yaml Generation

The `.maelstrom.yaml` must include:

- **`port_names`**: Derived from chosen components. Each component that listens on a port gets a name
  (e.g., `FRONTEND`, `SERVER`, `API`). The first port (`PORT_BASE * 10 + 0`) is the app URL shown by
  maelstrom, so the primary web-facing component should be listed first.
- **`install_cmd`**: Based on stack:
  - TS monorepo: `pnpm install`
  - Python only: `uv sync`
  - Mixed: `pnpm install && cd server && uv sync` (or similar combined command)
- **`linear` section**: `team_id` (from Q&A or placeholder), `workspace_labels` (default
  `[alpha, bravo, charlie]`), `product_label` (project name).

Example for TS web + TS server:
```yaml
port_names:
  - FRONTEND
  - SERVER
install_cmd: "pnpm install"
linear:
  team_id: "ME"
  workspace_labels: [alpha, bravo, charlie]
  product_label: "my-project"
```

#### Procfile Generation

Each component that runs a dev server gets a Procfile entry. Commands must:
- `cd` into the component directory
- Reference `$<PORT_NAME>_PORT` env vars for port binding
- Use the component's native dev command

Example:
```
web: cd web && pnpm dev --port $FRONTEND_PORT
server: cd server && pnpm dev --port $SERVER_PORT
```

#### .env / Port Variable Usage

Components are run via `mael env start` which injects env vars into Procfile processes. No dotenv
loading is needed in components - they read from process env:

- **TypeScript**: `process.env.SERVER_PORT`
- **Python**: `os.environ["SERVER_PORT"]`

Scaffold entry points must use these env vars for port binding.

#### CLAUDE.md Structure

```markdown
# <Project Name>

<brief description>

## Running Commands

<per-component commands for test, lint, typecheck, deadcode>

## Dev Environment

bin/dev to start, bin/test to test, bin/lint to lint, bin/ci-check for full CI check.

## Maelstrom Workflow

**Always load the `/mael` skill before beginning any work.** It provides essential instructions for
git operations, commits, branches, PRs, Linear tasks, and development workflows.

**Plan mode is required** for `/plan-task`, `/create-subtasks`, and `/review-branch` commands.

(maelstrom instructions end)
```

#### .claude/settings.json Generation

Generate `.claude/settings.json` with permission rules that allow Claude Code to run the project's
dev tools without prompting. The allowed commands are derived from the chosen stack:

**TypeScript (pnpm) components:**
- `Bash(pnpm lint:*)` - linting
- `Bash(pnpm test:*)` - testing
- `Bash(pnpm build:*)` - building
- `Bash(pnpm dev:*)` - dev server

**Python (uv) components:**
- `Bash(uv sync:*)` - dependency sync
- `Bash(uv run:*)` - running commands in venv

Example for TS web + Python server:
```json
{
  "permissions": {
    "allow": [
      "Bash(pnpm lint:*)",
      "Bash(pnpm test:*)",
      "Bash(pnpm build:*)",
      "Bash(pnpm dev:*)",
      "Bash(uv sync:*)",
      "Bash(uv run:*)"
    ]
  }
}
```

Only include entries relevant to the chosen stack. For a Python-only project, omit pnpm entries and
vice versa.

#### GitHub Actions run-tests.yml

Trigger: `pull_request` (not main). Jobs:

- **lint**: Per component - run linter, type checker, dead code detector
- **test**: Per component - run unit tests
- **e2e**: Run Playwright tests (if e2e tests exist)
- Path filtering to scope jobs to relevant directories

#### bin/ Scripts

All scripts: bash, executable, `#!/usr/bin/env bash`, `set -euo pipefail`:

- `bin/dev` - `mael env start` wrapper
- `bin/test` - Run unit tests for all components
- `bin/lint` - Run lint + typecheck + deadcode for all components
- `bin/test-e2e` - Run Playwright e2e tests
- `bin/ci-check` - Run lint + test + test-e2e

#### Handling Different Stack Combinations

Dynamically adjust based on chosen components:

| Stack | Package Manager | Monorepo | Procfile services |
|-------|----------------|----------|-------------------|
| TS web + TS server | pnpm | pnpm-workspace.yaml | web + server |
| TS web + Python server | pnpm + uv | pnpm-workspace.yaml + pyproject.toml | web + server |
| Tauri (TS + Rust) | pnpm + cargo | pnpm-workspace.yaml | Single Tauri dev |
| Python only | uv | No | Single service |
| TS web + TS server + iOS | pnpm | pnpm-workspace.yaml | web + server (iOS separate) |

### 5. Exit Plan Mode

Call ExitPlanMode with allowedPrompts:

```json
[
  { "tool": "Write", "prompt": "scaffold project" }
]
```

### 6. Execution Phase (After Approval)

After the user approves the plan, create all files:

1. **Create directories** as needed for each component
2. **Write each file** using the Write tool with the exact content from the plan
3. **Make bin/ scripts executable**: `chmod +x bin/*`
4. **Create `.maelstrom.yaml`** then run `mael env reset` to generate `.env` with port allocations
5. **Run install commands**: `pnpm install`, `uv sync`, or equivalent based on stack
6. **Confirm completion**: Report what was created and next steps

Entry points must be minimal but working:

- **React web**: Basic App component with Vite dev server reading `$FRONTEND_PORT`
- **Hono server**: Hello-world handler reading `$SERVER_PORT`
- **FastAPI server**: Hello-world endpoint reading `$SERVER_PORT`
- **Python CLI**: Minimal `__main__.py` entry point

## Error Cases

- **Not in plan mode**: "Project-skeleton command requires plan mode. Please enter plan mode first."
- **No components selected**: Ask again - at least one component is required.
- **Conflicting conventions**: Present the conflict and ask user to choose.

## Implementation Notes

- **Plan mode detection**: Check for `Plan mode is active` in system-reminder tags. If not present,
  output error message and stop immediately.
- **Skill loading**: Read `~/.claude/skills/project-conventions/SKILL.md` early - it informs the
  default conventions presented in Round 2.
- **Interactive Q&A in parent agent**: The main slash command handler conducts the interactive
  discussion via AskUserQuestion. Do not delegate Q&A to subagents.
- **Split planning and execution**: Planning (Q&A + plan writing) happens before ExitPlanMode.
  Execution (file creation) happens only after user approves.
- **Exact file content in plan**: The plan file must contain the exact content of every file to be
  created, not just descriptions. This lets the user review everything before approval.
- **Progress tracking**: Use TodoWrite to track scaffold progress during execution.
- **Convention defaults**: If project-conventions skill is not available, use the defaults documented
  in Round 2 of this command.
