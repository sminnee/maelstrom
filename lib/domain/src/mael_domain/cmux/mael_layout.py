"""Policy layer for cmux integration — the only place that knows maelstrom.

It knows the ``{project}-{worktree}`` workspace name. The orchestrator makes a
worktree's workspace with one terminal and links to it; closing the worktree
closes the workspace.

``MaelCmux.current()`` returns ``None`` when no cmux app answers. After that,
every method is non-fatal: it returns ``None``, ``False`` or an empty dict.
"""

import shlex
from collections.abc import Iterable

from .api import CliCmuxApi, CmuxApi, Workspace
from .client import current_client

TERMINAL_TITLE = "Terminal"


def workspace_name(project: str, worktree: str) -> str:
    """Canonical cmux workspace name: ``{project}-{worktree}``."""
    return f"{project}-{worktree}"


def _first_terminal(api: CmuxApi, workspace: str):
    return next((s for s in api.list_surfaces(workspace) if s.type == "terminal"), None)


def _terminal_url(api: CmuxApi, workspace: Workspace) -> str | None:
    """The deep link to the pane of the workspace's first terminal tab."""
    terminal = _first_terminal(api, workspace.ref)
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

        Any process reaches it, in cmux or not; see docs/dev/cmux.md.
        """
        client = current_client()
        if client is None:
            return None
        return MaelCmux(CliCmuxApi(client))

    def worktree(
        self, project: str, worktree: str, path: str | None = None
    ) -> "WorktreeWorkspace":
        """The workspace of one worktree. ``path`` is where its terminal starts."""
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
        # Reversed, so the first workspace of a name wins.
        for workspace in reversed(self._api.list_workspaces()):
            if workspace.title in wanted:
                found[workspace.title] = workspace
        urls: dict[tuple[str, str], str] = {}
        for name, workspace in found.items():
            url = _terminal_url(self._api, workspace)
            if url is not None:
                urls[wanted[name]] = url
        return urls


class WorktreeWorkspace:
    """One worktree's cmux workspace: make it with one terminal, or close it."""

    def __init__(
        self, api: CmuxApi, project: str, worktree: str, path: str | None
    ) -> None:
        self._api = api
        self._name = workspace_name(project, worktree)
        self._path = path

    def ensure_terminal(self) -> str | None:
        """The terminal link, after making the workspace if it is missing.

        A new workspace has one terminal tab in the worktree and runs nothing:
        a worktree the orchestrator shows is already installed. Nothing is
        focused; the link does that. ``None`` when cmux fails.
        """
        workspace = self._workspace() or self._make_workspace()
        return _terminal_url(self._api, workspace) if workspace else None

    def close(self) -> bool:
        """Close the workspace, if present. No-op (False) otherwise."""
        workspace = self._workspace()
        if workspace is None:
            return False
        return self._api.close_workspace(workspace.ref)

    def _workspace(self) -> Workspace | None:
        """The named workspace: the first one with this title."""
        return next(
            (w for w in self._api.list_workspaces() if w.title == self._name), None
        )

    def _make_workspace(self) -> Workspace | None:
        """Make the named workspace, its one terminal in the worktree."""
        # The workspace's initial terminal runs only the `cd`.
        cd = f"cd {shlex.quote(self._path)}" if self._path is not None else ""
        ref = self._api.new_workspace(cd)
        if ref is None:
            return None
        self._api.rename_workspace(ref, self._name)
        terminal = _first_terminal(self._api, ref)
        if terminal is not None:
            self._api.rename_tab(terminal.ref, TERMINAL_TITLE)
        return self._workspace()
