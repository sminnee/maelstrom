"""Policy layer for cmux integration — the only place that knows maelstrom.

Knows the maelstrom concepts the lower layers deliberately don't: the
``{project}-{worktree}`` workspace name, the standard 3-pane layout (pane 0 =
Claude, pane 1 = shell, pane 2 = browsers), and what to run (Claude, the install
command, app/PR URLs).

:class:`MaelCmux` is maelstrom's view of one cmux. :class:`WorktreeWorkspace` is
one worktree's workspace, with its intents as methods: open it for an agent,
open it for a shell, link to its terminal. Each intent issues
:class:`~mael_domain.cmux.model.CmuxLayout` verbs, or reads the API directly
when it reads more than one workspace or only reads.

``MaelCmux.current()`` and ``MaelCmux.for_caller()`` return ``None`` when cmux
should not be driven; docs/dev/cmux.md "Outside cmux" says which to use. After
that, every method is non-fatal: it returns ``None``, ``False`` or an empty dict.
"""

import os
from collections.abc import Iterable

from mael_agent.harness_model import is_driven_agent

from .api import CliCmuxApi, CmuxApi, Workspace
from .client import current_client
from .model import BrowserTab, CmuxLayout, TerminalTab

# cmux sets this in each pane it runs.
WORKSPACE_ID_ENV = "CMUX_WORKSPACE_ID"

# The standard 3-pane workspace layout.
CLAUDE_PANE = 0
SHELL_PANE = 1
BROWSER_PANE = 2

# github PRs all recycle the one github.com browser tab in the browser pane.
GITHUB_URL_PREFIX = "https://github.com"


def workspace_name(project: str, worktree: str) -> str:
    """Canonical cmux workspace name: ``{project}-{worktree}``."""
    return f"{project}-{worktree}"


def _terminal_url(api: CmuxApi, workspace: Workspace) -> str | None:
    """The deep link to the pane of the workspace's first terminal tab."""
    terminal = next(
        (s for s in api.list_surfaces(workspace.ref) if s.type == "terminal"), None
    )
    if terminal is None:
        return None
    return f"cmux://workspace/{workspace.id}/pane/{terminal.pane_id}"


class MaelCmux:
    """Maelstrom's view of one cmux."""

    def __init__(self, api: CmuxApi) -> None:
        self._api = api

    @staticmethod
    def current() -> "MaelCmux | None":
        """The running cmux app, or ``None`` when none answers.

        Any process reaches it, in cmux or not; see docs/dev/cmux.md "Outside cmux".
        """
        client = current_client()
        if client is None:
            return None
        return MaelCmux(CliCmuxApi(client))

    @staticmethod
    def for_caller() -> "MaelCmux | None":
        """The running cmux, or ``None`` unless the caller runs in a cmux pane.

        A driven agent is never in a pane, whatever it inherited.
        """
        if not os.environ.get(WORKSPACE_ID_ENV) or is_driven_agent():
            return None
        return MaelCmux.current()

    def worktree(
        self, project: str, worktree: str, path: str | None = None
    ) -> "WorktreeWorkspace":
        """The workspace of one worktree. ``path`` is where its terminals start."""
        return WorktreeWorkspace(self._api, project, worktree, path)

    def terminal_urls(
        self, worktrees: Iterable[tuple[str, str]]
    ) -> dict[tuple[str, str], str]:
        """Each worktree's terminal link, by ``(project, worktree)``.

        A worktree with no workspace, or no terminal, is absent. One
        ``list-workspaces`` for all of them, then one ``list-panels`` per match.
        """
        wanted = {workspace_name(p, w): (p, w) for p, w in worktrees}
        found: dict[str, Workspace] = {}
        # Reversed, so the first workspace of a name wins, as in CmuxLayout.
        for workspace in reversed(self._api.list_workspaces()):
            if workspace.title in wanted:
                found[workspace.title] = workspace
        urls: dict[tuple[str, str], str] = {}
        for name, workspace in found.items():
            url = _terminal_url(self._api, workspace)
            if url is not None:
                urls[wanted[name]] = url
        return urls

    def show_pr_browser(self, url: str) -> str | None:
        """Open or recycle the github browser tab in the caller's workspace.

        Recycles by the ``github.com`` prefix so a PR/issue tab is navigated in
        place rather than recreated. Returns the surface ref, or ``None``. The
        browser verbs act on the caller's workspace, so the layout needs no name.
        """
        return CmuxLayout(self._api, "").ensure_browser(
            BROWSER_PANE, BrowserTab(url, match=GITHUB_URL_PREFIX)
        )


