# Review Branch Command

**PLAN MODE REQUIRED**: This command ONLY works in plan mode. You must stop with an error message
immediately if not in plan mode.

Reviews the current feature branch, examining each commit for quality and correctness. Review
findings become the implementation plan - executing the approved plan fixes the issues.

This command replaces the marketplace `/code-review` plugin for maelstrom projects. The marketplace
plugin posts GitHub PR comments; this workflow is for local review-then-fix before PR submission.

## Usage

```
/review-branch
```

## What This Command Does

1. **Validates environment** - Fails if not in plan mode
2. **Checks for uncommitted changes** - All work must be committed for proper review
3. **Gets branch commits** - All commits since diverging from origin/main
4. **Loads review guides** - Cross-project and per-project review criteria
5. **Reviews each commit** - Using Task tool with Explore agents
6. **Reviews overall change** - Cross-cutting concerns across all commits
7. **Writes findings to plan file** - Organized by commit with fix instructions
8. **Exits plan mode** - With allowed prompts for fixing issues

## Command Logic

### 1. Plan Mode Check (MANDATORY)

Check for `Plan mode is active` in system-reminder tags. If not present:

```
Error: Review command requires plan mode. Please enter plan mode first.
```

Stop immediately - do not proceed with any other logic.

### 2. Check for Uncommitted Changes

Run `git status --porcelain` to detect uncommitted changes.

If uncommitted changes exist, add as the first review finding:

```markdown
## Pre-Flight Issues

- **[Uncommitted Changes]**: Working directory has uncommitted changes
  - Files: <list of files>
  - Action: Commit these changes before proceeding with review
```

### 3. Get Commit Information

Use `mael gh show-code --committed` to get commits since branching from main.

Parse the output to extract:

- List of commits with SHA and message
- Combined diff for context

For detailed per-commit review, also run:

```bash
git log --format="%H|%s" origin/main..HEAD
```

Then for each commit SHA, get its diff with:

```bash
git show --format="" <sha>
```

### 4. Load Review Guides

Use Task tool with Explore agent to find and read review guides:

**Cross-project guide (required):**

Find the maelstrom shared directory and read `review-guides/core.md`. This contains universal
review criteria that apply to all projects.

**Per-project guides (optional):**

Check for `.claude/review-guides/` in the current project directory. Read all `.md` files found
there. These contain language-specific and project-specific criteria.

### 5. Per-Commit Review

For each commit on the branch, review against these criteria:

**Commit Message Quality:**

- Does the message explain WHY the change was made (not just WHAT)?
- Is it properly formatted (imperative mood, reasonable length)?
- Does it reference issue IDs where appropriate?

**Code Quality:**

- Does code meet project coding standards (from CLAUDE.md and review guides)?
- Are there any obvious bugs or issues?
- Is error handling appropriate?

**Test Coverage:**

- Are behavior changes covered by tests?
- Are tests meaningful (not just coverage padding)?

**Duplication Check:**

- Is there unnecessary code duplication?
- Could changes extend existing code rather than duplicate?
- Is there functional duplication (new feature vs extending existing)?

Use Task tool with Explore agents to examine:

- The commit's diff
- Related existing code that might be duplicated
- Test files to verify coverage

### 6. Cross-Cutting Review

Review all commits together for:

- **Logical coherence**: Do the commits tell a coherent story?
- **Atomicity**: Is each commit one logical change?
- **Architectural fit**: Does the overall change fit the codebase architecture?
- **Feature scope**: Could this extend existing features rather than add new ones?

### 7. Write Plan

Write review findings to the plan file (path provided in system context).

Structure the plan so that executing it will fix the issues:

