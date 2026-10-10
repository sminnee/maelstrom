"""The Orchestrator client: how a call fails, and the best-effort notify.

A stub HTTP server stands in for the orchestrator. ``tests/test_cli_over_api.py``
proves the calls that succeed against the real one.
"""

import json
import os
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mael_cli.orchestrator_client import (
    OrchestratorClient,
    OrchestratorRefused,
    OrchestratorUnconfigured,
    OrchestratorUnreachable,
    tell_orchestrator,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A ``$HOME`` of the test's own, so the user's real config is never read."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def configure(home, url: str) -> None:
    """Name the server in the notebook root's config, as a dev environment does."""
    config = Path(os.environ["MAEL_NOTEBOOK_ROOT"]) / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"orchestrator_url: {url}\n")


class Stub:
    """A server that answers every request with one canned reply, and records each."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body
        self.heard: list[tuple[str, str]] = []


@contextmanager
def serve_stub(stub: Stub, tls: ssl.SSLContext | None = None) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            stub.heard.append((self.command, self.path))
            self.send_response(stub.status)
            self.send_header("Content-Length", str(len(stub.body)))
            self.end_headers()
            self.wfile.write(stub.body)

        do_GET = do_POST = _answer

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    scheme = "http"
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"{scheme}://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def free_port() -> int:
    """A port with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_no_configured_url_names_the_config_key(home):
    with pytest.raises(OrchestratorUnconfigured, match="orchestrator_url"):
        OrchestratorClient.from_config()


@pytest.mark.binds_socket
def test_a_url_with_nothing_listening_names_the_url_and_how_to_start_it(home):
    url = f"http://127.0.0.1:{free_port()}"
    configure(home, url)

    with pytest.raises(OrchestratorUnreachable) as caught:
        OrchestratorClient.from_config().get("/api/tasks")

    assert url in caught.value.message
    assert "mael self-env start orchestrator" in caught.value.message


@pytest.mark.binds_socket
def test_an_error_reply_is_refused_with_its_code_and_message(home):
    body = json.dumps({"error": {"code": "stale_version", "message": "Moved on"}})
    with serve_stub(Stub(409, body.encode())) as url:
        with pytest.raises(OrchestratorRefused) as caught:
            OrchestratorClient(url).post("/api/tasks/p/t/status", {"status": "done"})

    assert (caught.value.code, caught.value.message, caught.value.status) == (
        "stale_version",
        "Moved on",
        409,
    )


@pytest.mark.binds_socket
def test_an_error_reply_with_no_json_is_refused_with_its_status(home):
    with serve_stub(Stub(502, b"Bad Gateway")) as url:
        with pytest.raises(OrchestratorRefused) as caught:
            OrchestratorClient(url).get("/api/tasks")

    assert (caught.value.code, caught.value.message) == ("invalid", "HTTP 502")


def test_telling_with_no_configured_url_is_silent(home):
    """No server configured is the ordinary case for a plain CLI user."""
    tell_orchestrator("/api/worktrees/refresh")


@pytest.mark.binds_socket
def test_telling_an_orchestrator_that_refuses_the_connection_is_silent(home):
    """A server that has been stopped. The caller's own work is already done."""
    configure(home, f"http://127.0.0.1:{free_port()}")
    tell_orchestrator("/api/worktrees/refresh")


@pytest.mark.binds_socket
def test_an_https_orchestrator_is_told_over_tls(home, tls_server_context):
    """Under ``dev_https:`` the server speaks only TLS, with a certificate that
    names the dev host rather than ``127.0.0.1``."""
    stub = Stub(204, b"")
    with serve_stub(stub, tls_server_context) as url:
        configure(home, url)
        tell_orchestrator("/api/worktrees/refresh")

    assert stub.heard == [("POST", "/api/worktrees/refresh")]


def test_a_url_with_no_scheme_counts_as_unset(home):
    """``desk:3220`` is not a URL urllib can open; it must not reach it."""
    configure(home, "desk:3220")

    with pytest.raises(OrchestratorUnconfigured):
        OrchestratorClient.from_config()


@pytest.mark.binds_socket
def test_a_reply_that_is_not_json_is_refused(home):
    """The URL names some other server, such as the web dev server."""
    with serve_stub(Stub(200, b"<html></html>")) as url:
        with pytest.raises(OrchestratorRefused) as caught:
            OrchestratorClient(url).get("/api/tasks")

    assert caught.value.code == "invalid"
    assert url in caught.value.message
