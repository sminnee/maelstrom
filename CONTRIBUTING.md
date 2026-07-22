# Contributing

Issues and pull requests are welcome.

## Setup

```bash
git clone https://github.com/sminnee/maelstrom.git
cd maelstrom
uv sync --all-extras
uv tool install --editable .
```

Python 3.11 or newer, and [uv](https://docs.astral.sh/uv/). `mael install` puts the Claude Code
skills and hooks in place if you want to use maelstrom on itself.

## Before you commit

```bash
uv run pytest --ignore=tests/e2e   # unit tests
uv run pytest tests/e2e/ -v        # end-to-end tests
bin/lint                           # pyright type checking
```

These are the three jobs `.github/workflows/test.yml` runs, and `bin/publish` runs the same three
before it uploads anything. During development `uv run pytest -m 'not slow'` skips the slow tests
for a faster loop, but run the full set before you push.

## Commits and pull requests

Prefix commits with `feat:`, `fix:`, `refactor:` or `chore:`. Explain why the change is right,
not what the diff already shows.

Add an entry to the `Unreleased` section of `CHANGELOG.md` for anything a user would notice — a
new command, a new flag, a changed default, a removed command. Changed behaviour especially: it
is what someone upgrading is scanning for, and the changelog is where they find out. `bin/publish`
refuses to release while `Unreleased` is empty.

Keep the documentation in step with the behaviour in the same change. `docs/reference/cli.md`
covers every command and flag, `docs/reference/configuration.md` every config key, and
`docs/reference/environment.md` every environment variable.

`CONTEXT.md` is the domain glossary. Read it before you name anything or write prose, and reuse
its terms verbatim — including the words each term says to avoid. New domain terms belong there
rather than defined inline.

## Releasing

See the [Release](README.md#release) section of the README.
