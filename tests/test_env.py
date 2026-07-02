"""Tests for maelstrom.env module."""

import itertools
import signal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from maelstrom.env import (
    EnvState,
    ProcfileEntry,
    ServiceState,
    ServiceStatus,
    SharedEnvState,
    build_service_env,
    cleanup_stale_env,
    cleanup_stale_shared,
    format_uptime,
    get_env_status,
    get_log_files,
    get_services,
    get_shared_status,
    is_service_alive,
    is_shared_service,
    list_all_envs,
    list_project_envs,
    load_env_state,
    load_shared_state,
    parse_procfile,
    read_service_logs,
    remove_env_state,
    remove_shared_state,
    save_env_state,
    save_shared_state,
    start_env,
    stop_all_envs,
    stop_env,
    stop_sessions,
    tail_log_file,
)
from maelstrom.env import regenerate_and_restart_if_running
from maelstrom.env_store import InMemoryEnvStore, JsonEnvStore
from maelstrom.session_discovery import LiveSession


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

    def test_save_and_load(self):
        """State round-trips through save/load."""
        store = InMemoryEnvStore()
        state = self._make_state()
        save_env_state(store, state)
        loaded = load_env_state(store, "myproject", "bravo")
        assert loaded is not None
        assert loaded.project == state.project
        assert loaded.worktree == state.worktree
        assert loaded.worktree_path == state.worktree_path
        assert loaded.started_at == state.started_at
        assert len(loaded.services) == 1
        assert loaded.services[0].name == "web"
        assert loaded.services[0].pid == 12345

    def test_load_missing_file(self):
        """Returns None for missing state file."""
        store = InMemoryEnvStore()
        assert load_env_state(store, "noproject", "alpha") is None

    def test_load_corrupt_json(self, tmp_path):
        """Returns None for corrupt JSON."""
        store = JsonEnvStore(root=tmp_path)
        state_dir = tmp_path / "myproject"
        state_dir.mkdir(parents=True)
        (state_dir / "bravo.json").write_text("not valid json{{{")
        assert load_env_state(store, "myproject", "bravo") is None

    def test_remove(self):
        """State entry is deleted by remove_env_state."""
        store = InMemoryEnvStore()
        state = self._make_state()
        save_env_state(store, state)
        assert store.exists("myproject/bravo.json")
        remove_env_state(store, "myproject", "bravo")
        assert not store.exists("myproject/bravo.json")

    def test_remove_nonexistent(self):
        """remove_env_state is a no-op if entry doesn't exist."""
        store = InMemoryEnvStore()
        remove_env_state(store, "noproject", "alpha")  # should not raise

    def test_creates_parent_dirs(self):
        """save_env_state persists the state entry."""
        store = InMemoryEnvStore()
        state = self._make_state()
        save_env_state(store, state)
        assert store.exists("myproject/bravo.json")


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
        store = InMemoryEnvStore()
        state = start_env(store, "proj", "bravo", wt_path)

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

        store = InMemoryEnvStore()
        start_env(store, "proj", "bravo", wt_path)
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

        store = InMemoryEnvStore()
        start_env(store, "proj", "bravo", Path("/project/bravo"), skip_install=True)
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
        store = InMemoryEnvStore()
        with pytest.raises(RuntimeError, match="already running"):
            start_env(store, "proj", "bravo", Path("/project/bravo"))

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

        store = InMemoryEnvStore()
        state = start_env(store, "proj", "bravo", Path("/project/bravo"))
        mock_save.assert_called_once_with(store, state)
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

        store = InMemoryEnvStore()
        start_env(store, "proj", "bravo", Path("/project/bravo"))
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

        store = InMemoryEnvStore()
        start_env(store, "proj", "bravo", Path("/project/bravo"))
        mock_cleanup.assert_called_once_with(store, "proj", "bravo")


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

        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
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

        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo", timeout=10.0)
        # Should have called killpg with both SIGTERM and SIGKILL
        assert call(100, signal.SIGTERM) in mock_killpg.call_args_list
        assert call(100, signal.SIGKILL) in mock_killpg.call_args_list
        assert any("SIGKILL" in m for m in messages)

    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_no_state(self, mock_load):
        """Returns message when no state file exists."""
        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
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
        store = InMemoryEnvStore()
        stop_env(store, "proj", "bravo")
        mock_remove.assert_called_once_with(store, "proj", "bravo")

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
        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
        assert any("stopped" in m for m in messages)


