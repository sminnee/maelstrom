# Python Review Guide

Python-specific criteria for maelstrom projects.

## Style

- PEP 8 compliance, verified with `uv run ruff check .`
- 88 character line length (black default)
- Imports at top of file, not in function/method bodies
- Imports ordered: stdlib, third-party, local (with blank lines between)

## Type Hints

- All public functions should have type hints
- Use `| None` for optional returns
- Use `list[T]` over `List[T]` (Python 3.9+)

## Documentation

- Public functions need docstrings (one-line summary minimum)
- Document Args/Returns/Raises only when not obvious from types

## Testing

- Test files: `test_<module>.py`
- Test classes: `Test<ClassName>` or `Test<FunctionName>`
- Use fixtures for common setup
- Use plain `assert` statements

## Patterns

- Use `pathlib.Path` over `os.path`
- Use `subprocess.run` over `os.system`
- Use context managers for resource cleanup

## CLI (Click)

- Use `click.group` for subcommands
- Use `click.ClickException` for user-facing errors
- Use `click.echo` for output

## Data

- Use dataclasses for simple data containers
