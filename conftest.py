"""Test fixtures for every workspace member.

Each member's ``tests/`` sits below this file, so what is here applies to
every suite. It imports no package: a member's suite must run without the
``mael`` CLI. The CLI's fixtures are in ``cli/tests/conftest.py``.
"""

import socket
import ssl
import subprocess
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


@pytest.fixture(autouse=True)
def _no_dev_certificate(monkeypatch):
    """Keep a developer's dev certificate out of the tests.

    ``mael-orchestrator serve`` takes ``DEV_TLS_CERT`` and ``DEV_TLS_KEY`` as
    defaults, so a shell under ``dev_https:`` would turn every serve test to TLS.
    """
    monkeypatch.delenv("DEV_TLS_CERT", raising=False)
    monkeypatch.delenv("DEV_TLS_KEY", raising=False)


@pytest.fixture(autouse=True)
def _outside_a_task_session(monkeypatch):
    """Keep the outer task session out of the tests.

    A task session exports ``MAEL_TASK_ID`` and ``MAEL_TASK_PARENT``, and
    ``mael task add`` reads them as defaults. The pre-push check runs the suite
    inside that session, so a leaked value would block every push from it.
    """
    monkeypatch.delenv("MAEL_TASK_ID", raising=False)
    monkeypatch.delenv("MAEL_TASK_PARENT", raising=False)


@pytest.fixture
def self_signed_cert(tmp_path) -> tuple[Path, Path]:
    """A throwaway certificate and key for ``localhost``, made by ``openssl``."""
    cert, key = tmp_path / "dev.crt", tmp_path / "dev.key"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"]
        + ["-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
        check=True,
        capture_output=True,
    )
    return cert, key


@pytest.fixture
def tls_server_context(self_signed_cert) -> ssl.SSLContext:
    """A server-side TLS context on :func:`self_signed_cert`."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(*self_signed_cert)
    return context