class TestStopSessions:
    """Tests for stop_sessions — SIGINT -> SIGTERM, never SIGKILL."""

    @staticmethod
    def _alive_tracker(alive_pids, die_on):
        """Fakes for (os.kill, is_service_alive) backed by a live-pid set.

        ``os.kill`` removes a pid from ``alive_pids`` when the signal is in
        ``die_on`` (e.g. ``{SIGINT}`` for a session that exits on interrupt,
        ``{SIGTERM}`` for one that only dies on terminate). ``is_service_alive``
        reads the set. Together they let a single test express "signal X kills
        this pid" without hand-scripting per-call return values.
        """
        def fake_kill(pid, sig):
            if sig in die_on:
                alive_pids.discard(pid)

        def fake_alive(pid):
            return pid in alive_pids

        return fake_kill, fake_alive

    def test_empty_input_is_noop(self):
        with patch("os.kill") as mock_kill:
            assert stop_sessions([]) == []
        mock_kill.assert_not_called()

    def test_only_self_pid_is_noop(self):
        with patch("os.getpid", return_value=999), patch("os.kill") as mock_kill:
            assert stop_sessions([LiveSession(pid=999, cwd=Path("/w/a"))]) == []
        mock_kill.assert_not_called()

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_sigint_alone_exits_no_sigterm(self, mock_monotonic, _sleep):
        mock_monotonic.side_effect = itertools.count(0.0, 100.0)  # deadline passes fast
        alive = {100}
        fake_kill, fake_alive = self._alive_tracker(alive, die_on={signal.SIGINT})
        with patch("os.getpid", return_value=1), \
                patch("os.kill", side_effect=fake_kill) as mock_kill, \
                patch("maelstrom.env.is_service_alive", side_effect=fake_alive):
            messages = stop_sessions([LiveSession(pid=100, cwd=Path("/w/a"))])
        assert call(100, signal.SIGINT) in mock_kill.call_args_list
        assert call(100, signal.SIGTERM) not in mock_kill.call_args_list
        assert call(100, signal.SIGKILL) not in mock_kill.call_args_list
        assert messages == ["claude session (pid 100): stopped"]

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_sigterm_after_surviving_sigint(self, mock_monotonic, _sleep):
        # Never dies from SIGINT; dies on SIGTERM. Both deadlines expire so both
        # stages fully poll.
        mock_monotonic.side_effect = itertools.count(0.0, 100.0)
        alive = {100}
        fake_kill, fake_alive = self._alive_tracker(alive, die_on={signal.SIGTERM})
        with patch("os.getpid", return_value=1), \
                patch("os.kill", side_effect=fake_kill) as mock_kill, \
                patch("maelstrom.env.is_service_alive", side_effect=fake_alive):
            messages = stop_sessions([LiveSession(pid=100, cwd=Path("/w/a"))])
        kills = mock_kill.call_args_list
        assert call(100, signal.SIGINT) in kills
        assert call(100, signal.SIGTERM) in kills
        assert call(100, signal.SIGKILL) not in kills
        assert messages == ["claude session (pid 100): stopped"]

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_survivor_after_sigterm_reported_never_sigkill(self, mock_monotonic, _sleep):
        mock_monotonic.side_effect = itertools.count(0.0, 100.0)
        alive = {100}  # never dies
        fake_kill, fake_alive = self._alive_tracker(alive, die_on=set())
        with patch("os.getpid", return_value=1), \
                patch("os.kill", side_effect=fake_kill) as mock_kill, \
                patch("maelstrom.env.is_service_alive", side_effect=fake_alive):
            messages = stop_sessions([LiveSession(pid=100, cwd=Path("/w/a"))])
        assert call(100, signal.SIGKILL) not in mock_kill.call_args_list
        assert messages == ["claude session (pid 100): still running after SIGTERM"]

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_self_pid_excluded_from_signalling(self, mock_monotonic, _sleep):
        mock_monotonic.side_effect = itertools.count(0.0, 100.0)
        alive = {100, 999}
        fake_kill, fake_alive = self._alive_tracker(alive, die_on={signal.SIGINT})
        with patch("os.getpid", return_value=999), \
                patch("os.kill", side_effect=fake_kill) as mock_kill, \
                patch("maelstrom.env.is_service_alive", side_effect=fake_alive):
            messages = stop_sessions([
                LiveSession(pid=100, cwd=Path("/w/a")),
                LiveSession(pid=999, cwd=Path("/w/a")),
            ])
        signalled = {c.args[0] for c in mock_kill.call_args_list}
        assert 999 not in signalled
        assert 100 in signalled
        assert messages == ["claude session (pid 100): stopped"]

    @patch("time.sleep")
    @patch("time.monotonic")
    def test_kill_errors_swallowed(self, mock_monotonic, _sleep):
        mock_monotonic.side_effect = itertools.count(0.0, 100.0)
        with patch("os.getpid", return_value=1), \
                patch("os.kill", side_effect=ProcessLookupError), \
                patch("maelstrom.env.is_service_alive", return_value=False):
            # is_service_alive False => nothing signalled, all reported stopped;
            # PermissionError/ProcessLookupError from os.kill must not propagate.
            messages = stop_sessions([LiveSession(pid=100, cwd=Path("/w/a"))])
        assert messages == ["claude session (pid 100): stopped"]


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

        store = InMemoryEnvStore()
        result = get_env_status(store, "proj", "bravo")
        assert result is not None
        assert len(result) == 2
        assert result[0].name == "web"
        assert result[0].alive is True
        assert result[1].name == "worker"
        assert result[1].alive is False

    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_none_when_no_state(self, mock_load):
        """Returns None when no state file exists."""
        store = InMemoryEnvStore()
        assert get_env_status(store, "proj", "bravo") is None


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
        store = InMemoryEnvStore()
        assert cleanup_stale_env(store, "proj", "bravo") is True
        mock_remove.assert_called_once_with(store, "proj", "bravo")

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
        store = InMemoryEnvStore()
        assert cleanup_stale_env(store, "proj", "bravo") is False
        mock_remove.assert_not_called()

    @patch("maelstrom.env.get_env_status", return_value=None)
    def test_no_state(self, mock_status):
        """Returns False when no state file exists."""
        store = InMemoryEnvStore()
        assert cleanup_stale_env(store, "proj", "bravo") is False


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

    @patch("maelstrom.env.is_service_alive", return_value=True)
    def test_lists_running_envs(self, mock_alive):
        """Returns states for running environments."""
        store = InMemoryEnvStore()
        state = self._make_state("proj", "alpha")
        save_env_state(store, state)
        state2 = self._make_state("proj", "bravo", pid=200)
        save_env_state(store, state2)

        result = list_project_envs(store, "proj")
        assert len(result) == 2
        worktrees = [s.worktree for s in result]
        assert "alpha" in worktrees
        assert "bravo" in worktrees

    def test_empty_project_dir(self):
        """Returns empty list for project with no env files."""
        store = InMemoryEnvStore()
        assert list_project_envs(store, "proj") == []

    def test_nonexistent_project(self):
        """Returns empty list for nonexistent project dir."""
        store = InMemoryEnvStore()
        assert list_project_envs(store, "noproject") == []

    @patch("maelstrom.env.is_service_alive", return_value=False)
    def test_stale_cleanup_during_listing(self, mock_alive):
        """Stale envs are cleaned up and excluded from results."""
        store = InMemoryEnvStore()
        state = self._make_state("proj", "alpha")
        save_env_state(store, state)

        result = list_project_envs(store, "proj")
        assert result == []
        # State entry should have been cleaned up
        assert not store.exists("proj/alpha.json")


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

    @patch("maelstrom.env.is_service_alive", return_value=True)
    def test_lists_across_projects(self, mock_alive):
        """Returns states from multiple projects."""
        store = InMemoryEnvStore()
        save_env_state(store, self._make_state("projA", "alpha", pid=100))
        save_env_state(store, self._make_state("projB", "bravo", pid=200))

        result = list_all_envs(store)
        assert len(result) == 2
        projects = {s.project for s in result}
        assert projects == {"projA", "projB"}

    def test_empty_state_dir(self):
        """Returns empty list when no envs dir exists."""
        store = InMemoryEnvStore()
        assert list_all_envs(store) == []


