"""Auto-starting the agent daemon.

The one part of ``mael agent`` that spawns a process, so these tests are the
only ones in the agent suite that are not subprocess-free. The autouse fixture
in ``conftest.py`` disables auto-start everywhere else; each test here turns it
back on explicitly.
"""

import asyncio
import subprocess
import threading
import time
from pathlib import Path

import pytest

from maelstrom import agent_server
from maelstrom.agent_transport import (
    NO_AUTOSTART_ENV,
    DaemonPaths,
    SocketDaemonClient,
    ensure_daemon,
    wait_for_daemon_gone,
)


@pytest.fixture()
def autostart_on(monkeypatch):
    """Undo the suite-wide disable, for the tests that exercise the spawn."""
    monkeypatch.delenv(NO_AUTOSTART_ENV, raising=False)


@pytest.fixture()
def paths(tmp_path) -> DaemonPaths:
    """A daemon root of the test's own: its socket, log and records together.

    The default root holds this machine's real agents, and a spawned daemon
    restores every record it finds under its root.
    """
    return DaemonPaths(tmp_path)


@pytest.fixture()
def socket_path(paths) -> str:
    return str(paths.socket)


@pytest.mark.binds_socket
def test_a_command_starts_the_daemon_when_none_runs(
    autostart_on, socket_path, monkeypatch, tmp_path
):
    """End to end: no daemon, one command, and the command still works."""
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


def test_a_daemon_never_spawns_a_daemon(autostart_on, paths, monkeypatch):
    """The child inherits the disable var, so the recursion cannot start."""
    spawned: list[dict] = []

    def fake_popen(argv, **kwargs):
        spawned.append(kwargs)
        raise AssertionError("spawn attempted")

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", fake_popen)
    with pytest.raises(AssertionError):
        asyncio.run(ensure_daemon(paths))
    assert spawned[0]["env"][NO_AUTOSTART_ENV] == "1"


def test_the_spawn_names_the_serve_subcommand_and_the_root(
    autostart_on, paths, monkeypatch
):
    """``daemon`` is a command group, so the spawn has to name a verb.

    A bare ``mael agent daemon`` prints help and exits, which would make every
    auto-start fail. This is the guard on that rename. The root travels too:
    a spawned daemon must serve the root the command asked for, not the
    default one, or a per-environment daemon would restore `_main`'s agents.
    """
    spawned: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        raise AssertionError("spawn attempted")

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", fake_popen)
    with pytest.raises(AssertionError):
        asyncio.run(ensure_daemon(paths))
    assert spawned[0][1:6] == ["agent", "daemon", "serve", "--root", str(paths.root)]


def test_a_daemon_that_never_binds_fails_fast(autostart_on, paths, monkeypatch):
    """A spawn that exits immediately is reported at once, not after the deadline."""
    monkeypatch.setattr("maelstrom.agent_transport.mael_path", lambda: "/usr/bin/false")
    with pytest.raises(OSError) as excinfo:
        asyncio.run(ensure_daemon(paths))
    assert "exited" in str(excinfo.value)


def test_a_failed_start_is_reported_in_its_own_words(
    autostart_on, paths, monkeypatch, tmp_path
):
    """An older daemon's crash must not be reported as this one's."""
    paths.log.write_text("an older daemon died of something else entirely\n")
    monkeypatch.setattr(
        "maelstrom.agent_transport.mael_path",
        lambda: str(_script(tmp_path, "echo this daemon could not bind >&2; exit 1")),
    )
    with pytest.raises(OSError) as excinfo:
        asyncio.run(ensure_daemon(paths))
    message = str(excinfo.value)
    assert "this daemon could not bind" in message
    assert "older daemon" not in message


def _script(directory: Path, body: str) -> Path:
    """A tiny executable shell script, standing in for the ``mael`` binary."""
    path = directory / "fake-mael"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


