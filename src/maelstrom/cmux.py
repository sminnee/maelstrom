"""cmux integration for maelstrom.

When running inside cmux (detected via CMUX_SOCKET_PATH env var),
maelstrom can create workspaces, open browser panes, and manage
terminal panes through the cmux CLI.

All operations are non-fatal; callers fall back to normal behavior on failure.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass


GITHUB_URL_PREFIX = "https://github.com"

# Browsers all live in the third pane of the standard 3-pane workspace layout
# (pane 1 = Claude, pane 2 = shell, pane 3 = browsers).
BROWSER_PANE_INDEX = 2


def is_cmux_mode() -> bool:
    """Return True if running inside cmux (CMUX_SOCKET_PATH is set)."""
    return bool(os.environ.get("CMUX_SOCKET_PATH"))


def _find_cmux_cli() -> str | None:
    """Find the cmux binary.

    Checks PATH first, then falls back to the macOS app bundle location.
    Returns the path to the binary or None if not found.
    """
    path = shutil.which("cmux")
    if path:
        return path

    app_path = "/Applications/cmux.app/Contents/Resources/bin/cmux"
    if os.path.isfile(app_path):
        return app_path

    return None


def cmux_cmd(*args: str) -> str | None:
    """Run a cmux command with --socket flag and parse the text response.

    cmux commands return plain text in "OK <ref>" format.
    Returns the ref string on success, empty string if just "OK", or None on failure.
    """
    cli = _find_cmux_cli()
    if cli is None:
        return None

    socket_path = os.environ.get("CMUX_SOCKET_PATH")
    if not socket_path:
        return None

    cmd = [cli, "--socket", socket_path, *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

def is_ok(result: str | None) -> str | None:
    """Check if a cmux command result is OK and extract the ref string."""

    if result and result.startswith("OK"):
        # "OK <ref>" -> return ref; "OK" -> return ""
        return result[3:] if len(result) > 2 else ""
    return None


def _first_ref(text: str | None, kind: str) -> str | None:
    """First `{kind}:N` ref in `text`, or None.

    cmux often replies with multiple refs, e.g. new-surface returns
    "surface:5 pane:2 workspace:1"; only the leading surface ref is a valid
    --surface handle.
    """
    if not text:
        return None
    match = re.search(rf'{kind}:\d+', text)
    return match.group(0) if match else None


def workspace_name(project: str, worktree: str) -> str:
    """Canonical cmux workspace name: '{project}-{worktree}'."""
    return f"{project}-{worktree}"


def find_workspace(name: str) -> str | None:
    """Workspace ref for `name` (first match), else None.

    Parses list-workspaces output, e.g. lines like:
      * workspace:13  maelstrom-bravo  [selected]
    """
    output = cmux_cmd("list-workspaces")
    if not output:
        return None

    for line in output.splitlines():
        match = re.match(r'.*?(workspace:\d+)\s+(\S+)', line)
        if match and match.group(2) == name:
            return match.group(1)
    return None


def list_panes(workspace_ref: str | None = None) -> list[str]:
    """Pane refs left→right, or [] if none / not in cmux mode.

    Uses re.findall (order-preserving) so it works for both space- and
    newline-separated output.
    """
    args = ["list-panes"]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    output = cmux_cmd(*args)
    if not output:
        return []
    return re.findall(r'pane:\d+', output)


def pane_idx(workspace_ref: str | None = None, index: int = 0) -> str | None:
    """Pane at `index` (left→right; negatives from the right), or None if OOB."""
    panes = list_panes(workspace_ref)
    if -len(panes) <= index < len(panes):
        return panes[index]
    return None


def add_tab(
    workspace_ref: str, pane_ref: str | None = None, title: str | None = None,
) -> str | None:
    """New terminal surface (tab) in the workspace, optionally in `pane_ref`.

    Omits --pane when pane_ref is None (cmux uses its default pane).
    Renames the surface to `title` if given. Returns the surface ref, else None.
    """
    args = ["new-surface", "--type", "terminal"]
    if pane_ref:
        args.extend(["--pane", pane_ref])
    args.extend(["--workspace", workspace_ref])
    # new-surface replies "OK surface:N pane:N workspace:N" — pull out the
    # surface ref; the multi-token string is not a valid --surface handle.
    surface_ref = _first_ref(is_ok(cmux_cmd(*args)), "surface")
    if surface_ref is None:
        return None

    if title:
        cmux_cmd("rename-tab", "--surface", surface_ref, title)

    return surface_ref


def start_claude_in_surface(surface_ref: str, worktree_path: str) -> None:
    """send `cd <path>\\n` then `claude\\n` into the surface."""
    cmux_cmd("send", "--surface", surface_ref, "--", f"cd {worktree_path}\n")
    cmux_cmd("send", "--surface", surface_ref, "--", "claude\n")


def focus_pane(pane_ref: str, workspace_ref: str | None = None) -> None:
    """Focus a pane, optionally within a specific workspace."""
    args = ["focus-pane", "--pane", pane_ref]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    cmux_cmd(*args)


def select_workspace(workspace_ref: str) -> None:
    """Select (bring to foreground) a workspace."""
    cmux_cmd("select-workspace", "--workspace", workspace_ref)


def open_claude_tab(
    project: str, worktree: str, worktree_path: str,
) -> str | None:
    """Add a new Claude tab to the leftmost pane of an existing workspace.

    Composes the primitives: find the workspace by canonical name, add a
    terminal tab titled "Claude" to its leftmost pane, start claude in it,
    then focus the pane and select the workspace so the tab is visible.

    Returns the new surface ref, or None if the workspace isn't found / not
    in cmux mode / the tab couldn't be created.
    """
    workspace_ref = find_workspace(workspace_name(project, worktree))
    if workspace_ref is None:
        return None

    pane_ref = pane_idx(workspace_ref, 0)
    surface_ref = add_tab(workspace_ref, pane_ref=pane_ref, title="Claude")
    if surface_ref is None:
        return None

    start_claude_in_surface(surface_ref, worktree_path)

    if pane_ref:
        focus_pane(pane_ref, workspace_ref)
    select_workspace(workspace_ref)

    return surface_ref


def create_cmux_workspace(
    project: str, worktree: str, worktree_path: str, command: str = "claude",
) -> str | None:
    """Create a cmux workspace for a worktree.

    Creates a workspace running ``command`` (default ``claude``), renames it to
    {project}-{worktree}, then opens a second terminal pane and cds into the
    worktree path. ``command`` is sent verbatim, so callers wanting flags or an
    initial prompt must shell-quote them.

    Returns the workspace ref string, or None on failure.
    """
    # Create workspace with claude as the initial command
    workspace_ref = is_ok(cmux_cmd(
        "new-workspace",
        "--command", f"cd {worktree_path}",
    ))
    if not workspace_ref:
        return None

    cmux_cmd("send", "--workspace", workspace_ref, "--", f"{command}\n")

    # Rename workspace
    cmux_cmd(
        "rename-workspace", "--workspace", workspace_ref,
        workspace_name(project, worktree),
    )

    # Open a second terminal pane to the right. new-pane replies
    # "OK surface:N pane:N workspace:N" — the surface ref is what `send` and
    # `rename-tab` target.
    surface_ref = _first_ref(is_ok(cmux_cmd(
        "new-pane", "--workspace", workspace_ref,
        "--type", "terminal", "--direction", "right",
    )), "surface")
    if surface_ref:
        cmux_cmd("send", "--surface", surface_ref, "--", f"cd {worktree_path}\n")
        cmux_cmd("rename-tab", "--surface", surface_ref, "Terminal")

    # Focus the new workspace's first pane
    first_pane = pane_idx(workspace_ref, 0)
    if first_pane:
        focus_pane(first_pane, workspace_ref)

    return workspace_ref


def open_browser_pane(url: str, workspace_ref: str | None = None) -> str | None:
    """Open a browser pane with the given URL.

    Returns the surface ref string, or None on failure.
    """
    args = ["new-pane", "--type", "browser", "--url", url]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    return is_ok(cmux_cmd(*args))


def open_browser_surface(
    pane_ref: str, url: str, workspace_ref: str | None = None,
) -> str | None:
    """Open a browser tab in `pane_ref` and return its surface ref, or None.

    Mirrors add_tab but for browser surfaces. new-surface replies
    "OK surface:N pane:N workspace:N"; only the leading surface ref is usable.
    """
    args = ["new-surface", "--type", "browser", "--pane", pane_ref, "--url", url]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    return _first_ref(is_ok(cmux_cmd(*args)), "surface")


def focus_surface(surface_ref: str, workspace_ref: str | None = None) -> None:
    """Make a surface the visible tab via focus-panel (surfaces are panels)."""
    args = ["focus-panel", "--panel", surface_ref]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    cmux_cmd(*args)


def browser_surface_exists(surface_ref: str) -> bool:
    """Check if a cmux browser surface is still alive.

    Returns True if the surface exists, False otherwise (including on any error).
    """
    result = cmux_cmd("browser", "get-url", "--surface", surface_ref)
    return is_ok(result) is not None


def set_status(text: str) -> bool:
    """Set the cmux task status line.

    Returns True if the command succeeded, False otherwise (including not in cmux mode).
    """
    result = cmux_cmd("set-status", "task", text, "--icon", "hammer")
    return is_ok(result) is not None


def clear_status() -> bool:
    """Clear the cmux task status line.

    Returns True if the command succeeded, False otherwise (including not in cmux mode).
    """
    result = cmux_cmd("clear-status", "task")
    return is_ok(result) is not None


def close_workspace(name: str) -> bool:
    """Close the cmux workspace matching the given name.

    Finds the workspace by name and closes it.
    Returns True if a workspace was closed, False otherwise.
    """
    workspace_ref = find_workspace(name)
    if workspace_ref is None:
        return False

    result = cmux_cmd("close-workspace", "--workspace", workspace_ref)
    return is_ok(result) is not None


def close_surface(surface_ref: str) -> bool:
    """Close a cmux surface by its ref.

    Returns True if the command succeeded, False otherwise.
    """
    result = cmux_cmd("close-surface", "--surface", surface_ref)
    return is_ok(result) is not None


@dataclass
class CmuxPanel:
    ref: str           # e.g. "surface:183"
    panel_type: str    # "terminal" or "browser"
    title: str         # surface title
    focused: bool


def _parse_panels(output: str) -> list[CmuxPanel]:
    """Parse the output of cmux list-panels into CmuxPanel objects.

    Expected format (one per line):
      surface:103  terminal  "title"
    * surface:104  terminal  [focused]  "title"
      surface:183  browser  "title"
    """
    panels = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        focused = line.startswith("*")
        if focused:
            line = line[1:].strip()
        # Parse: ref  type  [focused]  "title"
        match = re.match(
            r'(surface:\d+)\s+(terminal|browser)\s+(?:\[focused\]\s+)?"(.*)"',
            line,
        )
        if match:
            panels.append(CmuxPanel(
                ref=match.group(1),
                panel_type=match.group(2),
                title=match.group(3),
                focused=focused,
            ))
    return panels


class CmuxWorkspace:
    """Queries and caches the panel state of the current cmux workspace."""

    def __init__(self):
        self._panels: list[CmuxPanel] | None = None  # lazy-loaded

    @staticmethod
    def current() -> "CmuxWorkspace | None":
        """Return a CmuxWorkspace if in cmux mode, else None."""
        if not is_cmux_mode():
            return None
        return CmuxWorkspace()

    @property
    def panels(self) -> list[CmuxPanel]:
        if self._panels is None:
            self._panels = _parse_panels(cmux_cmd("list-panels") or "")
        return self._panels

    def refresh(self):
        self._panels = None

    def browsers(self) -> list[CmuxPanel]:
        """Return all browser panels."""
        return [p for p in self.panels if p.panel_type == "browser"]

    def _get_browser_url(self, panel: CmuxPanel) -> str | None:
        """Get the current URL of a browser panel via `browser get-url`."""
        return cmux_cmd("browser", "get-url", "--surface", panel.ref)

    def find_browser_by_url(self, url_prefix: str) -> CmuxPanel | None:
        """Return the first browser panel whose URL starts with url_prefix, or None.

        Uses `browser get-url` to query each browser panel's actual URL.
        Other browser panels (docs, unrelated pages) are ignored.
        """
        for panel in self.browsers():
            panel_url = self._get_browser_url(panel)
            if panel_url and panel_url.startswith(url_prefix):
                return panel
        return None

    def open_in_browser_pane(self, url: str) -> str | None:
        """Open url as a browser tab in pane 3, creating pane 3 if needed.

        Pane 3 is the standard browser pane (BROWSER_PANE_INDEX). If it already
        exists, the browser opens as a tab there; otherwise the rightmost pane is
        focused and a new pane is split off to the right to become pane 3. The
        new browser is focused (made the visible tab). Returns its surface ref.
        """
        pane3 = pane_idx(index=BROWSER_PANE_INDEX)
        if pane3:
            ref = open_browser_surface(pane3, url)
        else:
            rightmost = pane_idx(index=-1)
            if rightmost:
                focus_pane(rightmost)
            ref = open_browser_pane(url)
        if ref:
            focus_surface(ref)        # make the new browser the visible tab
            self.refresh()
        return ref

    def ensure_browser(self, url: str) -> str | None:
        """Return ref of a browser already showing this app's URL, or open a new one.

        Matches by URL prefix — e.g. url="http://localhost:3000" matches
        "http://localhost:3000/dashboard". Ignores unrelated browser panels.
        New browsers open as a tab in pane 3 (the standard browser pane).
        """
        existing = self.find_browser_by_url(url)
        if existing:
            return existing.ref
        return self.open_in_browser_pane(url)

    def close_browser(self, url: str) -> bool:
        """Close the browser panel matching the given URL prefix, if one exists."""
        browser = self.find_browser_by_url(url)
        if browser:
            result = close_surface(browser.ref)
            self.refresh()
            return result
        return False

    def open_github_url(self, url: str) -> str | None:
        """Close any existing github.com browser, then open url in pane 3.

        Recycles by prefix: any browser whose current URL starts with
        https://github.com is closed first, so PR browsers don't accumulate.
        The replacement opens as a tab in pane 3 (the standard browser pane).
        Returns the new surface ref, or None on failure.
        """
        self.close_browser(GITHUB_URL_PREFIX)   # find+close-by-prefix; no-op if none
        return self.open_in_browser_pane(url)