class TestStopAllEnvs:
    """Tests for stop_all_envs function."""

    @patch("maelstrom.env.stop_env")
    @patch("maelstrom.env.list_all_envs")
    def test_stops_all_envs(self, mock_list, mock_stop):
        """Calls stop_env for each running environment."""
        mock_list.return_value = [
            EnvState(
                project="projA", worktree="alpha",
                worktree_path="/project/alpha",
                started_at="2025-01-01T00:00:00+00:00",
                services=[],
            ),
            EnvState(
                project="projB", worktree="bravo",
                worktree_path="/project/bravo",
                started_at="2025-01-01T00:00:00+00:00",
                services=[],
            ),
        ]
        mock_stop.side_effect = [
            ["web (pid 100): stopped"],
            ["app (pid 200): stopped"],
        ]

        store = InMemoryEnvStore()
        results = stop_all_envs(store)
        assert len(results) == 2
        assert results[0] == ("projA", "alpha", ["web (pid 100): stopped"])
        assert results[1] == ("projB", "bravo", ["app (pid 200): stopped"])
        assert mock_stop.call_args_list == [
            call(store, "projA", "alpha", timeout=10.0),
            call(store, "projB", "bravo", timeout=10.0),
        ]

    @patch("maelstrom.env.stop_env")
    @patch("maelstrom.env.list_all_envs")
    def test_no_envs(self, mock_list, mock_stop):
        """Returns empty list when no environments running."""
        mock_list.return_value = []
        store = InMemoryEnvStore()
        results = stop_all_envs(store)
        assert results == []
        mock_stop.assert_not_called()


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