@pytest.mark.binds_socket
def test_a_daemon_from_another_tree_warns_but_still_serves(
    autostart_on, socket_path, monkeypatch, capsys
):
    """The recorded failure: a daemon holding another worktree's code.

    It answers, so the command works. But nothing said which tree was
    serving, and a stale daemon once deleted a spawn record that way. A
    warning names the mismatch without refusing a working daemon.
    """
    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def watch(argv, **kwargs):
        child = real_popen(argv, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr("maelstrom.agent_transport.subprocess.Popen", watch)
    client = SocketDaemonClient(socket_path=socket_path)
    try:
        # First command starts it. The skew only exists for a *later* caller,
        # so the warning belongs to the second command, not this one.
        assert client.request({"cmd": "list"}).get("agents") == []
        capsys.readouterr()
        monkeypatch.setattr(
            "maelstrom.agent_transport.local_source_tree",
            lambda: "/Users/x/Projects/maelstrom/some-other-tree",
        )
        assert client.request({"cmd": "list"}).get("agents") == []
        warning = capsys.readouterr().err
        assert "some-other-tree" in warning
        assert "daemon restart" in warning
    finally:
        for child in started:
            child.kill()
            child.wait(timeout=5)


def test_a_daemon_too_old_to_know_ping_is_named_as_stale(
    autostart_on, paths, monkeypatch, capsys
):
    """The very case the warning exists for must not be the silent one.

    A daemon predating `ping` cannot report its tree. That is not "no skew" —
    it is proof the daemon is older than this code, which is exactly what the
    caller needs to hear.
    """
    monkeypatch.setattr(
        "maelstrom.agent_transport._probe", _answers_yes := _always(True)
    )

    # What the real pre-`ping` daemon answers: it falls through to the agent
    # lookup with an empty id, so the error names an agent, not the command.
    async def no_ping(socket_path, payload, *, autostart=True):
        return {"error": "no such agent: "}

    monkeypatch.setattr("maelstrom.agent_transport.request_over_socket", no_ping)
    asyncio.run(ensure_daemon(paths))
    warning = capsys.readouterr().err
    assert "older" in warning
    assert "daemon restart" in warning


def _always(value):
    """A stand-in for an async predicate that always answers the same."""

    async def answer(*args, **kwargs):
        return value

    return answer


def test_a_restart_waits_for_the_old_daemon_to_release_the_lock(autostart_on, paths):
    """Shutdown unlinks the socket before it releases the lock.

    So a wait that watches the socket alone returns while the old daemon still
    holds the lock. The restart then spawns, the new daemon loses the bind, and
    the whole restart fails with "a daemon is already serving".
    """
    held = agent_server._take_lock(paths.lock)
    assert held is not None
    released: list[float] = []

    def release_soon() -> None:
        """Stands in for the old daemon finishing its shutdown."""
        released.append(time.monotonic())
        agent_server._release_lock(held)

    timer = threading.Timer(0.3, release_soon)
    timer.start()
    try:
        wait_for_daemon_gone(paths)
        assert released, "returned while the old daemon still held the lock"
    finally:
        timer.cancel()
        if not released:
            agent_server._release_lock(held)


def test_a_daemon_that_misses_the_deadline_is_terminated_not_killed(
    autostart_on, paths, monkeypatch
):
    """SIGTERM, so `serve`'s `finally` runs its shutdown.

    A daemon that binds late has already restored its agents. SIGKILL would
    leave those children running on a dead pipe; SIGTERM lets the daemon stop
    them and leave their records resumable.
    """

    class Slow:
        returncode = None

        def __init__(self) -> None:
            self.signals: list[str] = []

        def poll(self):
            return None

        def terminate(self):
            self.signals.append("terminate")

        def kill(self):
            self.signals.append("kill")

    child = Slow()
    monkeypatch.setattr(
        "maelstrom.agent_transport.spawn_daemon", lambda paths: (child, 0)
    )
    monkeypatch.setattr("maelstrom.agent_transport._probe", _always(False))
    monkeypatch.setattr("maelstrom.agent_transport.READY_TIMEOUT", 0.05)
    with pytest.raises(OSError, match="did not start"):
        asyncio.run(ensure_daemon(paths))
    assert child.signals == ["terminate"]
