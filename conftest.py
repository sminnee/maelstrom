"""Test fixtures for every workspace member.

Each member's ``tests/`` sits below this file, so what is here applies to
every suite. It imports no package: a member's suite must run without the
``mael`` CLI. The CLI's fixtures are in ``cli/tests/conftest.py``.
"""

import socket
import tempfile
from pathlib import Path

import pytest


def _can_bind_sockets() -> bool:
    """Whether this process may ``bind()`` a Unix socket and a loopback TCP port.

    Probed rather than sniffed for, so it stays right whatever the sandbox is.
    The daemon listens on the first and the orchestrator server on the second.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.bind(str(Path(tmp) / "probe.sock"))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
        except OSError:
            return False
    return True


def pytest_collection_modifyitems(config, items):
    """Skip the tests that need a ``bind()`` when this process may not."""
    if _can_bind_sockets():
        return
    skip = pytest.mark.skip(reason="the sandbox denies bind()")
    for item in items:
        if "binds_socket" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_agent_paths(monkeypatch, tmp_path):
    """Keep every test off a real daemon root.

    Every command reads ``MAEL_AGENT_ROOT``, and a developer's shell sets it to
    a live root. An unpinned test would read the real spawn records and see
    whatever agents run on the machine.
    """
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "maelstrom"))


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
