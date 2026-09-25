"""Tests for the cmux policy layer (mael_layout.py).

These drive the maelstrom-aware spec builders through a real CmuxLayout over a
RecordingCmuxClient, asserting the right verbs fire with the right pane indices
and specs. CmuxLayout.current is patched to return a layout bound to the
recording client.
"""

import json
from unittest.mock import patch

from mael_domain.cmux import mael_layout
from mael_domain.cmux.client import RecordingCmuxClient
from mael_domain.cmux.model import CmuxLayout


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
            assert (
                mael_layout.ensure_worktree_workspace(
                    "proj",
                    "alpha",
                    "/wt",
                    command="claude",
                    install_cmd="npm i",
                )
                is False
            )

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
            if args[0] == "new-surface":
                return "OK surface:6 pane:0 workspace:1"
            if args[0] == "list-pane-surfaces":
                pane = args[2]
                return {
                    "pane:0": '  surface:5  terminal  "shell"',
                    "pane:9": '  surface:91  terminal  "shell"',
                }.get(pane, "")
            return "OK"

        client, patcher = _patch_current(fn)
        with patcher, patch("mael_domain.cmux.model.time.sleep"):
            placed = mael_layout.ensure_worktree_workspace(
                "myproject",
                "alpha",
                "/wt",
                command="claude",
                install_cmd="npm i",
            )
        assert placed is True
        # Workspace created running the worktree cd.
        assert ("new-workspace", "--command", "cd /wt") in client.calls
        # Claude starts only after the installer shell is ready.
        assert (
            "send",
            "--surface",
            "surface:6",
            "--workspace",
            "workspace:1",
            "--",
            "claude\n",
        ) in client.calls
        # Shell pane (pane 1) split off and install run there.
        assert any(c[0] == "new-split" for c in client.calls)
        assert (
            "send",
            "--surface",
            "surface:91",
            "--workspace",
            "workspace:1",
            "--",
            "npm i\n",
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
                "myproject",
                "alpha",
                "/wt",
                command="claude",
                install_cmd="npm i",
            )
        assert placed is True
        # Added a fresh Claude tab to pane 0 (add_terminal → new-surface).
        assert (
            "new-surface",
            "--type",
            "terminal",
            "--pane",
            "pane:0",
            "--workspace",
            "workspace:13",
        ) in client.calls
        # The reused workspace is brought to the foreground.
        assert ("select-workspace", "--workspace", "workspace:13") in client.calls
        # Did NOT create the workspace or run install again.
        assert not any(c[0] == "new-workspace" for c in client.calls)
        assert not any(c[0] == "send" and "npm i\n" in c for c in client.calls)

    def test_create_path_returns_false_when_new_workspace_fails(self):
        """new-workspace non-OK (dead socket) → placement failed, return False."""

        def fn(*args):
            if args[0] == "list-workspaces":
                return ""  # no existing workspace → create path
            if args[0] == "new-workspace":
                return None  # cmux error: no workspace ref
            return "OK"

        _, patcher = _patch_current(fn)
        with patcher, patch("mael_domain.cmux.model.time.sleep"):
            placed = mael_layout.ensure_worktree_workspace(
                "myproject",
                "alpha",
                "/wt",
                command="claude",
                install_cmd="npm i",
            )
        assert placed is False


class TestEnsureWorktreeShellWorkspace:
    def test_create_runs_the_installer_in_the_initial_shell(self):
        state = {"created": False}

        def fn(*args):
            if args[0] == "list-workspaces":
                return "workspace:1  myproject-alpha" if state["created"] else ""
            if args[0] == "new-workspace":
                state["created"] = True
                return "OK workspace:1"
            if args[0] == "list-panes":
                return "pane:0"
            if args[0] == "list-pane-surfaces":
                return 'surface:5 terminal "shell"'
            return "OK"

        client, patcher = _patch_current(fn)
        with patcher, patch("mael_domain.cmux.model.time.sleep"):
            assert mael_layout.ensure_worktree_shell_workspace(
                "myproject", "alpha", "/wt", install_cmd="npm i"
            )
        assert ("send", "--workspace", "workspace:1", "--", "npm i\n") in client.calls

    def test_reuse_adds_a_shell_without_running_the_installer(self):
        def fn(*args):
            if args[0] == "list-workspaces":
                return "workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "new-surface":
                return "OK surface:99 pane:1 workspace:13"
            return "OK"

        client, patcher = _patch_current(fn)
        with patcher:
            assert mael_layout.ensure_worktree_shell_workspace(
                "myproject", "alpha", "/wt", install_cmd="npm i"
            )
        assert not any(c[0] == "send" and "npm i\n" in c for c in client.calls)

    def test_reuse_path_returns_false_when_new_surface_fails(self):
        """Existing workspace but add_terminal (new-surface) non-OK → False."""

        def fn(*args):
            if args[0] == "list-workspaces":
                return "  workspace:13  myproject-alpha"
            if args[0] == "list-panes":
                return "pane:0 pane:1"
            if args[0] == "new-surface":
                return None  # cmux error: tab not placed
            return "OK"

        _, patcher = _patch_current(fn)
        with patcher:
            placed = mael_layout.ensure_worktree_workspace(
                "myproject",
                "alpha",
                "/wt",
                command="claude",
                install_cmd="npm i",
            )
        assert placed is False


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
                "myproject",
                "alpha",
                "http://localhost:3000",
            )
        assert ref == "surface:200"
        # Opened in pane 2 (BROWSER_PANE).
        assert (
            "new-surface",
            "--type",
            "browser",
            "--pane",
            "pane:2",
            "--url",
            "http://localhost:3000",
        ) in client.calls

    def test_none_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert (
                mael_layout.show_app_browser(
                    "p",
                    "a",
                    "http://localhost:3000",
                )
                is None
            )


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
            assert (
                mael_layout.hide_app_browser(
                    "p",
                    "a",
                    "http://localhost:3000",
                )
                is True
            )
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
            "browser",
            "--surface",
            "surface:183",
            "goto",
            "https://github.com/owner/repo/pull/9",
        ) in client.calls

    def test_none_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.show_pr_browser("https://github.com/x") is None


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