class TestGetLogFiles:
    """Tests for get_log_files function."""

    @patch("maelstrom.env.load_env_state")
    def test_from_state(self, mock_load, tmp_path):
        """Returns log paths from running env state."""
        log_file = tmp_path / "web.log"
        log_file.write_text("some logs")
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file=str(log_file), started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        store = InMemoryEnvStore()
        result = get_log_files(store, "proj", "bravo")
        assert result == {"web": log_file}

    @patch("maelstrom.env._get_log_dir")
    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_fallback_to_dir_scan(self, mock_load, mock_log_dir, tmp_path):
        """Falls back to scanning log directory when no state."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "web.log").write_text("web logs")
        (log_dir / "worker.log").write_text("worker logs")
        mock_log_dir.return_value = log_dir

        store = InMemoryEnvStore()
        result = get_log_files(store, "proj", "bravo")
        assert set(result.keys()) == {"web", "worker"}

    @patch("maelstrom.env._get_log_dir")
    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_no_state_no_dir(self, mock_load, mock_log_dir, tmp_path):
        """Returns empty dict when no state and no log dir."""
        mock_log_dir.return_value = tmp_path / "nonexistent"
        store = InMemoryEnvStore()
        result = get_log_files(store, "proj", "bravo")
        assert result == {}

    @patch("maelstrom.env._get_log_dir")
    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_empty_dir(self, mock_load, mock_log_dir, tmp_path):
        """Returns empty dict when log dir exists but has no .log files."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        mock_log_dir.return_value = log_dir
        store = InMemoryEnvStore()
        result = get_log_files(store, "proj", "bravo")
        assert result == {}

    @patch("maelstrom.env._get_log_dir")
    @patch("maelstrom.env.load_env_state")
    def test_state_with_missing_files_falls_back(self, mock_load, mock_log_dir, tmp_path):
        """Falls back to dir scan when state log files don't exist on disk."""
        mock_load.return_value = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="x", pid=100,
                    log_file="/nonexistent/web.log",
                    started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "web.log").write_text("fallback logs")
        mock_log_dir.return_value = log_dir

        store = InMemoryEnvStore()
        result = get_log_files(store, "proj", "bravo")
        assert "web" in result


