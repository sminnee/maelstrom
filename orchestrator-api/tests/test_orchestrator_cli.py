"""``mael-orchestrator serve``, with the server itself patched out."""

import asyncio
import logging
import os
import re
import signal
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from mael_domain.github_model import GitHubCommandFailed
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.types import SchemaTooOldError
from mael_domain.task import list_tasks
from mael_domain.task_table import InMemoryTaskTable
from mael_domain.worktree import CloseResult
from mael_domain.worktree_close import FullCloseResult
from mael_orchestrator.cli import (
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    build_orchestrator,
    cli,
    run_server,
)
from mael_orchestrator.sources import CloseBlocked, ListAllWorktreeSource


@pytest.fixture
def migrated_notebook(tmp_path, monkeypatch):
    """A migrated state database under tmp_path, so no test touches the real one.

    ``run_server`` opens one and checks it, which without this would read the
    developer's live ``~/.maelstrom/state.db``.
    """
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    db = open_state_db()
    asyncio.run(db.migrate())
    db.close()


def test_serve_passes_its_flags_to_the_server():
    with patch("mael_orchestrator.cli.run_server") as run_server:
        result = CliRunner().invoke(
            cli, ["serve", "--host", "0.0.0.0", "--port", "9000"]
        )
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with(
        "0.0.0.0", 9000, DEFAULT_LOG_LEVEL, ssl_context=None
    )


def test_serve_defaults_to_localhost_and_the_default_port():
    with patch("mael_orchestrator.cli.run_server") as run_server:
        result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 0, result.output
    run_server.assert_called_once_with(
        DEFAULT_HOST, DEFAULT_PORT, DEFAULT_LOG_LEVEL, ssl_context=None
    )


def test_serve_reads_its_certificate_from_the_worktree_env(self_signed_cert):
    """``.env`` names the dev certificate, so the service command needs no
    flags and no shell test for HTTPS."""
    cert, key = self_signed_cert
    env = {"DEV_TLS_CERT": str(cert), "DEV_TLS_KEY": str(key)}
    with patch("mael_orchestrator.cli.run_server") as run_server:
        result = CliRunner().invoke(cli, ["serve", "--port", "9000"], env=env)
    assert result.exit_code == 0, result.output
    assert "Serving on https://127.0.0.1:9000" in result.output
    assert isinstance(run_server.call_args.kwargs["ssl_context"], ssl.SSLContext)


def test_serve_refuses_a_certificate_without_its_key(tmp_path):
    cert = tmp_path / "dev.crt"
    cert.write_text("")
    result = CliRunner().invoke(cli, ["serve", "--tls-cert", str(cert)])
    assert result.exit_code == 2
    assert "--tls-key" in result.output


def test_serve_takes_no_root_flag():
    """The server talks to the daemon its own environment names. A flag here
    pointed the everyday orchestrator at a worktree's daemon, and back."""
    result = CliRunner().invoke(cli, ["serve", "--root", "/tmp/a"])
    assert result.exit_code == 2
    assert "no such option" in result.output.lower()