```markdown
# Code Review Findings

## Summary

- X commits reviewed
- Y issues found
- Z suggestions

## Pre-Flight Issues

(If any uncommitted changes or blocking issues)

## Commit: <sha> "<message>"

### Issues

- **[Issue Type]**: Description of the issue
  - File: path/to/file.py:123
  - Existing: path/to/existing.py:45 (if duplication)
  - Fix: Describe what needs to change
  - Fixup target: <sha>

### Suggestions

- **[Suggestion Type]**: Optional improvements (non-blocking)

## Cross-Cutting Issues

- **[Issue Type]**: Issues spanning multiple commits

## After Fixes

1. Each fix should be committed with `git commit --fixup=<sha>`
2. Ask user to confirm the fixup commits are acceptable
3. If approved, run `mael review squash` to combine fixups with originals
4. Create PR with `mael gh create-pr`
```

### 8. Exit Plan Mode

Call ExitPlanMode with allowedPrompts:

```json
[
  { "tool": "Bash", "prompt": "create fixup commit" },
  { "tool": "Bash", "prompt": "run tests" },
  { "tool": "Bash", "prompt": "squash fixup commits" },
  { "tool": "Bash", "prompt": "create PR" }
]
```

## Review Criteria Summary

From the review guides, prioritize these criteria:

| Criterion              | Severity  | Description                                    |
| ---------------------- | --------- | ---------------------------------------------- |
| Uncommitted changes    | Blocking  | All work must be committed                     |
| Commit message quality | Important | Explains WHY, imperative mood                  |
| Code duplication       | Important | Extend existing code, don't duplicate          |
| Functional duplication | Important | Extend existing features, don't create parallel |
| Test coverage          | Important | Behavior changes need tests                    |
| Coding standards       | Important | Follow project conventions                     |
| Commit atomicity       | Moderate  | One logical change per commit                  |

## Fixup Commit Workflow

When executing the plan to fix issues:

1. **Make the code fix**
2. **Stage the changes**: `git add <files>`
3. **Create fixup commit**: `git commit --fixup=<original-sha>`

The commit message will automatically be `fixup! <original message>`.

### Confirmation Before Squash

After all fixup commits are created, **ask the user to confirm** before squashing:

1. Show what fixup commits exist using `git log --oneline origin/main..HEAD`
2. Show the diff of the fixup changes using `git show <fixup-sha>` for each fixup
3. Use AskUserQuestion to ask the user if the changes are acceptable:
   - **Approve**: Proceed to squash and create PR
   - **Revise**: Make additional changes before squashing
   - **Abort**: Cancel the workflow

### Squashing

Only after user approval, run:

```bash
mael review squash
```

This squashes all fixup commits into their target commits.

## Error Cases

- **Not in plan mode**: Stop with error message
- **Not on a feature branch**: "Cannot review main branch"
- **No commits ahead of main**: "No commits to review"
- **No origin/main**: "Cannot determine merge-base"

## Example Output

```markdown
# Code Review Findings

## Summary

- 3 commits reviewed
- 2 issues found
- 1 suggestion

## Commit: abc1234 "Add user registration"

### Issues

- **[Commit Message]**: Message doesn't explain why registration was added
  - Fix: Amend commit to explain the business need

- **[Test Coverage]**: No tests for email validation edge cases
  - File: src/auth/register.py:45
  - Fix: Add tests for invalid email formats
  - Fixup target: abc1234

## Commit: def5678 "Add validation helpers"

### Issues

- **[Duplication]**: `validate_email()` duplicates existing validator
  - File: src/auth/validation.py:12
  - Existing: src/utils/validators.py:78
  - Fix: Remove duplicate, import from utils
  - Fixup target: def5678

### Suggestions

- **[Naming]**: Consider `is_valid_email()` for consistency with other validators

## After Fixes

1. Each fix should be committed with `git commit --fixup=<sha>`
2. Ask user to confirm the fixup commits are acceptable
3. If approved, run `mael review squash` to combine fixups with originals
4. Create PR with `mael gh create-pr`
```

## Implementation Notes

- **Plan mode detection**: Check for `Plan mode is active` in system-reminder tags
- **Progress tracking**: Use TodoWrite to track review progress
- **Explore agents**: Use Task tool with Explore subagent for codebase research
- **Git operations**: Use `mael gh show-code` for commits, direct git for per-commit diffs