class TestTailLogFile:
    """Tests for tail_log_file function."""

    def test_last_n_lines(self, tmp_path):
        """Returns last N lines."""
        log = tmp_path / "test.log"
        log.write_text("\n".join(f"line {i}" for i in range(200)))
        result = tail_log_file(log, n=5)
        assert len(result) == 5
        assert result[-1] == "line 199"

    def test_fewer_than_n(self, tmp_path):
        """Returns all lines when fewer than N exist."""
        log = tmp_path / "test.log"
        log.write_text("line 1\nline 2\n")
        result = tail_log_file(log, n=100)
        assert len(result) == 2

    def test_missing_file(self, tmp_path):
        """Returns empty list for missing file."""
        result = tail_log_file(tmp_path / "missing.log")
        assert result == []

    def test_empty_file(self, tmp_path):
        """Returns empty list for empty file."""
        log = tmp_path / "empty.log"
        log.write_text("")
        result = tail_log_file(log)
        assert result == []


class TestReadServiceLogs:
    """Tests for read_service_logs function."""

    @patch("maelstrom.env.get_log_files")
    def test_single_service(self, mock_files, tmp_path):
        """Reads logs for a specific service."""
        log = tmp_path / "web.log"
        log.write_text("request 1\nrequest 2\n")
        mock_files.return_value = {"web": log, "worker": tmp_path / "worker.log"}

        store = InMemoryEnvStore()
        result = read_service_logs(store, "proj", "bravo", service="web")
        assert all(name == "web" for name, _ in result)
        assert len(result) == 2

    @patch("maelstrom.env.get_log_files")
    def test_all_services(self, mock_files, tmp_path):
        """Reads logs for all services when no service specified."""
        web_log = tmp_path / "web.log"
        web_log.write_text("web line\n")
        worker_log = tmp_path / "worker.log"
        worker_log.write_text("worker line\n")
        mock_files.return_value = {"web": web_log, "worker": worker_log}

        store = InMemoryEnvStore()
        result = read_service_logs(store, "proj", "bravo")
        names = [name for name, _ in result]
        assert "web" in names
        assert "worker" in names

    @patch("maelstrom.env.get_log_files")
    def test_service_not_found(self, mock_files, tmp_path):
        """Raises ValueError for unknown service."""
        mock_files.return_value = {"web": tmp_path / "web.log"}
        store = InMemoryEnvStore()
        with pytest.raises(ValueError, match="Service 'db' not found"):
            read_service_logs(store, "proj", "bravo", service="db")

    @patch("maelstrom.env.get_log_files")
    def test_no_logs(self, mock_files):
        """Raises ValueError when no logs exist."""
        mock_files.return_value = {}
        store = InMemoryEnvStore()
        with pytest.raises(ValueError, match="No logs found"):
            read_service_logs(store, "proj", "bravo")


# --- Shared Services Tests ---


class TestIsSharedService:
    """Tests for is_shared_service function."""

    def test_shared_suffix(self):
        """Returns True for names ending in -shared."""
        assert is_shared_service("db-shared") is True
        assert is_shared_service("redis-shared") is True

    def test_non_shared(self):
        """Returns False for regular service names."""
        assert is_shared_service("web") is False
        assert is_shared_service("worker") is False
        assert is_shared_service("shared") is False

    def test_edge_cases(self):
        """Edge cases for shared service detection."""
        assert is_shared_service("-shared") is True
        assert is_shared_service("shared-web") is False


class TestSharedEnvStateRoundTrip:
    """Tests for shared state save/load/remove."""

    def _make_shared_state(self):
        return SharedEnvState(
            project="myproject",
            worktree_path="/home/user/myproject/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared",
                    command="postgres -p 5432",
                    pid=12345,
                    log_file="/tmp/db-shared.log",
                    started_at="2025-01-01T00:00:00+00:00",
                )
            ],
            subscribers=["alpha", "bravo"],
        )

    def test_save_and_load(self):
        """Shared state round-trips through save/load."""
        store = InMemoryEnvStore()
        state = self._make_shared_state()
        save_shared_state(store, state)
        loaded = load_shared_state(store, "myproject")
        assert loaded is not None
        assert loaded.project == state.project
        assert loaded.worktree_path == state.worktree_path
        assert loaded.started_at == state.started_at
        assert len(loaded.services) == 1
        assert loaded.services[0].name == "db-shared"
        assert loaded.subscribers == ["alpha", "bravo"]

    def test_load_missing(self):
        """Returns None for missing shared state."""
        store = InMemoryEnvStore()
        assert load_shared_state(store, "noproject") is None

    def test_load_corrupt(self, tmp_path):
        """Returns None for corrupt JSON."""
        store = JsonEnvStore(root=tmp_path)
        state_dir = tmp_path / "myproject"
        state_dir.mkdir(parents=True)
        (state_dir / "_shared.json").write_text("not valid json{{{")
        assert load_shared_state(store, "myproject") is None

    def test_remove(self):
        """Shared state entry is deleted by remove_shared_state."""
        store = InMemoryEnvStore()
        state = self._make_shared_state()
        save_shared_state(store, state)
        assert store.exists("myproject/_shared.json")
        remove_shared_state(store, "myproject")
        assert not store.exists("myproject/_shared.json")

    def test_remove_nonexistent(self):
        """remove_shared_state is a no-op if entry doesn't exist."""
        store = InMemoryEnvStore()
        remove_shared_state(store, "noproject")  # should not raise


