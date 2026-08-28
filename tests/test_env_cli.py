"""Tests for maelstrom.env_cli module."""

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.config import MaelstromConfig
from maelstrom.env import EnvState, ServiceState, ServiceStatus
from maelstrom.env_cli import (
    ensure_cmux_browser,
    print_service_status,
    resolve_service_target,
)


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
    def test_success(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path
    ):
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
            ANY,
            "proj",
            "bravo",
            ctx.worktree_path,
            skip_install=False,
            services=None,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_skip_install_flag(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path
    ):
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
            ANY,
            "proj",
            "bravo",
            ctx.worktree_path,
            skip_install=True,
            services=None,
        )

    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_app_url(
        self, mock_ctx, mock_start, mock_status, mock_load, mock_app, tmp_path
    ):
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
        mock_start.side_effect = RuntimeError(
            "Services already running for proj/bravo: web"
        )

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
    """Tests for cmux browser placement in env start.

    The CLI now delegates to mael_layout.show_app_browser (the policy seam);
    its recycle-vs-open behaviour is tested in test_mael_layout.py, so here we
    just assert the seam is called with the right (project, worktree, url).
    """

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.mael_layout.show_app_browser", return_value="surface:183")
    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_app_browser(
        self,
        mock_ctx,
        mock_start,
        mock_status,
        mock_load,
        mock_app,
        mock_show,
        mock_save,
        tmp_path,
    ):
        """Delegates to show_app_browser with the env's project/worktree/url."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_load.return_value = _make_state()
        mock_start.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0
        # state.project / ctx.worktree drive the call.
        args = mock_show.call_args.args
        assert args[2] == "http://localhost:3000"

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.mael_layout.show_app_browser", return_value=None)
    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_no_cmux_skips_browser(
        self,
        mock_ctx,
        mock_start,
        mock_status,
        mock_load,
        mock_app,
        mock_show,
        mock_save,
        tmp_path,
    ):
        """When show_app_browser returns None (outside cmux), no surface stored."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_start.return_value = _make_state()
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start"])
        assert result.exit_code == 0


class TestEnvStopBrowser:
    """Tests for browser close on env stop."""

    @patch("maelstrom.env_cli.mael_layout.hide_app_browser", return_value=True)
    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_closes_browser_on_stop(
        self,
        mock_ctx,
        mock_stop,
        mock_app,
        mock_hide,
    ):
        """Delegates to hide_app_browser with the env's project/worktree/url."""
        mock_ctx.return_value = MagicMock(
            project="proj", worktree="bravo", project_path=Path("/proj")
        )
        mock_stop.return_value = ["web (pid 100): stopped"]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop"])
        assert result.exit_code == 0
        mock_hide.assert_called_once_with("proj", "bravo", "http://localhost:3000")


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

    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", False)
    )
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

    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.format_uptime", return_value="5m")
    @patch("maelstrom.env_cli.list_project_envs")
    @patch("maelstrom.env_cli.resolve_context")
    def test_with_running_envs(
        self, mock_ctx, mock_list, mock_uptime, mock_status, mock_app
    ):
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

    @patch(
        "maelstrom.env_cli.get_app_url", return_value=("http://localhost:3000", True)
    )
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
        mock_read.assert_called_once_with(ANY, "proj", "bravo", None, 50)

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
        mock_follow.assert_called_once_with(ANY, "proj", "bravo", None, False)


