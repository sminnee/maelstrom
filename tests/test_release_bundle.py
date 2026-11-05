"""A release bundle of this checkout, installed and served.

``bin/build-bundle`` builds it, the bundle's own ``install.py`` installs it
into a temporary root with a real uv venv, and the installed orchestrator
serves the UI and the API from one port. `lib/domain/tests/
test_release_bundle_install.py` covers what a second install copies.
"""

import json
import os
import socket
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.e2e, pytest.mark.binds_socket]


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    """The install root."""
    tmp = tmp_path_factory.mktemp("bundle")
    tarball = tmp / "maelstrom.tar.gz"
    subprocess.run(
        [str(REPO / "bin" / "build-bundle"), "--allow-dirty", "--out", str(tarball)],
        check=True,
    )
    unpacked = tmp / "unpacked"
    with tarfile.open(tarball) as tar:
        tar.extractall(unpacked, filter="data")
    root = tmp / "install"
    # As a new host runs it: the bundle's own copy, with no project around it.
    subprocess.run(
        ["uv", "run", "--no-project", "python", "install.py", "--root", str(root)],
        cwd=unpacked / "maelstrom",
        env={k: v for k, v in os.environ.items() if k != "UV_ENV_FILE"},
        check=True,
    )
    return root


def test_the_installed_cli_runs(installed):
    """A wheel or a requirement the bundle lacks fails at import."""
    mael = installed / "current" / "venv" / "bin" / "mael"
    result = subprocess.run([str(mael), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str) -> tuple[str, bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.headers["Content-Type"], response.read()


def test_the_installed_orchestrator_serves_the_ui_and_the_api(installed, tmp_path):
    root = installed
    bin_dir = root / "current" / "venv" / "bin"
    home = tmp_path / "home"
    (home / ".maelstrom").mkdir(parents=True)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("MAEL_")},
        "HOME": str(home),
        "MAEL_NOTEBOOK_ROOT": str(home / ".maelstrom"),
        "MAEL_AGENT_ROOT": str(tmp_path / "daemon"),
    }
    # A cwd outside any checkout, so no `.env` names another notebook.
    subprocess.run(
        [str(bin_dir / "python"), "-m", "mael_domain.state_db.migrate"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    port = _free_port()
    # A file, not a pipe: the server logs every poll, and a full pipe blocks it.
    log = tmp_path / "serve.log"
    server = subprocess.Popen(
        [str(bin_dir / "mael-orchestrator"), "serve", "--port", str(port)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=log.open("w"),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                host_type, host = _get(f"{base}/api/host")
                break
            except OSError:
                if server.poll() is not None:
                    pytest.fail(log.read_text())
                assert time.monotonic() < deadline, "the server never answered"
                time.sleep(0.2)
        index_type, index = _get(f"{base}/")
    finally:
        server.terminate()
        server.wait(timeout=10)

    assert host_type.startswith("application/json")
    assert "host" in json.loads(host)
    assert index_type.startswith("text/html")
    assert b'<div id="root">' in index
