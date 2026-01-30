# Code Review Guide

Core review criteria for all maelstrom projects.

## Pre-Flight

- **Uncommitted changes** (blocking): All work must be committed before review

## Commit Quality

### Messages

- Explain WHY, not just WHAT (the diff shows WHAT)
- Use imperative mood ("Add", "Fix", not "Added", "Fixing")
- Subject line under 72 characters
- Reference issue IDs where applicable

### Atomicity

- One logical change per commit
- If you need "and" in the message, consider splitting
- Avoid mixing feature code with refactoring

## Code Quality

### Duplication

- Check for existing code that could be extended
- Avoid copy-pasting code blocks
- Avoid creating features that overlap with existing functionality

### Test Coverage

- Behavior changes need tests
- Tests should assert behavior, not just exercise code
- Cover edge cases and error conditions

### Error Handling

- User-facing errors need clear, actionable messages
- Avoid silent failures

## Cross-Cutting

### Feature Scope

- Extend existing patterns where applicable
- Avoid creating parallel systems
- Follow established conventions

### Security

- Sanitize user input
- No hardcoded credentials
- Watch for injection vulnerabilities (SQL, command)

### Performance

- Watch for N+1 query patterns
- Avoid unnecessary data loading