class TestEnvStatusShared:
    """Tests for shared service display in env status."""

    @patch("maelstrom.env_cli.get_shared_status")
    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_shows_shared_services(
        self, mock_ctx, mock_status, mock_load, mock_app, mock_shared
    ):
        """Shared services appear in status output with (shared) tag."""
        mock_ctx.return_value = MagicMock(project="proj", worktree="bravo")
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status("web", pid=100, alive=True)]
        mock_shared.return_value = [
            ServiceStatus(
                name="db-shared",
                pid=200,
                alive=True,
                command="postgres",
                log_file="/tmp/db.log",
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
    def test_no_shared_services(
        self, mock_ctx, mock_status, mock_load, mock_app, mock_shared
    ):
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
        self,
        mock_ctx,
        mock_load,
        mock_stop,
        mock_start,
        mock_status,
        mock_app,
        tmp_path,
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
        mock_stop.assert_called_once_with(ANY, "proj", "bravo", services=None)
        mock_start.assert_called_once_with(
            ANY,
            "proj",
            "bravo",
            ctx.worktree_path,
            skip_install=True,
            services=None,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.stop_env", return_value=["web (pid 100): stopped"])
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_with_install(
        self,
        mock_ctx,
        mock_load,
        mock_stop,
        mock_start,
        mock_status,
        mock_app,
        tmp_path,
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
            ANY,
            "proj",
            "bravo",
            ctx.worktree_path,
            skip_install=False,
            services=None,
        )

    @patch("maelstrom.env_cli.env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.load_env_state", return_value=None)
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_not_running(
        self, mock_ctx, mock_load, mock_stop, mock_start, mock_status, tmp_path
    ):
        """When no env state exists, restart skips stop and just starts."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = MagicMock()
        mock_start.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart"])
        assert result.exit_code == 0
        mock_stop.assert_not_called()
        mock_start.assert_called_once()

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

    @patch(
        "maelstrom.env_cli.regenerate_and_restart_if_running", return_value=([], None)
    )
    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_not_running(self, mock_ctx, mock_helper, tmp_path):
        """Regenerates .env without stop/start when env is not running."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code == 0
        assert "Regenerated .env" in result.output
        mock_helper.assert_called_once_with(
            ANY,
            "proj",
            "bravo",
            ctx.project_path,
            ctx.worktree_path,
        )

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.regenerate_and_restart_if_running")
    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_running_stops_and_restarts(
        self,
        mock_ctx,
        mock_helper,
        mock_load,
        mock_status,
        mock_app,
        tmp_path,
    ):
        """Stops env, regenerates .env, and restarts when env is running."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        state = _make_state()
        mock_helper.return_value = (["web (pid 100): stopped"], state)
        mock_load.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code == 0
        assert "Environment stopped" in result.output
        assert "Regenerated .env" in result.output
        mock_helper.assert_called_once_with(
            ANY,
            "proj",
            "bravo",
            ctx.project_path,
            ctx.worktree_path,
        )

    @patch(
        "maelstrom.env_cli.regenerate_and_restart_if_running", return_value=([], None)
    )
    @patch("maelstrom.env_cli.resolve_context")
    def test_reset_copies_back_new_worktree_var(self, mock_ctx, mock_helper, tmp_path):
        """Copies a new worktree var back to the parent before regenerating."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        # Parent template has only an existing var.
        (ctx.project_path / ".env").write_text("EXISTING=1\n")
        # Worktree .env carries a managed section plus a brand-new user var.
        (ctx.worktree_path / ".env").write_text(
            "# Maelstrom port allocations\n"
            "WORKTREE=bravo\n"
            "# End Maelstrom port allocations\n"
            "\nEXISTING=1\nFOO=bar\n"
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "reset"])
        assert result.exit_code == 0, result.output
        assert "Copied 1 new var(s) back" in result.output
        assert "+FOO=bar" in result.output
        parent_text = (ctx.project_path / ".env").read_text()
        assert "FOO=bar" in parent_text

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


class TestResolveServiceTarget:
    """Tests for resolve_service_target — service name versus worktree target."""

    def _ctx(self, worktree="bravo"):
        return MagicMock(
            project="proj",
            worktree=worktree,
            worktree_path=Path(f"/proj/{worktree}"),
            project_path=Path("/proj"),
        )

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_declared_service_name_is_a_service(self, mock_ctx, mock_config):
        """A positional matching a declared service selects that service."""
        mock_ctx.return_value = self._ctx()
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve", "optional": True}}}
        )
        ctx, name = resolve_service_target("ladle", None, None)
        assert name == "ladle"
        assert ctx.worktree == "bravo"

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_undeclared_name_is_a_target(self, mock_ctx, mock_config):
        """A positional that is not a declared service stays a worktree target."""
        mock_ctx.side_effect = [self._ctx("bravo"), self._ctx("charlie")]
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"web": {"command": "node server.ts"}}}
        )
        ctx, name = resolve_service_target("c", None, None)
        assert name is None
        assert ctx.worktree == "charlie"

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_dotted_positional_is_always_a_target(self, mock_ctx, mock_config):
        """A dotted positional is a target; the config is never consulted."""
        mock_ctx.return_value = self._ctx("bravo")
        ctx, name = resolve_service_target("proj.b", None, None)
        assert name is None
        mock_config.assert_not_called()

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_service_option_makes_positional_a_target(self, mock_ctx, mock_config):
        """--service names the service; the positional stays a target."""
        mock_ctx.return_value = self._ctx("bravo")
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve"}}}
        )
        ctx, name = resolve_service_target("proj.b", "ladle", None)
        assert name == "ladle"
        assert ctx.worktree == "bravo"

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_worktree_option_selects_the_worktree(self, mock_ctx, mock_config):
        """-w takes a project.worktree string alongside a service positional."""
        mock_ctx.return_value = self._ctx("bravo")
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve"}}}
        )
        ctx, name = resolve_service_target("ladle", None, "askastro.b")
        assert name == "ladle"
        assert mock_ctx.call_args_list[0][0][0] == "askastro.b"

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_ambiguous_name_errors(self, mock_ctx, mock_config):
        """A name that is both a service and a worktree name is rejected."""
        mock_ctx.return_value = self._ctx("bravo")
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"bravo": {"command": "serve"}}}
        )
        with pytest.raises(ValueError, match="--service"):
            resolve_service_target("bravo", None, None)


