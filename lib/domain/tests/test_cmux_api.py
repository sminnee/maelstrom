"""Tests for the typed cmux API (api.py): the CLI parsing, and the fake's contract."""

from unittest.mock import patch

import pytest

from mael_domain.cmux.api import CliCmuxApi, FakeCmux, Pane, Surface, Workspace
from mael_domain.cmux.client import RecordingCmuxClient

_JSON = ("--json", "--id-format", "both")

# Replies copied from cmux 0.61 ``--json --id-format both`` output, trimmed.
_WORKSPACES_JSON = """{
  "workspaces" : [
    {
      "index" : 0,
      "title" : "myproject-alpha",
      "ref" : "workspace:79",
      "selected" : true,
      "id" : "41093E35-52B6-4299-A8FC-77A44457E555",
      "pinned" : false
    },
    {
      "title" : "myproject-bravo",
      "pinned" : false,
      "ref" : "workspace:75",
      "index" : 1,
      "id" : "4D510C9F-5AE9-419B-B13A-F36057732685",
      "selected" : false
    },
    {"title" : "no ref", "id" : "0FFABC6C-AF74-47E1-89BF-39EE7F1A0E2E"}
  ]
}"""

_PANES_JSON = """{
  "workspace_ref" : "workspace:79",
  "panes" : [
    {
      "selected_surface_ref" : "surface:535",
      "id" : "D4C9DDF2-8B5D-4D7F-A87D-71F27D88274A",
      "ref" : "pane:241",
      "index" : 1,
      "surface_refs" : ["surface:535"]
    },
    {
      "selected_surface_ref" : "surface:536",
      "focused" : true,
      "id" : "E1551BFA-B9A9-4E5A-863B-053B3E937AD9",
      "ref" : "pane:240",
      "index" : 0,
      "surface_refs" : ["surface:534", "surface:536"]
    }
  ]
}"""

_PANELS_JSON = """{
  "workspace_ref" : "workspace:79",
  "surfaces" : [
    {
      "pane_id" : "FB0A869D-3616-44CE-931D-67C2405EEB20",
      "title" : "[DEV] Sign in",
      "pane_ref" : "pane:242",
      "ref" : "surface:538",
      "type" : "browser",
      "id" : "AB4D347F-2994-46C1-8C23-25D9AA9D6BD6",
      "index_in_pane" : 0,
      "index" : 3
    },
    {
      "ref" : "surface:534",
      "id" : "786C64B9-1447-4576-B5C0-76136D876FE8",
      "type" : "terminal",
      "index_in_pane" : 0,
      "pane_id" : "E1551BFA-B9A9-4E5A-863B-053B3E937AD9",
      "pane_ref" : "pane:240",
      "title" : "…/Projects/myproject/myproject-alpha",
      "index" : 0
    }
  ]
}"""


@pytest.fixture(autouse=True)
def _no_split_settle():
    with patch("mael_domain.cmux.api.time.sleep"):
        yield


def _api(responses=None):
    client = RecordingCmuxClient(responses)
    return CliCmuxApi(client), client


class TestCliReads:
    def test_list_workspaces(self):
        api, _ = _api({(*_JSON, "list-workspaces"): _WORKSPACES_JSON})
        # An entry with no ref is dropped.
        assert api.list_workspaces() == [
            Workspace(
                "workspace:79",
                "41093E35-52B6-4299-A8FC-77A44457E555",
                "myproject-alpha",
            ),
            Workspace(
                "workspace:75",
                "4D510C9F-5AE9-419B-B13A-F36057732685",
                "myproject-bravo",
            ),
        ]

    def test_list_panes_left_to_right(self):
        api, _ = _api(
            {(*_JSON, "list-panes", "--workspace", "workspace:79"): _PANES_JSON}
        )
        assert api.list_panes("workspace:79") == [
            Pane("pane:240", "E1551BFA-B9A9-4E5A-863B-053B3E937AD9", 0, "surface:536"),
            Pane("pane:241", "D4C9DDF2-8B5D-4D7F-A87D-71F27D88274A", 1, "surface:535"),
        ]

    def test_list_panes_of_the_callers_workspace(self):
        api, client = _api()
        api.list_panes(None)
        assert client.calls == [(*_JSON, "list-panes")]

    def test_a_pane_with_no_selected_surface_gives_its_first(self):
        reply = '{"panes": [{"ref": "pane:1", "id": "P", "index": 0,'
        reply += ' "surface_refs": ["surface:7", "surface:8"]}]}'
        api, _ = _api(lambda *args: reply)
        assert api.list_panes(None) == [Pane("pane:1", "P", 0, "surface:7")]

    def test_list_surfaces_in_workspace_order(self):
        api, _ = _api(
            {(*_JSON, "list-panels", "--workspace", "workspace:79"): _PANELS_JSON}
        )
        assert api.list_surfaces("workspace:79") == [
            Surface(
                "surface:534",
                "786C64B9-1447-4576-B5C0-76136D876FE8",
                "terminal",
                "…/Projects/myproject/myproject-alpha",
                "pane:240",
                "E1551BFA-B9A9-4E5A-863B-053B3E937AD9",
            ),
            Surface(
                "surface:538",
                "AB4D347F-2994-46C1-8C23-25D9AA9D6BD6",
                "browser",
                "[DEV] Sign in",
                "pane:242",
                "FB0A869D-3616-44CE-931D-67C2405EEB20",
            ),
        ]

    def test_a_failed_read_is_empty(self):
        api, _ = _api()
        assert api.list_workspaces() == []
        assert api.list_panes("workspace:1") == []
        assert api.list_surfaces(None) == []
        assert api.browser_url("surface:1") is None

    def test_a_read_that_is_not_json_is_empty(self):
        api, _ = _api(lambda *args: "workspace:1  myproject-alpha")
        assert api.list_workspaces() == []
        assert api.list_panes(None) == []

    def test_browser_url(self):
        api, _ = _api(
            {("browser", "get-url", "--surface", "surface:5"): "http://localhost:3000"}
        )
        assert api.browser_url("surface:5") == "http://localhost:3000"


