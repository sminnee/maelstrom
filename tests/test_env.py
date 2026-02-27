"""Tests for maelstrom.env module."""

import json
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from maelstrom.env import (
    EnvState,
    ProcfileEntry,
    ServiceState,
    ServiceStatus,
    build_service_env,
    cleanup_stale_env,
    format_uptime,
    get_env_status,
    get_services,
    is_service_alive,
    list_all_envs,
    list_project_envs,
    load_env_state,
    parse_procfile,
    remove_env_state,
    save_env_state,
    start_env,
    stop_env,
)


class TestParseProcfile:
    """Tests for parse_procfile function."""

    def test_standard_format(self, tmp_path):
        """Parse a basic Procfile with multiple services."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("web: python manage.py runserver\nworker: celery -A app worker\n")
        result = parse_procfile(procfile)
        assert result == [
            ProcfileEntry(name="web", command="python manage.py runserver"),
            ProcfileEntry(name="worker", command="celery -A app worker"),
        ]

    def test_comments_and_empty_lines(self, tmp_path):
        """Skip comments and blank lines."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("# This is a comment\n\nweb: python app.py\n\n# Another comment\n")
        result = parse_procfile(procfile)
        assert result == [ProcfileEntry(name="web", command="python app.py")]

    def test_colon_in_command(self, tmp_path):
        """Commands containing colons are handled (split on first colon only)."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("web: uvicorn app:main --host 0.0.0.0:8000\n")
        result = parse_procfile(procfile)
        assert result == [
            ProcfileEntry(
                name="web", command="uvicorn app:main --host 0.0.0.0:8000"
            )
        ]

    def test_missing_file(self, tmp_path):
        """FileNotFoundError for a missing Procfile."""
        with pytest.raises(FileNotFoundError):
            parse_procfile(tmp_path / "Procfile")

    def test_invalid_line_no_colon(self, tmp_path):
        """ValueError for a line without a colon."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("this is invalid\n")
        with pytest.raises(ValueError, match="no colon"):
            parse_procfile(procfile)

    def test_empty_name(self, tmp_path):
        """ValueError for a line with an empty name."""
        procfile = tmp_path / "Procfile"
        procfile.write_text(": some command\n")
        with pytest.raises(ValueError, match="empty name"):
            parse_procfile(procfile)

    def test_empty_procfile(self, tmp_path):
        """An empty Procfile returns no entries."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("")
        assert parse_procfile(procfile) == []

    def test_whitespace_trimming(self, tmp_path):
        """Names and commands are stripped of surrounding whitespace."""
        procfile = tmp_path / "Procfile"
        procfile.write_text("  web  :  python app.py  \n")
        result = parse_procfile(procfile)
        assert result == [ProcfileEntry(name="web", command="python app.py")]


class TestGetServices:
    """Tests for get_services function."""

    def test_procfile_present(self, tmp_path):
        """Use Procfile when it exists."""
        (tmp_path / "Procfile").write_text("web: python app.py\n")
        result = get_services(tmp_path)
        assert result == [ProcfileEntry(name="web", command="python app.py")]

    @patch("maelstrom.env.load_config_or_default")
    def test_fallback_to_start_cmd(self, mock_config, tmp_path):
        """Fall back to start_cmd as single 'app' service."""
        mock_config.return_value = MagicMock(start_cmd="npm start")
        result = get_services(tmp_path)
        assert result == [ProcfileEntry(name="app", command="npm start")]

    @patch("maelstrom.env.load_config_or_default")
    def test_neither_available(self, mock_config, tmp_path):
        """RuntimeError when no Procfile and no start_cmd."""
        mock_config.return_value = MagicMock(start_cmd="")
        with pytest.raises(RuntimeError, match="No Procfile"):
            get_services(tmp_path)

    @patch("maelstrom.env.load_config_or_default")
    def test_procfile_takes_precedence(self, mock_config, tmp_path):
        """Procfile is used even when start_cmd is configured."""
        (tmp_path / "Procfile").write_text("web: gunicorn app\n")
        mock_config.return_value = MagicMock(start_cmd="npm start")
        result = get_services(tmp_path)
        assert result == [ProcfileEntry(name="web", command="gunicorn app")]
        mock_config.assert_not_called()


class TestEnvStateRoundTrip:
    """Tests for save_env_state / load_env_state / remove_env_state."""

    def _make_state(self):
        return EnvState(
            project="myproject",
            worktree="bravo",
            worktree_path="/home/user/myproject/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web",
                    command="python app.py",
                    pid=12345,
                    log_file="/tmp/web.log",
                    started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_save_and_load(self, mock_dir, tmp_path):
        """State round-trips through save/load."""
        mock_dir.return_value = tmp_path
        state = self._make_state()
        save_env_state(state)
        loaded = load_env_state("myproject", "bravo")
        assert loaded is not None
        assert loaded.project == state.project
        assert loaded.worktree == state.worktree
        assert loaded.worktree_path == state.worktree_path
        assert loaded.started_at == state.started_at
        assert len(loaded.services) == 1
        assert loaded.services[0].name == "web"
        assert loaded.services[0].pid == 12345

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_load_missing_file(self, mock_dir, tmp_path):
        """Returns None for missing state file."""
        mock_dir.return_value = tmp_path
        assert load_env_state("noproject", "alpha") is None

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_load_corrupt_json(self, mock_dir, tmp_path):
        """Returns None for corrupt JSON."""
        mock_dir.return_value = tmp_path
        state_dir = tmp_path / "envs" / "myproject"
        state_dir.mkdir(parents=True)
        (state_dir / "bravo.json").write_text("not valid json{{{")
        assert load_env_state("myproject", "bravo") is None

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_remove(self, mock_dir, tmp_path):
        """State file is deleted by remove_env_state."""
        mock_dir.return_value = tmp_path
        state = self._make_state()
        save_env_state(state)
        path = tmp_path / "envs" / "myproject" / "bravo.json"
        assert path.exists()
        remove_env_state("myproject", "bravo")
        assert not path.exists()

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_remove_nonexistent(self, mock_dir, tmp_path):
        """remove_env_state is a no-op if file doesn't exist."""
        mock_dir.return_value = tmp_path
        remove_env_state("noproject", "alpha")  # should not raise

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_creates_parent_dirs(self, mock_dir, tmp_path):
        """save_env_state creates parent directories."""
        mock_dir.return_value = tmp_path
        state = self._make_state()
        save_env_state(state)
        assert (tmp_path / "envs" / "myproject" / "bravo.json").exists()