class TestGetSharedStatus:
    """Tests for get_shared_status function."""

    @patch("maelstrom.env.is_service_alive")
    @patch("maelstrom.env.load_shared_state")
    def test_returns_status(self, mock_load, mock_alive):
        """Returns ServiceStatus for each shared service."""
        mock_load.return_value = SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=100,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha"],
        )
        mock_alive.return_value = True

        store = InMemoryEnvStore()
        result = get_shared_status(store, "proj")
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "db-shared"
        assert result[0].alive is True

    @patch("maelstrom.env.load_shared_state", return_value=None)
    def test_none_when_no_state(self, mock_load):
        """Returns None when no shared state exists."""
        store = InMemoryEnvStore()
        assert get_shared_status(store, "proj") is None


class TestCleanupStaleShared:
    """Tests for cleanup_stale_shared function."""

    @patch("maelstrom.env.remove_shared_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("maelstrom.env.load_shared_state")
    def test_cleans_dead(self, mock_load, mock_alive, mock_remove):
        """Removes shared state when all services are dead."""
        mock_load.return_value = SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=100,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha"],
        )
        store = InMemoryEnvStore()
        assert cleanup_stale_shared(store, "proj") is True
        mock_remove.assert_called_once_with(store, "proj")

    @patch("maelstrom.env.remove_shared_state")
    @patch("maelstrom.env.is_service_alive", return_value=True)
    @patch("maelstrom.env.load_shared_state")
    def test_preserves_alive(self, mock_load, mock_alive, mock_remove):
        """Does not remove shared state when services are alive."""
        mock_load.return_value = SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=100,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha"],
        )
        store = InMemoryEnvStore()
        assert cleanup_stale_shared(store, "proj") is False
        mock_remove.assert_not_called()

    @patch("maelstrom.env.load_shared_state", return_value=None)
    def test_no_state(self, mock_load):
        """Returns False when no shared state exists."""
        store = InMemoryEnvStore()
        assert cleanup_stale_shared(store, "proj") is False


