"""Tests for the cmux transport layer (client.py)."""

import subprocess
from unittest.mock import MagicMock, patch

from mael_domain.cmux.client import (
    COMMAND_TIMEOUT_SECONDS,
    CmuxResult,
    RecordingCmuxClient,
    SubprocessCmuxClient,
    _find_cmux_cli,
    current_client,
    resolve_socket_path,
)

# ===========================================================================
# Transport layer — client.py
# ===========================================================================


class TestCmuxResult:
    """Tests for the CmuxResult value object (ok/text/ref parsing)."""

    def test_ok_true_for_ok_line(self):
        assert CmuxResult("OK ws-123").ok is True

    def test_ok_true_for_bare_ok(self):
        assert CmuxResult("OK").ok is True

    def test_ok_false_for_error(self):
        assert CmuxResult("ERR something").ok is False

    def test_ok_false_for_none(self):
        assert CmuxResult(None).ok is False

    def test_ok_false_for_empty(self):
        assert CmuxResult("").ok is False

    def test_text_extracts_ref(self):
        assert CmuxResult("OK ws-123").text == "ws-123"

    def test_text_empty_for_bare_ok(self):
        assert CmuxResult("OK").text == ""

    def test_text_empty_for_non_ok(self):
        assert CmuxResult("ERR x").text == ""

    def test_text_empty_for_none(self):
        assert CmuxResult(None).text == ""

    def test_ref_extracts_leading_surface(self):
        result = CmuxResult("OK surface:99 pane:0 workspace:13")
        assert result.ref("surface") == "surface:99"

    def test_ref_extracts_pane(self):
        result = CmuxResult("OK surface:99 pane:7 workspace:1")
        assert result.ref("pane") == "pane:7"

    def test_ref_none_when_kind_absent(self):
        assert CmuxResult("OK pane:0 workspace:1").ref("surface") is None

    def test_ref_none_for_non_ok(self):
        assert CmuxResult("ERR surface:5").ref("surface") is None

    def test_ref_none_for_none(self):
        assert CmuxResult(None).ref("surface") is None


class TestFindCmuxCli:
    """Tests for the _find_cmux_cli discovery helper."""

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


class TestSubprocessCmuxClient:
    """Tests for the real subprocess-backed client."""

    def test_runs_command_with_socket_flag(self):
        mock_result = MagicMock()
        mock_result.stdout = "OK ws-123\n"
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = client.run("new-workspace", "--command", "claude")
        assert result.raw == "OK ws-123"
        assert result.text == "ws-123"
        mock_run.assert_called_once_with(
            [
                "/usr/bin/cmux",
                "--socket",
                "/tmp/cmux.sock",
                "new-workspace",
                "--command",
                "claude",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )

    def test_strips_stdout(self):
        mock_result = MagicMock()
        mock_result.stdout = "OK\n"
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch("subprocess.run", return_value=mock_result):
            assert client.run("rename-workspace", "foo").raw == "OK"

    def test_preserves_non_ok_output(self):
        mock_result = MagicMock()
        mock_result.stdout = "ERR something went wrong\n"
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch("subprocess.run", return_value=mock_result):
            result = client.run("bad-command")
        assert result.raw == "ERR something went wrong"
        assert result.ok is False

    def test_none_on_called_process_error(self):
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "cmux"),
        ):
            assert client.run("bad-command").raw is None

    def test_none_on_timeout(self):
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmux", COMMAND_TIMEOUT_SECONDS),
        ):
            assert client.run("list-panes").raw is None

    def test_none_on_any_os_error(self):
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch("subprocess.run", side_effect=PermissionError):
            assert client.run("list-panes").raw is None

    def test_none_on_file_not_found(self):
        client = SubprocessCmuxClient("/usr/bin/cmux", "/tmp/cmux.sock")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert client.run("status").raw is None


class TestRecordingCmuxClient:
    """Tests for the in-memory fake client."""

    def test_records_calls(self):
        client = RecordingCmuxClient()
        client.run("list-workspaces")
        client.run("send", "--surface", "surface:1", "--", "ls\n")
        assert client.calls == [
            ("list-workspaces",),
            ("send", "--surface", "surface:1", "--", "ls\n"),
        ]

    def test_dict_responses(self):
        client = RecordingCmuxClient({("list-panes",): "pane:0 pane:1"})
        assert client.run("list-panes").raw == "pane:0 pane:1"
        # Unmatched calls return None.
        assert client.run("other").raw is None

    def test_callable_responses(self):
        def fn(*args):
            return "OK ws-9" if args[0] == "new-workspace" else "OK"

        client = RecordingCmuxClient(fn)
        assert client.run("new-workspace").text == "ws-9"
        assert client.run("rename-workspace").raw == "OK"

    def test_no_responses_returns_none(self):
        client = RecordingCmuxClient()
        assert client.run("anything").raw is None


def _ping_reply(raw):
    """A ``subprocess.run`` stub whose CompletedProcess carries ``raw`` stdout.

    ``raw=None`` models a dead socket: raise so ``SubprocessCmuxClient.run``
    degrades to ``CmuxResult(None)``. Any string models a live daemon's reply.
    """

    def fake_run(cmd, *args, **kwargs):
        if raw is None:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=raw, stderr="")

    return fake_run


class TestCurrentClient:
    """Tests for current_client."""

    def test_unset_socket_falls_back_to_default(self):
        # Unset/empty CMUX_SOCKET_PATH falls back to DEFAULT_SOCKET_PATH and
        # probes it, so a caller that did not inherit the var still reaches a
        # running cmux.
        run = MagicMock(side_effect=_ping_reply("OK"))
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", run),
        ):
            assert isinstance(current_client(), SubprocessCmuxClient)
            default = resolve_socket_path()
        assert run.call_args_list[0].args[0][2] == default

    def test_none_when_no_binary(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch("mael_domain.cmux.client._find_cmux_cli", return_value=None),
        ):
            assert current_client() is None

    def test_none_when_socket_dead(self):
        # Env + binary present, but ping yields CmuxResult(None): the daemon is
        # gone (the app was quit). current_client must return None rather than a
        # live-looking client.
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", side_effect=_ping_reply(None)),
        ):
            assert current_client() is None

    def test_returns_client_when_ping_replies(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", side_effect=_ping_reply("OK")),
        ):
            client = current_client()
            assert isinstance(client, SubprocessCmuxClient)
