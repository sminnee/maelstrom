# Learn Project Structure Common

# STEP 1: Learn project structure

Produce a comprehensive `project-conventions.md` describing development conventions. The output
document has language sections (e.g. "Python", "TypeScript") plus a cross-cutting section, based on
what's actually in the project.

## Deliverable

A single file: `/project-conventions.md` in project root

## Key principles

- **Generic, not project-specific**: Describe the _patterns_ and _conventions_, not the specific
  classes, services, or libraries in the repo. For example, write "services each have their own
  `pyproject.toml`" not "smartypants, typesetter, and lector each have their own `pyproject.toml`".
- **Language sections, not platform sections**: Use "Python" and "TypeScript" rather than "Server
  Python" and "Web TypeScript". If there are genuinely different conventions for different platforms
  within the same language, note the differences within the language section.
- **`.gitignore` is per-language**: The entries in `.gitignore` are language/platform-specific, so
  document them in the relevant language section, not as cross-cutting.

## Structure of the output file

### Header

- List of languages/platforms present in the project

### Code layout (top-level section, before language sections)

- Overall repo structure: monorepo vs single-package, key top-level directories
- How services and libraries are organised (pattern, not specific names)
- Where shared code lives
- How the languages/platforms relate to each other

### Per-language sections

Discover which languages exist by examining the repo (e.g. Python source, TypeScript/Node packages,
Rust/Cargo, Swift/Xcode, Go modules, etc.). For each language found, create a section labelled by
language (e.g. "Python", "TypeScript", "Rust", "Go", "Swift").

Each language section covers the following (where applicable):

#### Package/dependency management

- Which package manager is used and how (lock files, install commands)
- Build system / build backend
- How dependencies are grouped (dev, optional, peer, etc.)
- Entry points / binary definitions
- Workspace/monorepo setup for this language (if applicable)

#### Linting and formatting

- Which tools are configured (look in config files, package.json scripts, pyproject.toml `[tool.*]`
  sections, dedicated config files like biome.json, .eslintrc, ruff.toml, etc.)
- Key rules or rule sets enabled
- How to run the linter/formatter

#### Type checking

- Which type checker (if any) and its configuration
- Strictness level and notable compiler/checker options
- Type annotation conventions observed in the code

#### Testing

- Test framework and configuration
- Test directory structure and naming conventions
- How tests are organized (class-based, describe blocks, flat functions)
- Fixture/setup patterns
- How to run tests (unit, e2e, coverage)
- Mocking patterns

#### Dead code detection

- Which tool (if any) and its configuration
- How to run it

#### Code style conventions

- Examine 3-4 representative source files for:
  - Import ordering
  - Docstring/JSDoc style and level of coverage
  - Error handling patterns
  - Naming conventions (constants, classes, functions, files)
  - Common patterns (dataclasses, factory methods, builder patterns, etc.)
- Document the _general patterns_ observed, not specific class/function names from the project

#### .gitignore

- What categories of files are ignored for this language (build outputs, caches, env files, etc.)

### Cross-cutting sections (after all language sections)

#### EditorConfig

- Document `.editorconfig` rules if present, or note its absence

#### Git

- Branch conventions if documented
- Pre-commit hooks and what they do

#### CI/CD

- GitHub Actions (or other CI) workflows
- What each workflow does (triggers, jobs, steps)
- How workflows are scoped (path filters, branch filters)
- Note which aspects are language-specific vs cross-cutting

#### Anything else notable

- Makefiles, justfiles, custom scripts in `bin/`
- Dev environment setup (Docker, devcontainers, .env files, etc.)
- Documentation structure
- Integration/E2E testing setup (if cross-language)

## Execution approach

1. **Discover languages**: Glob for `pyproject.toml`, `package.json`, `Cargo.toml`, `*.xcodeproj`,
   `go.mod`, etc. to determine which languages exist
2. **Read all config files**: EditorConfig, gitignore, CI workflows, linter configs, tsconfig,
   pyproject.toml, package.json, biome.json, etc.
3. **Sample source code**: Read 3-4 representative source files per language to observe code style
   conventions
4. **Sample test code**: Read 2-3 test files per language to observe testing conventions
5. **Write the document**: Produce `/project-conventions.md` with the structure above, only including
   sections for languages actually present. Describe patterns generically without naming specific
   services, libraries, or classes from the project.

## Verification

- Re-read the generated file
- Spot-check claims against actual config files
- Ensure no language present in the repo was missed
- Verify that specific service/library names are not mentioned — only generic patterns

# STEP 2: Apply to project-conventions skill

Check for a 'project-conventions' skill in ~/.claude/skills/project-conventions/SKILL.md

If it does not exist:

- add a yaml front-matter to `project-conventions.md` with the description "Conventions when setting a up project. Use t
- move project-conventions.md to ~/.claude/skills/project-conventions/SKILL.md

If it already exists:

- Read both ~/.claude/skills/project-conventions/SKILL.md
- Read project-conventions.md
- Make changes to SKILL.md to incorporate new information from project-conventions.md. Where there are conflicts, ask the user which convention is their preference.
