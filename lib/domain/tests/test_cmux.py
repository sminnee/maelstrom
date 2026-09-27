"""Tests for the cmux transport (client.py) and layout/domain (model.py) layers."""

import subprocess
from unittest.mock import MagicMock, patch

from mael_domain.cmux.api import FakeCmux
from mael_domain.cmux.client import (
    COMMAND_TIMEOUT_SECONDS,
    DEFAULT_SOCKET_PATH,
    CmuxResult,
    RecordingCmuxClient,
    SubprocessCmuxClient,
    _find_cmux_cli,
    current_client,
    ensure_cmux_running,
)
from mael_domain.cmux.model import BrowserTab, CmuxLayout, TerminalTab

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
        assert run.call_args_list[0].args[0][2] == DEFAULT_SOCKET_PATH

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


class TestEnsureCmuxRunning:
    """Tests for ensure_cmux_running (probe + start-the-app)."""

    def test_false_when_no_binary(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch("mael_domain.cmux.client._find_cmux_cli", return_value=None),
        ):
            assert ensure_cmux_running() is False

    def test_already_live_does_not_open(self):
        run = MagicMock(side_effect=_ping_reply("OK"))
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", run),
        ):
            assert ensure_cmux_running() is True
        # Only the ping ran — never `open`.
        assert all(call.args[0][0] != "open" for call in run.call_args_list)

    def test_dead_then_up_issues_open_and_polls(self):
        # First ping fails (dead), `open` succeeds, next ping answers → True.
        pings = [subprocess.CalledProcessError(1, "cmux")]

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "open":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            outcome = pings.pop(0) if pings else None
            if isinstance(outcome, Exception):
                raise outcome
            return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")

        run = MagicMock(side_effect=fake_run)
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", run),
            patch("time.sleep"),
        ):
            assert ensure_cmux_running() is True
        assert any(call.args[0][0] == "open" for call in run.call_args_list)

    def test_never_up_returns_false_after_timeout(self):
        # ping always dead; `open` runs but the socket never answers.
        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "open":
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise subprocess.CalledProcessError(1, cmd)

        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", side_effect=fake_run),
            patch("time.sleep"),
        ):
            assert ensure_cmux_running(timeout_s=0.5) is False

    def test_false_when_app_not_installed(self):
        # ping dead, `open` itself fails (app bundle missing).
        def fake_run(cmd, *args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "mael_domain.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
            patch("subprocess.run", side_effect=fake_run),
            patch("time.sleep"),
        ):
            assert ensure_cmux_running(timeout_s=0.5) is False


# ===========================================================================
# Layout / domain layer — model.py, over the FakeCmux
# ===========================================================================

NAME = "myproject-alpha"


def _layout(cmux: FakeCmux, name: str = NAME) -> CmuxLayout:
    return CmuxLayout(cmux, name)


class TestHasWorkspace:
    def test_true_when_present(self):
        assert _layout(FakeCmux().with_workspace(NAME)).has_workspace() is True

    def test_false_when_absent(self):
        assert _layout(FakeCmux().with_workspace("other")).has_workspace() is False


class TestEnsureWorkspace:
    """CmuxLayout.ensure_workspace — create if absent, no-op if present."""

    def test_no_op_when_present(self):
        cmux = FakeCmux().with_workspace(NAME)
        ref = _layout(cmux).ensure_workspace(
            TerminalTab("Claude", cwd="/wt", command="claude")
        )
        assert ref == cmux.workspace_ref(NAME)
        assert len(cmux.list_workspaces()) == 1
        assert cmux.sent == []

    def test_the_first_of_two_names_wins(self):
        cmux = FakeCmux().with_workspace("other").with_workspace(NAME)
        cmux.with_workspace(NAME)
        ref = _layout(cmux).ensure_workspace(TerminalTab("Claude"))
        assert ref == cmux.list_workspaces()[1].ref

    def test_creates_with_the_tab_in_its_initial_terminal(self):
        cmux = FakeCmux()
        ref = _layout(cmux).ensure_workspace(
            TerminalTab("Claude", cwd="/wt", command="claude")
        )
        assert ref == cmux.workspace_ref(NAME)
        # One terminal, renamed to the tab title.
        assert cmux.tabs(NAME) == [["Claude"]]
        # The cd starts the workspace, then the command runs in the same terminal.
        surface = cmux.surface_ref(NAME, 0)
        assert cmux.sent == [(surface, "cd /wt\n"), (surface, "claude\n")]
        assert cmux.focused == []


