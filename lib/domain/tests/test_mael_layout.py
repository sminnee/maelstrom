"""Tests for the cmux policy layer (mael_layout.py).

These drive :class:`MaelCmux` and :class:`WorktreeWorkspace` over a
:class:`FakeCmux` and assert the resulting workspace: its panes, tabs and the
command its terminal ran.
"""

from unittest.mock import patch

import pytest

from mael_domain.cmux import mael_layout
from mael_domain.cmux.api import FakeCmux
from mael_domain.cmux.client import RecordingCmuxClient
from mael_domain.cmux.mael_layout import MaelCmux
from mael_domain.ssh_target import SshTarget

NAME = "myproject-alpha"


def _worktree(cmux: FakeCmux, path: str | None = "/wt"):
    return MaelCmux(cmux).worktree("myproject", "alpha", path)


class TestWorkspaceName:
    def test_combines_project_and_worktree(self):
        assert mael_layout.workspace_name("maelstrom", "bravo") == "maelstrom-bravo"


class TestCurrent:
    def test_none_outside_cmux(self):
        with patch.object(mael_layout, "current_client", lambda: None):
            assert MaelCmux.current() is None

    def test_over_the_running_cmux_inside_it(self):
        client = RecordingCmuxClient(lambda *args: '{"workspaces": []}')
        with patch.object(mael_layout, "current_client", lambda: client):
            cmux = MaelCmux.current()
        assert cmux is not None
        assert cmux.worktree("myproject", "alpha").close() is False
        assert client.calls == [("--json", "--id-format", "both", "list-workspaces")]


class TestEnsureTerminal:
    def test_a_missing_workspace_gets_one_terminal(self):
        cmux = FakeCmux()
        url = _worktree(cmux).ensure_terminal()
        assert url == cmux.pane_link(NAME, 0)
        # One terminal tab that runs only the `cd`.
        assert cmux.tabs(NAME) == [["Terminal"]]
        assert cmux.commands == ["cd /wt"]

    def test_a_live_workspace_gains_nothing(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        assert _worktree(cmux).ensure_terminal() == cmux.pane_link(NAME, 0)
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"]]
        assert cmux.commands == []

    def test_a_browser_before_the_first_terminal_is_skipped(self):
        cmux = FakeCmux().with_workspace(
            NAME, [[("browser", "http://x")], ["Terminal"]]
        )
        assert _worktree(cmux).ensure_terminal() == cmux.pane_link(NAME, 1)

    def test_none_when_the_workspace_cannot_be_made(self):
        cmux = FakeCmux()
        with patch.object(cmux, "new_workspace", return_value=None):
            assert _worktree(cmux).ensure_terminal() is None


class TestSshTerminal:
    REMOTE = "myproject-alpha@monolith"
    TARGET = SshTarget("sam", "monolith.tail.ts.net", "/wt")

    def test_a_new_workspace_runs_the_ssh_command(self):
        cmux = FakeCmux()
        url = MaelCmux(cmux).ssh_terminal(self.REMOTE, self.TARGET)
        assert url == cmux.pane_link(self.REMOTE, 0)
        assert cmux.tabs(self.REMOTE) == [["Terminal"]]
        assert cmux.commands == [self.TARGET.command()]

    def test_a_second_call_reuses_the_workspace(self):
        cmux = FakeCmux()
        first = MaelCmux(cmux).ssh_terminal(self.REMOTE, self.TARGET)
        assert MaelCmux(cmux).ssh_terminal(self.REMOTE, self.TARGET) == first
        assert len(cmux.commands) == 1

    @pytest.mark.parametrize(
        ("name", "host", "expected"),
        [
            ("maelstrom-alpha", "monolith.ts.net", "maelstrom-alpha@monolith"),
            # Every tailnet IP starts 100., so an IP stays whole.
            ("maelstrom-alpha", "100.64.1.2", "maelstrom-alpha@100.64.1.2"),
            ("x; rm -rf ~", "monolith", None),
            ("a" * 101, "monolith", None),
        ],
    )
    def test_the_remote_name_holds_the_ssh_host(self, name, host, expected):
        target = SshTarget("sam", host, "/wt")
        assert mael_layout.remote_workspace_name(name, target) == expected


class TestTerminalUrls:
    def test_links_for_matched_worktrees_only(self):
        cmux = (
            FakeCmux()
            .with_workspace(NAME, [["Claude"], ["Terminal"]])
            .with_workspace("myproject-bravo", [[("browser", "http://x")]])
            .with_workspace("other-alpha")
        )
        urls = MaelCmux(cmux).terminal_urls(
            [("myproject", "alpha"), ("myproject", "bravo"), ("myproject", "charlie")]
        )
        # bravo has no terminal and charlie has no workspace: both are absent.
        assert urls == {("myproject", "alpha"): cmux.pane_link(NAME, 0)}

    def test_the_first_workspace_of_a_name_wins(self):
        cmux = FakeCmux().with_workspace(NAME).with_workspace(NAME)
        urls = MaelCmux(cmux).terminal_urls([("myproject", "alpha")])
        # pane_link names the first workspace of the title.
        assert urls == {("myproject", "alpha"): cmux.pane_link(NAME, 0)}

    def test_lists_workspaces_once(self):
        cmux = FakeCmux().with_workspace(NAME).with_workspace("myproject-bravo")
        with patch.object(
            cmux, "list_workspaces", wraps=cmux.list_workspaces
        ) as listed:
            MaelCmux(cmux).terminal_urls(
                [("myproject", "alpha"), ("myproject", "bravo")]
            )
        assert listed.call_count == 1


class TestClose:
    def test_closes_matching(self):
        cmux = FakeCmux().with_workspace(NAME).with_workspace("other")
        assert _worktree(cmux, path=None).close() is True
        assert [w.title for w in cmux.list_workspaces()] == ["other"]

    def test_false_when_absent(self):
        assert _worktree(FakeCmux(), path=None).close() is False
