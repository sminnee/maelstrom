"""Layout / domain layer for cmux integration.

Pure cmux mechanics over an injected :class:`~maelstrom.cmux.client.CmuxClient` —
**no maelstrom concepts** (no ``{project}-{worktree}`` naming, no Claude/install
knowledge, no "pane 2 is the browser" convention; that all lives in the policy
layer, ``mael_layout.py``).

The public surface is :class:`CmuxLayout`, which performs **partial, idempotent
reconciliation via discrete assertion verbs**. Each verb asserts a constraint
about a *subset* of one workspace, computes the delta for just that subset,
applies it, and is a no-op when the constraint already holds. Everything not
named by a verb is left untouched — so callers never describe the whole tree and
the layout never gets "weird" when the user has opened other things.

All verbs are non-fatal: they return a ref / bool and never raise.
``CmuxLayout.current()`` returns ``None`` outside cmux.
"""

import re
import time
from dataclasses import dataclass
from typing import Literal

from .client import CmuxClient, current_client


# cmux splits inherit the source pane's *current* cwd; give the workspace's
# initial `cd {path}` time to land so a freshly-split pane starts in that cwd
# rather than wherever the command was invoked from. 0.25s is long enough for a
# shell to process a `cd` without a noticeable hang; the per-pane `cd` send in
# ensure_terminal is a fallback for slower shells.
_PANE_CD_SETTLE_SECONDS = 0.25


# --- value objects: inert specs (write side; no I/O, no maelstrom concepts) ---


@dataclass(frozen=True)
class TerminalTab:
    """A desired terminal tab: title, optional starting cwd, optional command.

    ``command`` is sent verbatim into the surface (shell-quoted by the caller).
    """

    title: str
    cwd: str | None = None
    command: str | None = None


@dataclass(frozen=True)
class BrowserTab:
    """A desired browser tab.

    ``match`` is the URL prefix used to recycle an existing browser by
    navigating it in place; it defaults to ``url``.
    """

    url: str
    match: str | None = None

    @property
    def match_prefix(self) -> str:
        return self.match if self.match is not None else self.url


# --- value objects: parsed state (read side; were CmuxPanel etc.) ---


@dataclass(frozen=True)
class Surface:
    ref: str
    type: Literal["terminal", "browser"]
    title: str
    focused: bool


@dataclass(frozen=True)
class Pane:
    ref: str


@dataclass(frozen=True)
class Workspace:
    ref: str
    name: str


