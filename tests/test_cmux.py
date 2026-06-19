"""Tests for the cmux transport (client.py) and layout/domain (model.py) layers."""

import subprocess
from unittest.mock import MagicMock, patch

from maelstrom.cmux.client import (
    CmuxResult,
    RecordingCmuxClient,
    SubprocessCmuxClient,
    _find_cmux_cli,
    current_client,
    is_cmux_mode,
)
from maelstrom.cmux.model import (
    BrowserTab,
    CmuxLayout,
    Surface,
    TerminalTab,
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
            ["/usr/bin/cmux", "--socket", "/tmp/cmux.sock",
             "new-workspace", "--command", "claude"],
            capture_output=True, text=True, check=True,
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


class TestCurrentClient:
    """Tests for current_client / is_cmux_mode."""

    def test_none_when_socket_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert current_client() is None
            assert is_cmux_mode() is False

    def test_none_when_socket_empty(self):
        with patch.dict("os.environ", {"CMUX_SOCKET_PATH": ""}):
            assert current_client() is None

    def test_none_when_no_binary(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch("maelstrom.cmux.client._find_cmux_cli", return_value=None),
        ):
            assert current_client() is None
            assert is_cmux_mode() is False

    def test_returns_client_when_in_cmux(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "maelstrom.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
        ):
            client = current_client()
            assert isinstance(client, SubprocessCmuxClient)
            assert is_cmux_mode() is True


# ===========================================================================
# Layout / domain layer — model.py
# ===========================================================================


def _layout(responses=None, name="myproject-alpha"):
    """Build a CmuxLayout over a RecordingCmuxClient; return (layout, client)."""
    client = RecordingCmuxClient(responses)
    return CmuxLayout(client, name), client


class TestCurrent:
    """CmuxLayout.current returns None outside cmux, a layout inside."""

    def test_none_outside_cmux(self):
        with patch.dict("os.environ", {}, clear=True):
            assert CmuxLayout.current("foo") is None

    def test_layout_inside_cmux(self):
        with (
            patch.dict("os.environ", {"CMUX_SOCKET_PATH": "/tmp/c.sock"}),
            patch(
                "maelstrom.cmux.client._find_cmux_cli",
                return_value="/usr/bin/cmux",
            ),
        ):
            lay = CmuxLayout.current("foo")
            assert isinstance(lay, CmuxLayout)


class TestHasWorkspace:
    """CmuxLayout.has_workspace."""

    def test_true_when_present(self):
        lay, _ = _layout({
            ("list-workspaces",): "  workspace:13  myproject-alpha",
        })
        assert lay.has_workspace() is True

    def test_false_when_absent(self):
        lay, _ = _layout({("list-workspaces",): "  workspace:14  other"})
        assert lay.has_workspace() is False

    def test_false_when_cmux_unavailable(self):
        lay, _ = _layout({("list-workspaces",): None})
        assert lay.has_workspace() is False

    def test_matches_first(self):
        lay, _ = _layout({
            ("list-workspaces",): (
                "  workspace:14  other\n"
                "  workspace:15  myproject-alpha\n"
                "  workspace:16  myproject-alpha"
            ),
        })
        # has_workspace is boolean, but the underlying find returns the first.
        assert lay.has_workspace() is True


class TestEnsureWorkspace:
    """CmuxLayout.ensure_workspace — create if absent, no-op if present."""

    def test_no_op_when_present(self):
        lay, client = _layout({
            ("list-workspaces",): "  workspace:13  myproject-alpha",
        })
        ref = lay.ensure_workspace(TerminalTab("Claude", cwd="/wt", command="claude"))
        assert ref == "workspace:13"
        # No creation commands issued.
        assert not any(c[0] == "new-workspace" for c in client.calls)

    def test_creates_with_initial_terminal(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return ""  # absent
            if args[0] == "new-workspace":
                return "OK workspace:1"
            if args[0] == "list-panes":
                return "pane:0"
            if args[0] == "list-pane-surfaces":
                return '  surface:5  terminal  "shell"'
            return "OK"

        lay, client = _layout(fn)
        ref = lay.ensure_workspace(
            TerminalTab("Claude", cwd="/wt", command="claude"),
        )
        assert ref == "workspace:1"
        # Created with the cd as its initial command.
        assert ("new-workspace", "--command", "cd /wt") in client.calls
        # Renamed to the canonical name.
        assert (
            "rename-workspace", "--workspace", "workspace:1", "myproject-alpha",
        ) in client.calls
        # The command is sent into the workspace's initial surface.
        assert (
            "send", "--workspace", "workspace:1", "--", "claude\n",
        ) in client.calls
        # The initial tab is renamed to the tab title.
        assert ("rename-tab", "--surface", "surface:5", "Claude") in client.calls

    def test_returns_none_on_creation_failure(self):
        lay, _ = _layout({
            ("list-workspaces",): "",
            ("new-workspace", "--command", "cd /wt"): None,
        })
        ref = lay.ensure_workspace(TerminalTab("Claude", cwd="/wt"))
        assert ref is None


class TestEnsureTerminal:
    """CmuxLayout.ensure_terminal — at-least-one terminal at a pane index."""

    def test_no_op_when_pane_present(self):
        """Pane exists → a terminal already exists there; no split."""
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "list-pane-surfaces":
                return '  surface:7  terminal  "Terminal"'
            return "OK"

        lay, client = _layout(fn)
        ref = lay.ensure_terminal(1, TerminalTab("Terminal", cwd="/wt"))
        assert ref == "surface:7"
        # No new pane was split.
        assert not any(c[0] == "new-split" for c in client.calls)

    def test_splits_and_reuses_initial_surface_when_pane_absent(self):
        # Pane 1 absent; split a new pane and send the command into ITS initial
        # surface (no new tab).
        panes_seq = ["pane:0", "pane:0 pane:9"]

        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return panes_seq.pop(0) if panes_seq else "pane:0 pane:9"
            if args[0] == "new-split":
                return "OK surface:90 workspace:13"
            if args[0] == "list-pane-surfaces":
                # rightmost pane:0's surface, then the new pane:9's surface
                pane = args[2]
                return {
                    "pane:0": '  surface:50  terminal  "x"',
                    "pane:9": '  surface:91  terminal  "shell"',
                }.get(pane, "")
            return "OK"

        lay, client = _layout(fn)
        with patch("maelstrom.cmux.model.time.sleep") as mock_sleep:
            ref = lay.ensure_terminal(1, TerminalTab("Terminal", cwd="/wt", command="npm i"))
        assert ref == "surface:91"
        # Split off the rightmost surface (focus-safe), not new-pane.
        assert any(c[0] == "new-split" for c in client.calls)
        assert not any(c[0] == "new-pane" for c in client.calls)
        # Settle sleep interposed before sending into the split pane.
        mock_sleep.assert_called_once()
        # cwd + command sent into the new pane's initial surface.
        assert (
            "send", "--surface", "surface:91", "--workspace", "workspace:13",
            "--", "cd /wt\n",
        ) in client.calls
        assert (
            "send", "--surface", "surface:91", "--workspace", "workspace:13",
            "--", "npm i\n",
        ) in client.calls

    def test_returns_none_when_no_workspace(self):
        lay, _ = _layout({("list-workspaces",): ""})
        assert lay.ensure_terminal(1, TerminalTab("T")) is None


class TestAddTerminal:
    """CmuxLayout.add_terminal — unconditionally add a new tab."""

    def test_adds_new_tab_and_focuses(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "new-surface":
                return "OK surface:99 pane:0 workspace:13"
            return "OK"

        lay, client = _layout(fn)
        ref = lay.add_terminal(0, TerminalTab("Claude", cwd="/wt", command="claude"))
        assert ref == "surface:99"
        # A new terminal surface tab was created in pane:0.
        assert (
            "new-surface", "--type", "terminal",
            "--pane", "pane:0", "--workspace", "workspace:13",
        ) in client.calls
        assert ("rename-tab", "--surface", "surface:99", "Claude") in client.calls
        # Command sent into the new surface, scoped to the workspace.
        assert (
            "send", "--surface", "surface:99", "--workspace", "workspace:13",
            "--", "claude\n",
        ) in client.calls
        # The workspace is brought to the foreground (it may be a background one),
        # the pane focused, and the new tab brought to front.
        assert (
            "select-workspace", "--workspace", "workspace:13",
        ) in client.calls
        assert (
            "focus-pane", "--pane", "pane:0", "--workspace", "workspace:13",
        ) in client.calls
        assert (
            "focus-panel", "--panel", "surface:99", "--workspace", "workspace:13",
        ) in client.calls

    def test_returns_none_when_pane_absent(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0"
            return "OK"

        lay, _ = _layout(fn)
        assert lay.add_terminal(2, TerminalTab("X")) is None

    def test_returns_none_when_no_workspace(self):
        lay, _ = _layout({("list-workspaces",): ""})
        assert lay.add_terminal(0, TerminalTab("X")) is None

    def test_sends_command_verbatim(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0"
            if args[0] == "new-surface":
                return "OK surface:99 pane:0 workspace:13"
            return "OK"

        lay, client = _layout(fn)
        cmd = "claude --permission-mode plan 'do the thing'"
        lay.add_terminal(0, TerminalTab("Claude", cwd="/wt", command=cmd))
        assert (
            "send", "--surface", "surface:99", "--workspace", "workspace:13",
            "--", f"{cmd}\n",
        ) in client.calls


class TestEnsureBrowser:
    """CmuxLayout.ensure_browser — recycle by URL prefix, else open new."""

    def test_recycles_existing_in_place(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "App"'
            if args[0] == "browser" and args[1] == "get-url":
                return "http://localhost:3000/dashboard"
            if args[0] == "browser" and "goto" in args:
                return "OK"
            return None

        lay, client = _layout(fn)
        ref = lay.ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert ref == "surface:183"
        # Navigated in place — no close, no new surface.
        assert (
            "browser", "--surface", "surface:183", "goto", "http://localhost:3000",
        ) in client.calls
        assert not any(c[0] == "close-surface" for c in client.calls)
        assert not any(c[0] == "new-surface" for c in client.calls)

    def test_opens_in_existing_pane_when_no_match(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:103  terminal  "Terminal"'
            if args[0] == "list-panes":
                return "pane:0 pane:1 pane:2"
            if args[0] == "new-surface":
                return "OK surface:200 pane:2 workspace:13"
            return None

        lay, client = _layout(fn)
        ref = lay.ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert ref == "surface:200"
        # Opened a browser tab in pane:2 (the browser pane).
        assert (
            "new-surface", "--type", "browser",
            "--pane", "pane:2", "--url", "http://localhost:3000",
        ) in client.calls

    def test_match_prefix_overrides_url(self):
        """match= recycles a different github page in place (PR navigation)."""
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "GitHub"'
            if args[0] == "browser" and args[1] == "get-url":
                return "https://github.com/owner/repo/issues/5"
            if args[0] == "browser" and "goto" in args:
                return "OK"
            return None

        lay, client = _layout(fn)
        ref = lay.ensure_browser(
            2,
            BrowserTab(
                "https://github.com/owner/repo/pull/9",
                match="https://github.com",
            ),
        )
        assert ref == "surface:183"
        assert (
            "browser", "--surface", "surface:183", "goto",
            "https://github.com/owner/repo/pull/9",
        ) in client.calls

    def test_splits_new_pane_when_browser_pane_absent(self):
        # No browser pane (pane index 2 absent): split off the rightmost,
        # focus-safely, and discard the placeholder surface.
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:103  terminal  "Terminal"'
            if args[0] == "list-panes":
                return "pane:0 pane:1"  # only 2 panes → index 2 absent
            if args[0] == "new-split":
                return "OK surface:430 workspace:13"
            if args[0] == "list-pane-surfaces":
                pane = args[2]
                return {
                    "pane:1": '  surface:90  terminal  "rightmost"',
                    "pane:9": '  surface:100  terminal  "placeholder"',
                }.get(pane, "")
            if args[0] == "new-surface":
                return "OK surface:200 pane:9 workspace:13"
            if args[0] == "close-surface":
                return "OK"
            return None

        # list-panes returns "pane:0 pane:1" for index-2 / rightmost lookups, then
        # "...pane:9" for the post-split rightmost lookup inside _split_pane_off.
        panes_seq = ["pane:0 pane:1", "pane:0 pane:1", "pane:0 pane:1 pane:9"]

        def fn2(*args):
            if args[0] == "list-panes":
                return panes_seq.pop(0) if panes_seq else "pane:0 pane:1 pane:9"
            return fn(*args)

        lay, client = _layout(fn2)
        ref = lay.ensure_browser(2, BrowserTab("http://localhost:3000"))
        assert ref == "surface:200"
        # Split via new-split --surface (no focus, no new-pane).
        assert any(
            c[0] == "new-split" and "--surface" in c for c in client.calls
        )
        assert not any(c[0] == "new-pane" for c in client.calls)
        # Placeholder terminal surface discarded.
        assert ("close-surface", "--surface", "surface:100") in client.calls
        # No focus grab.
        assert not any(c[0] == "focus-pane" for c in client.calls)
        assert not any(c[0] == "focus-panel" for c in client.calls)

    def test_opens_new_tab_when_navigate_fails(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "GitHub"'
            if args[0] == "browser" and args[1] == "get-url":
                return "https://github.com/owner/repo"
            if args[0] == "browser" and "goto" in args:
                return None  # navigation failed
            if args[0] == "list-panes":
                return "pane:0 pane:1 pane:2"
            if args[0] == "new-surface":
                return "OK surface:300 pane:2 workspace:13"
            return None

        lay, _ = _layout(fn)
        ref = lay.ensure_browser(
            2, BrowserTab("https://github.com/owner/repo/pull/9", match="https://github.com"),
        )
        assert ref == "surface:300"


class TestEnsureAbsentBrowser:
    """CmuxLayout.ensure_absent_browser — close a matching browser, if any."""

    def test_closes_matching(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "App"'
            if args[0] == "browser" and args[1] == "get-url":
                return "http://localhost:3000"
            if args[0] == "close-surface":
                return "OK"
            return None

        lay, client = _layout(fn)
        assert lay.ensure_absent_browser("http://localhost:3000") is True
        assert ("close-surface", "--surface", "surface:183") in client.calls

    def test_no_op_when_no_match(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:103  terminal  "Terminal"'
            return None

        lay, client = _layout(fn)
        assert lay.ensure_absent_browser("http://localhost:3000") is False
        assert not any(c[0] == "close-surface" for c in client.calls)


class TestEnsureAbsentPane:
    """CmuxLayout.ensure_absent_pane — collapse a pane, if present."""

    def test_closes_present_pane(self):
        def fn(*args):
            if args[0] == "list-panes":
                return "pane:0 pane:1 pane:2"
            if args[0] == "list-pane-surfaces":
                return '  surface:55  terminal  "x"'
            if args[0] == "close-surface":
                return "OK"
            return None

        lay, client = _layout(fn)
        assert lay.ensure_absent_pane(2) is True
        assert ("close-surface", "--surface", "surface:55") in client.calls

    def test_no_op_when_pane_absent(self):
        lay, client = _layout({("list-panes",): "pane:0 pane:1"})
        assert lay.ensure_absent_pane(2) is False
        assert not any(c[0] == "close-surface" for c in client.calls)


class TestStatusAndClose:
    """CmuxLayout.set_status / clear_status / close."""

    def test_set_status(self):
        lay, client = _layout({
            ("set-status", "task", "Working", "--icon", "hammer"): "OK",
        })
        assert lay.set_status("Working") is True
        assert (
            "set-status", "task", "Working", "--icon", "hammer",
        ) in client.calls

    def test_set_status_false_on_failure(self):
        lay, _ = _layout(lambda *a: None)
        assert lay.set_status("Working") is False

    def test_clear_status(self):
        lay, client = _layout({("clear-status", "task"): "OK"})
        assert lay.clear_status() is True
        assert ("clear-status", "task") in client.calls

    def test_close_closes_matching_workspace(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "* workspace:13  myproject-alpha  [selected]"
            if args[0] == "close-workspace":
                return "OK"
            return None

        lay, client = _layout(fn)
        assert lay.close() is True
        assert ("close-workspace", "--workspace", "workspace:13") in client.calls

    def test_close_false_when_absent(self):
        lay, _ = _layout({("list-workspaces",): "  workspace:14  other"})
        assert lay.close() is False


class TestListSurfaces:
    """The browser-state parsing seam (_list_surfaces / Surface objects)."""

    def test_parses_terminal_and_browser(self):
        output = (
            '  surface:103  terminal  "Terminal"\n'
            '  surface:183  browser  "My App"\n'
        )
        lay, _ = _layout({("list-panels",): output})
        surfaces = lay._list_surfaces()
        assert surfaces == [
            Surface(ref="surface:103", type="terminal", title="Terminal", focused=False),
            Surface(ref="surface:183", type="browser", title="My App", focused=False),
        ]

    def test_parses_focused(self):
        output = '* surface:104  terminal  [focused]  "Terminal"\n'
        lay, _ = _layout({("list-panels",): output})
        surfaces = lay._list_surfaces()
        assert surfaces[0].focused is True
        assert surfaces[0].ref == "surface:104"

    def test_skips_malformed_and_blank(self):
        output = (
            '\n  surface:103  terminal  "Terminal"\n'
            "  garbage line\n"
            '  surface:104  browser  "App"\n\n'
        )
        lay, _ = _layout({("list-panels",): output})
        assert len(lay._list_surfaces()) == 2