class TestStartEnvShared:
    """Tests for shared service handling in start_env."""

    @patch("maelstrom.env.save_shared_state")
    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env.cleanup_stale_shared")
    @patch("maelstrom.env.load_shared_state", return_value=None)
    @patch("maelstrom.env._get_log_dir")
    @patch("maelstrom.env._get_shared_log_dir")
    def test_splits_shared_and_local(
        self,
        mock_shared_log_dir,
        mock_log_dir,
        mock_shared_load,
        mock_shared_cleanup,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        mock_shared_save,
        tmp_path,
    ):
        """Shared services are separated from local and started independently."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_shared_log_dir.return_value = tmp_path / "shared_logs"
        mock_services.return_value = [
            ProcfileEntry(name="web", command="python app.py"),
            ProcfileEntry(name="db-shared", command="postgres"),
        ]
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        store = InMemoryEnvStore()
        state = start_env(store, "proj", "bravo", Path("/project/bravo"))

        # Local state should only contain non-shared services
        assert len(state.services) == 1
        assert state.services[0].name == "web"

        # Shared state should have been saved with the shared service
        mock_shared_save.assert_called_once()
        shared_state = mock_shared_save.call_args[0][1]
        assert len(shared_state.services) == 1
        assert shared_state.services[0].name == "db-shared"
        assert shared_state.subscribers == ["bravo"]

    @patch("maelstrom.env.save_shared_state")
    @patch("maelstrom.env.save_env_state")
    @patch("maelstrom.env.Popen")
    @patch("maelstrom.env.build_service_env", return_value={})
    @patch("maelstrom.env.get_services")
    @patch("maelstrom.env.run_install_cmd")
    @patch("maelstrom.env.get_env_status", return_value=None)
    @patch("maelstrom.env.cleanup_stale_env")
    @patch("maelstrom.env.cleanup_stale_shared")
    @patch("maelstrom.env.load_shared_state")
    @patch("maelstrom.env._get_log_dir")
    def test_subscribes_to_existing_shared(
        self,
        mock_log_dir,
        mock_shared_load,
        mock_shared_cleanup,
        mock_cleanup,
        mock_status,
        mock_install,
        mock_services,
        mock_env,
        mock_popen,
        mock_save,
        mock_shared_save,
        tmp_path,
    ):
        """Second worktree subscribes to existing shared services."""
        mock_log_dir.return_value = tmp_path / "logs"
        mock_services.return_value = [
            ProcfileEntry(name="web", command="python app.py"),
            ProcfileEntry(name="db-shared", command="postgres"),
        ]
        # Shared services already running with alpha as subscriber
        existing_shared = SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=999,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha"],
        )
        mock_shared_load.return_value = existing_shared
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_popen.return_value = mock_proc

        store = InMemoryEnvStore()
        state = start_env(store, "proj", "bravo", Path("/project/bravo"))

        # Only local service should be spawned (1 Popen call for "web")
        assert mock_popen.call_count == 1
        assert state.services[0].name == "web"

        # Shared state should be updated with bravo as subscriber
        mock_shared_save.assert_called_once()
        saved = mock_shared_save.call_args[0][1]
        assert saved.subscribers == ["alpha", "bravo"]


class TestStopEnvShared:
    """Tests for shared service handling in stop_env."""

    @patch("maelstrom.env.remove_shared_state")
    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_shared_state")
    @patch("maelstrom.env.load_env_state")
    def test_unsubscribes_keeps_shared(
        self, mock_load, mock_shared_load, mock_killpg, mock_alive,
        mock_remove, mock_shared_remove,
    ):
        """Shared services stay running when other subscribers remain."""
        mock_load.return_value = EnvState(
            project="proj", worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        mock_shared_load.return_value = SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=200,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha", "bravo"],
        )

        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
        assert any("stopped" in m for m in messages)
        assert any("still used by 1" in m for m in messages)
        # Shared services should NOT be killed
        mock_shared_remove.assert_not_called()

    @patch("maelstrom.env.remove_shared_state")
    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_shared_state")
    @patch("maelstrom.env.load_env_state")
    def test_last_subscriber_stops_shared(
        self, mock_load, mock_shared_load, mock_killpg, mock_alive,
        mock_remove, mock_shared_remove,
    ):
        """Shared services are stopped when last subscriber disconnects."""
        mock_load.return_value = EnvState(
            project="proj", worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )
        mock_shared_load.return_value = SharedEnvState(
            project="proj",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=200,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["bravo"],
        )

        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
        # Both local and shared should be stopped
        assert any("web" in m and "stopped" in m for m in messages)
        assert any("db-shared" in m and "stopped" in m for m in messages)
        mock_shared_remove.assert_called_once_with(store, "proj")

    @patch("maelstrom.env.load_shared_state", return_value=None)
    @patch("maelstrom.env.remove_env_state")
    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("os.killpg")
    @patch("maelstrom.env.load_env_state")
    def test_no_shared_services(
        self, mock_load, mock_killpg, mock_alive, mock_remove, mock_shared_load,
    ):
        """Works normally when no shared services exist."""
        mock_load.return_value = EnvState(
            project="proj", worktree="bravo",
            worktree_path="/project/bravo",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        )

        store = InMemoryEnvStore()
        messages = stop_env(store, "proj", "bravo")
        assert any("stopped" in m for m in messages)
        assert not any("shared" in m for m in messages)


class TestListProjectEnvsShared:
    """Tests for list_project_envs skipping shared state."""

    @patch("maelstrom.env.is_service_alive", return_value=True)
    def test_skips_shared_state_file(self, mock_alive):
        """_shared.json is not returned as a worktree env."""
        store = InMemoryEnvStore()

        # Save a regular env
        save_env_state(store, EnvState(
            project="proj", worktree="alpha",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="x", pid=100,
                    log_file="/tmp/web.log", started_at="2025-01-01T00:00:00+00:00",
                )
            ],
        ))

        # Save shared state
        save_shared_state(store, SharedEnvState(
            project="proj",
            worktree_path="/project/alpha",
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="db-shared", command="postgres", pid=200,
                    log_file="/tmp/db.log", started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
            subscribers=["alpha"],
        ))

        result = list_project_envs(store, "proj")
        assert len(result) == 1
        assert result[0].worktree == "alpha"


class TestRegenerateAndRestartIfRunning:
    """Tests for regenerate_and_restart_if_running helper."""

    @patch("maelstrom.env.start_env")
    @patch("maelstrom.env.stop_env")
    @patch("maelstrom.env.regenerate_env_file")
    @patch("maelstrom.env.load_env_state", return_value=None)
    def test_when_stopped(self, mock_load, mock_regen, mock_stop, mock_start, tmp_path):
        """When env not running: regenerate .env, no stop/start, returns ([], None)."""
        store = InMemoryEnvStore()
        result = regenerate_and_restart_if_running(
            store, "proj", "bravo", tmp_path / "proj", tmp_path / "wt",
        )
        assert result == ([], None)
        mock_regen.assert_called_once_with(tmp_path / "proj", tmp_path / "wt", "bravo")
        mock_stop.assert_not_called()
        mock_start.assert_not_called()

    @patch("maelstrom.env.is_service_alive", return_value=True)
    @patch("maelstrom.env.start_env")
    @patch("maelstrom.env.stop_env", return_value=["web (pid 100): stopped"])
    @patch("maelstrom.env.regenerate_env_file")
    @patch("maelstrom.env.load_env_state")
    def test_when_running(
        self, mock_load, mock_regen, mock_stop, mock_start, mock_alive, tmp_path,
    ):
        """When env running: stop, regenerate, start with skip_install=True."""
        state = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path=str(tmp_path / "wt"),
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web",
                    command="python app.py",
                    pid=100,
                    log_file="/tmp/web.log",
                    started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        mock_load.return_value = state
        new_state = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path=str(tmp_path / "wt"),
            started_at="2025-01-01T00:00:01+00:00",
            services=[],
        )
        mock_start.return_value = new_state

        store = InMemoryEnvStore()
        stop_messages, returned_state = regenerate_and_restart_if_running(
            store, "proj", "bravo", tmp_path / "proj", tmp_path / "wt",
        )

        assert stop_messages == ["web (pid 100): stopped"]
        assert returned_state is new_state
        mock_stop.assert_called_once_with(store, "proj", "bravo")
        mock_regen.assert_called_once_with(tmp_path / "proj", tmp_path / "wt", "bravo")
        mock_start.assert_called_once_with(
            store, "proj", "bravo", tmp_path / "wt", skip_install=True,
        )

    @patch("maelstrom.env.is_service_alive", return_value=False)
    @patch("maelstrom.env.start_env")
    @patch("maelstrom.env.stop_env")
    @patch("maelstrom.env.regenerate_env_file")
    @patch("maelstrom.env.load_env_state")
    def test_state_exists_but_dead(
        self, mock_load, mock_regen, mock_stop, mock_start, mock_alive, tmp_path,
    ):
        """State file exists but no services alive: treat as stopped."""
        state = EnvState(
            project="proj",
            worktree="bravo",
            worktree_path=str(tmp_path / "wt"),
            started_at="2025-01-01T00:00:00+00:00",
            services=[
                ServiceState(
                    name="web", command="python app.py", pid=100,
                    log_file="/tmp/web.log",
                    started_at="2025-01-01T00:00:00+00:00",
                ),
            ],
        )
        mock_load.return_value = state

        store = InMemoryEnvStore()
        stop_messages, returned_state = regenerate_and_restart_if_running(
            store, "proj", "bravo", tmp_path / "proj", tmp_path / "wt",
        )

        assert stop_messages == []
        assert returned_state is None
        mock_stop.assert_not_called()
        mock_start.assert_not_called()
        mock_regen.assert_called_once()