class TestBuildServiceEnv:
    """Tests for build_service_env function."""

    @patch("maelstrom.env.read_env_file")
    def test_merges_env_file(self, mock_read, monkeypatch):
        """os.environ is overlaid with .env vars."""
        monkeypatch.setenv("EXISTING", "original")
        mock_read.return_value = {"NEW_VAR": "from_env", "EXISTING": "overridden"}
        env = build_service_env(Path("/some/worktree"))
        assert env["NEW_VAR"] == "from_env"
        assert env["EXISTING"] == "overridden"

    @patch("maelstrom.env.read_env_file")
    def test_no_env_file(self, mock_read, monkeypatch):
        """Works when .env has no variables."""
        monkeypatch.setenv("PATH", "/usr/bin")
        mock_read.return_value = {}
        env = build_service_env(Path("/some/worktree"))
        assert env["PATH"] == "/usr/bin"


class TestIsServiceAlive:
    """Tests for is_service_alive function."""

    @patch("os.kill")
    def test_alive(self, mock_kill):
        """Returns True when os.kill succeeds."""
        mock_kill.return_value = None
        assert is_service_alive(12345) is True
        mock_kill.assert_called_once_with(12345, 0)

    @patch("os.kill")
    def test_dead(self, mock_kill):
        """Returns False on ProcessLookupError."""
        mock_kill.side_effect = ProcessLookupError
        assert is_service_alive(12345) is False

    @patch("os.kill")
    def test_permission_error(self, mock_kill):
        """Returns True on PermissionError (process exists, can't signal)."""
        mock_kill.side_effect = PermissionError
        assert is_service_alive(12345) is True