_WS_UUID = "D2022D3A-292F-4647-B095-E6ACE6263CE3"
_PANE_UUIDS = [
    "3B338EDA-6066-410F-B2BD-B0AD86C7E83C",
    "4CDF2624-0414-4AB8-A7FE-E38BEC2C01EC",
]


def _fake_cmux(workspace: bool, panes: int, json_forms: bool = True):
    """A cmux with at most one workspace, ``myproject-alpha``, as ``fn(*args)``.

    Answers the text forms the layout verbs read and the ``--json --id-format
    uuids`` forms the id read uses, from one state that the creating verbs
    change. The JSON is the shape of real cmux 0.61 output, trimmed. With
    ``json_forms`` off, the JSON forms get no answer.
    """
    state = {"workspace": workspace, "panes": panes}

    def fn(*args):
        if args[:3] == ("--json", "--id-format", "uuids"):
            if not json_forms:
                return None
            if args[3] == "list-workspaces":
                listed = (
                    [{"title": "myproject-alpha", "id": _WS_UUID, "index": 0}]
                    if state["workspace"]
                    else []
                )
                return json.dumps({"workspaces": listed})
            if args[3] == "list-panes" and args[5] == _WS_UUID:
                return json.dumps(
                    {
                        "workspace_id": _WS_UUID,
                        "panes": [
                            {"id": _PANE_UUIDS[i], "index": i}
                            for i in range(state["panes"])
                        ],
                    }
                )
            return None
        if args[0] == "list-workspaces":
            return "  workspace:1  myproject-alpha" if state["workspace"] else ""
        if args[0] == "new-workspace":
            state["workspace"], state["panes"] = True, 1
            return "OK workspace:1"
        if args[0] == "list-panes":
            return " ".join(f"pane:{i}" for i in range(state["panes"]))
        if args[0] == "new-split":
            state["panes"] += 1
            return "OK surface:90 workspace:1"
        if args[0] == "list-pane-surfaces":
            return f'  surface:{args[2][5:]}0  terminal  "shell"'
        return "OK"

    return fn


_SHELL_URL = f"cmux://workspace/{_WS_UUID}/pane/{_PANE_UUIDS[1]}"


class TestWorktreeShellUrls:
    def test_url_for_each_worktree_with_a_shell_pane(self):
        client = RecordingCmuxClient(_fake_cmux(workspace=True, panes=2))
        with patch.object(mael_layout, "current_client", lambda: client):
            urls = mael_layout.worktree_shell_urls(
                [("myproject", "alpha"), ("myproject", "bravo")]
            )
        assert urls == {("myproject", "alpha"): _SHELL_URL}

    def test_no_url_without_the_shell_pane(self):
        client = RecordingCmuxClient(_fake_cmux(workspace=True, panes=1))
        with patch.object(mael_layout, "current_client", lambda: client):
            assert mael_layout.worktree_shell_urls([("myproject", "alpha")]) == {}

    def test_empty_outside_cmux(self):
        with patch.object(mael_layout, "current_client", lambda: None):
            assert mael_layout.worktree_shell_urls([("myproject", "alpha")]) == {}


class TestCreateWorktreeTerminal:
    def test_live_pane_is_reused_without_a_new_tab(self):
        client, patcher = _patch_current(_fake_cmux(workspace=True, panes=2))
        with patcher:
            url = mael_layout.create_worktree_terminal("myproject", "alpha", "/wt")
        assert url == _SHELL_URL
        assert not any(c[0] in ("new-split", "new-surface") for c in client.calls)

    def test_missing_workspace_is_created_with_the_shell_pane(self):
        client, patcher = _patch_current(_fake_cmux(workspace=False, panes=0))
        with patcher, patch("mael_domain.cmux.model.time.sleep"):
            url = mael_layout.create_worktree_terminal("myproject", "alpha", "/wt")
        assert url == _SHELL_URL
        assert ("new-workspace", "--command", "cd /wt") in client.calls
        # The new pane gets its cd and nothing else: no installer runs, since
        # the worktree is already installed.
        assert [c for c in client.calls if c[0] == "send"] == [
            (
                "send",
                "--surface",
                "surface:10",
                "--workspace",
                "workspace:1",
                "--",
                "cd /wt\n",
            )
        ]

    def test_live_workspace_gains_the_shell_pane(self):
        client, patcher = _patch_current(_fake_cmux(workspace=True, panes=1))
        with patcher, patch("mael_domain.cmux.model.time.sleep"):
            url = mael_layout.create_worktree_terminal("myproject", "alpha", "/wt")
        assert url == _SHELL_URL
        assert not any(c[0] == "new-workspace" for c in client.calls)

    def test_none_when_the_pane_ids_cannot_be_read(self):
        # A cmux that answers the text forms but not the JSON ones.
        _, patcher = _patch_current(
            _fake_cmux(workspace=True, panes=2, json_forms=False)
        )
        with patcher:
            assert (
                mael_layout.create_worktree_terminal("myproject", "alpha", "/wt")
                is None
            )

    def test_none_outside_cmux(self):
        with patch.object(CmuxLayout, "current", staticmethod(lambda n: None)):
            assert mael_layout.create_worktree_terminal("p", "a", "/wt") is None
