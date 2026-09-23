"""The ``mael-agent-daemon`` console script serves a daemon from this package."""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import mael_daemon
from mael_agent.agent_transport import ROOT_ENV, client, daemon_paths


def _ping(socket: Path) -> dict:
    # A loop of its own, closed before the SIGTERM: the daemon's shutdown
    # waits for open connections, and this one would still be open.
    return asyncio.run(client(socket_path=str(socket)).request({"cmd": "ping"}))


@pytest.mark.binds_socket
def test_the_console_script_serves_and_answers_ping():
    # Short and under /tmp: a Unix socket path is capped near 104 bytes, and
    # pytest's tmp_path on macOS is longer than that.
    root = Path(tempfile.mkdtemp(prefix="mad-", dir="/tmp"))
    log = root / "daemon.log"
    daemon = None
    try:
        with log.open("w") as out:
            daemon = subprocess.Popen(
                [str(Path(sys.executable).with_name("mael-agent-daemon")), "serve"],
                env={**os.environ, ROOT_ENV: str(root)},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        socket = daemon_paths(root).socket
        deadline = time.monotonic() + 15
        # Ready means answering: the socket file exists a moment before the
        # daemon listens on it.
        reply: dict = {}
        while "daemon" not in reply:
            assert daemon.poll() is None, log.read_text()
            assert time.monotonic() < deadline, f"no answer: {reply}"
            if socket.exists():
                reply = _ping(socket)
            time.sleep(0.05)

        assert reply["daemon"]["version"] == mael_daemon.__version__
        assert reply["daemon"]["source_tree"] == str(
            Path(__file__).resolve().parents[2]
        )
    finally:
        if daemon is not None:
            daemon.send_signal(signal.SIGTERM)
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()
        shutil.rmtree(root, ignore_errors=True)
