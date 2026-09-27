"""Tests for the cmux policy layer (mael_layout.py).

These drive :class:`MaelCmux` and :class:`WorktreeWorkspace` over a
:class:`FakeCmux` and assert the resulting workspace: its panes, tabs, the text
sent in, and what was focused.
"""

from unittest.mock import patch

from mael_domain.cmux import mael_layout
from mael_domain.cmux.api import FakeCmux
from mael_domain.cmux.client import RecordingCmuxClient
from mael_domain.cmux.mael_layout import MaelCmux
from mael_domain.cmux.model import TerminalTab

NAME = "myproject-alpha"
AGENT = TerminalTab("Claude", cwd="/wt", command="claude")


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


class TestOpenForAgent:
    """open_for_agent — create-vs-reuse business logic."""

    def test_create_path_builds_claude_and_shell(self):
        """No workspace → pane 0 gains Claude, pane 1 runs the installer."""
        cmux = FakeCmux()
        assert _worktree(cmux).open_for_agent(AGENT, install_cmd="npm i") is True
        assert cmux.tabs(NAME) == [["Claude", "Claude"], [""]]
        # The installer is sent before Claude starts.
        assert cmux.texts().index("npm i\n") < cmux.texts().index("claude\n")

    def test_reuse_path_adds_claude_tab_only(self):
        """A live workspace → a Claude tab in pane 0, and no second installer."""
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        assert _worktree(cmux).open_for_agent(AGENT, install_cmd="npm i") is True
        assert cmux.tabs(NAME) == [["Claude", "Claude"], ["Terminal"]]
        assert "npm i\n" not in cmux.texts()
        # The reused workspace is brought to the foreground.
        assert cmux.workspace_ref(NAME) in cmux.focused

    def test_false_when_the_workspace_cannot_be_made(self):
        cmux = FakeCmux()
        with patch.object(cmux, "new_workspace", return_value=None):
            assert _worktree(cmux).open_for_agent(AGENT, install_cmd="npm i") is False

    def test_false_when_the_tab_cannot_be_added(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        with patch.object(cmux, "new_surface", return_value=None):
            assert _worktree(cmux).open_for_agent(AGENT, install_cmd="npm i") is False


class TestPrepareInstallShellThenAddAgent:
    def test_the_installer_is_ready_before_the_agent(self):
        cmux = FakeCmux()
        workspace = _worktree(cmux)
        assert workspace.prepare_install_shell(install_cmd="uv sync") is True
        assert cmux.tabs(NAME) == [["Claude"], [""]]
        assert cmux.texts()[-1] == "uv sync\n"
        assert workspace.add_agent(AGENT) is True
        assert cmux.tabs(NAME) == [["Claude", "Claude"], [""]]

    def test_a_live_workspace_is_unchanged(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        assert _worktree(cmux).prepare_install_shell(install_cmd="uv sync") is True
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"]]
        assert cmux.sent == []


class TestOpenForShell:
    def test_create_runs_the_installer_in_the_initial_shell(self):
        cmux = FakeCmux()
        assert _worktree(cmux).open_for_shell(install_cmd="npm i") is True
        assert cmux.tabs(NAME) == [["Terminal"]]
        assert cmux.texts() == ["cd /wt\n", "npm i\n"]

    def test_reuse_adds_a_shell_without_running_the_installer(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        assert _worktree(cmux).open_for_shell(install_cmd="npm i") is True
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal", "Terminal"]]
        assert "npm i\n" not in cmux.texts()


class TestTerminalUrl:
    def test_the_first_terminals_pane_in_an_agent_workspace(self):
        cmux = FakeCmux().with_workspace(
            NAME, [["Claude"], ["Terminal"], [("browser", "http://x")]]
        )
        assert _worktree(cmux).terminal_url() == cmux.pane_link(NAME, 0)

    def test_a_browser_before_the_first_terminal_is_skipped(self):
        cmux = FakeCmux().with_workspace(
            NAME, [[("browser", "http://x")], ["Terminal"]]
        )
        assert _worktree(cmux).terminal_url() == cmux.pane_link(NAME, 1)

    def test_none_without_a_terminal(self):
        cmux = FakeCmux().with_workspace(NAME, [[("browser", "http://x")]])
        assert _worktree(cmux).terminal_url() is None

    def test_none_without_the_workspace(self):
        assert _worktree(FakeCmux().with_workspace("other")).terminal_url() is None


class TestEnsureTerminal:
    def test_a_missing_workspace_gets_one_terminal(self):
        cmux = FakeCmux()
        url = _worktree(cmux).ensure_terminal()
        assert url == cmux.pane_link(NAME, 0)
        # One terminal tab, no Claude tab, no installer, and no focus.
        assert cmux.tabs(NAME) == [["Terminal"]]
        assert cmux.texts() == ["cd /wt\n"]
        assert cmux.focused == []

    def test_a_live_workspace_gains_nothing(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        assert _worktree(cmux).ensure_terminal() == cmux.pane_link(NAME, 0)
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"]]
        assert cmux.sent == []

    def test_none_when_the_workspace_cannot_be_made(self):
        cmux = FakeCmux()
        with patch.object(cmux, "new_workspace", return_value=None):
            assert _worktree(cmux).ensure_terminal() is None


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


class TestAppBrowser:
    def test_show_opens_in_the_browser_pane(self):
        cmux = FakeCmux().with_workspace(NAME, [["Claude"], ["Terminal"]])
        ref = _worktree(cmux).show_app_browser("http://localhost:3000")
        assert ref is not None
        assert cmux.tabs(NAME)[2] == ["http://localhost:3000"]

    def test_hide_closes_the_matching_browser(self):
        cmux = FakeCmux().with_workspace(
            NAME, [["Claude"], ["Terminal"], [("browser", "http://localhost:3000")]]
        )
        assert _worktree(cmux).hide_app_browser("http://localhost:3000") is True
        assert cmux.tabs(NAME) == [["Claude"], ["Terminal"]]


class TestShowPrBrowser:
    def test_recycles_github_browser_in_place(self):
        cmux = FakeCmux().with_workspace(
            NAME,
            [["Claude"], ["Terminal"], [("browser", "https://github.com/o/r")]],
            current=True,
        )
        ref = MaelCmux(cmux).show_pr_browser("https://github.com/o/r/pull/9")
        assert ref is not None
        assert cmux.tabs(NAME)[2] == ["https://github.com/o/r/pull/9"]


class TestClose:
    def test_closes_matching(self):
        cmux = FakeCmux().with_workspace(NAME).with_workspace("other")
        assert _worktree(cmux, path=None).close() is True
        assert [w.title for w in cmux.list_workspaces()] == ["other"]

    def test_false_when_absent(self):
        assert _worktree(FakeCmux(), path=None).close() is False
