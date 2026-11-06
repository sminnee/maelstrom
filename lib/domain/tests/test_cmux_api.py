"""Tests for the typed cmux API (api.py): the CLI parsing, and the fake's contract."""

from mael_domain.cmux.api import CliCmuxApi, FakeCmux, Surface, Workspace
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
        assert api.list_surfaces("workspace:1") == []

    def test_a_read_that_is_not_json_is_empty(self):
        api, _ = _api(lambda *args: "workspace:1  myproject-alpha")
        assert api.list_workspaces() == []


class TestCliWrites:
    """Each write sends the argv cmux expects."""

    def test_new_workspace(self):
        api, client = _api({("new-workspace", "--command", "cd /wt"): "OK workspace:1"})
        assert api.new_workspace("cd /wt") == "workspace:1"
        assert api.new_workspace("cd /other") is None
        assert client.calls[0] == ("new-workspace", "--command", "cd /wt")

    def test_the_other_writes(self):
        api, client = _api(lambda *args: "OK")
        assert api.rename_workspace("workspace:1", "myproject-alpha")
        assert api.rename_tab("surface:5", "Terminal")
        assert api.close_workspace("workspace:1")
        assert client.calls == [
            ("rename-workspace", "--workspace", "workspace:1", "myproject-alpha"),
            ("rename-tab", "--surface", "surface:5", "Terminal"),
            ("close-workspace", "--workspace", "workspace:1"),
        ]

    def test_a_failed_write_is_false(self):
        api, _ = _api()
        assert api.close_workspace("workspace:1") is False


class TestFakeCmux:
    """The behaviour that tests over the fake rely on."""

    def test_a_new_workspace_has_one_terminal(self):
        cmux = FakeCmux()
        ref = cmux.new_workspace("cd /wt")
        assert ref is not None
        (surface,) = cmux.list_surfaces(ref)
        assert surface.type == "terminal"
        assert cmux.commands == ["cd /wt"]

    def test_surfaces_are_listed_per_workspace(self):
        cmux = FakeCmux().with_workspace("a").with_workspace("b", [["x"], ["y"]])
        a, b = (w.ref for w in cmux.list_workspaces())
        assert [s.title for s in cmux.list_surfaces(a)] == ["Terminal"]
        assert [s.title for s in cmux.list_surfaces(b)] == ["x", "y"]
