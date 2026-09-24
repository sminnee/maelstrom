"""The ``mael-orchestrator`` console script serves the world from this package."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _migrate(path: Path) -> None:
    # The server refuses a database behind its build, so the test stands in
    # for `mael admin migrate`.
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        asyncio.run(db.migrate())
    finally:
        db.close()


@pytest.mark.binds_socket
def test_the_console_script_serves_the_world(tmp_path):
    # The notebook and daemon roots are the autouse fixtures' temporary ones,
    # which the server inherits through os.environ.
    _migrate(get_state_db_path())
    # A home of its own, so the world is built from an empty projects
    # directory and not from the developer's real one.
    home = tmp_path / "home"
    (home / ".maelstrom").mkdir(parents=True)
    (tmp_path / "Projects").mkdir()
    (home / ".maelstrom" / "config.yaml").write_text(
        f"projects_dir: {tmp_path / 'Projects'}\n"
    )
    port = _free_port()
    log = tmp_path / "orchestrator.log"
    server = None
    try:
        with log.open("w") as out:
            server = subprocess.Popen(
                [
                    str(Path(sys.executable).with_name("mael-orchestrator")),
                    "serve",
                    "--port",
                    str(port),
                ],
                env={**os.environ, "HOME": str(home)},
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 15
        body: dict = {}
        while "projects" not in body:
            assert server.poll() is None, log.read_text()
            assert time.monotonic() < deadline, log.read_text()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/projects", timeout=5
                ) as reply:
                    body = json.load(reply)
            except urllib.error.HTTPError as error:
                # Up, but failing: the regression this test is for.
                pytest.fail(f"{error.code}: {error.read().decode()}\n{log.read_text()}")
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)

        assert body == {"projects": []}
    finally:
        if server is not None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
