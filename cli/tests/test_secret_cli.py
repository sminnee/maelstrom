"""``mael secret``: what the command line adds to the secret routes.

``tests/test_secret_over_api.py`` proves the commands against a real server.
"""

import os
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from mael_cli.secret_cli import secret


@pytest.fixture
def no_server(tmp_path, monkeypatch) -> str:
    """A config that names a server, with nothing listening there."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    config = Path(os.environ["MAEL_NOTEBOOK_ROOT"]) / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"orchestrator_url: {url}\n")
    return url


@pytest.mark.binds_socket
@pytest.mark.parametrize(
    "args",
    [
        ["unlock"],
        ["set", "API_KEY", "--global"],
        ["list"],
        ["rm", "API_KEY", "--global"],
    ],
    ids=["unlock", "set", "list", "rm"],
)
def test_each_command_fails_and_names_the_server_when_none_answers(no_server, args):
    result = CliRunner().invoke(secret, args, input="text\n")

    assert result.exit_code == 1
    assert no_server in result.output


def test_set_takes_no_value_as_an_argument(no_server):
    result = CliRunner().invoke(secret, ["set", "API_KEY", "canary-123", "--global"])

    assert result.exit_code == 2
    assert "unexpected extra argument" in result.output


def test_a_scope_is_a_project_or_global_and_not_both(no_server):
    result = CliRunner().invoke(
        secret, ["set", "API_KEY", "-p", "northwind", "--global"], input="v\n"
    )

    assert result.exit_code == 2
    assert "--project or --global" in result.output