class TestEnvStartNamedService:
    """Tests for `mael env start <service>`."""

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_named_start_passes_selection(
        self,
        mock_ctx,
        mock_config,
        mock_start,
        mock_status,
        mock_load,
        mock_app,
        tmp_path,
    ):
        """The service name reaches start_env, with install skipped."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve", "optional": True}}}
        )
        state = _make_state()
        mock_start.return_value = state
        mock_load.return_value = state
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start", "ladle"])
        assert result.exit_code == 0
        mock_start.assert_called_once_with(
            ANY,
            "proj",
            "bravo",
            ctx.worktree_path,
            skip_install=True,
            services=["ladle"],
        )

    @patch("maelstrom.env_cli.resolve_context")
    def test_unknown_service_lists_declared(self, mock_ctx, tmp_path):
        """An unknown --service name lists what the project declares."""
        ctx = _mock_ctx_with_path(tmp_path)
        (ctx.worktree_path / ".maelstrom.yaml").write_text(
            "services:\n  web:\n    command: node server.ts\n"
        )
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start", "-s", "nope"])
        assert result.exit_code != 0
        assert "Declared services: web" in result.output

    @patch("maelstrom.env_cli.resolve_context")
    def test_procfile_project_rejects_named_service(self, mock_ctx, tmp_path):
        """A Procfile project reports why a named service cannot work."""
        ctx = _mock_ctx_with_path(tmp_path)
        (ctx.worktree_path / "Procfile").write_text("web: node server.ts\n")
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "start", "-s", "ladle"])
        assert result.exit_code != 0
        assert "Procfile" in result.output


class TestEnvStopNamedService:
    """Tests for `mael env stop <service>`."""

    @patch("maelstrom.env_cli.mael_layout.hide_app_browser")
    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_partial_stop_leaves_browser_alone(
        self, mock_ctx, mock_config, mock_stop, mock_app, mock_hide
    ):
        """A named stop does not close the main app's browser pane."""
        mock_ctx.return_value = MagicMock(
            project="proj",
            worktree="bravo",
            project_path=Path("/proj"),
            worktree_path=Path("/proj/bravo"),
        )
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve", "optional": True}}}
        )
        mock_stop.return_value = ["ladle (pid 200): stopped"]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop", "ladle"])
        assert result.exit_code == 0
        mock_hide.assert_not_called()
        assert "Service stopped for proj/bravo: ladle." in result.output
        mock_stop.assert_called_once_with(ANY, "proj", "bravo", services=["ladle"])


