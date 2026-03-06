"""Tests for maelstrom.cmux module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from maelstrom.cmux import (
    _find_cmux_cli,
    close_surface,
    close_workspace,
    cmux_cmd,
    create_cmux_workspace,
    is_cmux_mode,
    is_ok,
    open_browser_pane,
    browser_surface_exists,
)


class TestIsCmuxMode:
    """Tests for is_cmux_mode function."""

    def test_returns_true_when_env_set(self):
        with patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}):
            assert is_cmux_mode() is True

    def test_returns_false_when_env_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            assert is_cmux_mode() is False

    def test_returns_false_when_env_empty(self):
        with patch.dict("os.environ", {"CMUX_SOCKET_PATH": ""}):
            assert is_cmux_mode() is False


class TestFindCmuxCli:
    """Tests for _find_cmux_cli function."""

    def test_finds_in_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/cmux"):
            assert _find_cmux_cli() == "/usr/local/bin/cmux"

    def test_falls_back_to_app_bundle(self):
        app_path = "/Applications/cmux.app/Contents/Resources/bin/cmux"
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.isfile", return_value=True),
        ):
            assert _find_cmux_cli() == app_path

    def test_returns_none_when_not_found(self):
        with (
            patch("shutil.which", return_value=None),
            patch("os.path.isfile", return_value=False),
        ):
            assert _find_cmux_cli() is None


class TestCmuxCmd:
    """Tests for cmux_cmd function."""

    def test_runs_command_with_flags(self):
        mock_result = MagicMock()
        mock_result.stdout = "OK ws-123\n"
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            result = cmux_cmd("new-workspace", "--command", "claude")
            assert result == "OK ws-123"
            mock_run.assert_called_once_with(
                ["/usr/bin/cmux", "--socket", "/tmp/cmux.sock",
                 "new-workspace", "--command", "claude"],
                capture_output=True, text=True, check=True,
            )

    def test_returns_raw_stdout_stripped(self):
        mock_result = MagicMock()
        mock_result.stdout = "OK\n"
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = cmux_cmd("rename-workspace", "foo")
            assert result == "OK"

    def test_returns_non_ok_output(self):
        mock_result = MagicMock()
        mock_result.stdout = "ERR something went wrong\n"
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = cmux_cmd("bad-command")
            assert result == "ERR something went wrong"

    def test_returns_none_when_cli_not_found(self):
        with patch("maelstrom.cmux._find_cmux_cli", return_value=None):
            assert cmux_cmd("status") is None

    def test_returns_none_when_no_socket(self):
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {}, clear=True),
        ):
            assert cmux_cmd("status") is None

    def test_returns_none_on_called_process_error(self):
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}),
            patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "cmux")),
        ):
            assert cmux_cmd("bad-command") is None

    def test_returns_none_on_file_not_found(self):
        with (
            patch("maelstrom.cmux._find_cmux_cli", return_value="/usr/bin/cmux"),
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/cmux.sock"}),
            patch("subprocess.run", side_effect=FileNotFoundError),
        ):
            assert cmux_cmd("status") is None



class TestIsOk:
    """Tests for is_ok function."""

    def test_extracts_ref_from_ok_response(self):
        assert is_ok("OK ws-123") == "ws-123"

    def test_returns_empty_string_for_bare_ok(self):
        assert is_ok("OK") == ""

    def test_returns_none_for_non_ok(self):
        assert is_ok("ERR something") is None

    def test_returns_none_for_none(self):
        assert is_ok(None) is None

    def test_returns_none_for_empty_string(self):
        assert is_ok("") is None


class TestCreateCmuxWorkspace:
    """Tests for create_cmux_workspace function."""

    def test_creates_workspace_and_pane(self):
        calls = []

        def mock_cmux_cmd(*args):
            calls.append(args)
            if args[0] == "new-workspace":
                return "OK ws-123"
            if args[0] == "rename-workspace":
                return "OK"
            if args[0] == "new-pane":
                return "OK pane-456"
            if args[0] == "send":
                return "OK"
            if args[0] == "rename-surface":
                return "OK"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "focus-pane":
                return "OK"
            return None

        with patch("maelstrom.cmux.cmux_cmd", side_effect=mock_cmux_cmd):
            result = create_cmux_workspace("myproject", "alpha", "/path/to/worktree")

        assert result == "ws-123"
        assert calls[0][0] == "new-workspace"
        assert calls[1][0] == "send"
        assert calls[2][0] == "rename-workspace"
        assert calls[3][0] == "new-pane"

    def test_returns_none_when_workspace_creation_fails(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value=None):
            result = create_cmux_workspace("myproject", "alpha", "/path/to/worktree")
            assert result is None

    def test_succeeds_even_if_pane_fails(self):
        def mock_cmux_cmd(*args):
            if args[0] == "new-workspace":
                return "OK ws-123"
            if args[0] == "send":
                return "OK"
            if args[0] == "rename-workspace":
                return "OK"
            if args[0] == "list-panes":
                return "pane:0"
            if args[0] == "focus-pane":
                return "OK"
            # new-pane fails
            return None

        with patch("maelstrom.cmux.cmux_cmd", side_effect=mock_cmux_cmd):
            result = create_cmux_workspace("myproject", "alpha", "/path/to/worktree")

        assert result == "ws-123"


class TestOpenBrowserPane:
    """Tests for open_browser_pane function."""

    def test_returns_surface_ref(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value="OK browser-789"):
            result = open_browser_pane("http://localhost:3000")
            assert result == "browser-789"

    def test_returns_none_on_failure(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value=None):
            result = open_browser_pane("http://localhost:3000")
            assert result is None


class TestSurfaceExists:
    """Tests for browser_surface_exists function."""

    def test_returns_true_when_surface_alive(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value="OK http://localhost:3000"):
            assert browser_surface_exists("browser-789") is True

    def test_returns_false_when_surface_dead(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value=None):
            assert browser_surface_exists("browser-789") is False

    def test_returns_false_on_error_response(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value="ERR not found"):
            assert browser_surface_exists("browser-789") is False


class TestCloseWorkspace:
    """Tests for close_workspace function."""

    def test_closes_matching_workspace(self):
        list_output = (
            "* workspace:13  maelstrom-bravo  [selected]\n"
            "  workspace:14  other-project"
        )
        calls = []

        def mock_cmux_cmd(*args):
            calls.append(args)
            if args[0] == "list-workspaces":
                return list_output
            if args[0] == "close-workspace":
                return "OK"
            return None

        with patch("maelstrom.cmux.cmux_cmd", side_effect=mock_cmux_cmd):
            assert close_workspace("maelstrom-bravo") is True

        assert ("close-workspace", "--workspace", "workspace:13") in calls

    def test_returns_false_when_no_match(self):
        list_output = "  workspace:14  other-project"

        with patch("maelstrom.cmux.cmux_cmd", return_value=list_output):
            assert close_workspace("maelstrom-bravo") is False

    def test_returns_false_when_cmux_unavailable(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value=None):
            assert close_workspace("maelstrom-bravo") is False


class TestCloseSurface:
    """Tests for close_surface function."""

    def test_returns_true_on_success(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value="OK"):
            assert close_surface("surface-123") is True

    def test_returns_false_on_failure(self):
        with patch("maelstrom.cmux.cmux_cmd", return_value=None):
            assert close_surface("surface-123") is False
