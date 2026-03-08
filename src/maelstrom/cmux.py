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


def create_cmux_workspace(
    project: str, worktree: str, worktree_path: str,
) -> str | None:
    """Create a cmux workspace for a worktree.

    Creates a workspace running claude, renames it to {project}-{worktree},
    then opens a second terminal pane and cds into the worktree path.

    Returns the workspace ref string, or None on failure.
    """
    # Create workspace with claude as the initial command
    workspace_ref = is_ok(cmux_cmd(
        "new-workspace",
        "--command", f"cd {worktree_path}",
    ))
    if not workspace_ref:
        return None

    cmux_cmd("send", "--workspace", workspace_ref, "--", "claude\n")

    # Rename workspace
    workspace_name = f"{project}-{worktree}"
    cmux_cmd("rename-workspace", "--workspace", workspace_ref, workspace_name)

    # Open a second terminal pane to the right
    pane_ref = is_ok(cmux_cmd(
        "new-pane", "--workspace", workspace_ref,
        "--type", "terminal", "--direction", "right",
    ))
    if pane_ref:
        cmux_cmd("send", "--surface", pane_ref, "--", f"cd {worktree_path}\n")
        cmux_cmd("rename-surface", "--surface", pane_ref, "Terminal")

    # Focus the new workspace's first pane
    panes = cmux_cmd("list-panes", "--workspace", workspace_ref)
    if panes and (matches := re.search('(pane:[0-9]+)', panes)):
        cmux_cmd("focus-pane", "--pane", matches.group(1),"--workspace", workspace_ref)

    return workspace_ref


def open_browser_pane(url: str, workspace_ref: str | None = None) -> str | None:
    """Open a browser pane with the given URL.

    Returns the surface ref string, or None on failure.
    """
    args = ["new-pane", "--type", "browser", "--url", url]
    if workspace_ref:
        args.extend(["--workspace", workspace_ref])
    return is_ok(cmux_cmd(*args))


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
    """Close cmux workspaces matching the given name.

    Parses list-workspaces output to find workspaces by name, then closes them.
    Returns True if any workspace was closed, False otherwise.
    """
    output = cmux_cmd("list-workspaces")
    if not output:
        return False

    closed = False
    for line in output.splitlines():
        # Lines like: * workspace:13  maelstrom-bravo  [selected]
        match = re.match(r'.*?(workspace:\d+)\s+(\S+)', line)
        if match and match.group(2) == name:
            result = cmux_cmd("close-workspace", "--workspace", match.group(1))
            if is_ok(result) is not None:
                closed = True

    return closed


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
        result = cmux_cmd("browser", "get-url", "--surface", panel.ref)
        ref = is_ok(result)
        return ref if ref else None

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

    def ensure_browser(self, url: str) -> str | None:
        """Return ref of a browser already showing this app's URL, or open a new one.

        Matches by URL prefix — e.g. url="http://localhost:3000" matches
        "http://localhost:3000/dashboard". Ignores unrelated browser panels.
        """
        existing = self.find_browser_by_url(url)
        if existing:
            return existing.ref
        ref = open_browser_pane(url)
        if ref:
            self.refresh()
        return ref

    def close_browser(self, url: str) -> bool:
        """Close the browser panel matching the given URL prefix, if one exists."""
        browser = self.find_browser_by_url(url)
        if browser:
            result = close_surface(browser.ref)
            self.refresh()
            return result
        return False