class TestEnsureCmuxBrowserPort:
    """The browser pane waits on the port it is about to open."""

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.mael_layout.show_app_browser", return_value=None)
    @patch("maelstrom.env_cli.wait_for_port")
    @patch("maelstrom.env_cli.get_app_url")
    def test_waits_on_the_app_url_port(self, mock_app, mock_wait, mock_show, mock_save):
        """It waits on the URL's own port, not the worktree's first port."""
        mock_app.return_value = ("http://localhost:3002", True)
        ensure_cmux_browser(_make_state(), Path("/proj"), "bravo")
        mock_wait.assert_called_once_with(3002)

    @patch("maelstrom.env_cli.save_env_state")
    @patch("maelstrom.env_cli.mael_layout.show_app_browser", return_value=None)
    @patch("maelstrom.env_cli.wait_for_port")
    @patch("maelstrom.env_cli.get_app_url")
    def test_passes_service_through(self, mock_app, mock_wait, mock_show, mock_save):
        """A named service restricts the URL search to that service."""
        mock_app.return_value = ("http://localhost:3005", True)
        ensure_cmux_browser(_make_state(), Path("/proj"), "bravo", service="ladle")
        mock_app.assert_called_once_with(Path("/proj"), "bravo", service="ladle")
        mock_wait.assert_called_once_with(3005)

    @patch("maelstrom.env_cli.wait_for_port")
    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    def test_no_web_port_opens_nothing(self, mock_app, mock_wait):
        """A service with no web port neither waits nor opens a pane."""
        ensure_cmux_browser(_make_state(), Path("/proj"), "bravo", service="worker")
        mock_wait.assert_not_called()


class TestEnvStatusDeclaredServices:
    """Declared services absent from the state show as stopped."""

    def _project(self, tmp_path, yaml_text):
        project_path = tmp_path / "proj"
        (project_path / "proj-bravo").mkdir(parents=True)
        (project_path / "proj-bravo" / ".maelstrom.yaml").write_text(yaml_text)
        return project_path

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_lists_unstarted_optional_service(
        self, mock_ctx, mock_status, mock_load, mock_app, tmp_path
    ):
        """An optional service that was never started shows as stopped."""
        project_path = self._project(
            tmp_path,
            "services:\n"
            "  web:\n"
            "    command: node server.ts\n"
            "  ladle:\n"
            "    command: ladle serve\n"
            "    optional: true\n",
        )
        mock_ctx.return_value = MagicMock(
            project="proj", worktree="bravo", project_path=project_path
        )
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status("web")]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        assert "ladle" in result.output
        assert "stopped" in result.output
        assert "(optional)" in result.output

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.resolve_context")
    def test_running_service_is_not_listed_twice(
        self, mock_ctx, mock_status, mock_load, mock_app, tmp_path
    ):
        """A declared service already in the state is not repeated."""
        project_path = self._project(
            tmp_path, "services:\n  web:\n    command: node server.ts\n"
        )
        mock_ctx.return_value = MagicMock(
            project="proj", worktree="bravo", project_path=project_path
        )
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status("web")]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "status"])
        assert result.exit_code == 0
        rows = [ln for ln in result.output.splitlines() if ln.startswith("web")]
        assert len(rows) == 1
        assert "stopped" not in result.output

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.get_env_status")
    def test_no_project_path_skips_the_block(self, mock_status, mock_load, mock_app):
        """With no project path there is no config to read, so nothing is added."""
        mock_load.return_value = _make_state()
        mock_status.return_value = [_make_status("web")]

        runner = CliRunner()
        with runner.isolation() as (out, _err, _):
            print_service_status("proj", "bravo", None)
            captured = out.getvalue().decode()
        assert "stopped" not in captured


class TestEnvRestartNamedService:
    """Tests for `mael env restart <service>`."""

    @patch("maelstrom.env_cli.get_app_url", return_value=None)
    @patch("maelstrom.env_cli.get_env_status")
    @patch("maelstrom.env_cli.start_env")
    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.load_env_state")
    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_restart_cycles_only_that_service(
        self,
        mock_ctx,
        mock_config,
        mock_load,
        mock_stop,
        mock_start,
        mock_status,
        mock_app,
        tmp_path,
    ):
        """The service name reaches both stop_env and start_env."""
        ctx = _mock_ctx_with_path(tmp_path)
        mock_ctx.return_value = ctx
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"ladle": {"command": "ladle serve", "optional": True}}}
        )
        state = _make_state()
        mock_load.return_value = state
        mock_start.return_value = state
        mock_stop.return_value = ["ladle (pid 200): stopped"]
        mock_status.return_value = [_make_status()]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "restart", "ladle"])
        assert result.exit_code == 0
        mock_stop.assert_called_once_with(ANY, "proj", "bravo", services=["ladle"])
        assert mock_start.call_args[1]["services"] == ["ladle"]


