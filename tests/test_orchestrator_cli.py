"""``mael orchestrator serve``, with the server itself patched out."""

import asyncio
import logging
import os
import re
import signal
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.orchestrator_cli import (
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    run_server,
)
from maelstrom.state_db import SchemaTooOldError, StateDb


@pytest.fixture
def state_db(tmp_path, monkeypatch):
    """A migrated state database under tmp_path, so no test touches the real one.

    ``run_server`` opens one and checks it, which without this would read the
    developer's live ``~/.maelstrom/state.db``.
    """
    monkeypatch.setattr("maelstrom.state_db.get_maelstrom_dir", lambda: tmp_path)
    monkeypatch.setattr("maelstrom.desk_store.get_maelstrom_dir", lambda: tmp_path)
    db = StateDb()
    asyncio.run(db.migrate())
    db.close()


def test_serve_passes_its_flags_to_the_server():
    with patch("maelstrom.orchestrator_cli.run_server") as run_server:
        result = CliRunner().invoke(
            cli, ["orchestrator", "serve", "--host", "0.0.0.0", "--port", "9000"]
        )
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with("0.0.0.0", 9000, DEFAULT_LOG_LEVEL)


def test_serve_defaults_to_localhost_and_the_default_port():
    with patch("maelstrom.orchestrator_cli.run_server") as run_server:
        result = CliRunner().invoke(cli, ["orchestrator", "serve"])
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with(DEFAULT_HOST, DEFAULT_PORT, DEFAULT_LOG_LEVEL)


def test_serve_takes_no_root_flag():
    """The server talks to the daemon its own environment names. A flag here
    pointed the everyday orchestrator at a worktree's daemon, and back."""
    result = CliRunner().invoke(cli, ["orchestrator", "serve", "--root", "/tmp/a"])
    assert result.exit_code == 2
    assert "no such option" in result.output.lower()


def test_a_bind_failure_is_an_error_not_a_traceback():
    with patch(
        "maelstrom.orchestrator_cli.run_server", side_effect=OSError("address in use")
    ):
        result = CliRunner().invoke(cli, ["orchestrator", "serve"])
    assert result.exit_code == 1
    assert "address in use" in result.output


def test_build_orchestrator_wires_the_notebook_list_all_and_a_worktree_opener(
    tmp_path, monkeypatch
):
    """The wiring the CLI does is what production runs; the flags tests never touch it.

    The daemon socket comes from the environment's own root, so this pins that
    the orchestrator talks to the daemon its environment names."""
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "root"))
    from types import SimpleNamespace
    from unittest.mock import patch

    from maelstrom.desk_store import SqliteDeskStore
    from maelstrom.orchestrator.sources import (
        ListAllWorktreeSource,
        NotebookTaskSource,
    )
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
        orchestrator = build_orchestrator()
        opened = orchestrator.tasks.open_worktree("northwind", "feat/x", "feat/base")
    assert isinstance(orchestrator.tasks, NotebookTaskSource)
    assert orchestrator.tasks.projects() == ["northwind"]
    assert orchestrator.tasks.store is store.return_value
    assert isinstance(orchestrator.worktrees, ListAllWorktreeSource)
    assert isinstance(orchestrator.desk, SqliteDeskStore)
    assert orchestrator.worktrees.projects_dir == projects_dir
    assert orchestrator.daemon.socket_path == f"{tmp_path / 'root'}/agent-daemon.sock"
    assert opened is setup
    open_wt.assert_called_once()
    assert open_wt.call_args.args[:3] == (
        projects_dir / "northwind",
        "northwind",
        "feat/x",
    )
    assert open_wt.call_args.kwargs["run_install"] is False
    assert open_wt.call_args.kwargs["base"] == "feat/base"


def test_serve_sets_up_logging_with_timestamps_before_it_serves(state_db):
    """The log must name when and how bad, or a crash leaves nothing to read.

    Without configuration every ``log.exception`` goes to the root logger's
    last-resort handler: no timestamp, no level, and every ``log.info``
    dropped.
    """
    with (
        patch("maelstrom.orchestrator_cli.serve_app"),
        patch("maelstrom.orchestrator_cli.build_orchestrator"),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT, log_level="INFO")
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert root.handlers, "no handler was installed"
    formatter = root.handlers[0].formatter
    assert formatter is not None
    line = formatter.format(
        logging.LogRecord("m", logging.WARNING, "p", 1, "the message", None, None)
    )
    assert "the message" in line
    assert "WARNING" in line
    # A timestamp, not just the text.
    assert re.search(r"\d{4}-\d{2}-\d{2}", line), line


