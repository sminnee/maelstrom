"""Global test fixtures for maelstrom test suite."""

import os
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.cmux.client import RecordingCmuxClient
from maelstrom.cmux.model import CmuxLayout


def _can_bind_a_unix_socket() -> bool:
    """Whether this process may ``bind()`` at all.

    Probed rather than sniffed for, so it stays right whatever the sandbox is.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.bind(str(Path(tmp) / "probe.sock"))
        except OSError:
            return False
    return True


def pytest_collection_modifyitems(config, items):
    """Skip the tests that need a ``bind()`` when this process may not."""
    if _can_bind_a_unix_socket():
        return
    skip = pytest.mark.skip(reason="the sandbox denies bind() on a Unix socket")
    for item in items:
        if "binds_socket" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True, scope="session")
def _block_real_cmux():
    """Prevent any test from accidentally invoking the real cmux binary.

    Patches the binary discovery used by the real transport to return None (no
    binary found) and removes CMUX_SOCKET_PATH from the environment, so
    ``current_client()`` returns None and nothing shells out.
    """
    saved = os.environ.pop("CMUX_SOCKET_PATH", None)
    with patch("maelstrom.cmux.client._find_cmux_cli", return_value=None):
        yield
    if saved is not None:
        os.environ["CMUX_SOCKET_PATH"] = saved


@pytest.fixture(autouse=True)
def _isolate_agent_paths(monkeypatch, tmp_path):
    """Keep every test off a real daemon root.

    Every command reads ``MAEL_AGENT_ROOT``, and a developer's shell sets it to
    a live root. An unpinned test would read the real spawn records and see
    whatever agents run on the machine.
    """
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "maelstrom"))


@pytest.fixture(autouse=True)
def _isolate_state_db_paths(monkeypatch, tmp_path):
    """Keep every test off the developer's live ``~/.maelstrom``.

    The desk ladder's import rung reads ``desk.json`` from there, so any test
    that migrates a database would otherwise pull the developer's real desk
    into its own. That read is silent: the rows arrive looking like the test's
    own, and the test fails somewhere else entirely.

    Both of this module's resolvers are pinned: the database and the files the
    rungs import resolve through different ones. See ``state_db/paths.py``.

    Pinned here rather than through ``MAEL_STATE_ROOT``, so no command reads as
    a playpen and prints the note.

    Autouse rather than per-test, because a test author cannot be expected to
    know that opening an in-memory database reaches a file at all.
    """
    monkeypatch.setattr(
        "maelstrom.state_db.paths.get_maelstrom_dir", lambda: tmp_path / "maelstrom"
    )
    monkeypatch.setattr(
        "maelstrom.state_db.paths.get_state_root", lambda: tmp_path / "maelstrom"
    )


@pytest.fixture(autouse=True)
def _pin_harness_env(monkeypatch):
    """Keep the outer shell's harness out of the tests.

    ``resolve_harness`` detects the harness from ``CLAUDECODE`` /
    ``OPENCODE_TERMINAL``, so running pytest inside a Claude Code or OpenCode
    session would otherwise flip every default-launch test to that harness.
    Tests for the detection itself patch the env explicitly.
    """
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("OPENCODE_TERMINAL", raising=False)


@pytest.fixture(autouse=True)
def _block_real_claude_branch_gen(monkeypatch):
    """Prevent branch-name generation from shelling out to a live ``claude``.

    ``branch_name._run_claude`` invokes ``claude -p`` to pick a descriptive
    branch slug; in tests we force it to fail so generation falls back to the
    deterministic offline slug. Tests that want to exercise the model path
    inject a fake ``runner`` into ``generate_branch_name`` (or re-patch
    ``_run_claude`` themselves) — the later ``monkeypatch.setattr`` wins.
    """
    from maelstrom import branch_name

    def _unavailable(prompt: str) -> str:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(branch_name, "_run_claude", _unavailable)


@pytest.fixture(autouse=True)
def _reset_task_db():
    """Forget the task CLI's cached database between tests.

    ``task_cli`` opens the state database once and keeps it in a module global,
    which is right in production — one process is one invocation. In the suite
    one process runs every test, so without this a test's temporary database
    would serve the next one, and the leak is invisible: the reads succeed and
    answer about the wrong notebook.
    """
    from maelstrom import task_cli

    task_cli._DB = None
    task_cli._CHECKED = False
    yield
    if task_cli._DB is not None:
        task_cli._DB.close()
    task_cli._DB = None
    task_cli._CHECKED = False


@pytest.fixture()
async def state_db():
    """A migrated in-memory state database, closed when the test ends.

    Four suites open one the same way. What each builds on top differs — a task
    table, a desk store, an export queue — so only the database is shared; the
    store fixtures stay with the contracts they exercise.
    """
    from maelstrom.state_db.migrate import open_state_db

    db = open_state_db(":memory:")
    await db.migrate()
    yield db
    db.close()


@pytest.fixture()
def store():
    """Shared task-table fixture for the model / CLI / actions test suites.

    Named ``store`` because that is what several hundred tests already call it;
    what it yields is an :class:`~maelstrom.task_table.InMemoryTaskTable`, the
    model's one injected collaborator now that the notebook is a table.

    The contract itself is exercised against both backends in
    ``tests/test_task_table.py``; here the in-memory twin keeps the behaviour
    suites fast and free of a database file.
    """
    from maelstrom.task_table import InMemoryTaskTable

    return InMemoryTaskTable()


@pytest.fixture()
def recording_layout():
    """Return a factory for a :class:`CmuxLayout` over a :class:`RecordingCmuxClient`.

    Call ``recording_layout(responses, name="ws")`` to build a layout whose
    client records every ``run`` call in ``client.calls`` and returns scripted
    results. ``responses`` is either a dict keyed by the exact args tuple or a
    callable ``fn(*args) -> str | None``. The returned tuple is
    ``(layout, client)`` so tests can assert on ``client.calls``.
    """

    def make(responses=None, name="myproject-alpha"):
        client = RecordingCmuxClient(responses)
        return CmuxLayout(client, name), client

    return make