def test_a_bind_failure_is_an_error_not_a_traceback():
    with patch(
        "mael_orchestrator.cli.run_server", side_effect=OSError("address in use")
    ):
        result = CliRunner().invoke(cli, ["serve"])
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

    from mael_domain.desk_store import SqliteDeskStore
    from mael_domain.task_attachments import SqliteTaskAttachmentTable
    from mael_domain.worktree import WorktreeSetup
    from mael_orchestrator.sources import NotebookTaskSource

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
            "mael_orchestrator.cli.load_global_config",
            return_value=SimpleNamespace(projects_dir=projects_dir),
        ),
        patch("mael_orchestrator.cli.SqliteTaskTable") as table,
        patch("mael_orchestrator.cli.open_state_db"),
        patch(
            "mael_orchestrator.cli.setup_worktree_for_branch", return_value=setup
        ) as open_wt,
        patch("mael_orchestrator.cli.refresh_env") as refresh,
    ):
        refresh.return_value.changed = False
        orchestrator = build_orchestrator()
        opened = orchestrator.tasks.open_worktree("northwind", "feat/x", "feat/base")
    assert isinstance(orchestrator.tasks, NotebookTaskSource)
    assert orchestrator.tasks.projects() == ["northwind"]
    assert orchestrator.tasks.table is table.return_value
    # One store: a task's start comes from the records the router writes.
    assert orchestrator.tasks.agents is orchestrator.daemon.agents
    assert isinstance(orchestrator.worktrees, ListAllWorktreeSource)
    assert isinstance(orchestrator.desk, SqliteDeskStore)
    # Not the in-memory default, which would lose every attached document at a restart.
    assert isinstance(orchestrator.task_attachments, SqliteTaskAttachmentTable)
    assert orchestrator.worktrees.projects_dir == projects_dir
    assert orchestrator.daemon.claude.socket_path == (
        f"{tmp_path / 'root'}/agent-daemon.sock"
    )
    assert opened is setup
    open_wt.assert_called_once()
    assert open_wt.call_args.args[:3] == (
        projects_dir / "northwind",
        "northwind",
        "feat/x",
    )
    assert open_wt.call_args.kwargs["run_install"] is False
    assert open_wt.call_args.kwargs["base"] == "feat/base"
    # A reused worktree's .env is rebuilt from the parent template.
    refresh.assert_called_once()
    assert refresh.call_args.args[1:] == (
        "northwind",
        "alpha",
        projects_dir / "northwind",
        setup.path,
    )


def test_a_failed_env_refresh_warns_and_still_opens(tmp_path, monkeypatch, capsys):
    """A stale .env must not refuse a launch from the UI."""
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "root"))
    from types import SimpleNamespace

    from mael_domain.worktree import WorktreeSetup

    setup = WorktreeSetup(path=tmp_path / "p-alpha", name="alpha", action="reused")
    with (
        patch(
            "mael_orchestrator.cli.load_global_config",
            return_value=SimpleNamespace(projects_dir=tmp_path),
        ),
        patch("mael_orchestrator.cli.SqliteTaskTable"),
        patch("mael_orchestrator.cli.open_state_db"),
        patch("mael_orchestrator.cli.setup_worktree_for_branch", return_value=setup),
        patch(
            "mael_orchestrator.cli.refresh_env",
            side_effect=ValueError("bad yaml"),
        ),
    ):
        opened = build_orchestrator().tasks.open_worktree("p", "feat/x", "")
    assert opened is setup
    assert "Warning: .env not refreshed: bad yaml" in capsys.readouterr().err


def _worktree_source(tmp_path, monkeypatch, table=None) -> ListAllWorktreeSource:
    """The worktree source ``build_orchestrator`` wires, over ``table``."""
    monkeypatch.setenv("MAEL_AGENT_ROOT", str(tmp_path / "root"))
    from types import SimpleNamespace

    with (
        patch(
            "mael_orchestrator.cli.load_global_config",
            return_value=SimpleNamespace(projects_dir=tmp_path),
        ),
        patch(
            "mael_orchestrator.cli.SqliteTaskTable",
            **({} if table is None else {"return_value": table}),
        ),
        patch("mael_orchestrator.cli.open_state_db"),
    ):
        worktrees = build_orchestrator().worktrees
    assert isinstance(worktrees, ListAllWorktreeSource)
    assert worktrees.ensure_terminal is not None
    return worktrees


def test_the_terminal_ports_ask_the_running_cmux(tmp_path, monkeypatch, fake_cmux):
    worktrees = _worktree_source(tmp_path, monkeypatch)
    assert worktrees.ensure_terminal is not None
    url = worktrees.ensure_terminal("northwind", "alpha", "/p")
    assert worktrees.terminal_urls([("northwind", "alpha")]) == {
        ("northwind", "alpha"): url
    }
    assert fake_cmux.tabs("northwind-alpha") == [["Terminal"]]