class TestStartEnv:
    """Tests for start_env function."""

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env")
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_spawns_services(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """Each service is spawned with correct args."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [
            ProcfileEntry(name="web", command="python app.py"),
            ProcfileEntry(name="worker", command="celery worker"),
        ]
        mock_env.return_value = {"PATH": "/usr/bin"}
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        wt_path = Path("/project/bravo")
        state = start_env("proj", "bravo", wt_path)

        assert len(state.services) == 2
        assert state.services[0].name == "web"
        assert state.services[1].name == "worker"
        assert mock_popen.call_count == 2

        # Verify Popen call args
        first_call = mock_popen.call_args_list[0]
        assert first_call[0][0] == ["sh", "-c", "python app.py"]
        assert first_call[1]["cwd"] == wt_path
        assert first_call[1]["start_new_session"] is True

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_runs_install_cmd(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """install_cmd is run before spawning services."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [ProcfileEntry(name="app", command="echo hi")]
        mock_popen.return_value = MagicMock(pid=1)
        wt_path = Path("/project/bravo")

        start_env("proj", "bravo", wt_path)
        mock_install.assert_called_once_with(wt_path)

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_skip_install(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """install_cmd is skipped when skip_install=True."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [ProcfileEntry(name="app", command="echo hi")]
        mock_popen.return_value = MagicMock(pid=1)

        start_env("proj", "bravo", Path("/project/bravo"), skip_install=True)
        mock_install.assert_not_called()

    @patch("maelstrom.env.get_env_status")
    @patch("maelstrom.env.cleanup_stale_env")
    def test_refuses_if_running(self, mock_cleanup, mock_status):
        """RuntimeError if services are already alive."""
        mock_status.return_value = [
            ServiceStatus(
                name="web", pid=123, alive=True, command="x",
                log_file="/tmp/x.log", started_at="2025-01-01T00:00:00+00:00",
            )
        ]
        with pytest.raises(RuntimeError, match="already running"):
            start_env("proj", "bravo", Path("/project/bravo"))

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_saves_state(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """State is saved after spawning."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [ProcfileEntry(name="app", command="echo hi")]
        mock_popen.return_value = MagicMock(pid=99)

        state = start_env("proj", "bravo", Path("/project/bravo"))
        mock_save.assert_called_once_with(state)
        assert state.project == "proj"
        assert state.worktree == "bravo"

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_log_dir_created(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """Log directory is created before spawning."""
        log_dir = tmp_path / "logs" / "proj" / "bravo"
        mock_log_dir.return_value = log_dir
        mock_services.return_value = [ProcfileEntry(name="app", command="echo hi")]
        mock_popen.return_value = MagicMock(pid=1)

        start_env("proj", "bravo", Path("/project/bravo"))
        assert log_dir.exists()

    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env._get_log_dir")
    def test_cleans_stale_before_start(
        self,
        mock_log_dir,
        mock_cleanup,
        mock_services,
        mock_install,
        mock_env,
        mock_popen,
        mock_save,
        tmp_path,
    ):
        """Stale env is cleaned up before checking for running services."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [ProcfileEntry(name="app", command="echo hi")]
        mock_popen.return_value = MagicMock(pid=1)

        start_env("proj", "bravo", Path("/project/bravo"))
        mock_cleanup.assert_called_once_with("proj", "bravo")


class TestStopEnv:
    """Tests for stop_env function."""

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_env_state")
    def test_sigterm_sent(self, mock_load, mock_killpg, mock_alive, mock_remove):
        """SIGTERM is sent to each process group."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )

        messages = stop_env("proj", "bravo")
        mock_killpg.assert_called_with(100, signal.SIGTERM)
        assert any("stopped" in m for m in messages)

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive")
    @patch("os.killpg")
    @patch("maelstrom.env.load_env_state")
    @patch("time.monotonic")
    @patch("time.sleep")
    def test_sigkill_after_timeout(
        self, mock_sleep, mock_monotonic, mock_load, mock_killpg, mock_alive, mock_remove
    ):
        """SIGKILL is sent after timeout when services don't die."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        # Service stays alive throughout the timeout
        mock_alive.return_value = True
        # Simulate time passing: first call sets deadline, then exceed it
        mock_monotonic.side_effect = [0.0, 11.0]

        messages = stop_env("proj", "bravo", timeout=10.0)
        # Should have called killpg with both SIGTERM and SIGKILL
        assert call(100, signal.SIGTERM) in mock_killpg.call_args_list
        assert call(100, signal.SIGKILL) in mock_killpg.call_args_list
        assert any("SIGKILL" in m for m in messages)

    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_no_state(self, mock_load):
        """Returns message when no state file exists."""
        messages = stop_env("proj", "bravo")
        assert len(messages) == 1
        assert "No running environment" in messages[0]

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_env_state")
    def test_removes_state(self, mock_load, mock_killpg, mock_alive, mock_remove):
        """State file is removed after stopping."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="x", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        stop_env("proj", "bravo")
        mock_remove.assert_called_once_with("proj", "bravo")

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_env_state")
    def test_handles_dead_processes(self, mock_load, mock_killpg, mock_alive, mock_remove):
        """Dead processes are handled gracefully (ProcessLookupError on SIGTERM)."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="x", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        mock_killpg.side_effect = ProcessLookupError
        messages = stop_env("proj", "bravo")
        assert any("stopped" in m for m in messages)


class TestGetEnvStatus:
    """Tests for get_env_status function."""

    @patch("maelstrom.env.is_service_alive")
    @patch("maelstrom.env.load_env_state")
    def test_returns_status_per_service(self, mock_load, mock_alive):
        """Returns a ServiceStatus for each tracked service."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                ),
                ServiceState(
                    name="worker", command="celery worker", pid=101,
                    log_file="/tmp/worker.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        mock_alive.side_effect = [True, False]

        result = get_env_status("proj", "bravo")
        assert result is not None
        assert len(result) == 2
        assert result[0].name == "web"
        assert result[0].alive is True
        assert result[1].name == "worker"
        assert result[1].alive is False

    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_none_when_no_state(self, mock_load):
        """Returns None when no state file exists."""
        assert get_env_status("proj", "bravo") is None


class TestCleanupStaleEnv:
    """Tests for cleanup_stale_env function."""

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.get_env_status")
    def test_cleans_dead(self, mock_status, mock_remove):
        """Removes state when all services are dead."""
        mock_status.return_value = [
            ServiceStatus(
                name="web", pid=100, alive=False, command="x",
                log_file="/tmp/x.log", started_at="2025-01-01T00:00:00+00:00",
            )
        ]
        assert cleanup_stale_env("proj", "bravo") is True
        mock_remove.assert_called_once_with("proj", "bravo")

    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.get_env_status")
    def test_preserves_alive(self, mock_status, mock_remove):
        """Does not remove state when services are alive."""
        mock_status.return_value = [
            ServiceStatus(
                name="web", pid=100, alive=True, command="x",
                log_file="/tmp/x.log", started_at="2025-01-01T00:00:00+00:00",
            )
        ]
        assert cleanup_stale_env("proj", "bravo") is False
        mock_remove.assert_not_called()

    @patch("maelstrom.env.get_env_status", return_value=None)
    def test_no_state(self, mock_status):
        """Returns False when no state file exists."""
        assert cleanup_stale_env("proj", "bravo") is False


class TestListProjectEnvs:
    """Tests for list_project_envs function."""

    def _make_state(self, project, worktree, pid=100):
        return EnvState(
            project=project,
            worktree=worktree,
            worktree_path=f"/project/{worktree}",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=pid,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )

    @patch("maelstrom.env.get_maelstrom_dir")
    @patch("maelstrom.env.is_service_alive", return_value=True)
    def test_lists_running_envs(self, mock_alive, mock_dir, tmp_path):
        """Returns states for running environments."""
        mock_dir.return_value = tmp_path
        state = self._make_state("proj", "alpha")
        save_env_state(state)
        state2 = self._make_state("proj", "bravo", pid=200)
        save_env_state(state2)

        result = list_project_envs("proj")
        assert len(result) == 2
        worktrees = [s.worktree for s in result]
        assert "alpha" in worktrees
        assert "bravo" in worktrees

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_empty_project_dir(self, mock_dir, tmp_path):
        """Returns empty list for project with no env files."""
        mock_dir.return_value = tmp_path
        (tmp_path / "envs" / "proj").mkdir(parents=True)
        assert list_project_envs("proj") == []

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_nonexistent_project(self, mock_dir, tmp_path):
        """Returns empty list for nonexistent project dir."""
        mock_dir.return_value = tmp_path
        assert list_project_envs("noproject") == []

    @patch("maelstrom.env.get_maelstrom_dir")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    def test_stale_cleanup_during_listing(self, mock_alive, mock_dir, tmp_path):
        """Stale envs are cleaned up and excluded from results."""
        mock_dir.return_value = tmp_path
        state = self._make_state("proj", "alpha")
        save_env_state(state)

        result = list_project_envs("proj")
        assert result == []
        # State file should have been cleaned up
        assert not (tmp_path / "envs" / "proj" / "alpha.json").exists()


class TestListAllEnvs:
    """Tests for list_all_envs function."""

    def _make_state(self, project, worktree, pid=100):
        return EnvState(
            project=project,
            worktree=worktree,
            worktree_path=f"/project/{worktree}",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=pid,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )

    @patch("maelstrom.env.get_maelstrom_dir")
    @patch("maelstrom.env.is_service_alive", return_value=True)
    def test_lists_across_projects(self, mock_alive, mock_dir, tmp_path):
        """Returns states from multiple projects."""
        mock_dir.return_value = tmp_path
        save_env_state(self._make_state("projA", "alpha", pid=100))
        save_env_state(self._make_state("projB", "bravo", pid=200))

        result = list_all_envs()
        assert len(result) == 2
        projects = {s.project for s in result}
        assert projects == {"projA", "projB"}

    @patch("maelstrom.env.get_maelstrom_dir")
    def test_empty_state_dir(self, mock_dir, tmp_path):
        """Returns empty list when no envs dir exists."""
        mock_dir.return_value = tmp_path
        assert list_all_envs() == []


class TestFormatUptime:
    """Tests for format_uptime function."""

    @patch("maelstrom.env.datetime")
    def test_seconds(self, mock_dt):
        """Shows seconds for very short uptime."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 1, 0, 0, 45, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "45s"

    @patch("maelstrom.env.datetime")
    def test_minutes(self, mock_dt):
        """Shows minutes for short uptime."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 1, 0, 5, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "5m"

    @patch("maelstrom.env.datetime")
    def test_hours_and_minutes(self, mock_dt):
        """Shows hours and minutes."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 1, 2, 30, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "2h 30m"

    @patch("maelstrom.env.datetime")
    def test_days_and_hours(self, mock_dt):
        """Shows days and hours."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 4, 5, 0, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "3d 5h"

    @patch("maelstrom.env.datetime")
    def test_days_only(self, mock_dt):
        """Shows just days when hours are zero."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 4, 0, 0, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "3d"

    @patch("maelstrom.env.datetime")
    def test_hours_only(self, mock_dt):
        """Shows just hours when minutes are zero."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "2h"

    @patch("maelstrom.env.datetime")
    def test_zero_seconds(self, mock_dt):
        """Shows 0s for no elapsed time."""
        from datetime import datetime, timezone
        mock_dt.fromisoformat = datetime.fromisoformat
        mock_dt.now.return_value = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert format_uptime("2025-01-01T00:00:00+00:00") == "0s"
