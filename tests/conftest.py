"""Fixtures for the suite that drives the CLI against a real orchestrator server.

The same isolation as ``cli/tests``: no real notebook, no naming model on the
network, no real cmux.
"""

import asyncio
import threading
from collections.abc import Iterator

import pytest
from domain_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    _block_real_cmux,  # pyright: ignore[reportUnusedImport]
    _block_real_naming_model,  # pyright: ignore[reportUnusedImport]
    _isolate_notebook_root,  # pyright: ignore[reportUnusedImport]
    _mark_test_commands_production,  # pyright: ignore[reportUnusedImport]
    _plain_terminal,  # pyright: ignore[reportUnusedImport]
)
from served_orchestrator import NOW, OTHER_PROJECT, PROJECT, Served, point_config_at

from mael_agent.agent_transport import ScriptedAsyncDaemonClient
from mael_domain.task_table import InMemoryTaskTable
from mael_orchestrator.routes import build_app, serving
from mael_orchestrator.server import Orchestrator
from mael_orchestrator.sources import InMemoryWorktreeSource, NotebookTaskSource


@pytest.fixture
def table() -> InMemoryTaskTable:
    return InMemoryTaskTable()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A ``$HOME`` of the test's own, and no session task in the environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MAEL_TASK_ID", raising=False)
    monkeypatch.delenv("MAEL_TASK_PARENT", raising=False)
    return tmp_path


@pytest.fixture
def start_server(table, home) -> Iterator:
    """Serve an orchestrator over ``table`` on its own thread and loop.

    The server is started lazily by :func:`start`, so a test seeds the table
    first and the server's first read holds what it seeded.
    """
    holder: dict = {}

    def start(worktrees: InMemoryWorktreeSource | None = None) -> Served:
        orch = Orchestrator(
            NotebookTaskSource(table, lambda: [PROJECT, OTHER_PROJECT]),
            worktrees or InMemoryWorktreeSource(),
            ScriptedAsyncDaemonClient(),
            clock=lambda: NOW,
        )
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        stop = asyncio.Event()

        async def serve() -> None:
            async with serving(build_app(orch), "127.0.0.1", 0) as port:
                holder["port"] = port
                ready.set()
                await stop.wait()

        thread = threading.Thread(target=loop.run_until_complete, args=(serve(),))
        thread.start()
        assert ready.wait(10), "the server never bound its port"
        holder.update(loop=loop, stop=stop, thread=thread)
        url = f"http://127.0.0.1:{holder['port']}"
        point_config_at(url)
        return Served(table, url)

    yield start  # type: ignore[misc]
    if "loop" in holder:
        holder["loop"].call_soon_threadsafe(holder["stop"].set)
        holder["thread"].join(10)
        holder["loop"].close()