class WorktreeWorkspace:
    """One worktree's cmux workspace, by its intents.

    Full agentic development: :meth:`open_for_agent`, or
    :meth:`prepare_install_shell` then :meth:`add_agent`. Terminal access only:
    :meth:`open_for_shell` for ``mael add --no-agent``, :meth:`ensure_terminal`
    and :meth:`terminal_url` for the orchestrator.
    """

    def __init__(
        self, api: CmuxApi, project: str, worktree: str, path: str | None
    ) -> None:
        self._api = api
        self._name = workspace_name(project, worktree)
        self._path = path
        self._layout = CmuxLayout(api, self._name)

    # === full agentic development ===

    def open_for_agent(self, agent: TerminalTab, install_cmd: str | None) -> bool:
        """Place the agent's tab in pane 0, and the installer shell in pane 1.

        A live workspace gains only the agent tab: its first install already
        ran. True only when the agent tab was placed. The installer shell is
        best-effort.
        """
        if not self.prepare_install_shell(install_cmd):
            return False
        return self.add_agent(agent)

    def prepare_install_shell(self, install_cmd: str | None) -> bool:
        """Make the workspace, with the installer running in pane 1.

        A live workspace is unchanged. Its first install already ran.
        """
        if self._layout.has_workspace():
            return True
        if self._layout.ensure_workspace(TerminalTab("Claude", cwd=self._path)) is None:
            return False
        self._layout.ensure_terminal(
            SHELL_PANE, TerminalTab("Terminal", cwd=self._path, command=install_cmd)
        )
        return True

    def add_agent(self, agent: TerminalTab) -> bool:
        """Add an agent tab to pane 0, after the installer shell is ready."""
        return self._layout.add_terminal(CLAUDE_PANE, agent) is not None

    # === terminal access only ===

    def open_for_shell(self, install_cmd: str | None) -> bool:
        """Focus a workspace with one shell.

        A new workspace runs its installer in that shell. A live workspace
        gains a shell tab in pane 1 and no second installer.
        """
        if self._layout.has_workspace():
            tab = TerminalTab("Terminal", cwd=self._path)
            return self._layout.add_terminal(SHELL_PANE, tab) is not None
        tab = TerminalTab("Terminal", cwd=self._path, command=install_cmd)
        return self._layout.ensure_workspace(tab) is not None

    def ensure_terminal(self) -> str | None:
        """The terminal link, after making the workspace if it is missing.

        A new workspace has one terminal tab in the worktree. It has no Claude
        tab and runs no installer: a worktree the orchestrator shows is already
        installed. Nothing is focused; the link does that. ``None`` when cmux
        fails.
        """
        workspace = self._layout.workspace()
        if workspace is None:
            tab = TerminalTab("Terminal", cwd=self._path)
            if self._layout.ensure_workspace(tab) is None:
                return None
            workspace = self._layout.workspace()
        return _terminal_url(self._api, workspace) if workspace else None

    def terminal_url(self) -> str | None:
        """The link to the pane of the workspace's first terminal tab."""
        workspace = self._layout.workspace()
        return _terminal_url(self._api, workspace) if workspace else None

    # === browsers and teardown ===

    def show_app_browser(self, url: str) -> str | None:
        """Show the app URL in the browser pane.

        Recycles a browser on the same URL prefix, else opens one in pane 2.
        Returns the browser surface ref (stored as
        ``EnvState.cmux_browser_surface``).
        """
        return self._layout.ensure_browser(BROWSER_PANE, BrowserTab(url))

    def hide_app_browser(self, url: str) -> bool:
        """Close the app browser matching ``url``, if present."""
        return self._layout.ensure_absent_browser(url)

    def close(self) -> bool:
        """Close the workspace, if present. No-op (False) otherwise."""
        return self._layout.close()
