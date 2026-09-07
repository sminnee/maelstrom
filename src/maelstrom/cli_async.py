"""One event loop for the whole CLI.

The core is async where it does I/O, so a command that reads worktrees or
talks to the agent daemon is a coroutine. Click does not await a callback, so
something has to. That something is here, and there is one of it.

:class:`AsyncGroup` is the group every ``mael`` group is built with. It opens
one loop per invocation and runs every coroutine callback under the tree on
it. A command converts by adding ``async`` and nothing else.

One loop per invocation, rather than one per command, is the whole point.
``asyncio.run`` cannot nest, so a codebase with a loop per call site has to
keep a blocking twin of everything a command might reach — which is what the
sync ``DaemonClient`` and ``session_discovery._sweep_blocking`` were. With one
loop at the top, nothing under a command re-enters asyncio, so the twins go.

This module is a leaf: it imports Click and the standard library, and nothing
of maelstrom's own, so any CLI module may build its group with it.
"""

import asyncio
import functools
import inspect
from typing import Any

import click


def _run(result: Any) -> Any:
    """Await ``result`` on this invocation's loop, if it is a coroutine.

    A sync callback returns its value straight through, so a group can hold
    both kinds and no caller has to know which it invoked.
    """
    if not inspect.isawaitable(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop yet: this is the outermost command, so it owns one.
        try:
            return asyncio.run(result)
        except KeyboardInterrupt:
            # A real Ctrl-C during an await never reaches the command: asyncio
            # cancels the task and re-raises here, outside it. So a command
            # cannot catch its own interrupt, and this is the only place that
            # can. `daemon serve` and `tail -f` end this way normally, so it
            # is a clean exit rather than an `Aborted!` and a non-zero code.
            return None
    # A loop is already running, so a group above already opened it. Handing
    # the coroutine back unawaited would be a silent no-op, so this is a bug
    # in the caller rather than a case to paper over.
    raise RuntimeError(
        f"a loop is already running on {loop!r}; a command must not open a second"
    )


class AsyncCommand(click.Command):
    """A command whose callback may be ``async def``."""

    def invoke(self, ctx: click.Context) -> Any:
        return _run(super().invoke(ctx))


class AsyncGroup(click.Group):
    """A group whose own callback, commands and subgroups may be ``async def``.

    The loop is opened once, around the whole invocation, so a group callback
    and the command it dispatches to share it.
    """

    command_class = AsyncCommand
    #: ``type`` is Click's sentinel for "this class", so a subgroup declared
    #: with ``@group.group(...)`` is an ``AsyncGroup`` too.
    group_class = type

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Click runs a group's own callback inside `Group.invoke` and discards
        # what it returns, so wrapping the return value alone would drop an
        # `async def` group callback with nothing but a RuntimeWarning: the
        # group's setup would silently not happen and the subcommand would run
        # against unset state. Wrapping the callback itself is what closes
        # that hole, and it is where Click calls it that matters, not what
        # `invoke` hands back.
        if self.callback is not None:
            self.callback = _awaiting(self.callback)

    def invoke(self, ctx: click.Context) -> Any:
        return _run(super().invoke(ctx))


def _awaiting(callback: Any) -> Any:
    """``callback``, with a coroutine result awaited before it is returned."""

    @functools.wraps(callback)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return _run(callback(*args, **kwargs))

    return wrapper
