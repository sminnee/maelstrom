"""``mael orchestrator serve``, with the server itself patched out."""

from unittest.mock import patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.orchestrator_cli import DEFAULT_HOST, DEFAULT_PORT


def test_serve_passes_its_flags_to_the_server():
    with patch("maelstrom.orchestrator_cli.run_server") as run_server:
        result = CliRunner().invoke(
            cli,
            [
                "orchestrator",
                "serve",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--socket",
                "/tmp/a.sock",
            ],
        )
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with("0.0.0.0", 9000, "/tmp/a.sock")


def test_serve_defaults_to_localhost_and_the_default_port():
    with patch("maelstrom.orchestrator_cli.run_server") as run_server:
        result = CliRunner().invoke(cli, ["orchestrator", "serve"])
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with(DEFAULT_HOST, DEFAULT_PORT, None)


def test_a_bind_failure_is_an_error_not_a_traceback():
    with patch(
        "maelstrom.orchestrator_cli.run_server", side_effect=OSError("address in use")
    ):
        result = CliRunner().invoke(cli, ["orchestrator", "serve"])
    assert result.exit_code == 1
    assert "address in use" in result.output