class TestEnsureTerminal:
    """CmuxLayout.ensure_terminal — at-least-one terminal at a pane index."""

    def test_no_op_when_pane_present(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        ref = _layout(cmux).ensure_terminal(1, TerminalTab("Terminal", cwd="/wt"))
        assert ref == cmux.surface_ref(NAME, 1)
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"]]
        assert cmux.sent == []

    def test_splits_and_reuses_initial_surface_when_pane_absent(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"]])
        ref = _layout(cmux).ensure_terminal(
            1, TerminalTab("Terminal", cwd="/wt", command="npm i")
        )
        # A new pane with one terminal, and no second tab.
        assert cmux.tabs(NAME) == [["Claude"], [""]]
        assert ref == cmux.surface_ref(NAME, 1)
        # cwd + command sent into the new pane's initial surface.
        assert cmux.sent == [(ref, "cd /wt\n"), (ref, "npm i\n")]
        assert cmux.focused == []

    def test_returns_none_when_no_workspace(self):
        assert _layout(FakeCmux()).ensure_terminal(1, TerminalTab("T")) is None


class TestAddTerminal:
    """CmuxLayout.add_terminal — unconditionally add a new tab."""

    def test_adds_new_tab_and_focuses(self):
        cmux = FakeCmux().with_workspace("other")
        cmux.with_workspace(NAME, [["Claude"], ["Terminal"]])
        cmd = "claude --permission-mode plan 'do the thing'"
        ref = _layout(cmux).add_terminal(
            0, TerminalTab("Claude", cwd="/wt", command=cmd)
        )
        assert ref == cmux.surface_ref(NAME, 0, 1)
        assert cmux.tabs(NAME) == [["Claude", "Claude"], ["Terminal"]]
        # The command is sent verbatim into the new surface, which sits in a
        # workspace other than the current one.
        assert cmux.sent == [(ref, "cd /wt\n"), (ref, f"{cmd}\n")]
        # The workspace comes to the foreground, then the pane, then the tab.
        pane = cmux.list_panes(cmux.workspace_ref(NAME))[0].ref
        assert cmux.focused == [cmux.workspace_ref(NAME), pane, ref]

    def test_returns_none_when_pane_absent(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"]])
        assert _layout(cmux).add_terminal(2, TerminalTab("X")) is None

    def test_returns_none_when_no_workspace(self):
        assert _layout(FakeCmux()).add_terminal(0, TerminalTab("X")) is None


class TestEnsureBrowser:
    """CmuxLayout.ensure_browser — recycle by URL prefix, else open new."""

    def test_recycles_existing_in_place(self):
        cmux = FakeCmux().with_workspace(
            NAME,
            [["Claude"], ["Terminal"], [("browser", "http://localhost:3000/dash")]],
        )
        ref = _layout(cmux).ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert ref == cmux.surface_ref(NAME, 2)
        # Navigated in place — no close, no new surface, no focus.
        assert cmux.tabs(NAME)[2] == ["http://localhost:3000"]
        assert cmux.focused == []

    def test_opens_in_existing_pane_when_no_match(self):
        cmux = FakeCmux().with_workspace(
            NAME, [["Claude"], ["Terminal"], [("browser", "https://docs.example")]]
        )
        ref = _layout(cmux).ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert ref == cmux.surface_ref(NAME, 2, 1)
        assert cmux.tabs(NAME)[2] == ["https://docs.example", "http://localhost:3000"]

    def test_match_prefix_overrides_url(self):
        """match= recycles a different github page in place (PR navigation)."""
        cmux = FakeCmux().with_workspace(
            NAME,
            [
                ["Claude"],
                ["Terminal"],
                [("browser", "https://github.com/o/r/issues/5")],
            ],
        )
        ref = _layout(cmux).ensure_browser(
            2, BrowserTab("https://github.com/o/r/pull/9", match="https://github.com")
        )
        assert ref == cmux.surface_ref(NAME, 2)
        assert cmux.tabs(NAME)[2] == ["https://github.com/o/r/pull/9"]

    def test_splits_new_pane_when_browser_pane_absent(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        ref = _layout(cmux).ensure_browser(2, BrowserTab("http://localhost:3000"))
        # A new rightmost pane holds the browser; its placeholder terminal is gone.
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"], ["http://localhost:3000"]]
        assert ref == cmux.surface_ref(NAME, 2)
        assert cmux.focused == []

    def test_acts_on_the_current_workspace(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"], ["x"]])
        cmux.with_workspace("other")
        _layout(cmux, "other").ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert cmux.tabs(NAME)[2] == ["x", "http://localhost:3000"]
        assert cmux.tabs("other") == [["Terminal"]]


class TestEnsureAbsentBrowser:
    """CmuxLayout.ensure_absent_browser — close a matching browser, if any."""

    def test_closes_matching(self):
        cmux = FakeCmux().with_workspace(
            NAME, [["Claude"], [("browser", "http://localhost:3000")]]
        )
        assert _layout(cmux).ensure_absent_browser("http://localhost:3000") is True
        assert cmux.tabs(NAME) == [["Claude"]]

    def test_no_op_when_no_match(self):
        cmux = FakeCmux().with_workspace(
            NAME, [["Claude"], [("browser", "https://docs.example")]]
        )
        assert _layout(cmux).ensure_absent_browser("http://localhost:3000") is False
        assert cmux.tabs(NAME) == [["Claude"], ["https://docs.example"]]


class TestClose:
    """CmuxLayout.close."""

    def test_close_closes_matching_workspace(self):
        cmux = FakeCmux().with_workspace(NAME).with_workspace("other")
        assert _layout(cmux).close() is True
        assert [w.title for w in cmux.list_workspaces()] == ["other"]

    def test_close_false_when_absent(self):
        assert _layout(FakeCmux().with_workspace("other")).close() is False
