"""Read the root group's ``--json`` flag from a CLI command."""

import click


def wants_json() -> bool:
    """True when ``mael --json`` is set.

    False when no parent context set the flag, such as a subcommand invoked
    directly in a test.
    """
    ctx = click.get_current_context(silent=True)
    obj = ctx.obj if ctx else None
    return bool(obj and obj.get("json", False))