def test_the_terminal_ports_outside_cmux(tmp_path, monkeypatch):
    worktrees = _worktree_source(tmp_path, monkeypatch)
    assert worktrees.ensure_terminal is not None
    # No terminal, and the user reads why.
    with pytest.raises(CloseBlocked, match="cmux could not make the terminal"):
        worktrees.ensure_terminal("northwind", "alpha", "/p")
    assert worktrees.terminal_urls([("northwind", "alpha")]) == {}


def _force_close(worktrees: ListAllWorktreeSource, *, had_unmerged_work: bool) -> None:
    """Force close alpha, with the teardown itself standing in as a success."""
    closed = FullCloseResult(
        close=CloseResult(
            success=True,
            message="Worktree closed",
            branch="feat/orders",
            had_unmerged_work=had_unmerged_work,
        )
    )
    assert worktrees.force_close is not None
    with patch(
        "mael_orchestrator.cli.close_worktree_fully", new=AsyncMock(return_value=closed)
    ):
        asyncio.run(worktrees.force_close("northwind", "alpha", "/p"))


def test_a_force_close_over_unmerged_work_writes_a_reopen_task(tmp_path, monkeypatch):
    table = InMemoryTaskTable()
    _force_close(_worktree_source(tmp_path, monkeypatch, table), had_unmerged_work=True)

    [task] = asyncio.run(list_tasks(table, project="northwind"))
    assert task.title == "Reopen feat/orders"
    assert task.command == "reopen-branch"
    assert task.branch == "feat/orders"


def test_a_force_close_with_nothing_unmerged_writes_no_task(tmp_path, monkeypatch):
    table = InMemoryTaskTable()
    _force_close(
        _worktree_source(tmp_path, monkeypatch, table), had_unmerged_work=False
    )

    assert asyncio.run(list_tasks(table, project="northwind")) == []


def test_a_force_close_still_succeeds_when_the_reopen_task_cannot_be_written(
    tmp_path, monkeypatch
):
    # The worktree is closed by then. Raising would report that close as failed.
    worktrees = _worktree_source(tmp_path, monkeypatch)
    with patch(
        "mael_orchestrator.cli.add_reopen_task",
        new=AsyncMock(side_effect=OSError("disk full")),
    ):
        _force_close(worktrees, had_unmerged_work=True)


async def _read_a_ready_pr(worktrees: ListAllWorktreeSource) -> None:
    """Give the wired source a reading that holds PR #118, ready, on feat/x."""
    row = {
        "name": "alpha",
        "path": "/p/alpha",
        "branch": "feat/x",
        "pr_number": 118,
        "pr_commits": 1,
        "pr_url": "",
        "pr_state": "ready",
        "pr_draft": False,
        "pr_merged_at": None,
        "pr_head_oid": "deadbee",
    }

    async def list_all(*_args, **_kwargs):
        return {"projects": [{"name": "northwind", "worktrees": [row]}]}

    with patch("mael_orchestrator.sources.build_list_all_data", list_all):
        await worktrees.read()


async def test_the_merge_port_merges_with_the_merge_token(tmp_path, monkeypatch):
    """The token is the point of the port: without it the merge uses the
    login the agents share."""
    monkeypatch.setenv("MAEL_GITHUB_MERGE_TOKEN", "ghp_merge")
    worktrees = _worktree_source(tmp_path, monkeypatch)
    await _read_a_ready_pr(worktrees)
    assert worktrees.merge is not None
    with patch("mael_orchestrator.cli.github.merge_pr") as merge_pr:
        await worktrees.merge("northwind", "feat/x", "/p/alpha")
    merge_pr.assert_called_once_with(
        118, cwd=Path("/p/alpha"), head_oid="deadbee", token="ghp_merge"
    )


async def test_the_merge_port_reports_what_github_refused(tmp_path, monkeypatch):
    worktrees = _worktree_source(tmp_path, monkeypatch)
    await _read_a_ready_pr(worktrees)
    assert worktrees.merge is not None
    refused = GitHubCommandFailed("merge the pull request", "Head branch was modified")
    with patch("mael_orchestrator.cli.github.merge_pr", side_effect=refused):
        with pytest.raises(CloseBlocked, match="Head branch was modified"):
            await worktrees.merge("northwind", "feat/x", "/p/alpha")