class CmuxLayout:
    """Change operations over a single named cmux workspace.

    Bound to a workspace *name* plus an injected client. Two families of verb:

    - ``ensure_*`` — a **presence** assertion: guarantee *at least one* of the
      named entity matching the spec exists. Creates one if none does; no-op if
      one already does. Idempotent. (The dual ``ensure_absent_*`` guarantees
      *zero* — removes if present.)
    - ``add_*`` — **unconditionally add a new** entity (e.g. a second terminal
      tab). Not idempotent; this is the explicit "I want another one" operation.

    A cmux workspace always has at least one terminal surface, so creating a
    workspace (or splitting a new pane) *is* placing its first terminal: the new
    initial surface is reused for that terminal rather than left idle. The
    private methods below carry the cmux mechanics (focus-safety, surface refs,
    the settle sleep).
    """

    def __init__(self, client: CmuxClient, workspace_name: str) -> None:
        self._client = client
        self._name = workspace_name

    @staticmethod
    def current(workspace_name: str) -> "CmuxLayout | None":
        """A layout over the current cmux client, or ``None`` outside cmux."""
        client = current_client()
        if client is None:
            return None
        return CmuxLayout(client, workspace_name)

    # === workspace existence ===

    def has_workspace(self) -> bool:
        """True if the named workspace exists."""
        return self._find_workspace() is not None

    def ensure_workspace(self, tab: TerminalTab) -> str | None:
        """Ensure the named workspace exists; create it if absent, else no-op.

        A workspace can't exist without a terminal, so creating one *is* placing
        its first terminal: the new workspace's initial surface is reused for
        ``tab`` (named, with its command sent in) rather than left idle. Returns
        the workspace ref, or ``None`` on failure / outside cmux.
        """
        existing = self._find_workspace()
        if existing is not None:
            return existing

        # Create running just the `cd` so the workspace's one initial terminal
        # lands in the worktree; the tab's command is then sent into that same
        # surface, and it is renamed to the tab title.
        cd = f"cd {tab.cwd}" if tab.cwd is not None else ""
        result = self._client.run("new-workspace", "--command", cd)
        workspace_ref = result.text or None if result.ok else None
        if workspace_ref is None:
            return None
        self._client.run("rename-workspace", "--workspace", workspace_ref, self._name)
        # `send --workspace` targets the workspace's active (initial) surface.
        if tab.command is not None:
            self._send_to_workspace(workspace_ref, f"{tab.command}\n")
        self._rename_pane_tab(workspace_ref, 0, tab.title)
        return workspace_ref

    # === terminal tabs by pane index ===

    def ensure_terminal(self, pane_index: int, tab: TerminalTab) -> str | None:
        """Ensure *at least one* terminal exists at pane ``pane_index``.

        If the pane already exists, it already has a terminal — no-op (returns
        that pane's surface). If it doesn't, split a new pane and reuse its
        initial surface for ``tab`` (send the command, rename) rather than adding
        a second tab. Returns the surface ref, or ``None``.
        """
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return None

        pane_ref = self._pane_at_index(workspace_ref, pane_index)
        if pane_ref is not None:
            # Pane present → at least one terminal already exists here.
            return self._pane_surface(pane_ref, workspace_ref)

        # The split inherits its initial cwd from the source pane's shell, which
        # may not have finished its own `cd` yet — let it settle *before* the
        # split so the new pane lands in the worktree. The per-pane `cd` in
        # _run_tab below is a further fallback for slow shells.
        time.sleep(_PANE_CD_SETTLE_SECONDS)
        new_pane = self._split_new_pane(workspace_ref)
        if new_pane is None:
            return None
        surface_ref = self._pane_surface(new_pane, workspace_ref)
        if surface_ref is None:
            return None
        self._run_tab(surface_ref, tab, workspace_ref)
        return surface_ref

    def add_terminal(self, pane_index: int, tab: TerminalTab) -> str | None:
        """Unconditionally add a *new* terminal tab to pane ``pane_index``.

        The explicit "I want another terminal" operation (not idempotent). The
        pane must already exist; returns the new surface ref, or ``None``. The
        new tab is brought to the front so the GUI shows it.
        """
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return None
        pane_ref = self._pane_at_index(workspace_ref, pane_index)
        if pane_ref is None:
            return None

        surface_ref = self._add_terminal_tab(workspace_ref, pane_ref, tab.title)
        if surface_ref is None:
            return None
        self._run_tab(surface_ref, tab, workspace_ref)
        # Bring the (possibly background) workspace to the foreground, focus the
        # pane, then bring the new tab itself to the front — add_tab selects it in
        # the pane's data model, but the GUI keeps the previously-active tab
        # visible until we focus this surface (a panel) explicitly.
        self._client.run("select-workspace", "--workspace", workspace_ref)
        self._focus_pane(pane_ref, workspace_ref)
        self._focus_surface(surface_ref, workspace_ref)
        return surface_ref

    # === browser tabs in a designated browser pane ===

    def ensure_browser(self, pane_index: int, tab: BrowserTab) -> str | None:
        """Ensure *at least one* browser matching ``tab`` exists in pane ``pane_index``.

        Recycle rule (stated once, here): if a browser whose current URL prefix
        matches ``tab.match`` exists, navigate it in place; otherwise open a new
        background browser tab in pane ``pane_index`` (splitting a new pane off
        the rightmost surface, focus-safely, if pane ``pane_index`` doesn't exist
        yet). Returns the surface ref, or ``None``.
        """
        existing = self._find_browser_by_url(tab.match_prefix)
        if existing is not None:
            # Recycle in place — no close, no recreate, no focus capture.
            if self._navigate_surface(existing.ref, tab.url):
                return existing.ref
            # Navigation failed; fall through to open a fresh tab.

        pane_ref = self._pane_at_index(None, pane_index)
        if pane_ref is not None:
            return self._open_browser_surface(pane_ref, tab.url)
        return self._open_browser_in_new_pane(tab.url)

    # === removal ===

    def ensure_absent_browser(self, url_prefix: str) -> bool:
        """Close the browser whose URL prefix-matches ``url_prefix``, if any."""
        browser = self._find_browser_by_url(url_prefix)
        if browser is None:
            return False
        return self._close_surface(browser.ref)

    def ensure_absent_pane(self, pane_index: int) -> bool:
        """Collapse the pane at ``pane_index`` (close its surface), if present."""
        pane_ref = self._pane_at_index(None, pane_index)
        if pane_ref is None:
            return False
        surface = self._pane_surface(pane_ref)
        if surface is None:
            return False
        return self._close_surface(surface)

    # === status / teardown ===

    def set_status(self, text: str) -> bool:
        """Set the cmux task status line."""
        return self._client.run(
            "set-status", "task", text, "--icon", "hammer",
        ).ok

    def clear_status(self) -> bool:
        """Clear the cmux task status line."""
        return self._client.run("clear-status", "task").ok

    def close(self) -> bool:
        """Close the whole workspace. No-op (False) if it doesn't exist."""
        workspace_ref = self._find_workspace()
        if workspace_ref is None:
            return False
        return self._client.run(
            "close-workspace", "--workspace", workspace_ref,
        ).ok

    # === private: terminal-tab mechanics ===

    def _run_tab(
        self, surface_ref: str, tab: TerminalTab, workspace_ref: str | None,
    ) -> None:
        """Send a tab's ``cd`` then command into an existing terminal surface."""
        if tab.cwd is not None:
            self._send(surface_ref, f"cd {tab.cwd}\n", workspace_ref)
        if tab.command is not None:
            self._send(surface_ref, f"{tab.command}\n", workspace_ref)

    def _send_to_workspace(self, workspace_ref: str, text: str) -> None:
        """Send ``text`` to a workspace's active surface (no explicit surface ref)."""
        self._client.run("send", "--workspace", workspace_ref, "--", text)

    def _rename_pane_tab(
        self, workspace_ref: str, pane_index: int, title: str,
    ) -> None:
        """Rename the (selected) surface of the pane at ``pane_index`` to ``title``."""
        if not title:
            return
        pane = self._pane_at_index(workspace_ref, pane_index)
        surface = self._pane_surface(pane, workspace_ref) if pane else None
        if surface:
            self._client.run("rename-tab", "--surface", surface, title)

    # === private cmux primitives (thin wrappers over self._client.run) ===

    def _find_workspace(self) -> str | None:
        """Workspace ref for this layout's name (first match), else None.

        Parses list-workspaces output, e.g. lines like:
          * workspace:13  maelstrom-bravo  [selected]
        """
        output = self._client.run("list-workspaces").raw
        if not output:
            return None
        for line in output.splitlines():
            match = re.match(r".*?(workspace:\d+)\s+(\S+)", line)
            if match and match.group(2) == self._name:
                return match.group(1)
        return None

    def _list_panes(self, workspace_ref: str | None = None) -> list[str]:
        """Pane refs left→right, or [] if none.

        Uses re.findall (order-preserving) so it works for both space- and
        newline-separated output.
        """
        args = ["list-panes"]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        output = self._client.run(*args).raw
        if not output:
            return []
        return re.findall(r"pane:\d+", output)

    def _pane_at_index(
        self, workspace_ref: str | None, index: int,
    ) -> str | None:
        """Pane at ``index`` (left→right; negatives from the right), or None."""
        panes = self._list_panes(workspace_ref)
        if -len(panes) <= index < len(panes):
            return panes[index]
        return None

    def _add_terminal_tab(
        self, workspace_ref: str, pane_ref: str | None, title: str | None,
    ) -> str | None:
        """New terminal surface (tab) in the workspace, optionally in ``pane_ref``.

        Omits --pane when ``pane_ref`` is None (cmux uses its default pane).
        Renames the surface to ``title`` if given. Returns the surface ref.
        new-surface replies "OK surface:N pane:N workspace:N"; only the leading
        surface ref is a valid --surface handle.
        """
        args = ["new-surface", "--type", "terminal"]
        if pane_ref:
            args.extend(["--pane", pane_ref])
        args.extend(["--workspace", workspace_ref])
        surface_ref = self._client.run(*args).ref("surface")
        if surface_ref is None:
            return None
        if title:
            self._client.run("rename-tab", "--surface", surface_ref, title)
        return surface_ref

    def _open_browser_surface(
        self, pane_ref: str, url: str, workspace_ref: str | None = None,
    ) -> str | None:
        """Open a browser tab in ``pane_ref`` and return its surface ref.

        Mirrors _add_terminal_tab but for browser surfaces. new-surface replies
        "OK surface:N pane:N workspace:N"; only the leading surface ref is usable.
        """
        args = ["new-surface", "--type", "browser", "--pane", pane_ref, "--url", url]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        return self._client.run(*args).ref("surface")

    def _pane_surface(
        self, pane_ref: str, workspace_ref: str | None = None,
    ) -> str | None:
        """The (selected) surface ref of a pane, or None.

        Reads ``list-pane-surfaces`` and returns the first surface ref. Used to
        get a ``--surface`` handle for a pane without focusing it (focusing a
        pane in another workspace would switch the selected workspace).
        """
        args = ["list-pane-surfaces", "--pane", pane_ref]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        output = self._client.run(*args).raw
        if not output:
            return None
        match = re.search(r"surface:\d+", output)
        return match.group(0) if match else None

    def _split_pane_off(
        self,
        surface_ref: str,
        direction: str = "right",
        workspace_ref: str | None = None,
    ) -> str | None:
        """Split a new pane off ``surface_ref``'s pane in ``direction``.

        Uses ``new-split --surface``, which targets a specific surface **without
        focusing it** — unlike ``new-pane``, which splits the focused pane and so
        requires a focus call that would steal workspace focus cross-workspace.
        Returns the new (placeholder) pane's ref, or None on failure.
        """
        args = ["new-split", direction, "--surface", surface_ref]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        if not self._client.run(*args).ok:
            return None
        # new-split returns the new surface but not its pane ref; the split lands
        # a new rightmost pane, now last in left→right order.
        return self._pane_at_index(workspace_ref, -1)

    def _split_new_pane(self, workspace_ref: str) -> str | None:
        """Split a new rightmost terminal pane off the workspace, focus-safely.

        Returns the new pane ref, or None on failure.
        """
        rightmost = self._pane_at_index(workspace_ref, -1)
        if rightmost is None:
            return None
        rightmost_surface = self._pane_surface(rightmost, workspace_ref)
        if rightmost_surface is None:
            return None
        return self._split_pane_off(
            rightmost_surface, direction="right", workspace_ref=workspace_ref,
        )

    def _open_browser_in_new_pane(self, url: str) -> str | None:
        """Split a new rightmost pane (focus-safe) and open url as a browser.

        Splits off the rightmost pane via ``new-split --surface`` (no focus),
        opens the browser tab in the resulting pane, then discards the
        placeholder terminal surface the split created. The browser tab is opened
        before the placeholder is closed so the pane always retains a surface.
        Returns the browser surface ref, or None on failure.
        """
        rightmost = self._pane_at_index(None, -1)
        rightmost_surface = self._pane_surface(rightmost) if rightmost else None
        if not rightmost_surface:
            return None
        new_pane = self._split_pane_off(rightmost_surface, direction="right")
        if not new_pane:
            return None
        placeholder = self._pane_surface(new_pane)
        ref = self._open_browser_surface(new_pane, url)
        if placeholder and placeholder != ref:
            self._close_surface(placeholder)
        return ref

    def _navigate_surface(self, surface_ref: str, url: str) -> bool:
        """Navigate an existing browser surface to url via ``browser goto``."""
        return self._client.run(
            "browser", "--surface", surface_ref, "goto", url,
        ).ok

    def _send(
        self, surface_ref: str, text: str, workspace_ref: str | None = None,
    ) -> None:
        """Send ``text`` verbatim into a surface.

        ``workspace_ref`` must be passed when the surface lives in a workspace
        other than the caller's: ``cmux send`` defaults ``--workspace`` to the
        caller's ``$CMUX_WORKSPACE_ID``, and without the right one it can't find
        the surface (it fails with "Surface is not a terminal").
        """
        ws = ["--workspace", workspace_ref] if workspace_ref else []
        self._client.run("send", "--surface", surface_ref, *ws, "--", text)

    def _focus_pane(self, pane_ref: str, workspace_ref: str | None = None) -> None:
        """Focus a pane, optionally within a specific workspace."""
        args = ["focus-pane", "--pane", pane_ref]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        self._client.run(*args)

    def _focus_surface(
        self, surface_ref: str, workspace_ref: str | None = None,
    ) -> None:
        """Make a surface the visible tab via focus-panel (surfaces are panels)."""
        args = ["focus-panel", "--panel", surface_ref]
        if workspace_ref:
            args.extend(["--workspace", workspace_ref])
        self._client.run(*args)

    def _close_surface(self, surface_ref: str) -> bool:
        """Close a cmux surface by its ref."""
        return self._client.run("close-surface", "--surface", surface_ref).ok

    # === browser state queries ===

    def _list_surfaces(self) -> list[Surface]:
        """Parse ``list-panels`` into :class:`Surface` value objects.

        Expected format (one per line):
          surface:103  terminal  "title"
        * surface:104  terminal  [focused]  "title"
          surface:183  browser  "title"
        """
        output = self._client.run("list-panels").raw or ""
        surfaces: list[Surface] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            focused = line.startswith("*")
            if focused:
                line = line[1:].strip()
            match = re.match(
                r'(surface:\d+)\s+(terminal|browser)\s+(?:\[focused\]\s+)?"(.*)"',
                line,
            )
            if match:
                surfaces.append(Surface(
                    ref=match.group(1),
                    type=match.group(2),  # type: ignore[arg-type]
                    title=match.group(3),
                    focused=focused,
                ))
        return surfaces

    def _browser_url(self, surface_ref: str) -> str | None:
        """Current URL of a browser surface via ``browser get-url``."""
        return self._client.run("browser", "get-url", "--surface", surface_ref).raw

    def _find_browser_by_url(self, url_prefix: str) -> Surface | None:
        """First browser surface whose current URL starts with ``url_prefix``.

        Queries each browser surface's actual URL via ``browser get-url``; other
        browsers (docs, unrelated pages) are ignored.
        """
        for surface in self._list_surfaces():
            if surface.type != "browser":
                continue
            url = self._browser_url(surface.ref)
            if url and url.startswith(url_prefix):
                return surface
        return None