class TestResolveServiceTargetOutsideAProject:
    """The resolver must not need a cwd context when the target supplies one."""

    def _outside_cwd(self, arg, **_kwargs):
        """Stand-in for resolve_context: only an explicit target resolves."""
        if arg is None:
            raise ValueError("Could not determine project.")
        project, _, worktree = arg.partition(".")
        return MagicMock(
            project=project,
            worktree=worktree or "alpha",
            worktree_path=Path(f"/{project}/{worktree}"),
            project_path=Path(f"/{project}"),
        )

    @patch("maelstrom.env_cli.resolve_context")
    def test_dotted_target_resolves_outside_a_project(self, mock_ctx):
        """`env start demo.alpha` works from a directory that is not a project."""
        mock_ctx.side_effect = self._outside_cwd
        ctx, name = resolve_service_target("demo.alpha", None, None)
        assert name is None
        assert ctx.project == "demo"

    @patch("maelstrom.env_cli.resolve_context")
    def test_service_option_with_dotted_target_outside_a_project(self, mock_ctx):
        """`env start -s ladle demo.alpha` works from outside a project too."""
        mock_ctx.side_effect = self._outside_cwd
        ctx, name = resolve_service_target("demo.alpha", "ladle", None)
        assert name == "ladle"
        assert ctx.project == "demo"

    @patch("maelstrom.env_cli.resolve_context")
    def test_no_positional_still_needs_a_context(self, mock_ctx):
        """A bare `env start` outside a project still reports the missing context."""
        mock_ctx.side_effect = self._outside_cwd
        with pytest.raises(ValueError, match="Could not determine project"):
            resolve_service_target(None, None, None)

    @patch("maelstrom.env_cli.load_config_or_default")
    @patch("maelstrom.env_cli.resolve_context")
    def test_worktree_option_makes_the_positional_a_service(
        self, mock_ctx, mock_config
    ):
        """With --worktree given, a bare positional can only be a service name."""
        mock_ctx.side_effect = self._outside_cwd
        mock_config.return_value = MaelstromConfig.from_dict(
            {"services": {"web": {"command": "node server.ts"}}}
        )
        with pytest.raises(ValueError, match="Declared services: web"):
            resolve_service_target("nope", None, "demo.alpha")


class TestEnvStopValidatesServiceNames:
    """A mistyped --service name is rejected, not reported as 'not running'."""

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_unknown_service_rejected(self, mock_ctx, mock_stop, tmp_path):
        """`env stop --service <typo>` lists the declared services and exits non-zero."""
        ctx = _mock_ctx_with_path(tmp_path)
        (ctx.worktree_path / ".maelstrom.yaml").write_text(
            "services:\n  web:\n    command: node server.ts\n"
        )
        mock_ctx.return_value = ctx

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop", "-s", "ladel"])
        assert result.exit_code != 0
        assert "Declared services: web" in result.output
        mock_stop.assert_not_called()

    @patch("maelstrom.env_cli.stop_env")
    @patch("maelstrom.env_cli.resolve_context")
    def test_declared_service_reaches_the_model(self, mock_ctx, mock_stop, tmp_path):
        """A declared name is passed through to stop_env unchanged."""
        ctx = _mock_ctx_with_path(tmp_path)
        (ctx.worktree_path / ".maelstrom.yaml").write_text(
            "services:\n  ladle:\n    command: ladle serve\n    optional: true\n"
        )
        mock_ctx.return_value = ctx
        mock_stop.return_value = ["ladle (pid 1): stopped"]

        runner = CliRunner()
        result = runner.invoke(cli, ["env", "stop", "-s", "ladle"])
        assert result.exit_code == 0
        mock_stop.assert_called_once_with(ANY, "proj", "bravo", services=["ladle"])