@pytest.mark.usefixtures("migrated_notebook")
def test_serve_sets_up_logging_with_timestamps_before_it_serves():
    """The log must name when and how bad, or a crash leaves nothing to read.

    Without configuration every ``log.exception`` goes to the root logger's
    last-resort handler: no timestamp, no level, and every ``log.info``
    dropped.
    """
    with (
        patch("mael_orchestrator.cli.serve_app"),
        patch("mael_orchestrator.cli.build_orchestrator"),
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


@pytest.mark.usefixtures("migrated_notebook")
def test_an_exception_that_escapes_a_task_is_logged(capsys):
    """A task that dies with nobody awaiting it must still say so."""
    captured = {}

    async def scenario(*_args, **_kwargs):
        captured["handler"] = asyncio.get_running_loop().get_exception_handler()

    with (
        patch("mael_orchestrator.cli.build_orchestrator"),
        patch("mael_orchestrator.cli.build_app"),
        patch("mael_orchestrator.cli.serve_app", new=scenario),
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


@pytest.mark.usefixtures("migrated_notebook")
def test_a_sigterm_shuts_the_server_down_cleanly():
    """A supervised restart sends SIGTERM, not Ctrl-C.

    Without a handler the default terminates the process outright, so
    ``runner.cleanup`` never runs and the orchestrator never stops: pollers
    are left running and the desk is never flushed.
    """
    stopped = []

    async def scenario(*_args, **_kwargs):
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
        patch("mael_orchestrator.cli.build_orchestrator"),
        patch("mael_orchestrator.cli.build_app"),
        patch("mael_orchestrator.cli.serve_app", new=scenario),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)

    assert stopped == ["cancelled"]


def test_an_unmigrated_state_database_refuses_to_serve(tmp_path, monkeypatch):
    """An ordinary open names the fix rather than upgrading under a running server.

    Every worktree shares one ``~/.maelstrom``, so a database this build cannot
    read is Tuesday. Refusing is the only non-destructive answer, and the
    message has to carry the command that clears it.
    """
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    with (
        patch("mael_orchestrator.cli.build_orchestrator"),
        patch("mael_orchestrator.cli.build_app"),
        patch("mael_orchestrator.cli.serve_app"),
        pytest.raises(SchemaTooOldError) as exc,
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)
    assert "mael admin migrate" in str(exc.value)


def test_serve_reports_a_refused_state_database_as_an_error(tmp_path, monkeypatch):
    """The refusal reaches the user as one line, not as a traceback."""
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(tmp_path))
    with patch(
        "mael_orchestrator.cli.run_server",
        side_effect=SchemaTooOldError("run `mael admin migrate`"),
    ):
        result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 1
    assert "mael admin migrate" in result.output


@pytest.mark.usefixtures("migrated_notebook")
def test_serve_reports_a_missing_daemon_root_as_an_error(monkeypatch):
    """The root has no fallback, so its absence must name the fix, not end in a
    traceback."""
    monkeypatch.delenv("MAEL_AGENT_ROOT")
    with patch("mael_orchestrator.cli.serve_app"):
        result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 1
    assert "MAEL_AGENT_ROOT is not set" in result.output


def test_a_maelstrom_dir_that_does_not_exist_is_created(tmp_path, monkeypatch):
    """A machine that has never run a mael command has no ~/.maelstrom.

    sqlite3 raises OperationalError rather than creating the directory, and
    that is not a StateDbError, so the refusal handler would miss it and the
    user would see a traceback.
    """
    home = tmp_path / "never-used"
    monkeypatch.setenv("MAEL_NOTEBOOK_ROOT", str(home))
    with (
        patch("mael_orchestrator.cli.build_orchestrator"),
        patch("mael_orchestrator.cli.build_app"),
        patch("mael_orchestrator.cli.serve_app"),
        pytest.raises(SchemaTooOldError),
    ):
        run_server(DEFAULT_HOST, DEFAULT_PORT)
    assert home.is_dir()
