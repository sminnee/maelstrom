"""Tests for the cmux policy layer (mael_layout.py).

These drive the maelstrom-aware spec builders through a real CmuxLayout over a
RecordingCmuxClient, asserting the right verbs fire with the right pane indices
and specs. CmuxLayout.current is patched to return a layout bound to the
recording client.
"""

from unittest.mock import patch

from maelstrom.cmux import mael_layout
from maelstrom.cmux.client import RecordingCmuxClient
from maelstrom.cmux.model import CmuxLayout


def _patch_current(responses, name="myproject-alpha"):
    """Patch CmuxLayout.current to return a layout over a recording client.

    Returns the recording client so tests can assert on ``client.calls``.
    """
    client = RecordingCmuxClient(responses)
    layout = CmuxLayout(client, name)
    patcher = patch.object(CmuxLayout, "current", staticmethod(lambda n: layout))
    return client, patcher


class TestWorkspaceName:
    def test_combines_project_and_worktree(self):
        assert mael_layout.workspace_name("maelstrom", "bravo") == "maelstrom-bravo"


class TestEnsureWorktreeWorkspace:
    """ensure_worktree_workspace — create-vs-reuse business logic."""

    def test_returns_false_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.ensure_worktree_workspace(
                "proj", "alpha", "/wt", command="claude", install_cmd="npm i",
            ) is False

    def test_create_path_builds_claude_and_shell(self):
        """No existing workspace → create with Claude (pane 0) + shell (pane 1)."""
        # The workspace doesn't exist until new-workspace runs; track that so
        # later list-workspaces lookups find it (as real cmux would). After the
        # shell pane is split, list-panes shows two panes.
        state = {"created": False, "split": False}

        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:1  myproject-alpha" if state["created"] else ""
            if args[0] == "new-workspace":
                state["created"] = True
                return "OK workspace:1"
            if args[0] == "list-panes":
                return "pane:0 pane:9" if state["split"] else "pane:0"
            if args[0] == "new-split":
                state["split"] = True
                return "OK surface:90 workspace:1"
            if args[0] == "list-pane-surfaces":
                pane = args[2]
                return {
                    "pane:0": '  surface:5  terminal  "shell"',
                    "pane:9": '  surface:91  terminal  "shell"',
                }.get(pane, "")
            return "OK"

        client, patcher = _patch_current(fn)
        with patcher, patch("maelstrom.cmux.model.time.sleep"):
            placed = mael_layout.ensure_worktree_workspace(
                "myproject", "alpha", "/wt", command="claude", install_cmd="npm i",
            )
        assert placed is True
        # Workspace created running the worktree cd.
        assert ("new-workspace", "--command", "cd /wt") in client.calls
        # Claude command sent into the initial pane-0 surface.
        assert ("send", "--workspace", "workspace:1", "--", "claude\n") in client.calls
        # Shell pane (pane 1) split off and install run there.
        assert any(c[0] == "new-split" for c in client.calls)
        assert (
            "send", "--surface", "surface:91", "--workspace", "workspace:1",
            "--", "npm i\n",
        ) in client.calls

    def test_reuse_path_adds_claude_tab_only(self):
        """Existing workspace → add a Claude tab to pane 0, no shell/install."""
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "new-surface":
                return "OK surface:99 pane:0 workspace:13"
            return "OK"

        client, patcher = _patch_current(fn)
        with patcher:
            placed = mael_layout.ensure_worktree_workspace(
                "myproject", "alpha", "/wt", command="claude", install_cmd="npm i",
            )
        assert placed is True
        # Added a fresh Claude tab to pane 0 (add_terminal → new-surface).
        assert (
            "new-surface", "--type", "terminal",
            "--pane", "pane:0", "--workspace", "workspace:13",
        ) in client.calls
        # The reused workspace is brought to the foreground.
        assert ("select-workspace", "--workspace", "workspace:13") in client.calls
        # Did NOT create the workspace or run install again.
        assert not any(c[0] == "new-workspace" for c in client.calls)
        assert not any(
            c[0] == "send" and "npm i\n" in c for c in client.calls
        )


class TestShowAppBrowser:
    def test_opens_in_browser_pane(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:103  terminal  "Terminal"'
            if args[0] == "list-panes":
                return "pane:0 pane:1 pane:2"
            if args[0] == "new-surface":
                return "OK surface:200 pane:2 workspace:13"
            return None

        client, patcher = _patch_current(fn)
        with patcher:
            ref = mael_layout.show_app_browser(
                "myproject", "alpha", "http://localhost:3000",
            )
        assert ref == "surface:200"
        # Opened in pane 2 (BROWSER_PANE).
        assert (
            "new-surface", "--type", "browser",
            "--pane", "pane:2", "--url", "http://localhost:3000",
        ) in client.calls

    def test_none_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.show_app_browser(
                "p", "a", "http://localhost:3000",
            ) is None


class TestHideAppBrowser:
    def test_closes_matching_browser(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "App"'
            if args[0] == "browser" and args[1] == "get-url":
                return "http://localhost:3000"
            if args[0] == "close-surface":
                return "OK"
            return None

        client, patcher = _patch_current(fn)
        with patcher:
            assert mael_layout.hide_app_browser(
                "p", "a", "http://localhost:3000",
            ) is True
        assert ("close-surface", "--surface", "surface:183") in client.calls


class TestShowPrBrowser:
    def test_recycles_github_browser_in_place(self):
        def fn(*args):
            if args[0] == "list-panels":
                return '  surface:183  browser  "GitHub"'
            if args[0] == "browser" and args[1] == "get-url":
                return "https://github.com/owner/repo"
            if args[0] == "browser" and "goto" in args:
                return "OK"
            return None

        client, patcher = _patch_current(fn)
        with patcher:
            ref = mael_layout.show_pr_browser(
                "https://github.com/owner/repo/pull/9",
            )
        assert ref == "surface:183"
        # Navigated the github tab in place (matched by github.com prefix).
        assert (
            "browser", "--surface", "surface:183", "goto",
            "https://github.com/owner/repo/pull/9",
        ) in client.calls

    def test_none_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.show_pr_browser("https://github.com/x") is None


class TestStatus:
    def test_set_status(self):
        _, patcher = _patch_current(
            {("set-status", "task", "Working", "--icon", "hammer"): "OK"},
        )
        with patcher:
            assert mael_layout.set_status("Working") is True

    def test_clear_status(self):
        _, patcher = _patch_current({("clear-status", "task"): "OK"})
        with patcher:
            assert mael_layout.clear_status() is True

    def test_set_status_false_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.set_status("Working") is False


class TestCloseWorkspace:
    def test_closes_matching(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "close-workspace":
                return "OK"
            return None

        client, patcher = _patch_current(fn)
        with patcher:
            assert mael_layout.close_workspace("myproject", "alpha") is True
        assert ("close-workspace", "--workspace", "workspace:13") in client.calls

    def test_false_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.close_workspace("p", "a") is False
