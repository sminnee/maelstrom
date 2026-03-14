"""Tests for maelstrom.env_cli module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.env import EnvState, ServiceState, ServiceStatus


def _make_state(project="proj", worktree="bravo", pid=100):
    return EnvState(
        project=project,
        worktree=worktree,
        worktree_path=f"/project/{worktree}",
        started_at="2025-01-01T00:00:00+00:00",
        services=[
            ServiceState(
                name="web",
                command="python app.py",
                pid=pid,
                log_file="/tmp/web.log",
                started_at="2025-01-01T00:00:00+00:00",
            )
        ],
    )


def _make_status(name="web", pid=100, alive=True):
    return ServiceStatus(
        name=name,
        pid=pid,
        alive=alive,
        command="python app.py",
        log_file="/tmp/web.log",
        started_at="2025-01-01T00:00:00+00:00",
    )


def _mock_ctx_with_path(tmp_path, project="proj", worktree="bravo"):
    """Create a mock context with a real worktree_path that exists."""
    wt_path = tmp_path / worktree
    wt_path.mkdir(exist_ok=True)
    project_path = tmp_path / project
    project_path.mkdir(exist_ok=True)
    return MagicMock(
        project=project,
        worktree=worktree,
        worktree_path=wt_path,
        project_path=project_path,
    )


class TestEnvStart:
    """Tests for mael env start command."""

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_success(self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path):
        """Starts env and prints status table with uptime."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_start.return_value = state
        mock_load.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "running" in result.output
        assert "UPTIME:" in result.output
        mock_start.assert_called_once_with(
            "proj", "bravo", ctx.worktree_path, skip_install=False,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_skip_install_flag(self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path):
        """Passes skip_install flag through."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_start.return_value = state
        mock_load.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start", "--skip-install"])
        assert result.exit_code == 0
        mock_start.assert_called_once_with(
            "proj", "bravo", ctx.worktree_path, skip_install=True,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_app_url(self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path):
        """Shows App URL when port is allocated."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_start.return_value = state
        mock_load.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0
        assert "APP RUNNING AT: http://localhost:3000" in result.output

    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_already_running(self, mock_ctx, mock_start, tmp_path):
        """Shows error when services are already running."""
        mock_ctx.return_value = _mock_ctx_with_path(tmp_path)
        mock_start.side_effect = RuntimeError("Services already running for proj/bravo: web")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code != 0
        assert "already running" in result.output

    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_services(self, mock_ctx, mock_start, tmp_path):
        """Shows error when no services are defined."""
        mock_ctx.return_value = _mock_ctx_with_path(tmp_path)
        mock_start.side_effect = RuntimeError("No Procfile found")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code != 0
        assert "No Procfile" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_worktree_not_found(self, mock_ctx):
        """Shows error when worktree path doesn't exist."""
        mock_ctx.return_value = MagicMock(
            project="proj",
            worktree="bravo",
            worktree_path=Path("/nonexistent/path"),
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code != 0
        assert "Worktree not found" in result.output


class TestEnvStartBrowserDedup:
    """Tests for cmux browser deduplication in env start."""

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.CmuxWorkspace.current")
    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_reuses_existing_browser(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app,
        mock_ws_current, mock_save, tmp_path, mock_cmux_workspace,
    ):
        """Reuses existing browser surface when one matches the URL."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_load.return_value = _make_state()
        mock_start.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        mock_cmux_workspace.ensure_browser.return_value = "surface:183"
        mock_ws_current.return_value = mock_cmux_workspace

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0
        mock_cmux_workspace.ensure_browser.assert_called_once_with("http://localhost:3000")

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.CmuxWorkspace.current")
    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_opens_new_when_no_existing(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app,
        mock_ws_current, mock_save, tmp_path, mock_cmux_workspace,
    ):
        """Opens new browser when no existing browser matches."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_start.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        mock_cmux_workspace.ensure_browser.return_value = "surface:200"
        mock_ws_current.return_value = mock_cmux_workspace

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0
        mock_cmux_workspace.ensure_browser.assert_called_once_with("http://localhost:3000")

    @patch("maelstrom.env_cli.CmuxWorkspace.current", return_value=None)
    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_cmux_skips_browser(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app,
        mock_ws_current, tmp_path,
    ):
        """Skips browser logic when not in cmux mode."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_start.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0


class TestEnvStopBrowser:
    """Tests for browser close on env stop."""

    @patch("maelstrom.env_cli.CmuxWorkspace.current")
    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_closes_browser_on_stop(
        self, mock_ctx, mock_stop, mock_app, mock_ws_current, mock_cmux_workspace,
    ):
        """Closes browser surface matching URL when stopping env."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo", project_path=Path("/proj"))
        mock_stop.return_value = ["web (pid 100): stopped"]

        mock_ws_current.return_value = mock_cmux_workspace

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        mock_cmux_workspace.close_browser.assert_called_once_with("http://localhost:3000")


class TestEnvStatus:
    """Tests for mael env status command."""

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_service_table(self, mock_ctx, mock_status, mock_load, mock_app):
        """Prints SERVICE/PID/STATUS/LOG table with uptime."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_load.return_value = _make_state()
        mock_status.return_value = [
            _make_status("web", pid=100, alive=True),
            _make_status("worker", pid=200, alive=False),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "worker" in result.output
        assert "running" in result.output
        assert "dead" in result.output
        assert "UPTIME:" in result.output

    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", False))
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_app_url(self, mock_ctx, mock_status, mock_load, mock_app):
        """Shows App line with port when allocated but not running."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "APP RUNNING AT: *3000" in result.output

    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_state(self, mock_ctx, mock_load):
        """Shows message when no environment state exists."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "No environment state" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_context_error(self, mock_ctx):
        """Shows error when context cannot be resolved."""
        mock_ctx.side_effect = ValueError("Could not determine worktree.")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code != 0
        assert "Could not determine worktree" in result.output


class TestEnvStop:
    """Tests for mael env stop command."""

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_success(self, mock_ctx, mock_stop):
        """Stops env and prints messages."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_stop.return_value = ["web (pid 100): stopped"]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        assert "web (pid 100): stopped" in result.output
        assert "Environment stopped" in result.output

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_not_running(self, mock_ctx, mock_stop):
        """Shows message when nothing is running."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_stop.return_value = ["No running environment for proj/bravo"]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        assert "No running environment" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_context_error(self, mock_ctx):
        """Shows error when context cannot be resolved."""
        mock_ctx.side_effect = ValueError("Could not determine project.")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code != 0
        assert "Could not determine project" in result.output


class TestEnvList:
    """Tests for mael env list command."""

    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.format_uptime", return_value="5m")
    @patch("maelstrom.env_cli.list_project_envs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_with_running_envs(self, mock_ctx, mock_list, mock_uptime, mock_status, mock_app):
        """Prints table with running environments and APP column."""
        mock_ctx.return_value = MagicMock(project="proj")
        mock_list.return_value = [_make_state()]
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "list"])
        assert result.exit_code == 0
        assert "bravo" in result.output
        assert "APP" in result.output
        assert "http://localhost:3000" in result.output
        assert "RUNNING SERVICES" in result.output
        assert "web" in result.output
        assert "5m" in result.output

    @patch("maelstrom.env_cli.list_project_envs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_envs(self, mock_ctx, mock_list):
        """Shows message when no envs running."""
        mock_ctx.return_value = MagicMock(project="proj")
        mock_list.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "list"])
        assert result.exit_code == 0
        assert "No running environments" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_context_error(self, mock_ctx):
        """Shows error when context cannot be resolved."""
        mock_ctx.side_effect = ValueError("Could not determine project.")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "list"])
        assert result.exit_code != 0
        assert "Could not determine project" in result.output


class TestEnvListAll:
    """Tests for mael env list-all command."""

    @patch("maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True))
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.format_uptime", return_value="2h")
    @patch("maelstrom.env_cli.list_all_envs")
    def test_with_envs(self, mock_list, mock_uptime, mock_status, mock_app):
        """Prints table with all environments and APP column."""
        mock_list.return_value = [
            _make_state("projA", "alpha", pid=100),
            _make_state("projB", "bravo", pid=200),
        ]
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "list-all"])
        assert result.exit_code == 0
        assert "projA" in result.output
        assert "projB" in result.output
        assert "APP" in result.output
        assert "RUNNING SERVICES" in result.output
        assert "STOPPED SERVICES" in result.output

    @patch("maelstrom.env_cli.list_all_envs")
    def test_empty(self, mock_list):
        """Shows message when no environments running."""
        mock_list.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "list-all"])
        assert result.exit_code == 0
        assert "No running environments" in result.output


class TestEnvStopAll:
    """Tests for mael env stop-all command."""

    @patch("maelstrom.env_cli.stop_all_envs")
    def test_success(self, mock_stop_all):
        """Stops all envs and prints per-env messages."""
        mock_stop_all.return_value = [
            ("projA", "alpha", ["web (pid 100): stopped"]),
            ("projB", "bravo", ["app (pid 200): stopped"]),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop-all"])
        assert result.exit_code == 0
        assert "projA/alpha:" in result.output
        assert "web (pid 100): stopped" in result.output
        assert "projB/bravo:" in result.output
        assert "app (pid 200): stopped" in result.output
        assert "Stopped 2 environment(s)." in result.output

    @patch("maelstrom.env_cli.stop_all_envs")
    def test_empty(self, mock_stop_all):
        """Shows message when no environments running."""
        mock_stop_all.return_value = []

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop-all"])
        assert result.exit_code == 0
        assert "No running environments" in result.output


class TestEnvLogs:
    """Tests for mael env logs command."""

    @patch("maelstrom.env_cli.get_log_files")
    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_single_service_no_prefix(self, mock_ctx, mock_read, mock_files):
        """Single service output has no [service] prefix."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.return_value = [("web", "line 1"), ("web", "line 2")]
        mock_files.return_value = {"web": Path("/tmp/web.log")}

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs"])
        assert result.exit_code == 0
        assert "line 1" in result.output
        assert "line 2" in result.output
        assert "[web]" not in result.output

    @patch("maelstrom.env_cli.get_log_files")
    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_multi_service_with_prefix(self, mock_ctx, mock_read, mock_files):
        """Multi-service output has [service] prefix."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.return_value = [("web", "web line"), ("worker", "worker line")]
        mock_files.return_value = {
            "web": Path("/tmp/web.log"),
            "worker": Path("/tmp/worker.log"),
        }

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs"])
        assert result.exit_code == 0
        assert "[web] web line" in result.output
        assert "[worker] worker line" in result.output

    @patch("maelstrom.env_cli.get_log_files")
    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_custom_n(self, mock_ctx, mock_read, mock_files):
        """Passes -n value through to read_service_logs."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.return_value = []
        mock_files.return_value = {"web": Path("/tmp/web.log")}

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs", "-n", "50"])
        assert result.exit_code == 0
        mock_read.assert_called_once_with("proj", "bravo", None, 50)

    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_logs_error(self, mock_ctx, mock_read):
        """Shows error when no logs exist."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.side_effect = ValueError("No logs found for proj/bravo")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs"])
        assert result.exit_code != 0
        assert "No logs found" in result.output

    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_service_not_found_error(self, mock_ctx, mock_read):
        """Shows error for unknown service."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.side_effect = ValueError("Service 'db' not found. Available: web")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs", "--", "bravo", "db"])
        assert result.exit_code != 0
        assert "Service 'db' not found" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_context_error(self, mock_ctx):
        """Shows error when context cannot be resolved."""
        mock_ctx.side_effect = ValueError("Could not determine worktree.")

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs"])
        assert result.exit_code != 0
        assert "Could not determine worktree" in result.output

    @patch("maelstrom.env_cli._follow_logs")
    @patch("maelstrom.env_cli.get_log_files")
    @patch("maelstrom.env_cli.read_service_logs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_follow_flag(self, mock_ctx, mock_read, mock_files, mock_follow):
        """Follow flag invokes _follow_logs."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_read.return_value = [("web", "line 1")]
        mock_files.return_value = {"web": Path("/tmp/web.log")}

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "logs", "-f"])
        assert result.exit_code == 0
        mock_follow.assert_called_once_with("proj", "bravo", None, False)


class TestEnvStatusShared:
    """Tests for shared service display in env status."""

    @patch("maelstrom.env_cli.get_shared_status")
    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_shared_services(self, mock_ctx, mock_status, mock_load, mock_app, mock_shared):
        """Shared services appear in status output with (shared) tag."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status("web", pid=100, alive=True)]
        mock_shared.return_value = [
            ServiceStatus(
                name="db-shared", pid=200, alive=True,
                command="postgres", log_file="/tmp/db.log",
                started_at="2025-01-01T00:00:00+00:00",
            ),
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "db-shared (shared)" in result.output

    @patch("maelstrom.env_cli.get_shared_status", return_value=None)
    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_shared_services(self, mock_ctx, mock_status, mock_load, mock_app, mock_shared):
        """Works normally when no shared services exist."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "shared" not in result.output


class TestEnvStopShared:
    """Tests for shared service messages in env stop."""

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_shared_unsubscribe_message(self, mock_ctx, mock_stop):
        """Shows message about shared services still in use."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_stop.return_value = [
            "web (pid 100): stopped",
            "Shared services still used by 1 other environment(s)",
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        assert "Shared services still used by 1" in result.output

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_shared_stop_messages(self, mock_ctx, mock_stop):
        """Shows shared service stop messages when last subscriber."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_stop.return_value = [
            "web (pid 100): stopped",
            "db-shared (shared) (pid 200): stopped",
        ]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        assert "db-shared (shared)" in result.output


class TestEnvRestart:
    """Tests for mael env restart command."""

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.stop_env", return_value=["web (pid 100): stopped"])
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_stops_and_starts(
        self, mock_ctx, mock_load, mock_stop, mock_start,
        mock_status, mock_app, tmp_path,
    ):
        """Stops running env and starts it again with skip_install=True."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_load.return_value = state
        mock_start.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart"])
        assert result.exit_code == 0
        assert "Environment stopped" in result.output
        mock_stop.assert_called_once_with("proj", "bravo")
        mock_start.assert_called_once_with(
            "proj", "bravo", ctx.worktree_path, skip_install=True,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.stop_env", return_value=["web (pid 100): stopped"])
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_with_install(
        self, mock_ctx, mock_load, mock_stop, mock_start,
        mock_status, mock_app, tmp_path,
    ):
        """Passes --install flag to start with skip_install=False."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_load.return_value = state
        mock_start.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart", "--install"])
        assert result.exit_code == 0
        mock_start.assert_called_once_with(
            "proj", "bravo", ctx.worktree_path, skip_install=False,
        )

    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_not_running(self, mock_ctx, mock_load, tmp_path):
        """Errors when no env state exists."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart"])
        assert result.exit_code != 0
        assert "No running environment" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_worktree_not_found(self, mock_ctx):
        """Errors when worktree path doesn't exist."""
        mock_ctx.return_value = MagicMock(
            project="proj",
            worktree="bravo",
            worktree_path=Path("/nonexistent/path"),
            project_path=Path("/nonexistent"),
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart"])
        assert result.exit_code != 0
        assert "Worktree not found" in result.output


class TestEnvReset:
    """Tests for mael env reset command."""

    @patch("maelstrom.env_cli.regenerate_env_file")
    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_not_running(self, mock_ctx, mock_load, mock_regen, tmp_path):
        """Regenerates .env without stop/start when env is not running."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code == 0
        assert "Regenerated .env" in result.output
        mock_regen.assert_called_once_with(ctx.project_path, ctx.worktree_path, "bravo")

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.regenerate_env_file")
    @patch("maelstrom.env_cli.stop_env", return_value=["web (pid 100): stopped"])
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_running_stops_and_restarts(
        self, mock_ctx, mock_load, mock_stop, mock_regen, mock_start,
        mock_status, mock_app, tmp_path,
    ):
        """Stops env, regenerates .env, and restarts when env is running."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_load.return_value = state
        mock_start.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code == 0
        assert "Environment stopped" in result.output
        assert "Regenerated .env" in result.output
        mock_stop.assert_called_once_with("proj", "bravo")
        mock_regen.assert_called_once_with(ctx.project_path, ctx.worktree_path, "bravo")
        mock_start.assert_called_once_with(
            "proj", "bravo", ctx.worktree_path, skip_install=True,
        )

    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_worktree_not_found(self, mock_ctx):
        """Shows error when worktree path doesn't exist."""
        mock_ctx.return_value = MagicMock(
            project="proj",
            worktree="bravo",
            worktree_path=Path("/nonexistent/path"),
            project_path=Path("/nonexistent"),
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code != 0
        assert "Worktree not found" in result.output
