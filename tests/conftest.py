"""Global test fixtures for maelstrom test suite."""

import os
from unittest.mock import patch

import pytest

from maelstrom.cmux.client import RecordingCmuxClient
from maelstrom.cmux.model import CmuxLayout


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


@pytest.fixture()
def store():
    """Shared task-store fixture for the model / CLI / actions test suites.

    Centralises store construction so the task-index cache can be wired in behind
    the reads with a single fixture change (see ``docs/dev/architecture-patterns.md``
    and the SQLite task-index work). Today it yields a bare
    :class:`~maelstrom.task_store.InMemoryStore`; the index is layered on later via
    :func:`_task_index` without touching any call site.
    """
    from maelstrom.task_store import InMemoryStore

    return InMemoryStore()


@pytest.fixture(autouse=True)
def _task_index(monkeypatch):
    """Give every test its own fresh in-memory task index.

    ``store`` and ``index`` are the model's two injected collaborators. Production
    wires a real on-disk :class:`~maelstrom.task_index.SqliteTaskIndex` from the CLI;
    the model falls back to a module-level default (``task._DEFAULT_INDEX``) for any
    call that omits ``index``. This fixture swaps that default for a *per-test* fresh
    in-memory SQLite index, so every behaviour test exercises the real index
    transparently without naming it, and no state leaks between tests. The ``store``
    fixture stays a plain store — the index sits beside it, not behind it.

    An in-memory store and this in-memory index both report ``head() is None`` and
    nothing stamps the index HEAD, so the model's staleness guard treats the index as
    fresh (``None == None``) and reads are served from it — the point of the exercise.
    """
    from maelstrom import task as model
    from maelstrom.task_index import SqliteTaskIndex

    monkeypatch.setattr(model, "_DEFAULT_INDEX", SqliteTaskIndex(":memory:"))
    yield


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
