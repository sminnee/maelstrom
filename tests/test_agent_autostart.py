"""Auto-starting the agent daemon.

The one part of ``mael agent`` that spawns a process, so these tests are the
only ones in the agent suite that are not subprocess-free. The autouse fixture
in ``conftest.py`` disables auto-start everywhere else; each test here turns it
back on explicitly.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from maelstrom.agent_transport import (
    NO_AUTOSTART_ENV,
    SocketDaemonClient,
    ensure_daemon,
)


@pytest.fixture()
def autostart_on(monkeypatch):
    """Undo the suite-wide disable, for the tests that exercise the spawn."""
    monkeypatch.delenv(NO_AUTOSTART_ENV, raising=False)


@pytest.fixture()
def socket_path(tmp_path) -> str:
    return str(tmp_path / "agent-daemon.sock")


@pytest.mark.binds_socket
def test_a_command_starts_the_daemon_when_none_runs(
    autostart_on, socket_path, monkeypatch, tmp_path
):
    """End to end: no daemon, one command, and the command still works."""
    monkeypatch.setenv("MAEL_AGENT_LOG", str(tmp_path / "daemon.log"))
    # Its own spawn records too: the default dir holds this machine's real
    # agents, and a spawned daemon restores every record it finds there.
    monkeypatch.setenv("MAEL_AGENT_SPEC_DIR", str(tmp_path / "agents"))
    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def watch(argv, **kwargs):
        child = real_popen(argv, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", watch)
    try:
        reply = SocketDaemonClient(socket_path=socket_path).request({"cmd": "list"})
        assert reply.get("agents") == []
        assert Path(socket_path).exists()
    finally:
        for child in started:
            child.kill()
            child.wait(timeout=5)


def test_the_disable_var_leaves_the_daemon_alone(socket_path, monkeypatch):
    """With auto-start off, an absent daemon is an error, not a spawn."""
    monkeypatch.setenv(NO_AUTOSTART_ENV, "1")
    reply = SocketDaemonClient(socket_path=socket_path).request({"cmd": "list"})
    assert "not reachable" in reply["error"]
    assert not Path(socket_path).exists()


def test_a_daemon_never_spawns_a_daemon(autostart_on, socket_path, monkeypatch):
    """The child inherits the disable var, so the recursion cannot start."""
    spawned: list[dict] = []

    def fake_popen(argv, **kwargs):
        spawned.append(kwargs)
        raise AssertionError("spawn attempted")

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", fake_popen)
    with pytest.raises(AssertionError):
        asyncio.run(ensure_daemon(socket_path))
    assert spawned[0]["env"][NO_AUTOSTART_ENV] == "1"


def test_the_spawn_names_the_serve_subcommand(
    autostart_on, socket_path, monkeypatch, tmp_path
):
    """``daemon`` is a command group, so the spawn has to name a verb.

    A bare ``mael agent daemon`` prints help and exits, which would make every
    auto-start fail. This is the guard on that rename.
    """
    monkeypatch.setenv("MAEL_AGENT_LOG", str(tmp_path / "daemon.log"))
    spawned: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        raise AssertionError("spawn attempted")

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", fake_popen)
    with pytest.raises(AssertionError):
        asyncio.run(ensure_daemon(socket_path))
    assert spawned[0][1:4] == ["agent", "daemon", "serve"]


def test_a_daemon_that_never_binds_fails_fast(autostart_on, socket_path, monkeypatch):
    """A spawn that exits immediately is reported at once, not after the deadline."""
    monkeypatch.setattr("maelstrom.agent_transport.mael_path", lambda: "/usr/bin/false")
    with pytest.raises(OSError) as excinfo:
        asyncio.run(ensure_daemon(socket_path))
    assert "exited" in str(excinfo.value)


def test_a_failed_start_is_reported_in_its_own_words(
    autostart_on, socket_path, monkeypatch, tmp_path
):
    """An older daemon's crash must not be reported as this one's."""
    log = tmp_path / "daemon.log"
    log.write_text("an older daemon died of something else entirely\n")
    monkeypatch.setenv("MAEL_AGENT_LOG", str(log))
    monkeypatch.setattr(
        "maelstrom.agent_transport.mael_path",
        lambda: str(_script(tmp_path, "echo this daemon could not bind >&2; exit 1")),
    )
    with pytest.raises(OSError) as excinfo:
        asyncio.run(ensure_daemon(socket_path))
    message = str(excinfo.value)
    assert "this daemon could not bind" in message
    assert "older daemon" not in message


def _script(directory: Path, body: str) -> Path:
    """A tiny executable shell script, standing in for the ``mael`` binary."""
    path = directory / "fake-mael"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path
