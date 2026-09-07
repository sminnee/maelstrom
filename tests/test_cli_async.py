"""The CLI runs on one event loop, which ``main`` opens.

The seam is the command boundary: a command whose callback is a coroutine
runs, and it can await the async core without opening a loop of its own.
"""

import asyncio
import os
import signal
import threading
import time

import click
from click.testing import CliRunner

from maelstrom.cli_async import AsyncGroup


def test_a_coroutine_command_runs():
    """An `async def` callback is awaited, not returned unrun."""

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.command("greet")
    async def greet():
        await asyncio.sleep(0)
        click.echo("hello")

    result = CliRunner().invoke(root, ["greet"])

    assert result.exit_code == 0
    assert result.output == "hello\n"


def test_a_sync_command_still_runs():
    """Most commands are still plain functions; they keep working."""

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.command("greet")
    def greet():
        click.echo("hello")

    result = CliRunner().invoke(root, ["greet"])

    assert result.exit_code == 0
    assert result.output == "hello\n"


def test_one_loop_serves_the_whole_command():
    """Two awaits in one command see the same loop.

    This is what lets the core drop its `asyncio.run` bridges: nothing under
    a command re-enters asyncio, so nothing has to ask whether a loop is
    already running.
    """
    seen = []

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.command("twice")
    async def twice():
        seen.append(asyncio.get_running_loop())
        await asyncio.sleep(0)
        seen.append(asyncio.get_running_loop())

    result = CliRunner().invoke(root, ["twice"])

    assert result.exit_code == 0
    assert seen[0] is seen[1]


def test_a_subgroup_declared_on_the_parent_runs_its_coroutines():
    """`group_class = type` makes a decorator-declared subgroup async too."""

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.group("task")
    def task_group():
        pass

    assert isinstance(task_group, AsyncGroup)

    @task_group.command("add")
    async def add():
        await asyncio.sleep(0)
        click.echo("added")

    result = CliRunner().invoke(root, ["task", "add"])

    assert result.exit_code == 0
    assert result.output == "added\n"


def test_a_subgroup_attached_with_add_command_runs_its_coroutines():
    """The shape `cli.py` actually uses.

    Every `mael` subgroup is declared in its own module and attached with
    `cli.add_command(...)`, so it is whatever class that module chose —
    `group_class` never reaches it. Its coroutines still have to run.
    """

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @click.group("task", cls=AsyncGroup)
    def task_group():
        pass

    @task_group.command("add")
    async def add():
        await asyncio.sleep(0)
        click.echo("added")

    root.add_command(task_group)

    result = CliRunner().invoke(root, ["task", "add"])

    assert result.exit_code == 0
    assert result.output == "added\n"


def test_a_command_owns_one_loop_end_to_end():
    """Loop-bound state set before an await is still valid after it.

    This is the property the core relies on: a client, a lock or an event
    made early in a command is still usable later in it, so nothing has to
    be rebuilt per await.
    """

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.command("stateful")
    async def stateful():
        gate = asyncio.Event()  # binds to the running loop
        await asyncio.sleep(0)
        gate.set()
        await gate.wait()
        click.echo("held")

    result = CliRunner().invoke(root, ["stateful"])

    assert result.exit_code == 0
    assert result.output == "held\n"


def test_an_error_from_a_coroutine_reaches_click():
    """A domain error raised across an await still becomes an exit code."""

    @click.group(cls=AsyncGroup)
    def root():
        pass

    @root.command("boom")
    async def boom():
        await asyncio.sleep(0)
        raise click.ClickException("it broke")

    result = CliRunner().invoke(root, ["boom"])

    assert result.exit_code == 1
    assert "it broke" in result.output


class TestTheRealTransportRunsUnderACommand:
    """A command must reach the real socket client, not only a test fake.

    The suite drives every `mael agent` command through
    `RecordingDaemonClient`, so a client that opened a loop of its own was
    never exercised: `mael agent daemon list` raised "asyncio.run() cannot be
    called from a running event loop" while the whole suite stayed green.

    The seam is the command boundary with the production factory in place; the
    socket is what is faked, one level below.
    """

    def test_a_command_awaits_the_socket_client(self, monkeypatch):
        from maelstrom import agent_transport

        asked = []

        async def fake_round_trip(socket_path, payload):
            asked.append(payload)
            return {"ok": True, "agents": []}

        monkeypatch.setattr(agent_transport, "request_over_socket", fake_round_trip)

        @click.group(cls=AsyncGroup)
        def root():
            pass

        @root.command("ask")
        async def ask():
            reply = await agent_transport.client().request({"cmd": "list"})
            click.echo(str(reply["ok"]))

        result = CliRunner().invoke(root, ["ask"])

        assert result.exit_code == 0, result.output
        assert result.output == "True\n"
        assert asked == [{"cmd": "list"}]


class TestCtrlCEndsALongRunningCommand:
    """Ctrl-C is how `daemon serve` and `tail -f` normally end.

    A real SIGINT during an await does not surface where a synthetic raise
    does: asyncio cancels the task and re-raises `KeyboardInterrupt` from
    `asyncio.run`, outside the command. So a command's own
    `except KeyboardInterrupt` never runs, and the interrupt has to be caught
    where the loop is opened.
    """

    @staticmethod
    def _interrupt_soon():
        """Send this process a real SIGINT once the command is awaiting."""

        def fire():
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=fire, daemon=True).start()

    def test_an_interrupted_command_exits_cleanly(self):
        """Not `Aborted!` and not exit 1: stopping a server is not a failure."""

        @click.group(cls=AsyncGroup)
        def root():
            pass

        @root.command("serve")
        async def serve():
            await asyncio.sleep(10)

        self._interrupt_soon()
        result = CliRunner().invoke(root, ["serve"], standalone_mode=False)

        assert result.exit_code == 0, result.output
        assert "Aborted" not in result.output


class TestAGroupCallbackIsAwaitedToo:
    """A group's own callback runs before the command it dispatches to.

    Click invokes it inside `Group.invoke` and discards what it returns, so an
    `async def` group callback was dropped with only a `RuntimeWarning` — the
    setup silently not happening, and the subcommand running against unset
    state. The module promises a callback converts by adding `async` and
    nothing else; that has to hold for a group as much as a command.
    """

    def test_an_async_group_callback_runs_before_its_subcommand(self):
        order = []

        @click.group(cls=AsyncGroup)
        async def root():
            await asyncio.sleep(0)
            order.append("group")

        @root.command("go")
        async def go():
            order.append("command")

        result = CliRunner().invoke(root, ["go"])

        assert result.exit_code == 0, result.output
        assert order == ["group", "command"]

    def test_a_sync_group_callback_still_runs(self):
        order = []

        @click.group(cls=AsyncGroup)
        def root():
            order.append("group")

        @root.command("go")
        async def go():
            order.append("command")

        result = CliRunner().invoke(root, ["go"])

        assert result.exit_code == 0, result.output
        assert order == ["group", "command"]
