"""Policy layer for cmux integration — the only place that knows maelstrom.

Knows the maelstrom concepts the domain layer deliberately doesn't: the
``{project}-{worktree}`` workspace name, the standard 3-pane layout (pane 0 =
Claude, pane 1 = shell, pane 2 = browsers), and what to run (Claude, the install
command, app/PR URLs). Each function builds a :class:`~maelstrom.cmux.model.CmuxLayout`
and issues declarative assertion verbs. These are the functions the CLI call
sites invoke; they never touch the cmux mechanics directly.

Every function degrades silently outside cmux (``CmuxLayout.current()`` is
``None``), returning ``None``/``False``.
"""

from .model import BrowserTab, CmuxLayout, TerminalTab


# The standard 3-pane workspace layout.
CLAUDE_PANE = 0
SHELL_PANE = 1
BROWSER_PANE = 2

# github PRs all recycle the one github.com browser tab in the browser pane.
GITHUB_URL_PREFIX = "https://github.com"


def workspace_name(project: str, worktree: str) -> str:
    """Canonical cmux workspace name: ``{project}-{worktree}``."""
    return f"{project}-{worktree}"


def ensure_worktree_workspace(
    project: str,
    worktree: str,
    path: str,
    *,
    command: str,
    install_cmd: str | None,
) -> bool:
    """Place a worktree's 3-pane workspace: Claude (pane 0) + shell (pane 1).

    Two distinct cases:

    - **Live workspace** — add a fresh Claude tab to pane 0 and leave every other
      pane untouched (it already installed; no duplicate install).
    - **No workspace** — create it with Claude as pane 0's initial terminal, then
      split a shell pane (pane 1) running ``install_cmd``.

    Returns True if placed (in cmux mode), False otherwise (caller falls back to
    a process-replacing ``run_cmd``).
    """
    lay = CmuxLayout.current(workspace_name(project, worktree))
    if lay is None:
        return False

    claude = TerminalTab("Claude", cwd=path, command=command)
    if lay.has_workspace():
        lay.add_terminal(CLAUDE_PANE, claude)
    else:
        lay.ensure_workspace(claude)
        lay.ensure_terminal(
            SHELL_PANE, TerminalTab("Terminal", cwd=path, command=install_cmd),
        )
    return True


def show_app_browser(project: str, worktree: str, url: str) -> str | None:
    """Ensure the app URL is shown in the workspace's browser pane.

    Recycles an existing browser on the same URL prefix, else opens one in pane
    2. Returns the browser surface ref (stored as
    ``EnvState.cmux_browser_surface``), or ``None`` outside cmux.
    """
    lay = CmuxLayout.current(workspace_name(project, worktree))
    if lay is None:
        return None
    return lay.ensure_browser(BROWSER_PANE, BrowserTab(url))


def hide_app_browser(project: str, worktree: str, url: str) -> bool:
    """Close the app browser matching ``url`` in the workspace, if present."""
    lay = CmuxLayout.current(workspace_name(project, worktree))
    if lay is None:
        return False
    return lay.ensure_absent_browser(url)


def show_pr_browser(url: str) -> str | None:
    """Open/recycle the github browser tab in the current workspace's pane 2.

    Recycles by the ``github.com`` prefix so a PR/issue tab is navigated in
    place rather than recreated. Returns the surface ref, or ``None``.
    """
    lay = _current_layout()
    if lay is None:
        return None
    return lay.ensure_browser(
        BROWSER_PANE, BrowserTab(url, match=GITHUB_URL_PREFIX),
    )


def set_status(text: str) -> bool:
    """Set the cmux task status line. No-op (False) outside cmux."""
    lay = _current_layout()
    if lay is None:
        return False
    return lay.set_status(text)


def clear_status() -> bool:
    """Clear the cmux task status line. No-op (False) outside cmux."""
    lay = _current_layout()
    if lay is None:
        return False
    return lay.clear_status()


def close_workspace(project: str, worktree: str) -> bool:
    """Close the worktree's workspace, if present. No-op (False) otherwise."""
    lay = CmuxLayout.current(workspace_name(project, worktree))
    if lay is None:
        return False
    return lay.close()


def _current_layout() -> CmuxLayout | None:
    """A layout over the *current* workspace (name irrelevant for these verbs).

    Status and the github-browser verbs act on the caller's current workspace —
    cmux scopes them to ``$CMUX_WORKSPACE_ID`` — so the bound name is unused. We
    pass an empty name purely to satisfy the constructor.
    """
    return CmuxLayout.current("")
