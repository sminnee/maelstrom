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


def test_build_orchestrator_wires_the_notebook_list_all_and_a_worktree_opener(tmp_path):
    """The wiring the CLI does is what production runs; the flags tests never touch it."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from maelstrom.orchestrator.sources import ListAllWorktreeSource, NotebookTaskSource
    from maelstrom.orchestrator_cli import build_orchestrator
    from maelstrom.worktree import WorktreeSetup

    projects_dir = tmp_path / "Projects"
    (projects_dir / "northwind").mkdir(parents=True)
    (projects_dir / "northwind" / ".mael").touch()
    setup = WorktreeSetup(
        path=projects_dir / "northwind" / "northwind-alpha",
        name="alpha",
        action="reused",
    )
    with (
        patch(
            "maelstrom.orchestrator_cli.load_global_config",
            return_value=SimpleNamespace(projects_dir=projects_dir),
        ),
        patch("maelstrom.orchestrator_cli.GitFileStore") as store,
        patch("maelstrom.orchestrator_cli.open_index"),
        patch(
            "maelstrom.orchestrator_cli.setup_worktree_for_branch", return_value=setup
        ) as open_wt,
    ):
        orchestrator = build_orchestrator("/tmp/a.sock")
        task = SimpleNamespace(base="feat/base")
        opened = orchestrator.tasks.open_worktree("northwind", task, "feat/x")
    assert isinstance(orchestrator.tasks, NotebookTaskSource)
    assert orchestrator.tasks.projects() == ["northwind"]
    assert orchestrator.tasks.store is store.return_value
    assert isinstance(orchestrator.worktrees, ListAllWorktreeSource)
    assert orchestrator.worktrees.projects_dir == projects_dir
    assert orchestrator.daemon.socket_path == "/tmp/a.sock"
    assert opened is setup
    open_wt.assert_called_once()
    assert open_wt.call_args.args[:3] == (
        projects_dir / "northwind",
        "northwind",
        "feat/x",
    )
    assert open_wt.call_args.kwargs["run_install"] is False
    assert open_wt.call_args.kwargs["base"] == "feat/base"