def test_an_exception_that_escapes_a_task_is_logged(capsys, state_db):
    """A task that dies with nobody awaiting it must still say so."""
    captured = {}

    async def scenario(*_args):
        captured["handler"] = asyncio.get_running_loop().get_exception_handler()

    with (
        patch("maelstrom.orchestrator_cli.build_orchestrator"),
        patch("maelstrom.orchestrator_cli.build_app"),
        patch("maelstrom.orchestrator_cli.serve_app", new=scenario),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)

    assert captured["handler"] is not None, "no loop exception handler was installed"

    loop = asyncio.new_event_loop()
    try:
        captured["handler"](loop, {"message": "task blew up"})
    finally:
        loop.close()
    # Stderr, because that is the stream ``mael env`` captures to the log file.
    assert "task blew up" in capsys.readouterr().err


def test_a_sigterm_shuts_the_server_down_cleanly(state_db):
    """A supervised restart sends SIGTERM, not Ctrl-C.

    Without a handler the default terminates the process outright, so
    ``runner.cleanup`` never runs and the orchestrator never stops: pollers
    are left running and the desk is never flushed.
    """
    stopped = []

    async def scenario(*_args):
        loop = asyncio.get_running_loop()
        # The handler must be installed by the time the server is serving.
        assert loop._signal_handlers.get(signal.SIGTERM) is not None, (
            "SIGTERM is not handled"
        )
        os.kill(os.getpid(), signal.SIGTERM)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append("cancelled")
            raise

    with (
        patch("maelstrom.orchestrator_cli.build_orchestrator"),
        patch("maelstrom.orchestrator_cli.build_app"),
        patch("maelstrom.orchestrator_cli.serve_app", new=scenario),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)

    assert stopped == ["cancelled"]


def test_an_unmigrated_state_database_refuses_to_serve(tmp_path, monkeypatch):
    """An ordinary open names the fix rather than upgrading under a running server.

    Every worktree shares one ``~/.maelstrom``, so a database this build cannot
    read is Tuesday. Refusing is the only non-destructive answer, and the
    message has to carry the command that clears it.
    """
    monkeypatch.setattr("maelstrom.state_db.get_maelstrom_dir", lambda: tmp_path)
    with (
        patch("maelstrom.orchestrator_cli.build_orchestrator"),
        patch("maelstrom.orchestrator_cli.build_app"),
        patch("maelstrom.orchestrator_cli.serve_app"),
        pytest.raises(SchemaTooOldError) as exc,
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)
    assert "mael admin migrate" in str(exc.value)


def test_serve_reports_a_refused_state_database_as_an_error(tmp_path, monkeypatch):
    """The refusal reaches the user as one line, not as a traceback."""
    monkeypatch.setattr("maelstrom.state_db.get_maelstrom_dir", lambda: tmp_path)
    with patch(
        "maelstrom.orchestrator_cli.run_server",
        side_effect=SchemaTooOldError("run `mael admin migrate`"),
    ):
        result = CliRunner().invoke(cli, ["orchestrator", "serve"])
    assert result.exit_code == 1
    assert "mael admin migrate" in result.output


def test_a_maelstrom_dir_that_does_not_exist_is_created(tmp_path, monkeypatch):
    """A machine that has never run a mael command has no ~/.maelstrom.

    sqlite3 raises OperationalError rather than creating the directory, and
    that is not a StateDbError, so the refusal handler would miss it and the
    user would see a traceback.
    """
    home = tmp_path / "never-used"
    monkeypatch.setattr("maelstrom.state_db.get_maelstrom_dir", lambda: home)
    with (
        patch("maelstrom.orchestrator_cli.build_orchestrator"),
        patch("maelstrom.orchestrator_cli.build_app"),
        patch("maelstrom.orchestrator_cli.serve_app"),
        pytest.raises(SchemaTooOldError),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)
    assert home.is_dir()