class TestCliWrites:
    """Each write sends the argv the layout has always sent."""

    def test_new_workspace(self):
        api, client = _api({("new-workspace", "--command", "cd /wt"): "OK workspace:1"})
        assert api.new_workspace("cd /wt") == "workspace:1"
        assert api.new_workspace("cd /other") is None
        assert client.calls[0] == ("new-workspace", "--command", "cd /wt")

    def test_new_surface_terminal(self):
        api, client = _api(lambda *args: "OK surface:99 pane:0 workspace:13")
        assert api.new_surface("terminal", "pane:0", "workspace:13") == "surface:99"
        assert client.calls == [
            (
                "new-surface",
                "--type",
                "terminal",
                "--pane",
                "pane:0",
                "--workspace",
                "workspace:13",
            )
        ]

    def test_new_surface_browser(self):
        api, client = _api(lambda *args: "OK surface:200 pane:2 workspace:13")
        assert api.new_surface("browser", "pane:2", None, url="http://x") == (
            "surface:200"
        )
        assert client.calls == [
            (
                "new-surface",
                "--type",
                "browser",
                "--pane",
                "pane:2",
                "--url",
                "http://x",
            )
        ]

    def test_new_surface_failure(self):
        api, _ = _api()
        assert api.new_surface("terminal", None, "workspace:1") is None

    def test_send(self):
        api, client = _api(lambda *args: "OK")
        assert api.send("surface:9", "workspace:1", "ls\n")
        assert api.send(None, "workspace:1", "claude\n")
        assert client.calls == [
            (
                "send",
                "--surface",
                "surface:9",
                "--workspace",
                "workspace:1",
                "--",
                "ls\n",
            ),
            ("send", "--workspace", "workspace:1", "--", "claude\n"),
        ]

    def test_the_other_writes(self):
        api, client = _api(lambda *args: "OK")
        assert api.rename_workspace("workspace:1", "myproject-alpha")
        assert api.new_split("surface:5", "right", "workspace:1")
        assert api.rename_tab("surface:5", "Claude")
        assert api.browser_goto("surface:5", "http://x")
        assert api.close_surface("surface:5")
        assert api.close_workspace("workspace:1")
        assert api.select_workspace("workspace:1")
        assert api.focus_pane("pane:0", "workspace:1")
        assert api.focus_surface("surface:5", None)
        assert client.calls == [
            ("rename-workspace", "--workspace", "workspace:1", "myproject-alpha"),
            (
                "new-split",
                "right",
                "--surface",
                "surface:5",
                "--workspace",
                "workspace:1",
            ),
            ("rename-tab", "--surface", "surface:5", "Claude"),
            ("browser", "--surface", "surface:5", "goto", "http://x"),
            ("close-surface", "--surface", "surface:5"),
            ("close-workspace", "--workspace", "workspace:1"),
            ("select-workspace", "--workspace", "workspace:1"),
            ("focus-pane", "--pane", "pane:0", "--workspace", "workspace:1"),
            ("focus-panel", "--panel", "surface:5"),
        ]

    def test_a_failed_write_is_false(self):
        api, _ = _api()
        assert api.send("surface:1", None, "x") is False
        assert api.close_workspace("workspace:1") is False


class TestFakeCmux:
    """The behaviour that tests over the fake rely on."""

    def test_a_new_workspace_has_one_terminal(self):
        cmux = FakeCmux()
        ref = cmux.new_workspace("cd /wt")
        assert ref is not None
        (pane,) = cmux.list_panes(ref)
        (surface,) = cmux.list_surfaces(ref)
        assert surface.type == "terminal"
        assert pane.selected_surface == surface.ref
        assert cmux.sent == [(surface.ref, "cd /wt\n")]

    def test_a_split_adds_a_pane_with_one_terminal(self):
        cmux = FakeCmux().with_workspace("w", [["Claude"], ["Terminal"]])
        ref = cmux.list_workspaces()[0].ref
        first = cmux.list_panes(ref)[0].selected_surface
        assert first is not None
        assert cmux.new_split(first, "right", ref)
        assert cmux.tabs("w") == [["Claude"], [""], ["Terminal"]]

    def test_closing_a_panes_last_tab_removes_the_pane(self):
        cmux = FakeCmux().with_workspace("w", [["Claude"], ["Terminal", "Terminal"]])
        assert cmux.close_surface(cmux.surface_ref("w", 1, 1))
        assert cmux.tabs("w") == [["Claude"], ["Terminal"]]
        assert cmux.close_surface(cmux.surface_ref("w", 1))
        assert cmux.tabs("w") == [["Claude"]]

    def test_a_surface_in_another_workspace_is_not_found(self):
        cmux = FakeCmux().with_workspace("a").with_workspace("b")
        a, b = (w.ref for w in cmux.list_workspaces())
        surface = cmux.list_surfaces(a)[0].ref
        assert cmux.send(surface, b, "ls\n") is False
        assert cmux.send(surface, a, "ls\n") is True

    def test_none_is_the_current_workspace(self):
        cmux = FakeCmux().with_workspace("a").with_workspace("b", current=True)
        assert [s.ref for s in cmux.list_surfaces(None)] == [
            s.ref for s in cmux.list_surfaces(cmux.list_workspaces()[1].ref)
        ]
